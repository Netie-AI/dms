"""Score a recogniser against the independently labelled corpus, and price it.

``scripts/cca_engagement.py`` answers "how good is the SHIPPED recogniser". This
answers "how good would a DIFFERENT one be", against the same 1712 questions,
the same labels, and the same two definitions, so the comparison is a number
rather than an argument.

    python scripts/cca_proposer_bench.py                       # the word list, free
    python scripts/cca_proposer_bench.py --proposer anthropic  # needs ANTHROPIC_API_KEY
    python scripts/cca_proposer_bench.py --proposer anthropic \\
        --model claude-sonnet-5 --limit 200 --out bench.json

Every model run costs real money, so ``--limit`` samples deterministically and
the script prints the estimated spend BEFORE it calls anything and the measured
spend after. Nothing here changes the shipped recogniser and nothing here can
turn the cascade on: DMS_CCA_CASCADE still gates that and still defaults to 0.

What a good number here would and would not license
---------------------------------------------------
A model that scores well has earned a place on the ask path behind the flag. It
has NOT earned the flag being flipped: that still needs the in-scope filter
floor in cca_engagement.py, which reads 0 today because 1232 public benchmark
questions contain none of the filters the packs claim. A recogniser cannot fix
a corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
for _pkg in ("packages/executor", "packages/core", "packages/cortex_client", "apps/api"):
    _p = str(ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cca_engagement as ce  # noqa: E402

#: Published per-MTok rates. Cache reads are ~0.1x input, writes ~1.25x.
#: Quoted so a run prices itself; confirm against the pricing page before
#: putting any of these numbers in front of a customer.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-8": (5.00, 25.00),
}


def _cost(model: str, usage: Mapping[str, int]) -> float:
    rate = PRICING.get(model)
    if not rate:
        return 0.0
    inp, out = rate
    return (
        usage.get("input", 0) * inp
        + usage.get("cache_read", 0) * inp * 0.1
        + usage.get("cache_write", 0) * inp * 1.25
        + usage.get("output", 0) * out
    ) / 1_000_000


def _sample(cases: Sequence[Mapping[str, Any]], limit: int | None) -> list[Mapping[str, Any]]:
    """Deterministic sample that keeps every filter-positive.

    Positives are the scarce class and the whole miss rate rests on them, so a
    random sample that thinned them would make the expensive half of the run
    the least informative part of it.
    """
    if not limit or limit >= len(cases):
        return list(cases)
    positives = [c for c in cases if c.get("carries_filter")]
    ordinary = [c for c in cases if not c.get("carries_filter")]
    ordinary.sort(key=lambda c: hashlib.sha256(str(c["id"]).encode()).hexdigest())
    room = max(0, limit - len(positives))
    return [*positives, *ordinary[:room]]


def build_proposer(kind: str, model: str | None) -> Any:
    from dms_executor.cca.proposer import (
        AnthropicProposer,
        LexiconProposer,
        ProposerUnavailable,
    )

    if kind == "lexicon":
        return LexiconProposer()
    if kind == "anthropic":
        return AnthropicProposer(model=model)
    raise ProposerUnavailable(f"--proposer {kind!r} is not benchable here")


def run(
    proposer: Any,
    cases: Sequence[Mapping[str, Any]],
    *,
    model: str,
    verbose: bool = False,
) -> tuple[list[ce.CaseResult], ce.Summary, dict[str, Any]]:
    _engages, polarity_is_unsettled, _proposals = ce._cascade()
    geo_index = ce._geo_pack_index()

    cache: dict[str, Any] = {}
    usage_total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    degraded: list[str] = []

    def proposal_for(question: str) -> Any:
        if question not in cache:
            p = proposer.propose(question)
            cache[question] = p
            got = dict(getattr(proposer, "last_usage", {}) or {})
            for k in usage_total:
                usage_total[k] += int(got.get(k, 0))
            if p.degraded:
                degraded.append(f"{question[:60]}: {p.degraded_reason}")
        return cache[question]

    started = time.monotonic()
    results = [
        ce.judge_case(
            case,
            engages=lambda q: proposal_for(q).engages,
            polarity_is_unsettled=polarity_is_unsettled,
            proposals_of=lambda q: proposal_for(q).as_proposal_flags(),
            geo_index=geo_index,
            corpus_id=str(case.get("_corpus") or ""),
        )
        for case in cases
    ]
    elapsed = time.monotonic() - started

    meta = {
        "proposer": proposer.name,
        "model": model,
        "questions": len(cache),
        "usage": usage_total,
        "cost_usd": round(_cost(model, usage_total), 4),
        "seconds": round(elapsed, 1),
        "per_question_ms": round(elapsed * 1000 / max(len(cache), 1), 1),
        "degraded": len(degraded),
        "degraded_examples": degraded[:5],
    }
    if verbose:
        for r in results:
            if r.outcome in {ce.FALSE_ENGAGE, ce.FALSE_MISS}:
                print(f"  {r.outcome:<12} {r.question[:80]}")
                print(f"               {r.detail}")
    return results, ce.score(results), meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--proposer", default="lexicon", choices=["lexicon", "anthropic"])
    ap.add_argument("--model", default=None, help="model id for --proposer anthropic")
    ap.add_argument("--limit", type=int, default=None, help="sample size; keeps all positives")
    ap.add_argument(
        "--independent-only",
        action="store_true",
        help="score only tier A_external and B_product_log, the corpora that may move the flag",
    )
    ap.add_argument("--verbose", action="store_true", help="print every failure")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--yes", action="store_true", help="skip the spend confirmation")
    args = ap.parse_args(argv)

    cases: list[dict[str, Any]] = []
    for path in ce.discover_corpora():
        corpus = ce.load_labels(path)
        if args.independent_only and not ce.is_independent(corpus):
            continue
        for case in ce.labelled_questions(corpus):
            row = dict(case)
            row["_corpus"] = path.name
            cases.append(row)
    if not cases:
        print("FAIL no labelled cases", file=sys.stderr)
        return 1

    chosen = _sample(cases, args.limit)
    model = args.model or (
        __import__("dms_executor.cca.proposer", fromlist=["DEFAULT_MODEL"]).DEFAULT_MODEL
        if args.proposer == "anthropic"
        else "n/a"
    )

    if args.proposer != "lexicon" and not args.yes:
        # ~1.5k cached system + ~40 question tokens in, ~120 out. Deliberately
        # an over-estimate: nobody has ever been annoyed by a bill smaller than
        # the warning.
        rate = PRICING.get(model, (5.0, 25.0))
        est = len(chosen) * ((1600 * rate[0] * 0.1) + (120 * rate[1])) / 1_000_000
        print(
            f"About to send {len(chosen)} questions to {model}.\n"
            f"Rough estimate: ${est:.2f} (assumes the system prompt caches).\n"
            "Re-run with --yes to proceed.",
            file=sys.stderr,
        )
        return 2

    proposer = build_proposer(args.proposer, args.model)
    results, summary, meta = run(proposer, chosen, model=model, verbose=args.verbose)

    payload = {
        "kind": "dms.cca_proposer_bench",
        "meta": meta,
        "summary": summary.as_dict(),
        "sampled": len(chosen),
        "of_total": len(cases),
        "independent_only": bool(args.independent_only),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"=== {meta['proposer']} / {meta['model']} ===")
        print(f"  questions      {meta['questions']} of {len(cases)} labelled")
        print(
            f"  false-engage   {ce._pct(summary.false_engage_rate)}   "
            f"({summary.false_engage}/{summary.ordinary} ordinary)"
        )
        print(
            f"  false-miss     {ce._pct(summary.false_miss_rate)}   "
            f"({summary.false_miss}/{summary.filter_positive} filter-positive)"
        )
        print(
            f"      in-scope positives {summary.in_scope_positive}; misses by scope "
            f"in={summary.false_miss_in_scope} out={summary.false_miss_out_of_scope} "
            f"unknown={summary.false_miss_scope_unknown}"
        )
        print(f"  uncertain      {summary.uncertain}   polarity {summary.polarity}")
        print(
            f"  cost           ${meta['cost_usd']}   "
            f"({meta['per_question_ms']} ms/question, {meta['degraded']} degraded)"
        )
        if meta["degraded"]:
            print("  DEGRADED CALLS - these are not 'no filter found', they are 'no answer':")
            for line in meta["degraded_examples"]:
                print(f"      {line}")
        print()
        print("  A good number here earns a place behind the flag, not the flag.")
        print("  cca_engagement.py's in-scope floor still gates DMS_CCA_CASCADE.")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
