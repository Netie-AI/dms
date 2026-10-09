"""One grader for the 52-question pack.

Gold is each certified oracle in ``tests/fixtures/curated_ceo/oracles.yaml``,
executed read-only on a fresh ``ensure_demo_warehouse`` file. ``$as_of`` binds
the connection's current date. A refuse oracle, or a question with no SQL, has
empty gold. Gold is never read from an envelope or a canned stub.

``--envelopes`` grades those served rows (point 4, then the order rule). It
does not execute ``sql_used``. A submit-stub pack (every confident row is the
one-cell ``n=1`` stub) is labelled stub and is not a score. ``--main`` builds
envelopes with the exec-SQL stub, flags off, and labels that score stub-exec.

A trap is a refusal. Gold-broken is the non-trap case with no certified
oracle, so it sits inside the non-trap 44.

Usage:
    python scripts/verify/grade52.py --self-test
    python scripts/verify/grade52.py --main
    python scripts/verify/grade52.py --envelopes PATH
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
ORACLE_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"

ABS_TOL = 0.005
# ponytail: product of per-column candidate counts. Above this the grade is
# unmappable. Upgrade is branch-and-bound if a real answer is wider than the
# value signature can narrow.
MAP_CAP = 1024
MODE_SERVED = "served"
MODE_STUB_EXEC = "stub-exec"
MODES = (
    MODE_SERVED,
    MODE_STUB_EXEC,
)
BUCKET_CORRECT = "CORRECT"
BUCKET_WRONG = "WRONG"
BUCKET_ABSTAIN = "ABSTAIN"
BUCKET_EMPTY = "EMPTY_GOLD"
BUCKET_REFUSAL_OK = "REFUSAL_OK"
BUCKET_REFUSAL_WRONG = "REFUSAL_WRONG"
BUCKET_GOLD_BROKEN = "GOLD_BROKEN"
_BUCKET_KEYS = (
    "correct",
    "wrong",
    "abstain",
    "refusal_ok",
    "refusal_wrong",
    "empty_gold",
    "gold_broken",
)
_REFUSAL_ROUTES = frozenset({"abstain", "blocked", "needs_clarification", "refused"})
_CREDENTIAL_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


class _Unmappable(Exception):
    """Candidate assignments exceed MAP_CAP."""


def _bootstrap() -> None:
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    ):
        entry = str(ROOT / rel)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def oracle_orders(sql: str) -> bool:
    """True when the parsed statement has a top-level ORDER BY.

    A nested ORDER BY (subquery or CTE) does not count. Question wording is
    not consulted.
    """
    from sqlglot import parse_one

    tree = parse_one(sql, read="duckdb")
    order = tree.args.get("order") if hasattr(tree, "args") else None
    return order is not None


def _is_bool(value: Any) -> bool:
    return type(value) is bool


def _decimal_places(value: Any) -> int | None:
    if _is_bool(value) or value is None:
        return None
    if isinstance(value, int):
        return 0
    if isinstance(value, Decimal):
        return max(0, -value.as_tuple().exponent)
    if isinstance(value, float):
        return _decimal_places(Decimal(str(value)))
    if isinstance(value, str):
        text = value.strip()
        if _as_float(text) is None:
            return None
        if "." not in text:
            return 0
        frac = text.split(".", 1)[1]
        return len(frac)
    return None


def _as_float(value: Any) -> float | None:
    if _is_bool(value) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def cells_equal(gold: Any, served: Any) -> bool:
    """Numbers within 0.005. Gold's decimal count wins when it is shorter."""
    if gold is None and served is None:
        return True
    if _is_bool(gold) or _is_bool(served):
        return _is_bool(gold) and _is_bool(served) and gold == served
    if gold is None or served is None:
        return False
    gold_iso = _iso(gold)
    served_iso = _iso(served)
    if gold_iso is not None or served_iso is not None:
        left = gold_iso if gold_iso is not None else str(gold).strip()
        right = served_iso if served_iso is not None else str(served).strip()
        return left == right
    gold_num = _as_float(gold)
    served_num = _as_float(served)
    if gold_num is not None and served_num is not None:
        gold_dp = _decimal_places(gold)
        served_dp = _decimal_places(served)
        if gold_dp is not None and served_dp is not None and gold_dp < served_dp:
            served_num = round(served_num, gold_dp)
        return abs(gold_num - served_num) <= ABS_TOL
    if isinstance(gold, str) or isinstance(served, str):
        return str(gold).strip() == str(served).strip()
    return gold == served


def _name_lock(gold_cols: Sequence[str], served_cols: Sequence[str]) -> dict[str, str] | None:
    """Exact names, then case-insensitive. None if any gold column is unnamed."""
    unused = set(served_cols)
    mapping: dict[str, str] = {}
    for name in gold_cols:
        if name in unused:
            mapping[name] = name
            unused.remove(name)
    folded: dict[str, str] = {}
    for name in served_cols:
        folded.setdefault(name.lower(), name)
    for name in gold_cols:
        if name in mapping:
            continue
        hit = folded.get(name.lower())
        if hit is None or hit not in unused:
            return None
        mapping[name] = hit
        unused.remove(hit)
    return mapping


def _column_values(rows: Sequence[Mapping[str, Any]], col: str) -> list[Any]:
    return [row.get(col) for row in rows]


def _column_compatible(
    gold_vals: Sequence[Any],
    served_vals: Sequence[Any],
) -> bool:
    """Value signature: one gold cell matches one served cell, duplicates kept."""
    if len(gold_vals) != len(served_vals):
        return False
    used = [False] * len(served_vals)
    for gold in gold_vals:
        found = False
        for index, served in enumerate(served_vals):
            if used[index]:
                continue
            if cells_equal(gold, served):
                used[index] = True
                found = True
                break
        if not found:
            return False
    return True


def _candidate_maps(
    gold_cols: Sequence[str],
    served_cols: Sequence[str],
    gold: Sequence[Mapping[str, Any]],
    served: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Exact name, then case-insensitive, then a capped signature assignment.

    A locked column is not reassigned. Unmatched columns permute only among
    served columns whose values are compatible. The product of those candidate
    counts is the cap. Over the cap this raises ``_Unmappable``.
    """
    if len(served_cols) < len(gold_cols):
        return []
    locked: dict[str, str] = {}
    unused = set(served_cols)
    pending: list[str] = []
    for name in gold_cols:
        if name in unused:
            locked[name] = name
            unused.remove(name)
        else:
            pending.append(name)
    folded: dict[str, str] = {}
    for name in served_cols:
        folded.setdefault(name.lower(), name)
    still: list[str] = []
    for name in pending:
        hit = folded.get(name.lower())
        if hit is not None and hit in unused:
            locked[name] = hit
            unused.remove(hit)
        else:
            still.append(name)
    if not still:
        return [locked]
    pool = [name for name in served_cols if name in unused]
    if len(pool) < len(still):
        return []
    gold_vals = {col: _column_values(gold, col) for col in still}
    served_vals = {col: _column_values(served, col) for col in pool}
    candidates: dict[str, list[str]] = {}
    upper = 1
    for col in still:
        hits = [
            name
            for name in pool
            if _column_compatible(gold_vals[col], served_vals[name])
        ]
        if not hits:
            return []
        candidates[col] = hits
        upper *= len(hits)
        if upper > MAP_CAP:
            raise _Unmappable
    maps: list[dict[str, str]] = []
    used = set(locked.values())

    def walk(index: int, current: dict[str, str]) -> None:
        if index == len(still):
            maps.append(dict(current))
            return
        col = still[index]
        for name in candidates[col]:
            if name in used:
                continue
            used.add(name)
            current[col] = name
            walk(index + 1, current)
            del current[col]
            used.remove(name)

    walk(0, dict(locked))
    return maps


def _row_agrees(gold_row: Mapping[str, Any], served_row: Mapping[str, Any], mapping: Mapping[str, str]) -> bool:
    return all(cells_equal(gold_row[col], served_row[mapping[col]]) for col in mapping)


def _multiset(gold: Sequence[Mapping[str, Any]], served: Sequence[Mapping[str, Any]], mapping: Mapping[str, str]) -> bool:
    used = [False] * len(served)
    for grow in gold:
        found = False
        for index, srow in enumerate(served):
            if used[index]:
                continue
            if _row_agrees(grow, srow, mapping):
                used[index] = True
                found = True
                break
        if not found:
            return False
    return True


def _positional(gold: Sequence[Mapping[str, Any]], served: Sequence[Mapping[str, Any]], mapping: Mapping[str, str]) -> bool:
    return all(_row_agrees(grow, srow, mapping) for grow, srow in zip(gold, served, strict=True))


def _project(
    served: Sequence[Mapping[str, Any]],
    gold_cols: Sequence[str],
    mapping: Mapping[str, str],
) -> list[dict[str, Any]]:
    """One projected row per answer row. Extra columns drop. Duplicates stay."""
    return [{col: row.get(mapping[col]) for col in gold_cols} for row in served]


def compare_rows(
    gold: Sequence[Mapping[str, Any]],
    served: Sequence[Mapping[str, Any]],
    *,
    ordered: bool,
) -> tuple[str, str]:
    """Map each gold column to its own answer column, project, then multiset.

    Try exact names, then case-insensitive, then a permutation of the rest.
    Extra answer columns are ignored. Duplicate projected rows are kept, so a
    fan-out does not collapse into a match.
    """
    if len(gold) != len(served):
        return BUCKET_WRONG, f"rowcount {len(served)} vs {len(gold)}"
    if not gold:
        return BUCKET_CORRECT, "match"
    gold_cols = list(gold[0].keys())
    served_cols = list(served[0].keys())
    try:
        maps = _candidate_maps(gold_cols, served_cols, gold, served)
    except _Unmappable:
        return BUCKET_WRONG, "unmappable"
    if not maps:
        return BUCKET_WRONG, "no column mapping"
    named = _name_lock(gold_cols, served_cols) is not None
    ident = {col: col for col in gold_cols}
    projected_hits: list[list[dict[str, Any]]] = []
    for item in maps:
        projected = _project(served, gold_cols, item)
        if _multiset(gold, projected, ident):
            projected_hits.append(projected)
    if ordered:
        if any(_positional(gold, item, ident) for item in projected_hits):
            return BUCKET_CORRECT, "match"
        if projected_hits:
            return BUCKET_WRONG, "order mismatch"
        if named:
            return BUCKET_WRONG, "row mismatch"
        return BUCKET_WRONG, "no column mapping"
    if projected_hits:
        return BUCKET_CORRECT, "match"
    if named:
        return BUCKET_WRONG, "row mismatch"
    return BUCKET_WRONG, "no column mapping"


def _is_abstain(env: Mapping[str, Any]) -> bool:
    if str(env.get("badge") or "") == "ABSTAIN":
        return True
    if env.get("abstained") is True:
        return True
    return str(env.get("route") or "").lower() in _REFUSAL_ROUTES


def abstain_code(env: Mapping[str, Any]) -> str | None:
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if _is_abstain(env):
        return str(env.get("route") or "abstain")
    return None


def _rows_of(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
    return out


def _is_trap(question: Mapping[str, Any]) -> bool:
    """A refusal case. Not an id list: refuse, or an abstain expectation on a trap id."""
    expect = str(question.get("expect") or "").strip().lower()
    if expect == "refuse":
        return True
    return expect == "abstain" and str(question.get("id") or "").startswith("trap_")


def grade_case(
    *,
    gold: list[dict[str, Any]] | None,
    gold_sql: str | None,
    env: Mapping[str, Any],
    served_rows: list[dict[str, Any]],
    as_of: str,
    trap: bool = False,
    gold_broken: bool = False,
) -> dict[str, Any]:
    """One case. Traps are refusals. Missing gold on a non-trap is gold-broken.

    ``gold is None`` without ``gold_broken`` is empty gold (no certified rows).
    """
    badge = env.get("badge")
    route = env.get("route")
    code = abstain_code(env)
    served_n = len(served_rows)
    base = {
        "route": route,
        "badge": badge,
        "abstain_code": code,
        "as_of": as_of,
        "served_n": served_n,
    }
    if trap:
        if _is_abstain(env):
            return {**base, "bucket": BUCKET_REFUSAL_OK, "reason": "correct refusal"}
        unit = "row" if served_n == 1 else "rows"
        return {
            **base,
            "bucket": BUCKET_REFUSAL_WRONG,
            "reason": f"served with {served_n} {unit}",
        }
    if gold_broken:
        return {**base, "bucket": BUCKET_GOLD_BROKEN, "reason": "no certified gold"}
    if gold is None or gold == []:
        if _is_abstain(env):
            return {**base, "bucket": BUCKET_EMPTY, "reason": "abstain"}
        unit = "row" if served_n == 1 else "rows"
        return {
            **base,
            "bucket": BUCKET_EMPTY,
            "reason": f"served with {served_n} {unit}",
        }
    if _is_abstain(env):
        return {**base, "bucket": BUCKET_ABSTAIN, "reason": "abstain"}
    ordered = bool(gold_sql) and oracle_orders(str(gold_sql))
    bucket, reason = compare_rows(gold, served_rows, ordered=ordered)
    return {**base, "bucket": bucket, "reason": reason}


def _certified_sql(oracle: Mapping[str, Any] | None) -> str | None:
    if not isinstance(oracle, Mapping):
        return None
    expect = str(oracle.get("expect") or "").strip().lower()
    if expect == "refuse":
        return None
    sql = oracle.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return None
    return sql


def _execute(con: Any, sql: str, as_of: str) -> list[dict[str, Any]]:
    from dms_executor.demo_warehouse import sql_has_reserved_as_of

    bind = {"as_of": as_of} if sql_has_reserved_as_of(sql) else None
    cur = con.execute(sql, bind) if bind else con.execute(sql)
    cols = [str(item[0]) for item in (cur.description or [])]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _open_warehouse(tmp: Path) -> tuple[Any, str, Path]:
    import duckdb
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = ensure_demo_warehouse(tmp / "warehouse.duckdb")
    con = duckdb.connect(str(path), read_only=True)
    row = con.execute("SELECT CAST(CURRENT_DATE AS VARCHAR)").fetchone()
    as_of = str(row[0]) if row and row[0] is not None else ""
    return con, as_of, path


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"bad yaml: {path}")
    return data


def _is_stub_row(rows: Sequence[Mapping[str, Any]]) -> bool:
    """The canned submit stub: one cell, column ``n``, value 1."""
    if len(rows) != 1:
        return False
    row = rows[0]
    if set(row) != {"n"}:
        return False
    value = row.get("n")
    return value == 1 or value == 1.0


def _is_submit_stub(envelopes: Sequence[Mapping[str, Any]]) -> bool:
    """True when every confident envelope is the one-cell submit stub."""
    confident = 0
    for item in envelopes:
        env = item.get("env")
        if not isinstance(env, Mapping) or _is_abstain(env):
            continue
        confident += 1
        if not _is_stub_row(_rows_of(env.get("rows"))):
            return False
    return confident > 0


def _record(data: Any, default_id: str | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SystemExit("grade52: envelope record is not an object")
    if isinstance(data.get("env"), dict):
        qid = str(data.get("id") or default_id or "")
        return {"id": qid, "env": data["env"]}
    if "rows" in data or "badge" in data:
        qid = str(data.get("id") or default_id or "")
        env = {key: value for key, value in data.items() if key != "id"}
        return {"id": qid, "env": env}
    raise SystemExit("grade52: envelope record has no env")


def load_envelopes(path: Path) -> list[dict[str, Any]]:
    """A directory of ``<id>.json`` files, or a JSONL file of ``{id, env}``."""
    if path.is_dir():
        files = sorted(item for item in path.iterdir() if item.suffix == ".json" and item.is_file())
        if not files:
            raise SystemExit("grade52: envelope directory is empty")
        rows: list[dict[str, Any]] = []
        for item in files:
            try:
                data = json.loads(item.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SystemExit("grade52: envelope file is not json") from exc
            rows.append(_record(data, default_id=item.stem))
        return rows
    if not path.is_file():
        raise SystemExit("grade52: envelopes path is missing")
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit("grade52: envelope file is not json") from exc
        if not isinstance(data, list):
            raise SystemExit("grade52: envelope file is not json")
        return [_record(item) for item in data]
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit("grade52: envelope file is not json") from exc
        rows.append(_record(data))
    if not rows:
        raise SystemExit("grade52: envelope file is empty")
    return rows


def _sql_same(left: str, right: str) -> bool:
    from sqlglot import parse_one

    try:
        a = parse_one(left, read="duckdb").sql(dialect="duckdb")
        b = parse_one(right, read="duckdb").sql(dialect="duckdb")
    except Exception:
        return False
    return a == b


def pack_gold_served_ids(
    questions: Sequence[Mapping[str, Any]],
    oracles: Mapping[str, Any],
) -> list[str]:
    """Score-pack exact ids, plus an ops mirror that serves the same gold SQL.

    The mirror is the ``ops_`` rename of a ``cq_`` id. Synonym ids stay out.
    """
    from dms_executor.demo_pack import SCORE_PACK_EXACT_IDS

    known = {str(item["id"]) for item in questions}
    found: set[str] = set()
    for qid in SCORE_PACK_EXACT_IDS:
        if qid not in known:
            continue
        found.add(qid)
        if not qid.startswith("cq_"):
            continue
        mirror = "ops_" + qid[len("cq_") :]
        if mirror not in known:
            continue
        left = _certified_sql(oracles.get(qid) if isinstance(oracles, Mapping) else None)
        right = _certified_sql(oracles.get(mirror) if isinstance(oracles, Mapping) else None)
        if left and right and _sql_same(left, right):
            found.add(mirror)
    return sorted(found)


def tally(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every case lands in one bucket. The seven buckets add up to the case count."""
    counts = {key: 0 for key in _BUCKET_KEYS}
    abstained = 0
    served: dict[str, int] = {}
    bucket_of = {
        BUCKET_CORRECT: "correct",
        BUCKET_WRONG: "wrong",
        BUCKET_ABSTAIN: "abstain",
        BUCKET_REFUSAL_OK: "refusal_ok",
        BUCKET_REFUSAL_WRONG: "refusal_wrong",
        BUCKET_EMPTY: "empty_gold",
        BUCKET_GOLD_BROKEN: "gold_broken",
    }
    for item in cases:
        key = bucket_of.get(str(item["bucket"]))
        if key is None:
            raise SystemExit(f"unknown bucket {item['bucket']}")
        counts[key] += 1
        if key != "empty_gold":
            continue
        if item.get("reason") == "abstain":
            abstained += 1
        else:
            cell = str(item.get("served_n", 0))
            served[cell] = served.get(cell, 0) + 1
    return {
        **counts,
        "empty_gold_abstained": abstained,
        "empty_gold_served": served,
        "graded": counts["correct"] + counts["wrong"] + counts["abstain"],
    }


def grade_envelopes(
    envelopes: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    warehouse_dir: Path | None = None,
) -> dict[str, Any]:
    """Grade pack order against certified oracles. ``envelopes`` are ``{id, env}``."""
    if mode not in MODES:
        raise SystemExit("grade52: --mode is required: " + ", ".join(MODES))
    _bootstrap()
    pack = _load_yaml(PACK_PATH)
    oracle_doc = _load_yaml(ORACLE_PATH).get("oracles") or {}
    oracles = oracle_doc if isinstance(oracle_doc, dict) else {}
    questions = list(pack["questions"])
    if len(questions) != 52:
        raise SystemExit(f"pack size {len(questions)} != 52")
    flagged = set(pack_gold_served_ids(questions, oracles))
    by_id = {str(item.get("id")): item.get("env") for item in envelopes}
    folder = warehouse_dir or Path(tempfile.mkdtemp(prefix="grade52-"))
    con, as_of, _path = _open_warehouse(folder)
    cases: list[dict[str, Any]] = []
    try:
        for question in questions:
            qid = str(question["id"])
            env = by_id.get(qid)
            if not isinstance(env, Mapping):
                raise SystemExit(f"missing envelope for {qid}")
            trap = _is_trap(question)
            sql = None if trap else _certified_sql(oracles.get(qid))
            broken = sql is None and not trap
            if sql is None:
                gold: list[dict[str, Any]] | None = None
            else:
                gold = _execute(con, sql, as_of)
            served = _rows_of(env.get("rows"))
            row = grade_case(
                gold=gold,
                gold_sql=sql,
                env=env,
                served_rows=served,
                as_of=as_of,
                trap=trap,
                gold_broken=broken,
            )
            row["id"] = qid
            row["pack_gold_served"] = qid in flagged
            cases.append(row)
    finally:
        con.close()
    counts = tally(cases)
    if _accounted(counts) != len(cases):
        raise SystemExit("grade52: buckets do not add up to n")
    return {
        "as_of": as_of,
        "n": len(cases),
        "pack_gold_served_ids": sorted(flagged),
        **counts,
        "cases": cases,
    }


def _artifact_dir() -> Path:
    root = os.environ.get("RUNNER_TEMP") or os.environ.get("TMPDIR") or tempfile.gettempdir()
    path = Path(root) / "grade52"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_report(report: dict[str, Any]) -> Path:
    """Write the run artifact. Caller prints the path. No SQL, no question text."""
    _require_label(report)
    dest = _artifact_dir() / "grade52.json"
    slim_cases = []
    for item in report["cases"]:
        slim_cases.append(
            {
                "id": item["id"],
                "bucket": item["bucket"],
                "reason": item["reason"],
                "route": item["route"],
                "badge": item["badge"],
                "abstain_code": item["abstain_code"],
                "as_of": item["as_of"],
                "pack_gold_served": bool(item.get("pack_gold_served")),
                "served_n": item.get("served_n"),
            }
        )
    without = report["without_pack_gold_served"]
    body = {
        "mode": report["mode"],
        "dms_sha": report["dms_sha"],
        "pack_gold_served": report["pack_gold_served"],
        "pack_gold_served_ids": report["pack_gold_served_ids"],
        "as_of": report["as_of"],
        "n": report["n"],
        "graded": report["graded"],
        "correct": report["correct"],
        "wrong": report["wrong"],
        "abstain": report["abstain"],
        "refusal_ok": report["refusal_ok"],
        "refusal_wrong": report["refusal_wrong"],
        "empty_gold": report["empty_gold"],
        "gold_broken": report["gold_broken"],
        "empty_gold_abstained": report["empty_gold_abstained"],
        "empty_gold_served": report["empty_gold_served"],
        "score": True,
        "without_pack_gold_served": without,
        "cases": slim_cases,
    }
    dest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def _served_label(served: Mapping[str, int]) -> str:
    if not served:
        return "none"
    parts = [f"{key}:{served[key]}" for key in sorted(served, key=int)]
    return ",".join(parts)


def _require_label(report: Mapping[str, Any]) -> None:
    mode = report.get("mode")
    sha = report.get("dms_sha")
    flag = report.get("pack_gold_served")
    if mode not in MODES or not isinstance(sha, str) or len(sha) < 40 or flag not in {
        "included",
        "excluded",
    }:
        raise SystemExit("grade52: output requires mode, sha, and pack_gold_served")


def _accounted(report: Mapping[str, Any]) -> int:
    return sum(int(report[key]) for key in _BUCKET_KEYS)


def summary_line(report: Mapping[str, Any]) -> str:
    """One score line. The seven buckets are disjoint and add up to n."""
    _require_label(report)
    for key in _BUCKET_KEYS:
        if key not in report:
            raise SystemExit("grade52: output requires mode, sha, and pack_gold_served")
    if _accounted(report) != int(report["n"]):
        raise SystemExit("grade52: buckets do not add up to n")
    return (
        f"sha={report['dms_sha']} pack_gold_served={report['pack_gold_served']} "
        f"{report['n']}: correct={report['correct']} wrong={report['wrong']} "
        f"abstain={report['abstain']} refusal_ok={report['refusal_ok']} "
        f"refusal_wrong={report['refusal_wrong']} empty_gold={report['empty_gold']} "
        f"gold_broken={report['gold_broken']} mode={report['mode']}"
    )


def _scrub_credentials() -> None:
    """Drop credential-shaped and model-shaped env names. No vendor list."""
    for name in list(os.environ):
        upper = name.upper()
        if (
            upper == "MODEL"
            or upper.startswith("MODEL_")
            or upper.endswith("_MODEL")
            or "_MODEL_" in upper
            or any(upper.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES)
        ):
            os.environ.pop(name, None)


def _prepare_flag_off() -> None:
    _bootstrap()
    os.environ.pop("DMS_ASK_CLARIFY", None)
    os.environ.pop("DMS_CLOOP_B", None)
    os.environ["DMS_DEMO_FALLBACK"] = "0"
    _scrub_credentials()


def _dms_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("grade52: dms sha unavailable") from exc
    sha = out.strip()
    if len(sha) < 40:
        raise SystemExit("grade52: dms sha unavailable")
    return sha


def _resolve_mode(mode: str | None) -> str:
    """External envelopes are ``served``. The in-repo proof is ``stub-exec``."""
    if mode is None:
        return MODE_SERVED
    if mode not in MODES:
        raise SystemExit("grade52: --mode is required: " + ", ".join(MODES))
    return str(mode)


def replay_executing() -> list[dict[str, Any]]:
    """Same capture, but submit executes the SQL it is given."""
    _prepare_flag_off()
    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from cortex_contract.execution import Manifest, QueryResult
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter
    from fastapi.testclient import TestClient
    from tests.fixtures.ask_guide.capture_flag_off_52 import (
        SESSION_ID,
        load_responses,
    )

    recorded = load_responses()
    by_id = recorded["by_id"]
    bind = recorded["session_bind"]
    pack = _load_yaml(PACK_PATH)
    questions = list(pack["questions"])
    spaces = dict(pack["spaces"])
    db = Path(tempfile.mkdtemp(prefix="grade52-exec-")) / "warehouse.duckdb"
    ensure_demo_warehouse(db)

    class _Stub:
        def __init__(self) -> None:
            self.current = ""

        def _row(self) -> dict[str, Any]:
            return by_id[self.current]

        def submit(self, req: Any) -> QueryResult:
            plan = getattr(req, "plan", None)
            kind = plan.get("kind") if isinstance(plan, dict) else None
            if kind == "session_bind":
                return QueryResult(
                    ok=bool(bind["ok"]),
                    status=str(bind["status"]),
                    run_id=str(bind["run_id"]),
                )
            body = getattr(req, "body", None)
            sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
            con = connect_file(db)
            try:
                cur = con.execute(sql)
                cols = [str(item[0]) for item in (cur.description or [])]
                rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            finally:
                con.close()
            return QueryResult(ok=True, status="ok", run_id="run_exec", output={"rows": rows})

        def ask(self, req: AskRequest) -> AskResponse:
            body = self._row()["ask"]
            _ = req
            return AskResponse(
                answer=str(body["answer"]),
                badge=str(body["badge"]),
                sql_used=str(body["sql_used"]),
                rows=list(body["rows"]),
                assumptions=body["assumptions"],
                audit_id=str(body["audit_id"]),
                route=str(body["route"]),
            )

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            body = self._row()["ledger"]
            return LedgerAppendResponse(
                entry_id=str(body["entry_id"]),
                hash=str(body["hash"]),
            )

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            _ = question
            return dict(self._row()["insights"])

    minter = ManifestMinter(openvault_url="http://127.0.0.1:9")

    def _mint(acl: Any) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    cortex = _Stub()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    rows: list[dict[str, Any]] = []
    for question in questions:
        cortex.current = str(question["id"])
        res = client.post(
            "/v1/chat/ask",
            json={
                "question": question["question"],
                "space_id": spaces[str(question["space"])],
                "session_id": SESSION_ID,
            },
        )
        if res.status_code != 200:
            raise SystemExit(f"{question['id']} HTTP {res.status_code}")
        body = res.json()
        if not isinstance(body, dict):
            raise SystemExit(f"{question['id']} envelope is not an object")
        rows.append({"id": question["id"], "env": body})
    if len(rows) != 52:
        raise SystemExit(f"replay size {len(rows)} != 52")
    return rows


def _labelled(report: dict[str, Any], *, mode: str, included: str) -> dict[str, Any]:
    report["mode"] = mode
    report["dms_sha"] = _dms_sha()
    report["pack_gold_served"] = included
    return report


def _finish(report: dict[str, Any], mode: str) -> dict[str, Any]:
    _labelled(report, mode=mode, included="included")
    rest = [item for item in report["cases"] if not item.get("pack_gold_served")]
    without = tally(rest)
    without.update(
        {
            "n": len(rest),
            "as_of": report["as_of"],
            "mode": mode,
            "dms_sha": report["dms_sha"],
            "pack_gold_served": "excluded",
            "pack_gold_served_ids": report["pack_gold_served_ids"],
        }
    )
    report["without_pack_gold_served"] = without
    path = write_report(report)
    print(summary_line(report))
    print(summary_line(without))
    print(f"artifact: {path}")
    return report


def grade_loaded(envelopes: Sequence[Mapping[str, Any]], *, mode: str) -> dict[str, Any]:
    """Grade served rows. A submit-stub pack is labelled stub and is not a score."""
    if _is_submit_stub(envelopes):
        print("mode=stub not a score")
        raise SystemExit("grade52: stub not a score")
    report = grade_envelopes(envelopes, mode=mode)
    return _finish(report, mode)


def grade_main(mode: str | None = None) -> dict[str, Any]:
    """In-repo proof: exec-SQL stub, flags off. Same product as main 57d85c5."""
    chosen = MODE_STUB_EXEC if mode is None else _resolve_mode(mode)
    if chosen != MODE_STUB_EXEC:
        raise SystemExit("grade52: --main grades the exec-SQL stub as stub-exec")
    return grade_loaded(replay_executing(), mode=MODE_STUB_EXEC)


def grade_envelopes_path(path: Path) -> dict[str, Any]:
    return grade_loaded(load_envelopes(path), mode=MODE_SERVED)


def self_test() -> dict[str, str]:
    """Plants. No pack ids. Returns the plant name -> bucket."""
    renamed_gold = [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 3}]
    renamed_served = [{"item": "A", "qty": 2}, {"item": "B", "qty": 3}]
    renamed = compare_rows(renamed_gold, renamed_served, ordered=False)

    dropped = compare_rows(renamed_gold, [{"item": "A", "qty": 2}], ordered=False)

    ordered_sql = "SELECT sku, qty FROM src ORDER BY qty"
    plain_sql = "SELECT sku, qty FROM src"
    nested_sql = "SELECT sku FROM (SELECT sku, qty FROM src ORDER BY qty) s"
    if not oracle_orders(ordered_sql):
        raise SystemExit("self-test: top-level ORDER BY was not seen")
    if oracle_orders(plain_sql) or oracle_orders(nested_sql):
        raise SystemExit("self-test: non-top-level ORDER BY counted")
    swapped_gold = [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]
    swapped_served = [{"sku": "B", "qty": 2}, {"sku": "A", "qty": 1}]
    swapped = compare_rows(swapped_gold, swapped_served, ordered=oracle_orders(ordered_sql))
    shuffled = compare_rows(swapped_gold, swapped_served, ordered=oracle_orders(plain_sql))

    rounded = compare_rows([{"n": 1.2}], [{"n": 1.23}], ordered=False)
    loose = compare_rows([{"n": 1.0}], [{"n": "1.004"}], ordered=False)
    bool_cell = compare_rows([{"flag": True}], [{"flag": 1}], ordered=False)
    null_cell = compare_rows([{"v": None}], [{"v": 0}], ordered=False)
    case_text = compare_rows([{"name": "Ab"}], [{"name": " ab "}], ordered=False)
    extra = compare_rows([{"sku": "A"}], [{"sku": "A", "extra": 9}], ordered=False)
    # Same row as extra, with that gold row duplicated on the answer side.
    duplicated = compare_rows(
        [{"sku": "A"}],
        [{"sku": "A", "extra": 1}, {"sku": "A", "extra": 2}],
        ordered=False,
    )
    kept = compare_rows(
        [{"sku": "A"}, {"sku": "A"}],
        [{"sku": "A", "extra": 1}, {"sku": "A", "extra": 2}],
        ordered=False,
    )
    folded = compare_rows([{"Sku": "A"}], [{"sku": "A"}], ordered=False)
    named_mismatch = compare_rows(
        [{"a": 1, "b": 2}],
        [{"a": 2, "b": 1}],
        ordered=False,
    )

    plants = {
        "renamed_column": renamed[0],
        "dropped_row": dropped[0],
        "swapped_order": swapped[0],
        "ordered_shuffle": swapped[0],
        "unordered_shuffle": shuffled[0],
        "extra_column": extra[0],
        "duplicated_row": duplicated[0],
    }
    expect = {
        "renamed_column": BUCKET_CORRECT,
        "dropped_row": BUCKET_WRONG,
        "swapped_order": BUCKET_WRONG,
        "ordered_shuffle": BUCKET_WRONG,
        "unordered_shuffle": BUCKET_CORRECT,
        "extra_column": BUCKET_CORRECT,
        "duplicated_row": BUCKET_WRONG,
    }
    for name, bucket in expect.items():
        if plants[name] != bucket:
            raise SystemExit(f"self-test {name}: {plants[name]} != {bucket}")
    if dropped[1] != "rowcount 1 vs 2":
        raise SystemExit(f"self-test dropped reason: {dropped[1]}")
    if swapped[1] != "order mismatch":
        raise SystemExit(f"self-test order reason: {swapped[1]}")
    if shuffled[1] != "match":
        raise SystemExit(f"self-test shuffle reason: {shuffled[1]}")
    if rounded[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: gold decimals did not round the served value")
    if loose[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: abs_tol 0.005 rejected a numeric string")
    if bool_cell[0] != BUCKET_WRONG or null_cell[0] != BUCKET_WRONG:
        raise SystemExit("self-test: bool or null collapsed into a number")
    if case_text[0] != BUCKET_WRONG:
        raise SystemExit("self-test: string case was folded")
    if extra[0] != BUCKET_CORRECT or extra[1] != "match":
        raise SystemExit("self-test: extra served column rejected")
    if duplicated[0] != BUCKET_WRONG or duplicated[1] != "rowcount 2 vs 1":
        raise SystemExit(f"self-test: duplicated gold row collapsed ({duplicated})")
    if kept[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: gold duplicates were dropped")
    if folded[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: case-insensitive column name missed")
    if named_mismatch[0] != BUCKET_WRONG or named_mismatch[1] != "row mismatch":
        raise SystemExit("self-test: exact names were permuted")

    empty_served = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L0_CERTIFIED", "route": "sql", "abstained": False},
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
    )
    empty_abstain = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "abstain", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
    )
    empty_zero = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L1_GOVERNED_METRIC", "route": "governed_metric", "abstained": False},
        served_rows=[],
        as_of="2026-10-09",
    )
    if empty_served["bucket"] != BUCKET_EMPTY or empty_served["reason"] != "served with 1 row":
        raise SystemExit("self-test: served empty-gold folded out of its bucket")
    if empty_abstain["bucket"] != BUCKET_EMPTY or empty_abstain["reason"] != "abstain":
        raise SystemExit("self-test: abstained empty-gold left its bucket")
    if empty_zero["bucket"] != BUCKET_EMPTY:
        raise SystemExit("self-test: zero-row empty-gold became a graded answer")
    counts = tally([empty_served, empty_abstain, empty_zero])
    if counts["wrong"] or counts["correct"] or counts["graded"]:
        raise SystemExit("self-test: empty-gold counted inside graded")
    if counts["empty_gold"] != 3 or counts["empty_gold_abstained"] != 1:
        raise SystemExit("self-test: empty-gold sub-counts drifted")
    if counts["empty_gold_served"] != {"0": 1, "1": 1}:
        raise SystemExit(f"self-test: served sub-count {counts['empty_gold_served']}")
    if _accounted(counts) != 3:
        raise SystemExit("self-test: empty-gold buckets did not add up")

    trap_served = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L0_CERTIFIED", "route": "sql", "abstained": False},
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
        trap=True,
    )
    trap_abstain = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "abstain", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
        trap=True,
    )
    broken = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "abstain", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
        gold_broken=True,
    )
    if trap_served["bucket"] != BUCKET_REFUSAL_WRONG:
        raise SystemExit("self-test: served trap was not a wrong refusal")
    if trap_abstain["bucket"] != BUCKET_REFUSAL_OK:
        raise SystemExit("self-test: abstained trap was not a correct refusal")
    if broken["bucket"] != BUCKET_GOLD_BROKEN:
        raise SystemExit("self-test: missing gold was not gold-broken")
    if trap_served["bucket"] == BUCKET_EMPTY or trap_abstain["bucket"] == BUCKET_EMPTY:
        raise SystemExit("self-test: a trap landed in empty gold")

    try:
        summary_line({"n": 52, "correct": 0, "wrong": 0, "abstain": 0})
    except SystemExit as exc:
        if "mode" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: unlabelled summary was accepted")
    if _resolve_mode(None) != MODE_SERVED:
        raise SystemExit("self-test: served is not the envelope mode")
    stub_only = [
        {
            "id": f"s{i}",
            "env": {"badge": "L1_GOVERNED_METRIC", "abstained": False, "rows": [{"n": 1}]},
        }
        for i in range(10)
    ]
    mixed = [
        *stub_only,
        {"id": "real", "env": {"badge": "L1_GOVERNED_METRIC", "abstained": False, "rows": [{"sku": "A"}]}},
    ]
    if not _is_submit_stub(stub_only) or _is_submit_stub(mixed):
        raise SystemExit("self-test: submit stub detector drifted")
    try:
        grade_loaded(stub_only, mode=MODE_SERVED)
    except SystemExit as exc:
        if "stub" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: submit stub was printed as a score")
    check_line = summary_line(
        {
            "mode": MODE_SERVED,
            "dms_sha": "0" * 40,
            "pack_gold_served": "included",
            "n": 52,
            "correct": 23,
            "wrong": 0,
            "abstain": 20,
            "refusal_ok": 6,
            "refusal_wrong": 2,
            "empty_gold": 0,
            "gold_broken": 1,
        }
    )
    if not check_line.endswith(
        "52: correct=23 wrong=0 abstain=20 refusal_ok=6 refusal_wrong=2 "
        "empty_gold=0 gold_broken=1 mode=served"
    ):
        raise SystemExit("self-test: check-shaped line drifted")
    try:
        summary_line(
            {
                "mode": MODE_SERVED,
                "dms_sha": "0" * 40,
                "pack_gold_served": "included",
                "n": 52,
                "correct": 23,
                "wrong": 0,
                "abstain": 21,
                "refusal_ok": 6,
                "refusal_wrong": 2,
                "empty_gold": 0,
                "gold_broken": 1,
            }
        )
    except SystemExit as exc:
        if "add up" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: 23+21+8+1 was accepted as 52")

    gold_wide = [{f"g{i}": i for i in range(8)}]
    served_unique = [{**{f"s{i}": i for i in range(8)}, **{f"x{i}": 100 + i for i in range(12)}}]
    served_named = [{**{f"g{i}": i for i in range(8)}, **{f"x{i}": 100 + i for i in range(12)}}]
    served_ambiguous = [{f"s{i}": 1 for i in range(20)}]
    gold_ambiguous = [{f"g{i}": 1 for i in range(8)}]
    started = time.monotonic()
    wide_unique = compare_rows(gold_wide, served_unique, ordered=False)
    wide_named = compare_rows(gold_wide, served_named, ordered=False)
    wide_ambiguous = compare_rows(gold_ambiguous, served_ambiguous, ordered=False)
    elapsed = time.monotonic() - started
    if elapsed > 0.5:
        raise SystemExit(f"self-test: 20-wide map took {elapsed:.3f}s")
    if wide_unique[0] != BUCKET_CORRECT or wide_named[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: signature-narrowed wide answer was rejected")
    if wide_ambiguous[1] != "unmappable":
        raise SystemExit(f"self-test: wide ambiguous map did not cap ({wide_ambiguous})")

    print("self-test ok")
    print(
        "plants: renamed_column=CORRECT dropped_row=WRONG "
        "swapped_order=WRONG ordered_shuffle=WRONG unordered_shuffle=CORRECT "
        "extra_column=CORRECT duplicated_row=WRONG "
        "empty_gold_served=EMPTY_GOLD refusal_wrong=REFUSAL_WRONG "
        "unlabelled=refused wide_ambiguous=unmappable"
    )
    return plants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--main", action="store_true")
    parser.add_argument("--envelopes", type=Path)
    args = parser.parse_args(argv)
    if args.main and args.envelopes is not None:
        raise SystemExit("grade52: pass --envelopes or --main")
    run_score = args.main or args.envelopes is not None
    if args.self_test or not run_score:
        self_test()
    if args.envelopes is not None:
        grade_envelopes_path(args.envelopes)
    elif args.main:
        grade_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
