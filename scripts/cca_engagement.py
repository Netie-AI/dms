"""CCA engagement measurement: two rates, against independently labelled questions.

The binders in ``dms_executor.cca`` are measured by ``scripts/cca_eval.py``.
That gate answers "given that this ask names a filter, did we bind the right
landed spellings?". It cannot answer the question that decides whether the
ask-path hook may turn on:

  of ordinary work, how often does the cascade engage and then refuse?
  of asks that actually name a filter, how often does it fail to notice?

Those two numbers were previously opinions about this product's own vocabulary
(46/106 and 35/37), taken by the same people who wrote the lexicon. This
script re-measures them against a corpus whose questions were harvested from
product surfaces that predate the cascade, and whose labels were written by a
labeler who was not allowed to read the lexicon.

  python scripts/cca_engagement.py

  python scripts/cca_engagement.py --json

Exit 0 always reports. Exit 1 only when the corpus will not load, a source
question has no label, or the labelled file is internally inconsistent. A high
false-engage or false-miss rate is the measurement, not a gate failure: failing
the run on those rates would train the next edit to retune ``intent.py`` until
the numbers look good, which is how the last round got 100 pct precision on a
hand-picked set and still refused 43 pct of ordinary work.

The ask-path hook stays off until both rates are low enough on *this* corpus
that turning it on would not make the product worse in either direction. That
bar is declared below and asserted by tests/test_cca_engagement.py against the
default of ``cascade_enabled()``.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

CORPUS_DIR = ROOT / "tests" / "fixtures" / "cca_eval"

LABELS = CORPUS_DIR / "engagement_labels.json"

#: Every labelled corpus this repo carries. The product log is the original
#: 77-question file; the others are added by ``--corpus`` or discovered here.
CORPUS_GLOB = "engagement_labels*.json"

#: A corpus counts toward the ship criterion only when its questions were
#: written by people who had never heard of this system. Questions authored in
#: the same session as the measurement are a stress probe, not evidence: an
#: author who knows what is being measured writes toward it, whether or not
#: they mean to. Both are reported; only the independent ones decide.
INDEPENDENT_TIERS = ("B_product_log", "A_external")

#: Kinds a label may name. These are the cascade stages a customer-facing
#: filter can occupy; the labeler is told the product concepts, not the alias
#: lists.
KINDS = ("sense", "asset_class", "geo", "segment")

#: Map from a labelled kind to the key ``_proposals`` returns.
_PROPOSAL_KEY = {
    "sense": "sense",
    "asset_class": "class",
    "geo": "geo",
    "segment": "segment",
}

#: Both rates must be at or under this before the ask-path hook may default
#: on. 0.05 is a ceiling, not a target: a control that still refuses 1 in 20
#: ordinary questions, or silently passes 1 in 20 named filters, is not
#: ready. The number exists so the flag has a criterion rather than a vibe.
SHIP_CEILING = 0.05

#: A rate computed on fewer labelled cases than this is not a rate, it is an
#: anecdote. The flag cannot flip on an anecdote in either direction.
MIN_ORDINARY = 40
MIN_FILTER = 8

WHAT_THIS_MEASURES = """\
=== WHAT THIS MEASURES, AND WHAT IT DOES NOT ===
  It calls cascade.engages / _proposals against questions harvested from the
  product's own chat surfaces (demo, CEO walkthrough, playground, hostile
  pack, free-form demo) and labelled by someone who did not write the
  lexicon. It does not POST /v1/chat/ask, does not start the API, and never
  reaches Cortex. It proves nothing about badges, values, rows or rendered
  text. It answers only: did the cascade decide this question carries a
  filter, and did that decision match an independent label of the same
  question.
  The golden binder corpus (tests/fixtures/cca_eval/corpus.json) is out of
  scope on purpose. Those questions were written by the lexicon authors.
  Measuring engagement against them would reproduce the 100 pct precision
  that invited the last overclaim."""

DEFINITIONS = """\
=== SCORING DEFINITIONS ===
  ordinary            labelled carries_filter=false. The product should answer
                      these the way it does today. The cascade must stay out.
  filter-positive     labelled carries_filter=true. At least one of sense,
                      asset_class, geo, segment restricts which rows count.
  FALSE ENGAGE        ordinary question where engages() is True. The control
                      is about to refuse (or constrain) work it was not asked
                      to touch.
  FALSE MISS          filter-positive question where at least one labelled
                      kind was not proposed. The named filter never reaches
                      the binder, so a later green trace is unconstrained on
                      the thing the customer named.
  uncertain           the labeler marked the case. Counted apart, never mixed
                      into either rate.
  polarity-unsettled  the label names exclude / comparison / mixed /
                      unsettled, or the cascade's own polarity guard fires.
                      Reported, not scored as engage/miss: polarity currently
                      fails closed and that trade is pinned elsewhere.
  false-engage rate   FALSE ENGAGE / ordinary. Target for flipping the flag:
                      <= 5 pct on at least 40 ordinary questions.
  false-miss rate     FALSE MISS / filter-positive. Target for flipping the
                      flag: <= 5 pct on at least 8 filter-positive questions."""


# ---------------------------------------------------------------------------
# Harvest. Product surfaces only. Never the CCA lexicon or its golden corpus.
# ---------------------------------------------------------------------------

_BANNED_SOURCE_FRAGMENTS = (
    "dms_executor/cca/",
    "tests/fixtures/cca_eval/corpus.json",
    "tests/test_cca_",
    "packages/executor/dms_executor/cca/",
)


def _norm_q(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip()


def _add(
    out: dict[str, dict[str, str]],
    *,
    question: str,
    source: str,
    source_id: str,
) -> None:
    q = _norm_q(question)
    if not q or q.endswith("{table_short}?"):
        return
    key = q.casefold()
    if key in out:
        return
    out[key] = {
        "id": source_id,
        "question": q,
        "source": source,
        "source_id": source_id,
    }


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_strs(node: ast.Dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            text = _const_str(val)
            if text is not None:
                out[key.value] = text
    return out


def _assign_named(tree: ast.AST, name: str) -> ast.AST | None:
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return node.value
    return None


def _cases_from_list(node: ast.AST, _source: str) -> list[tuple[str, str]]:
    if not isinstance(node, ast.List):
        return []
    found: list[tuple[str, str]] = []
    for elt in node.elts:
        if not isinstance(elt, ast.Dict):
            continue
        fields = _dict_strs(elt)
        question = fields.get("question")
        if not question:
            continue
        found.append((fields.get("id") or question[:40], question))
    return found


def _from_yaml(path: Path, question_key: str) -> list[tuple[str, str]]:
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = payload.get("questions") or []
    found: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = row.get(question_key) or row.get("question") or row.get("prompt")
        if not isinstance(text, str) or not text.strip():
            continue
        found.append((str(row.get("id") or text[:40]), text))
    return found


def harvest(root: Path = ROOT) -> list[dict[str, str]]:
    """Unique product questions, first source wins, CCA files excluded.

    Reads the source files as data (AST / YAML / regex). It does not import
    ``dms_executor`` and so cannot accidentally pick up the lexicon.
    """
    out: dict[str, dict[str, str]] = {}

    freeform = ast.parse((root / "scripts" / "verify_freeform_demo.py").read_text(encoding="utf-8"))
    for source_id, question in _cases_from_list(
        _assign_named(freeform, "DEMO_SET") or ast.List(elts=[]),
        "scripts/verify_freeform_demo.py",
    ):
        _add(
            out,
            question=question,
            source="scripts/verify_freeform_demo.py",
            source_id=source_id,
        )

    hostile = ast.parse((root / "scripts" / "score_answers.py").read_text(encoding="utf-8"))
    for source_id, question in _cases_from_list(
        _assign_named(hostile, "QUESTION_PACK") or ast.List(elts=[]),
        "scripts/score_answers.py",
    ):
        _add(
            out,
            question=question,
            source="scripts/score_answers.py",
            source_id=source_id,
        )

    l2 = ast.parse((root / "scripts" / "verify_l2_vs_l1.py").read_text(encoding="utf-8"))
    for name in ("L0_L1_CASES", "L2_CASES"):
        for source_id, question in _cases_from_list(
            _assign_named(l2, name) or ast.List(elts=[]),
            "scripts/verify_l2_vs_l1.py",
        ):
            _add(
                out,
                question=question,
                source="scripts/verify_l2_vs_l1.py",
                source_id=source_id,
            )

    constructor_src = (root / "scripts" / "constructor_source.py").read_text(encoding="utf-8")
    constructor = ast.parse(constructor_src)
    asks = _assign_named(constructor, "OBJECT_ASKS")
    if isinstance(asks, ast.Dict):
        for key, val in zip(asks.keys, asks.values):
            obj = _const_str(key)
            question = _const_str(val)
            if obj and question:
                _add(
                    out,
                    question=question,
                    source="scripts/constructor_source.py",
                    source_id=f"constructor_{obj}",
                )

    for rel, key in (
        ("tests/fixtures/curated_ceo/questions.yaml", "question"),
        ("playground/questions.yaml", "prompt"),
        ("playground/my_questions.yaml", "prompt"),
    ):
        path = root / rel
        for source_id, text in _from_yaml(path, key):
            _add(out, question=text, source=rel, source_id=source_id)

    demo_live = ast.parse((root / "scripts" / "verify_demo_live.py").read_text(encoding="utf-8"))
    for name in ("REVENUE", "WHERE_SKU", "SCALAR_Q"):
        value = _const_str(_assign_named(demo_live, name))
        if value:
            _add(
                out,
                question=value,
                source="scripts/verify_demo_live.py",
                source_id=name,
            )
    demo_text = (root / "scripts" / "verify_demo_live.py").read_text(encoding="utf-8")
    for question in re.findall(r'ask\(\s*c,\s*"([^"{}]+)"', demo_text):
        if " " not in question:
            continue
        _add(
            out,
            question=question,
            source="scripts/verify_demo_live.py",
            source_id=_norm_q(question)[:40],
        )

    # Act A of the runbook is the live customer script. Other backtick cells are
    # paths and commands, not questions.
    runbook = (root / "docs" / "DEMO_RUNBOOK.md").read_text(encoding="utf-8")
    act_a = re.search(
        r"### Act A.*?\n\| # \|.*?\n\|---.*?\n(?P<body>(?:\|.*\n)+)",
        runbook,
    )
    if act_a:
        for i, question in enumerate(re.findall(r"`([^`]+)`", act_a.group("body")), start=1):
            if question.startswith(":") or "\\" in question or question.startswith("http"):
                continue
            _add(
                out,
                question=question.replace("…", "").strip(),
                source="docs/DEMO_RUNBOOK.md",
                source_id=f"runbook_a{i}",
            )

    for item in out.values():
        source = item["source"].replace("\\", "/")
        if any(frag in source for frag in _BANNED_SOURCE_FRAGMENTS):
            raise RuntimeError(f"harvest pulled a banned source: {source}")

    return sorted(out.values(), key=lambda r: (r["source"], r["id"]))


def load_labels(path: Path = LABELS) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def labelled_questions(corpus: Mapping[str, Any]) -> list[dict[str, Any]]:
    cases = list(corpus.get("cases") or [])
    if not cases:
        raise ValueError("engagement_labels.json has no cases")
    return cases


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

FALSE_ENGAGE = "FALSE ENGAGE"
FALSE_MISS = "FALSE MISS"
OK_ORDINARY = "ok ordinary"
OK_FILTER = "ok filter"
UNCERTAIN = "uncertain"
POLARITY = "polarity"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    question: str
    carries_filter: bool
    uncertain: bool
    outcome: str
    detail: str
    engaged: bool
    proposed: tuple[str, ...]
    labelled_kinds: tuple[str, ...]
    geo_scope: str = "unknown"
    corpus: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "question": self.question,
            "carries_filter": self.carries_filter,
            "uncertain": self.uncertain,
            "outcome": self.outcome,
            "detail": self.detail,
            "engaged": self.engaged,
            "proposed": list(self.proposed),
            "labelled_kinds": list(self.labelled_kinds),
            "geo_scope": self.geo_scope,
            "corpus": self.corpus,
        }


@dataclass(frozen=True)
class Summary:
    total: int
    ordinary: int
    filter_positive: int
    uncertain: int
    false_engage: int
    false_miss: int
    polarity: int
    ordinary_ok: int
    filter_ok: int
    false_miss_in_scope: int = 0
    false_miss_out_of_scope: int = 0
    false_miss_scope_unknown: int = 0
    failures: tuple[str, ...] = ()

    @property
    def false_engage_rate(self) -> float | None:
        if self.ordinary < 1:
            return None
        return self.false_engage / self.ordinary

    @property
    def false_miss_rate(self) -> float | None:
        if self.filter_positive < 1:
            return None
        return self.false_miss / self.filter_positive

    @property
    def shippable(self) -> bool:
        """May the ask-path hook default on? Both rates, both floors."""
        fe = self.false_engage_rate
        fm = self.false_miss_rate
        if fe is None or fm is None:
            return False
        if self.ordinary < MIN_ORDINARY or self.filter_positive < MIN_FILTER:
            return False
        return fe <= SHIP_CEILING and fm <= SHIP_CEILING

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "dms.cca_engagement",
            "total": self.total,
            "ordinary": self.ordinary,
            "filter_positive": self.filter_positive,
            "uncertain": self.uncertain,
            "false_engage": self.false_engage,
            "false_miss": self.false_miss,
            "polarity": self.polarity,
            "ordinary_ok": self.ordinary_ok,
            "filter_ok": self.filter_ok,
            "false_miss_in_scope": self.false_miss_in_scope,
            "false_miss_out_of_scope": self.false_miss_out_of_scope,
            "false_miss_scope_unknown": self.false_miss_scope_unknown,
            "false_engage_rate": (
                None if self.false_engage_rate is None else round(self.false_engage_rate, 4)
            ),
            "false_miss_rate": (
                None if self.false_miss_rate is None else round(self.false_miss_rate, 4)
            ),
            "ship_ceiling": SHIP_CEILING,
            "min_ordinary": MIN_ORDINARY,
            "min_filter": MIN_FILTER,
            "shippable": self.shippable,
            "failures": list(self.failures),
        }


def _geo_pack_index() -> dict[str, str]:
    """Normalised alias -> canonical member for every declared geo region pack.

    Read by the SCORER, never by a labeler. The labeler records which place a
    question named, which is an extraction; deciding whether that place is
    inside the shipped pack is the measurement's job, not theirs.
    """
    try:
        from dms_executor.cca.geo import GEO_REGION_MEMBERS
    except Exception:  # noqa: BLE001 - scorer must still run without the pack
        return {}
    index: dict[str, str] = {}
    for pack in GEO_REGION_MEMBERS.values():
        index.update(pack.alias_index())
    return index


def geo_scope(case: Mapping[str, Any], index: Mapping[str, str]) -> str:
    """Is the place this question named inside the shipped geo pack?

    ``in_scope``      the pack claims to know this place, so a miss is a
                      recognition failure and counts against the hook.
    ``out_of_scope``  the pack has never claimed to cover this place. A miss
                      here measures pack coverage, which is a different and
                      much larger question than whether the cue rule works.
    ``unknown``       the label recorded no place string.
    """
    values = [
        str(f.get("value") or "").strip()
        for f in (case.get("filters") or ())
        if str(f.get("kind")) == "geo"
    ]
    values = [v for v in values if v]
    if not values:
        return "unknown"
    if not index:
        return "unknown"
    for value in values:
        key = re.sub(r"[^0-9a-z]+", " ", value.casefold()).strip()
        if key in index:
            return "in_scope"
    return "out_of_scope"


def _labelled_kinds(case: Mapping[str, Any]) -> tuple[str, ...]:
    kinds: list[str] = []
    for filt in case.get("filters") or ():
        kind = str(filt.get("kind") or "")
        if kind in KINDS and kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def _cascade():
    """Import the cascade only when scoring, never when harvesting labels."""
    for package in ("packages/executor", "packages/core", "packages/cortex_client", "apps/api"):
        path = str(ROOT / package)
        if path not in sys.path:
            sys.path.insert(0, path)
    from dms_executor.cca.cascade import _proposals, engages, polarity_is_unsettled

    return engages, polarity_is_unsettled, _proposals


def _proposed_kinds(question: str, proposals: Mapping[str, bool]) -> tuple[str, ...]:
    return tuple(kind for kind, key in _PROPOSAL_KEY.items() if proposals.get(key))


def judge_case(
    case: Mapping[str, Any],
    *,
    engages,
    polarity_is_unsettled,
    proposals_of,
    geo_index: Mapping[str, str] | None = None,
    corpus_id: str = "",
) -> CaseResult:
    question = _norm_q(str(case["question"]))
    carries = bool(case.get("carries_filter"))
    uncertain = bool(case.get("uncertain"))
    labelled = _labelled_kinds(case)
    proposed = _proposed_kinds(question, proposals_of(question))
    engaged = bool(engages(question))
    polar_label = str(case.get("polarity") or "none")
    polar_cascade = bool(polarity_is_unsettled(question))

    if uncertain:
        outcome, detail = UNCERTAIN, "labeler marked uncertain; counted apart"
    elif polar_label in {"exclude", "comparison", "mixed", "unsettled"} or (
        carries and polar_cascade
    ):
        # Polarity is a different failure class, already fail-closed. Do not
        # launder it into engage/miss: an exclusion that abstains is the
        # current honest behaviour, not a miss of recognition.
        outcome, detail = POLARITY, f"label polarity={polar_label}, cascade={polar_cascade}"
    elif not carries:
        if engaged:
            outcome, detail = FALSE_ENGAGE, f"ordinary ask proposed {list(proposed)}"
        else:
            outcome, detail = OK_ORDINARY, "did not engage, as it must not"
    else:
        missing = [k for k in labelled if k not in proposed]
        if not labelled:
            missing = ["<unspecified>"] if not engaged else []
        if missing or not engaged:
            outcome, detail = FALSE_MISS, (
                f"labelled {list(labelled)} but proposed {list(proposed)}"
            )
        else:
            outcome, detail = OK_FILTER, f"proposed {list(proposed)}"

    return CaseResult(
        case_id=str(case["id"]),
        question=question,
        carries_filter=carries,
        uncertain=uncertain,
        outcome=outcome,
        detail=detail,
        engaged=engaged,
        proposed=proposed,
        labelled_kinds=labelled,
        geo_scope=geo_scope(case, geo_index or {}),
        corpus=corpus_id,
    )


def score(results: Sequence[CaseResult]) -> Summary:
    def n(label: str) -> int:
        return sum(1 for r in results if r.outcome == label)

    ordinary = [r for r in results if r.outcome in {OK_ORDINARY, FALSE_ENGAGE}]
    positive = [r for r in results if r.outcome in {OK_FILTER, FALSE_MISS}]
    return Summary(
        total=len(results),
        ordinary=len(ordinary),
        filter_positive=len(positive),
        uncertain=n(UNCERTAIN),
        false_engage=n(FALSE_ENGAGE),
        false_miss=n(FALSE_MISS),
        polarity=n(POLARITY),
        ordinary_ok=n(OK_ORDINARY),
        filter_ok=n(OK_FILTER),
        false_miss_in_scope=sum(
            1 for r in results if r.outcome == FALSE_MISS and r.geo_scope == "in_scope"
        ),
        false_miss_out_of_scope=sum(
            1 for r in results if r.outcome == FALSE_MISS and r.geo_scope == "out_of_scope"
        ),
        false_miss_scope_unknown=sum(
            1 for r in results if r.outcome == FALSE_MISS and r.geo_scope == "unknown"
        ),
        failures=tuple(
            f"{r.case_id}: {r.outcome} - {r.detail}"
            for r in results
            if r.outcome in {FALSE_ENGAGE, FALSE_MISS}
        ),
    )


def evaluate(
    corpus: Mapping[str, Any], *, corpus_id: str = ""
) -> tuple[list[CaseResult], Summary]:
    engages, polarity_is_unsettled, proposals_of = _cascade()
    geo_index = _geo_pack_index()
    results = [
        judge_case(
            case,
            engages=engages,
            polarity_is_unsettled=polarity_is_unsettled,
            proposals_of=proposals_of,
            geo_index=geo_index,
            corpus_id=corpus_id or str(corpus.get("tier") or ""),
        )
        for case in labelled_questions(corpus)
    ]
    return results, score(results)


def coverage_gaps(harvested: Sequence[Mapping[str, str]], corpus: Mapping[str, Any]) -> list[str]:
    """Harvested questions that the labelled file does not yet carry.

    A new demo question with no label would silently drop out of both rates.
    """
    labelled = {_norm_q(str(c["question"])).casefold() for c in labelled_questions(corpus)}
    return [
        f"{row['source']} {row['id']}: {row['question']}"
        for row in harvested
        if _norm_q(row["question"]).casefold() not in labelled
    ]


def validate_corpus(corpus: Mapping[str, Any], harvested: Sequence[Mapping[str, str]]) -> list[str]:
    errors: list[str] = []
    labeler = corpus.get("labeler") or {}
    if not str(labeler.get("id") or "").strip():
        errors.append("labeler.id is missing")
    if not labeler.get("did_not_read"):
        errors.append("labeler.did_not_read is missing")
    ids: list[str] = []
    n_filter = 0
    for case in labelled_questions(corpus):
        cid = str(case.get("id") or "")
        if not cid:
            errors.append("a case has no id")
            continue
        ids.append(cid)
        src_obj = case.get("source")
        if isinstance(src_obj, dict):
            src = str(src_obj.get("path") or "")
        else:
            src = str(src_obj or "")
        src = src.replace("\\", "/")
        if any(frag in src for frag in _BANNED_SOURCE_FRAGMENTS):
            errors.append(f"{cid}: source is the lexicon or its golden corpus")
        if not _norm_q(str(case.get("question") or "")):
            errors.append(f"{cid}: empty question")
        if "carries_filter" not in case:
            errors.append(f"{cid}: carries_filter missing")
        if str(case.get("polarity") or "") not in {
            "include",
            "exclude",
            "comparison",
            "mixed",
            "none",
            "unsettled",
        }:
            errors.append(f"{cid}: polarity {case.get('polarity')!r} is not a declared value")
        if case.get("carries_filter") and not case.get("uncertain"):
            n_filter += 1
            kinds = _labelled_kinds(case)
            if not kinds:
                errors.append(f"{cid}: carries_filter true but filters[] names no kind")
            for filt in case.get("filters") or ():
                if str(filt.get("kind")) not in KINDS:
                    errors.append(f"{cid}: unknown filter kind {filt.get('kind')!r}")
        why = str(case.get("why") or "").strip()
        if not why:
            errors.append(f"{cid}: no why")
    if len(ids) != len(set(ids)):
        errors.append("case ids are not unique")
    tier = str(corpus.get("tier") or "")
    if not tier:
        errors.append("corpus declares no tier; independence cannot be established")
    elif tier not in {*INDEPENDENT_TIERS, "C_authored"}:
        errors.append(f"corpus tier {tier!r} is not a declared tier")
    # The n floors are a property of the whole independent measurement, not of
    # any one file, and are checked in combined_report. The harvest
    # cross-check applies only to the product log: an external corpus is not
    # expected to contain this repo's demo questions.
    if tier == "B_product_log":
        gaps = coverage_gaps(harvested, corpus)
        if gaps:
            errors.append(
                f"{len(gaps)} harvested question(s) have no label: " + "; ".join(gaps[:8])
            )
    if n_filter < 0:
        errors.append("negative filter count is impossible")
    return errors


def discover_corpora(directory: Path = CORPUS_DIR) -> list[Path]:
    """Every labelled corpus on disk, product log first."""
    found = sorted(directory.glob(CORPUS_GLOB))
    return sorted(found, key=lambda p: (p.name != LABELS.name, p.name))


def corpus_tier(corpus: Mapping[str, Any]) -> str:
    return str(corpus.get("tier") or "B_product_log")


def is_independent(corpus: Mapping[str, Any]) -> bool:
    return corpus_tier(corpus) in INDEPENDENT_TIERS


def by_source(
    results: Sequence[CaseResult], cases: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, int]]:
    """Per-origin counts, so one domain cannot be averaged away by another.

    A rate over a pooled corpus hides the case where the control is fine on
    warehouse questions and hopeless on property questions. The pooled number
    is the headline; this is the number that tells you whether to believe it.
    """
    origin: dict[str, str] = {}
    for case in cases:
        src = case.get("source")
        if isinstance(src, Mapping):
            label = str(src.get("path") or src.get("corpus") or "?")
        else:
            label = str(src or "?")
        origin[str(case.get("id"))] = label
    out: dict[str, dict[str, int]] = {}
    for res in results:
        key = origin.get(res.case_id, "?")
        row = out.setdefault(
            key,
            {"ordinary": 0, "false_engage": 0, "filter": 0, "false_miss": 0, "other": 0},
        )
        if res.outcome == OK_ORDINARY:
            row["ordinary"] += 1
        elif res.outcome == FALSE_ENGAGE:
            row["ordinary"] += 1
            row["false_engage"] += 1
        elif res.outcome == OK_FILTER:
            row["filter"] += 1
        elif res.outcome == FALSE_MISS:
            row["filter"] += 1
            row["false_miss"] += 1
        else:
            row["other"] += 1
    return out


def _pct(rate: float | None) -> str:
    if rate is None:
        return "n/a (n=0)"
    return f"{rate * 100:6.2f} pct"


def report(results: Sequence[CaseResult], summary: Summary) -> None:
    print(WHAT_THIS_MEASURES)
    print()
    print(DEFINITIONS)
    print()
    print(f"=== CASES ({summary.total}) ===")
    by = {}
    for res in results:
        by.setdefault(res.outcome, []).append(res)
    for outcome, group in by.items():
        print(f"  -- {outcome} ({len(group)})")
        for res in group:
            print(f"     {res.case_id:<42} engaged={int(res.engaged)}  {res.detail}")

    print("\n=== RESULT ===")
    print(
        f"  false-engage   {_pct(summary.false_engage_rate)}   "
        f"({summary.false_engage}/{summary.ordinary} ordinary)"
    )
    print(
        f"  false-miss     {_pct(summary.false_miss_rate)}   "
        f"({summary.false_miss}/{summary.filter_positive} filter-positive)"
    )
    print(f"  uncertain      {summary.uncertain}   (apart from both rates)")
    print(f"  polarity       {summary.polarity}   (fail-closed class, apart from both rates)")
    print(f"  shippable      {summary.shippable}   (ceiling {SHIP_CEILING * 100:.0f} pct, "
          f"floors n>={MIN_ORDINARY} ordinary and n>={MIN_FILTER} filter)")
    print()
    if summary.shippable:
        print("SHIP  both rates are at or under the ceiling on enough labelled cases.")
        print("      That is the criterion to consider DMS_CCA_CASCADE=1 as a default.")
        return
    print("HOLD  the ask-path hook stays off. Binding a term to landed data is")
    print("      solved; deciding from free text whether a question carries a")
    print("      filter is not, and these two numbers are why.")


def combined_report(loaded: Sequence[tuple[str, dict, list, Summary]]) -> dict[str, Any]:
    """Per corpus, then the pooled independent number that decides the flag."""
    ind_results: list[CaseResult] = []
    ind_cases: list[Mapping[str, Any]] = []
    stress_results: list[CaseResult] = []
    for _name, corpus, results, _summary in loaded:
        if is_independent(corpus):
            ind_results.extend(results)
            ind_cases.extend(labelled_questions(corpus))
        else:
            stress_results.extend(results)

    pooled = score(ind_results)
    stress = score(stress_results) if stress_results else None

    print("=== PER CORPUS ===")
    for name, corpus, _results, summary in loaded:
        tier = corpus_tier(corpus)
        mark = "independent" if is_independent(corpus) else "STRESS ONLY, not evidence"
        print(f"  {name}  [{tier}, {mark}]")
        print(
            f"      false-engage {_pct(summary.false_engage_rate)} "
            f"({summary.false_engage}/{summary.ordinary})   "
            f"false-miss {_pct(summary.false_miss_rate)} "
            f"({summary.false_miss}/{summary.filter_positive})"
        )

    print()
    print("=== BY ORIGIN (independent corpora only) ===")
    rows = by_source(ind_results, ind_cases)
    for src in sorted(rows):
        r = rows[src]
        fe = None if not r["ordinary"] else r["false_engage"] / r["ordinary"]
        fm = None if not r["filter"] else r["false_miss"] / r["filter"]
        print(
            f"  {src[:56]:<56} engage {_pct(fe)} ({r['false_engage']}/{r['ordinary']})"
            f"   miss {_pct(fm)} ({r['false_miss']}/{r['filter']})"
        )

    print()
    print("=== POOLED INDEPENDENT RESULT ===")
    print(
        f"  false-engage   {_pct(pooled.false_engage_rate)}   "
        f"({pooled.false_engage}/{pooled.ordinary} ordinary)"
    )
    print(
        f"  false-miss     {_pct(pooled.false_miss_rate)}   "
        f"({pooled.false_miss}/{pooled.filter_positive} filter-positive)"
    )
    print(
        f"      of those misses: {pooled.false_miss_in_scope} named a place the shipped geo "
        f"pack claims, {pooled.false_miss_out_of_scope} named one it never claimed, "
        f"{pooled.false_miss_scope_unknown} recorded no place"
    )
    print(f"  uncertain      {pooled.uncertain}   (apart from both rates)")
    print(f"  polarity       {pooled.polarity}   (fail-closed class, apart from both rates)")
    print(
        f"  shippable      {pooled.shippable}   (ceiling {SHIP_CEILING * 100:.0f} pct, "
        f"floors n>={MIN_ORDINARY} ordinary and n>={MIN_FILTER} filter)"
    )
    if stress is not None:
        print()
        print("=== AUTHORED STRESS SET (reported, never counted) ===")
        print(
            f"  false-engage   {_pct(stress.false_engage_rate)}   "
            f"({stress.false_engage}/{stress.ordinary})   "
            f"false-miss {_pct(stress.false_miss_rate)}   "
            f"({stress.false_miss}/{stress.filter_positive})"
        )
        print("  These questions were written in the same session as the measurement.")
        print("  They probe shapes the benchmarks underweight. They are not evidence,")
        print("  and no lexicon edit may be justified by them.")

    print()
    if pooled.shippable:
        print("SHIP  both rates are at or under the ceiling on enough independent cases.")
        print("      That is the criterion to consider DMS_CCA_CASCADE=1 as a default.")
    else:
        print("HOLD  the ask-path hook stays off. Binding a term to landed data is")
        print("      solved; deciding from free text whether a question carries a")
        print("      filter is not, and these numbers are why.")

    return {
        "kind": "dms.cca_engagement.multi",
        "pooled_independent": pooled.as_dict(),
        "stress": None if stress is None else stress.as_dict(),
        "by_origin": rows,
        "corpora": [
            {
                "name": name,
                "tier": corpus_tier(corpus),
                "independent": is_independent(corpus),
                "summary": summary.as_dict(),
            }
            for name, corpus, _results, summary in loaded
        ],
        "cases": [r.as_dict() for _n, _c, results, _s in loaded for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--corpus",
        type=Path,
        action="append",
        help="a labelled corpus file; repeatable. Default: every "
        f"{CORPUS_GLOB} under {CORPUS_DIR.relative_to(ROOT)}",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument(
        "--harvest-only",
        action="store_true",
        help="print the harvested questions and stop; used to build the labelled file",
    )
    args = ap.parse_args(argv)

    harvested = harvest()
    if args.harvest_only:
        print(json.dumps({"count": len(harvested), "questions": harvested}, indent=2))
        return 0

    paths = list(args.corpus or discover_corpora())
    if not paths:
        print("FAIL no labelled corpus found", file=sys.stderr)
        return 1

    loaded: list[tuple[str, dict, list, Summary]] = []
    errors: list[str] = []
    for path in paths:
        try:
            corpus = load_labels(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL {path} did not load: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        errs = validate_corpus(corpus, harvested)
        errors.extend(f"{path.name}: {e}" for e in errs)
        results, summary = evaluate(corpus, corpus_id=path.name)
        loaded.append((path.name, corpus, results, summary))

    # The n floors belong to the pooled independent measurement, not to any
    # one file. Checking them per file would fail a small external corpus that
    # is doing exactly what it should: adding cases to a pool.
    ind_cases = [
        c
        for _n, corpus, _r, _s in loaded
        if is_independent(corpus)
        for c in labelled_questions(corpus)
    ]
    n_ord = sum(1 for c in ind_cases if not c.get("carries_filter") and not c.get("uncertain"))
    n_pos = sum(1 for c in ind_cases if c.get("carries_filter") and not c.get("uncertain"))
    if n_ord < 1:
        errors.append("no independent ordinary case; a false-engage rate cannot be a number")
    if n_pos < 1:
        errors.append("no independent filter-positive case; a miss rate cannot be a number")

    if errors:
        print("FAIL labelled corpus is not a measurement:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if not args.json:
        print(WHAT_THIS_MEASURES)
        print()
        print(DEFINITIONS)
        print()
    if args.json:
        # The report prints as it goes; --json wants the payload only, so the
        # narrative is captured and dropped rather than duplicated on stdout.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            payload = combined_report(loaded)
        print(json.dumps(payload, indent=2))
    else:
        payload = combined_report(loaded)
    if args.out and payload is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
