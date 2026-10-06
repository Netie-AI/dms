"""HELDOUT-PACK-D harness hook: score a frozen, blind held-out pack through /v1/chat/ask.

Plan D proposal for EPIC-A1 (dms#257). The pack is NOT in this repo and must not be:
it lives in its own git repo outside the dms tree, frozen by a SHA-256 manifest, so
nobody can tune DMS to it quietly. This script only reads it.

Judging is the existing row judge (#292/#299/#300 rules), imported, not copied:
``score_curated.judge_envelope_detailed`` with the case's gold SQL run read-only
against the pack's own DuckDB (``--oracle-db``). ORACLE_ERROR is never OK. Refusal
questions are judged by badge: a confident answer is WRONG.

  python scripts/score_heldout.py --self-check --pack <dir> --oracle-db <duckdb>
  DMS_API_BASE=http://127.0.0.1:8090 python scripts/score_heldout.py --live \\
      --pack <dir> --oracle-db <duckdb> --space <space_id>

Fail-closed preflight (both modes), before any network call:
  1. --pack resolves outside this repo tree.
  2. Every file in MANIFEST.json matches its sha256, and the manifest root hash equals
     PACK_D_ROOT_SHA256 (or an explicit --expect-root, which is labelled "not pack D").
  3. scan/result.json in the pack says PASS (counts-only personal-data scan).
  4. Every gold SQL, run on --oracle-db, reproduces the frozen gold rows, and every
     table of --oracle-db matches db/fingerprint.json (rows hashed per table), so a
     database edited where no gold query looks is caught too. A drifted oracle is
     CONFIG, not a score.

No scored round has been run from this script. The first live run is a baseline with
no target (EPIC-A1), and it waits for the A1 baseline gates. No accuracy figure is
printed without n, the outcome counts and the rule-of-three bound.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
# Same as bird_minidev.py: the CLI must import the judge's dms_executor / dms_core
# helpers without an installed package or PYTHONPATH, or every oracle call errors.
_PACKAGES = ("core", "cortex_client", "executor", "ledger")  # pyproject pythonpath, minus api
for _path in (SCRIPTS, *(ROOT / "packages" / name for name in _PACKAGES)):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from oracle_row_match import (  # noqa: E402
    envelope_rows,
    has_order_by_limit,
    numeric_scale_from_sql,
    rows_equal,
    rows_mismatch_reason,
    run_oracle_select,
)
from score_bound import bound_line  # noqa: E402
from score_curated import (  # noqa: E402
    ask_error_envelope,
    judge_envelope_detailed,
    no_envelope_verdict,
)

PACK_NAME = "heldout-pack-d"
# Root of the frozen pack's MANIFEST.json (tag pack-d-v1, commit 9c1c2e7 in the pack
# repo). A different pack can be scored only with --expect-root, and every line it
# prints then says it is not pack D.
PACK_D_ROOT_SHA256 = "e63b422e26336c6cae5f824d1af69f68ee5236544160a0685d0f982fad2ceee7"
VERDICTS = ("OK", "LAYER", "ABSTAIN", "WRONG", "ORACLE_ERROR", "INVALID", "RATE_LIMIT")
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3
TRANSPORT_BLOCK = frozenset({"ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException"})
PUBLIC_BINDS = frozenset({"0.0.0.0", "*", "::", "[::]"})


class PackError(Exception):
    """Preflight refusal. Always CONFIG, never a score."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_root(files: dict[str, dict[str, Any]]) -> str:
    """sha256 over sorted '<sha256>  <path>' lines, LF-terminated."""
    lines = "".join(f"{files[p]['sha256']}  {p}\n" for p in sorted(files))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def verify_pack(pack_dir: Path, expect_root: str | None = None) -> dict[str, Any]:
    """Manifest + scan checks. Returns the manifest. Raises PackError."""
    pack_dir = pack_dir.resolve()
    if _inside(pack_dir, ROOT.resolve()):
        raise PackError(f"pack is inside the dms tree ({pack_dir}); it must live outside it")
    mpath = pack_dir / "MANIFEST.json"
    if not mpath.is_file():
        raise PackError(f"no MANIFEST.json in {pack_dir}")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise PackError("MANIFEST.json lists no files")
    bad: list[str] = []
    for rel, meta in sorted(files.items()):
        path = pack_dir / rel
        if not _inside(path.resolve(), pack_dir) or not path.is_file():
            bad.append(f"missing:{rel}")
        elif sha256_file(path) != meta.get("sha256"):
            bad.append(f"sha256:{rel}")
    if bad:
        raise PackError(f"manifest mismatch on {len(bad)} file(s): {', '.join(bad[:5])}")
    root = manifest_root(files)
    if root != manifest.get("root_sha256"):
        raise PackError("manifest root_sha256 does not match its file list")
    want = expect_root or PACK_D_ROOT_SHA256
    if root != want:
        raise PackError(f"pack root {root[:12]} is not the pinned root {want[:12]}")
    for need in (
        "pack/questions.json",
        "pack/gold_results.json",
        "scan/result.json",
        "db/fingerprint.json",
    ):
        if need not in files:
            raise PackError(f"manifest does not cover {need}")
    scan = json.loads((pack_dir / "scan" / "result.json").read_text(encoding="utf-8"))
    if scan.get("verdict") != "PASS":
        raise PackError(f"personal-data scan verdict is {scan.get('verdict')!r}, not PASS")
    manifest["_root"] = root
    manifest["_pinned"] = expect_root is None
    return manifest


def load_cases(pack_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    doc = json.loads((pack_dir / "pack" / "questions.json").read_text(encoding="utf-8"))
    gold = json.loads((pack_dir / "pack" / "gold_results.json").read_text(encoding="utf-8"))
    cases = list(doc.get("questions") or [])
    if not cases:
        raise PackError("pack has no questions")
    for case in cases:
        if case.get("expect") not in {"l0", "refuse"}:
            raise PackError(f"{case.get('id')}: expect must be l0 or refuse")
        if case["expect"] == "l0" and (not case.get("gold_sql") or case["id"] not in gold):
            raise PackError(f"{case.get('id')}: answerable case without gold SQL or gold rows")
        if case["expect"] == "l0":
            case["_gold_rows"] = frozen_rows(gold[case["id"]])
            case["_pack_scale"] = true_round_scale(str(case["gold_sql"]))
    return cases, gold


def true_round_scale(sql: str) -> int | None:
    """Largest literal n over every ROUND(x, n) in the parsed SQL; None when there is none.

    oracle_row_match.numeric_scale_from_sql reads ROUND with a regex that stops at the first
    comma inside the argument, so ROUND(AVG(COALESCE(x, 0)), 2) is read as scale 0. The pack
    declares its scale by parse, and scale_guard holds the judge to it.
    """
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="duckdb")
        found = []
        for node in tree.find_all(exp.Round):
            dec = node.args.get("decimals")
            if isinstance(dec, exp.Literal) and not dec.is_string:
                found.append(int(dec.this))
        return max(found) if found else None
    except Exception:  # noqa: BLE001 - unparsable: fall back to the judge's own reading
        return numeric_scale_from_sql(sql)


def scale_guard(
    case: dict[str, Any], env: dict[str, Any], verdict: str, reason: str
) -> tuple[str, str, str | None]:
    """Re-compare at the pack's parsed ROUND scale when it differs from the judge's.

    Returns (verdict, reason, change) where change is None, "tightened" or "relabelled".
    A hidden error becomes WRONG (the judge compared at a coarser scale than the gold
    declares). A WRONG that only exists because the judge fell back to exact comparison
    becomes INVALID: an instrument disagreement, kept in n, never a verdict.
    """
    gold_rows = case.get("_gold_rows")
    if case["expect"] != "l0" or gold_rows is None or verdict not in {"OK", "LAYER", "WRONG"}:
        return verdict, reason, None
    sql = str(case["gold_sql"])
    judge_scale = numeric_scale_from_sql(sql)
    pack_scale = case.get("_pack_scale")
    if judge_scale == pack_scale:
        return verdict, reason, None
    same = rows_equal(
        gold_rows, envelope_rows(env), ordered=has_order_by_limit(sql), scale=pack_scale
    )
    if verdict in {"OK", "LAYER"} and not same:
        return "WRONG", f"rows_mismatch:scale(judge={judge_scale},pack={pack_scale})", "tightened"
    if verdict == "WRONG" and reason.startswith("rows_mismatch") and same:
        return "INVALID", f"scale_mismatch:judge={judge_scale},pack={pack_scale}", "relabelled"
    return verdict, reason, None


def frozen_rows(entry: dict[str, Any]) -> list[dict[str, Any]]:
    cols = [str(c) for c in entry.get("columns") or []]
    return [dict(zip(cols, row, strict=True)) for row in entry.get("rows") or []]


def check_oracle(cases: list[dict[str, Any]], gold: dict[str, Any], oracle_db: Path) -> list[str]:
    """Each gold SQL on the oracle must reproduce the frozen rows. Returns drift ids."""
    drift: list[str] = []
    for case in cases:
        if case["expect"] != "l0":
            continue
        sql = str(case["gold_sql"])
        rows, err = run_oracle_select(oracle_db, sql)
        if err is not None:
            drift.append(f"{case['id']}:oracle_error")
            continue
        # The frozen rows play "got"; the judge's own rounding and order rules apply.
        if rows_mismatch_reason(frozen_rows(gold[case["id"]]), rows or [], sql=sql):
            drift.append(f"{case['id']}:rows")
    return drift


def _json_cell(value: Any) -> Any:
    """Cell rendering for the fingerprint. Must match the pack's tools/packlib.to_json_cell."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def db_fingerprint(db: Path) -> dict[str, dict[str, Any]]:
    """Per base table: row count, column count, sha256 of rows sorted by every column.

    Same recipe as the pack's tools/freeze.py, so the database the scorer treats as
    truth must be the database the pack was frozen against, not just agree on the
    gold queries.
    """
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY 1"
            ).fetchall()
        ]
        out: dict[str, dict[str, Any]] = {}
        for table in tables:
            cols = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
            order = ", ".join(f'"{c}"' for c in cols)
            digest = hashlib.sha256()
            n = 0
            for row in con.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall():
                line = json.dumps([_json_cell(v) for v in row], ensure_ascii=False)
                digest.update((line + "\n").encode())
                n += 1
            out[table] = {"rows": n, "columns": len(cols), "sha256": digest.hexdigest()}
        return out
    finally:
        con.close()


def check_fingerprint(pack_dir: Path, oracle_db: Path) -> list[str]:
    """Tables whose rows differ from the frozen fingerprint ('missing:'/'extra:' too)."""
    frozen = json.loads((pack_dir / "db" / "fingerprint.json").read_text(encoding="utf-8"))
    want = frozen.get("tables")
    if not isinstance(want, dict) or not want:
        raise PackError("db/fingerprint.json lists no tables")
    got = db_fingerprint(oracle_db)
    diff = [f"missing:{t}" for t in sorted(set(want) - set(got))]
    diff += [f"extra:{t}" for t in sorted(set(got) - set(want))]
    diff += [t for t in sorted(set(want) & set(got)) if want[t] != got[t]]
    return diff


def preflight(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if args.pack is None:
        raise PackError("--pack DIR is required (the frozen pack repo, outside dms)")
    if args.oracle_db is None or not Path(args.oracle_db).is_file():
        raise PackError("--oracle-db must be the DuckDB built by the pack's db/build_db.py")
    pack_dir = Path(args.pack).resolve()
    manifest = verify_pack(pack_dir, args.expect_root)
    cases, gold = load_cases(pack_dir)
    drift = check_oracle(cases, gold, Path(args.oracle_db))
    if drift:
        raise PackError(f"oracle drift on {len(drift)} case(s): {', '.join(drift[:5])}")
    changed = check_fingerprint(pack_dir, Path(args.oracle_db))
    if changed:
        raise PackError(
            f"oracle fingerprint differs on {len(changed)} table(s): {', '.join(changed[:5])}"
        )
    return manifest, cases


def label(manifest: dict[str, Any]) -> str:
    root = str(manifest.get("_root") or "")[:12]
    if manifest.get("_pinned"):
        return f"{PACK_NAME} root={root}"
    return f"NOT pack D (explicit --expect-root) root={root}"


def wrong_upper_pct(wrong: int, answered: int) -> float | None:
    """One-sided 95 pct Clopper-Pearson upper bound on WRONG/answered, in pct.

    wrong=0 gives about 300/n (the rule of three). Bisection; no scipy.
    """
    if answered <= 0:
        return None
    if wrong >= answered:
        return 100.0

    def cdf(p: float) -> float:
        return sum(
            math.comb(answered, k) * p**k * (1 - p) ** (answered - k) for k in range(wrong + 1)
        )

    lo, hi = wrong / answered, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return 100.0 * hi


@dataclass
class Scored:
    """One scored round. Counts only what was asked; a block stops the round."""

    status: str = "ok"
    tallies: Counter[str] = field(default_factory=Counter)
    groups: dict[str, Counter[str]] = field(default_factory=dict)
    badges: Counter[str] = field(default_factory=Counter)
    envelope: Counter[str] = field(default_factory=Counter)
    scale: Counter[str] = field(default_factory=Counter)
    cases: list[dict[str, Any]] = field(default_factory=list)


def envelope_violation(env: dict[str, Any]) -> str | None:
    """CLAUDE.md rule 10a: the served envelope must pass assert_envelope_valid (E1-E13).

    None when valid, else the first violation ("E1: abstained=true but badge=...").
    A validator crash on a malformed envelope is a violation, never a pass.
    """
    from dms_executor.envelope import assert_envelope_valid

    try:
        assert_envelope_valid(env)
    except AssertionError as exc:
        return " ".join(str(exc).split())[:160] or "E?: assertion"
    except Exception as exc:  # noqa: BLE001 - malformed envelope fails closed
        return f"E?: validator raised {type(exc).__name__}"
    return None


def _ecode(violation: str) -> str:
    head = violation.split(":", 1)[0].strip()
    return head if head.startswith("E") and len(head) <= 4 else "E?"


def summary_lines(scored: Scored, n: int) -> list[str]:
    tallies = scored.tallies
    answered = tallies["OK"] + tallies["LAYER"]
    counts = " ".join(f"{v}={tallies[v]}" for v in VERDICTS)
    upper = wrong_upper_pct(tallies["WRONG"], answered)
    upper_s = "n/a" if upper is None else f"{upper:.2f} pct"
    badges = " ".join(f"{b}={c}" for b, c in sorted(scored.badges.items())) or "none"
    env = " ".join(f"{e}={c}" for e, c in sorted(scored.envelope.items())) or "none"
    lines = [
        f"n={n} {counts}",
        f"WRONG={tallies['WRONG']} of answered={answered}; {bound_line(answered)}; "
        f"95 pct upper bound on WRONG/answered {upper_s}",
        f"badges served: {badges} (LAYER = rows match under a confident non-L0 badge)",
        f"envelope violations (assert_envelope_valid): {env}",
        f"scale guard: the judge's ROUND scale differs from the pack's on "
        f"{scored.scale['queries_affected']} gold queries; "
        f"tightened to WRONG={scored.scale['tightened']}, "
        f"relabelled INVALID={scored.scale['relabelled']}",
    ]
    for name in sorted(scored.groups):
        t = scored.groups[name]
        a = t["OK"] + t["LAYER"]
        lines.append(
            f"  {name}: n={sum(t.values())} answered={a} abstained={t['ABSTAIN']} "
            f"WRONG={t['WRONG']} INVALID={t['INVALID']} ORACLE_ERROR={t['ORACLE_ERROR']}; "
            f"{bound_line(a)}"
        )
    return lines


def _ask(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    import httpx

    resp = httpx.post(
        f"{base}/v1/chat/ask",
        json={"question": question, "space_id": space_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("ask response is not an object")
    return body


def live_url(env: dict[str, str], url_arg: str | None) -> str:
    raw = (url_arg or env.get("DMS_API_BASE") or "").strip()
    if not raw:
        raise PackError("DMS_API_BASE or --url is required for --live; unset is CONFIG")
    host = (urlparse(raw).hostname or "").lower()
    if host in PUBLIC_BINDS:
        raise PackError(
            f"hostname {host!r} is a public bind; DMS stays on 127.0.0.1 or the tailnet"
        )
    return raw.rstrip("/")


def score(cases: list[dict[str, Any]], *, ask: Any, oracle_db: Path, echo: bool = True) -> Scored:
    """Ask, judge rows, then hold the served envelope to the badge contract.

    The row judge runs first, so a WRONG is never hidden. A served envelope that
    fails assert_envelope_valid turns any other verdict into INVALID, so a green
    badge on an abstention cannot pass as ABSTAIN. A harness-synthesized grant
    refusal (403/409) is not a DMS envelope and is not validated.
    """
    out = Scored()
    out.scale["queries_affected"] = sum(
        1
        for c in cases
        if c["expect"] == "l0"
        and c.get("_gold_rows") is not None
        and numeric_scale_from_sql(str(c["gold_sql"])) != c.get("_pack_scale")
    )
    for case in cases:
        qid = str(case["id"])
        # Refusals are reported per reason: ambiguous_metric is judgement-dependent and
        # must not be pooled with missing_data or out_of_range.
        group = (
            f"refuse:{case.get('refuse_reason') or 'unspecified'}"
            if case["expect"] == "refuse"
            else str(case.get("difficulty") or "unrated")
        )
        served = True
        try:
            env = ask(str(case["question"]))
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if name in TRANSPORT_BLOCK or status == 404:
                if echo:
                    print(f"{qid}\tBLOCKED\t{name}")
                out.status = "blocked"
                return out
            env = ask_error_envelope(exc)
            if env is None:
                verdict, reason = no_envelope_verdict(exc)
                out.tallies[verdict] += 1
                out.groups.setdefault(group, Counter())[verdict] += 1
                out.cases.append({"id": qid, "group": group, "verdict": verdict, "reason": reason})
                if echo:
                    print(f"{qid}\t{group}\t{verdict}\t-\t{reason}")
                continue
            served = False
        scored = {"id": qid, "question": case["question"], "expect": case["expect"]}
        oracle_sql = str(case.get("gold_sql") or "") if case["expect"] == "l0" else None
        res = judge_envelope_detailed(scored, env, oracle_db=oracle_db, oracle_sql=oracle_sql)
        verdict, reason = res.verdict, res.reason
        if served:
            verdict, reason, change = scale_guard(case, env, verdict, reason)
            if change:
                out.scale[change] += 1
        violation = envelope_violation(env) if served else None
        if violation:
            out.envelope[_ecode(violation)] += 1
            if verdict != "WRONG":
                verdict, reason = "INVALID", f"envelope:{violation}"
        badge = str(env.get("badge")) if served else "grant_refusal(harness)"
        out.badges[badge] += 1
        out.tallies[verdict] += 1
        out.groups.setdefault(group, Counter())[verdict] += 1
        out.cases.append(
            {
                "id": qid,
                "group": group,
                "verdict": verdict,
                "reason": reason,
                "badge": badge,
                "envelope_violation": violation,
            }
        )
        if echo:
            print(f"{qid}\t{group}\t{verdict}\t{badge}\t{reason}")
    return out


def _nudge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move the first numeric cell by one unit of its own scale, else tag a text cell."""
    out = [dict(r) for r in rows]
    for row in out:
        for key, value in row.items():
            try:
                num = Decimal(str(value))
            except Exception:  # noqa: BLE001 - not a number, keep looking
                continue
            if isinstance(value, bool) or not num.is_finite():
                continue
            places = (
                max(0, -num.as_tuple().exponent) if isinstance(num.as_tuple().exponent, int) else 0
            )
            row[key] = str(num + Decimal(1).scaleb(-places))
            return out
    first = out[0]
    key = next(iter(first))
    first[key] = f"{first[key]}x"
    return out


# responder -> (verdict on answerable questions, verdict on refusal questions)
SYNTHETIC_EXPECT: dict[str, tuple[str, str]] = {
    "perfect": ("OK", "ABSTAIN"),
    "all_abstain": ("ABSTAIN", "ABSTAIN"),
    "doubled_row": ("WRONG", "ABSTAIN"),
    "nudged_value": ("WRONG", "ABSTAIN"),
    "confident_on_refusals": ("OK", "WRONG"),
    "green_abstention": ("INVALID", "ABSTAIN"),
}


def synthetic_envelopes(
    cases: list[dict[str, Any]], gold: dict[str, Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Per responder, question text -> served envelope, built by DMS's own constructor.

    No DMS is asked and no model is called: the pack's own frozen gold is replayed as a
    perfect answerer and then deliberately damaged, to show the instrument can tell.
    """
    from dms_executor.envelope import build_answer_envelope

    as_of = "2026-01-01T00:00:00Z"

    def answer(case: dict[str, Any], rows: list[dict[str, Any]], badge: str = "L0_CERTIFIED"):
        return build_answer_envelope(
            answer_id=f"ans_{case['id']}",
            text="The rows are listed below.",
            badge=badge,
            values=[{"label": "rows", "value": "listed"}],
            sql_used=str(case.get("gold_sql") or "SELECT 1"),
            rows=rows,
            as_of=as_of,
            audit_id=f"aud_{case['id']}",
        )

    def abstain(case: dict[str, Any]):
        return build_answer_envelope(
            answer_id=f"abs_{case['id']}",
            text="I cannot answer that from this Space.",
            badge="ABSTAIN",
            rows=[],
            as_of=as_of,
        )

    out: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in SYNTHETIC_EXPECT}
    for case in cases:
        q = str(case["question"])
        if case["expect"] == "l0":
            rows = frozen_rows(gold[case["id"]])
            out["perfect"][q] = answer(case, rows)
            out["all_abstain"][q] = abstain(case)
            out["doubled_row"][q] = answer(case, rows + [dict(rows[0])])
            out["nudged_value"][q] = answer(case, _nudge(rows))
            out["confident_on_refusals"][q] = answer(case, rows)
            out["green_abstention"][q] = {**abstain(case), "badge": "L0_CERTIFIED"}
        else:
            made_up = [{"answer": "42"}]
            out["perfect"][q] = abstain(case)
            out["all_abstain"][q] = abstain(case)
            out["doubled_row"][q] = abstain(case)
            out["nudged_value"][q] = abstain(case)
            out["confident_on_refusals"][q] = answer(case, made_up, badge="L2_VALIDATED")
            out["green_abstention"][q] = abstain(case)
    return out


def synthetic_selftest(args: argparse.Namespace) -> int:
    """Instrument self-test: can this pack and judge tell a perfect answerer from damaged ones?"""
    try:
        manifest, cases = preflight(args)
        gold = json.loads((Path(args.pack) / "pack" / "gold_results.json").read_text("utf-8"))
    except PackError as exc:
        print(f"CONFIG: {exc}")
        print("VERDICT: CONFIG. Not a score.")
        return EXIT_CONFIG
    print(label(manifest))
    n_ans = sum(c["expect"] == "l0" for c in cases)
    n_ref = len(cases) - n_ans
    print(
        f"synthetic responders over n={len(cases)} (answerable={n_ans}, refuse={n_ref}); "
        "DMS is not asked"
    )
    envs = synthetic_envelopes(cases, gold)
    failures = 0
    print(
        f"{'responder':<22}{'OK':>5}{'LAYER':>7}{'ABSTAIN':>9}{'WRONG':>7}"
        f"{'INVALID':>9}{'ORA_ERR':>9}  expected"
    )
    for name, (want_ans, want_ref) in SYNTHETIC_EXPECT.items():
        got = score(cases, ask=envs[name].__getitem__, oracle_db=Path(args.oracle_db), echo=False)
        t = got.tallies
        off = [
            c
            for c, case in zip(got.cases, cases, strict=True)
            if c["verdict"] != (want_ans if case["expect"] == "l0" else want_ref)
        ]
        # DMS's own masker turns some gold cells into DMSMASK_ tokens; the instrument
        # reports that as INVALID masked_compare, which is its design, not its failure.
        masked = [c["id"] for c in off if str(c.get("reason", "")).startswith("masked_compare")]
        deviants = [c["id"] for c in off if c["id"] not in masked]
        failures += bool(deviants)
        print(
            f"{name:<22}{t['OK']:>5}{t['LAYER']:>7}{t['ABSTAIN']:>9}{t['WRONG']:>7}"
            f"{t['INVALID']:>9}{t['ORACLE_ERROR']:>9}  "
            f"answerable->{want_ans}, refuse->{want_ref}: "
            f"{'as expected' if not deviants else f'{len(deviants)} DEVIATE {deviants[:5]}'}"
            f"{f' (+{len(masked)} masked by DMS masker)' if masked else ''}"
        )
    if failures:
        print("VERDICT: INSTRUMENT FAILED its own self-test. Do not score DMS with it.")
        return EXIT_FAIL
    print(
        "VERDICT: instrument discriminates as designed. Synthetic responders only: not a DMS score."
    )
    return EXIT_PASS


def _artifact(report: dict[str, Any]) -> Path:
    out = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    out.mkdir(parents=True, exist_ok=True)
    path = out / "score_heldout.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def self_check(args: argparse.Namespace) -> int:
    try:
        manifest, cases = preflight(args)
    except PackError as exc:
        print(f"CONFIG: {exc}")
        print("VERDICT: CONFIG. Not a score.")
        return EXIT_CONFIG
    kinds = Counter(c["expect"] for c in cases)
    diffs = Counter(str(c.get("difficulty")) for c in cases if c["expect"] == "l0")
    print(label(manifest))
    print(
        f"cases={len(cases)} answerable={kinds['l0']} refuse={kinds['refuse']} "
        + " ".join(f"{k}={v}" for k, v in sorted(diffs.items()))
    )
    print("manifest OK; scan PASS; oracle reproduces every frozen gold result")
    print("VERDICT: PACK OK. Not a score: no question was asked.")
    return EXIT_PASS


def live(args: argparse.Namespace) -> int:
    try:
        url = live_url(dict(os.environ), args.url)
        if not args.space:
            raise PackError("--space is required: the Space the pack DB is attached to")
        manifest, cases = preflight(args)
    except PackError as exc:
        print(f"CONFIG: {exc}")
        print("VERDICT: CONFIG. Not a score.")
        return EXIT_CONFIG
    print(label(manifest))
    print(f"DMS_API_BASE={url} space_id={args.space}")
    scored = score(
        cases,
        ask=lambda q: _ask(url, q, args.space, args.timeout),
        oracle_db=Path(args.oracle_db),
    )
    report = {
        "kind": "dms.score_heldout",
        "pack": label(manifest),
        "space_id": args.space,
        "n": len(cases),
        "tallies": dict(scored.tallies),
        "groups": {k: dict(v) for k, v in scored.groups.items()},
        "badges": dict(scored.badges),
        "envelope_violations": dict(scored.envelope),
        "baseline": False,
        "target": None,
        "cases": scored.cases,
    }
    if scored.status == "blocked":
        report["blocked"] = True
        _artifact(report)
        print("VERDICT: BLOCKED. No OK/ABSTAIN/WRONG invented for unasked cases.")
        return EXIT_BLOCKED
    for line in summary_lines(scored, len(cases)):
        print(line)
    _artifact(report)
    print("Held-out pack measure. No target. Not a baseline until EPIC-A1 says so.")
    failed = scored.tallies["WRONG"] or scored.tallies["ORACLE_ERROR"] or scored.envelope
    return EXIT_FAIL if failed else EXIT_PASS


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Instrument self-test with synthetic responders. Asks no DMS, calls no model.",
    )
    p.add_argument("--pack", type=Path, default=None)
    p.add_argument("--oracle-db", type=Path, default=None)
    p.add_argument("--expect-root", default=None, help="Score a pack other than D. Labelled.")
    p.add_argument("--space", default=None)
    p.add_argument("--url", default=None)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args(argv)
    if args.self_check:
        return self_check(args)
    if args.synthetic:
        return synthetic_selftest(args)
    if args.live:
        return live(args)
    print(
        "usage: score_heldout.py --self-check | --synthetic | --live  (--pack DIR --oracle-db DB)"
    )
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
