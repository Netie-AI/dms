"""One grader for the 52-question pack.

Gold is each certified oracle in ``tests/fixtures/curated_ceo/oracles.yaml``,
executed read-only on a fresh ``ensure_demo_warehouse`` file. ``$as_of`` binds
the connection's current date. A refuse oracle, or a question with no SQL, has
empty gold. Gold is never read from an envelope or a canned stub.

Served rows are the envelope's ``sql_used`` executed on that same warehouse.
The flag-off replay stub returns ``{n: 1}`` for every submit and does not run
SQL; grading those cells would grade the stub. Abstain envelopes have no SQL,
so their served rows are the envelope rows.

Usage:
    python scripts/verify/grade52.py --self-test
    python scripts/verify/grade52.py --main
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from itertools import permutations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
ORACLE_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"

ABS_TOL = 0.005
BUCKET_CORRECT = "CORRECT"
BUCKET_WRONG = "WRONG"
BUCKET_ABSTAIN = "ABSTAIN"
BUCKET_EMPTY = "ABSTAIN_EMPTY_GOLD"
_REFUSAL_ROUTES = frozenset({"abstain", "blocked", "needs_clarification", "refused"})


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


def _candidate_maps(gold_cols: Sequence[str], served_cols: Sequence[str]) -> list[dict[str, str]]:
    """Exact name, then case-insensitive, then permutation of what is left.

    A locked column is not reassigned. ponytail: permutations of the unmatched
    tail only. Ceiling is a handful of curated columns; upgrade is assignment
    if a result grows wide.
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
    maps: list[dict[str, str]] = []
    for perm in permutations(pool, len(still)):
        mapping = dict(locked)
        mapping.update(zip(still, perm, strict=True))
        maps.append(mapping)
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
    maps = _candidate_maps(gold_cols, served_cols)
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


def grade_case(
    *,
    gold: list[dict[str, Any]] | None,
    gold_sql: str | None,
    env: Mapping[str, Any],
    served_rows: list[dict[str, Any]],
    as_of: str,
) -> dict[str, Any]:
    """One case. ``gold is None`` means empty gold (no certified rows)."""
    badge = env.get("badge")
    route = env.get("route")
    code = abstain_code(env)
    base = {
        "route": route,
        "badge": badge,
        "abstain_code": code,
        "as_of": as_of,
    }
    if gold is None:
        if _is_abstain(env):
            return {**base, "bucket": BUCKET_EMPTY, "reason": "abstain"}
        if len(served_rows) == 0:
            return {**base, "bucket": BUCKET_CORRECT, "reason": "match"}
        return {
            **base,
            "bucket": BUCKET_WRONG,
            "reason": f"rowcount {len(served_rows)} vs 0",
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


def served_rows_for(con: Any, env: Mapping[str, Any], as_of: str) -> list[dict[str, Any]]:
    """Rows of ``sql_used`` on this connection. Abstain keeps envelope rows."""
    if _is_abstain(env):
        return _rows_of(env.get("rows"))
    sql = env.get("sql_used")
    if isinstance(sql, str) and sql.strip():
        return _execute(con, sql, as_of)
    return _rows_of(env.get("rows"))


def grade_envelopes(envelopes: Sequence[Mapping[str, Any]], *, warehouse_dir: Path | None = None) -> dict[str, Any]:
    """Grade pack order against certified oracles. ``envelopes`` are ``{id, env}``."""
    _bootstrap()
    pack = _load_yaml(PACK_PATH)
    oracles = _load_yaml(ORACLE_PATH).get("oracles") or {}
    questions = list(pack["questions"])
    if len(questions) != 52:
        raise SystemExit(f"pack size {len(questions)} != 52")
    by_id = {str(item.get("id")): item.get("env") for item in envelopes}
    folder = warehouse_dir or Path(tempfile.mkdtemp(prefix="grade52-"))
    con, as_of, _path = _open_warehouse(folder)
    cases: list[dict[str, Any]] = []
    certified = 0
    try:
        for question in questions:
            qid = str(question["id"])
            env = by_id.get(qid)
            if not isinstance(env, Mapping):
                raise SystemExit(f"missing envelope for {qid}")
            sql = _certified_sql(oracles.get(qid) if isinstance(oracles, dict) else None)
            if sql is None:
                gold: list[dict[str, Any]] | None = None
            else:
                gold = _execute(con, sql, as_of)
                certified += 1
            served = served_rows_for(con, env, as_of)
            row = grade_case(gold=gold, gold_sql=sql, env=env, served_rows=served, as_of=as_of)
            row["id"] = qid
            cases.append(row)
    finally:
        con.close()
    counts = {
        "correct": sum(1 for item in cases if item["bucket"] == BUCKET_CORRECT),
        "wrong": sum(1 for item in cases if item["bucket"] == BUCKET_WRONG),
        "abstain": sum(1 for item in cases if item["bucket"] == BUCKET_ABSTAIN),
        "empty_gold": sum(1 for item in cases if item["bucket"] == BUCKET_EMPTY),
    }
    graded = certified
    return {
        "as_of": as_of,
        "n": len(cases),
        "graded": graded,
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
            }
        )
    body = {
        "as_of": report["as_of"],
        "n": report["n"],
        "graded": report["graded"],
        "correct": report["correct"],
        "wrong": report["wrong"],
        "abstain": report["abstain"],
        "empty_gold": report["empty_gold"],
        "cases": slim_cases,
    }
    dest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def summary_line(report: Mapping[str, Any]) -> str:
    return (
        f"52: correct={report['correct']} wrong={report['wrong']} "
        f"abstain={report['abstain']} empty_gold={report['empty_gold']} "
        f"graded={report['graded']} as_of={report['as_of']}"
    )


def replay_flag_off() -> list[dict[str, Any]]:
    """Flag-off product ask path. No model key. Not the one-SQL canned stub."""
    _bootstrap()
    os.environ.pop("DMS_ASK_CLARIFY", None)
    os.environ.pop("DMS_CLOOP_B", None)
    os.environ["DMS_DEMO_FALLBACK"] = "0"
    for name in list(os.environ):
        if name.startswith(("OPENAI", "ANTHROPIC", "AZURE_OPENAI", "MODEL_")):
            os.environ.pop(name, None)
    from tests.fixtures.ask_guide.capture_flag_off_52 import replay_pack

    rows = replay_pack()
    if len(rows) != 52:
        raise SystemExit(f"replay size {len(rows)} != 52")
    return rows


def grade_main() -> dict[str, Any]:
    report = grade_envelopes(replay_flag_off())
    path = write_report(report)
    print(summary_line(report))
    print(f"artifact: {path}")
    return report


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
    print("self-test ok")
    print(
        "plants: renamed_column=CORRECT dropped_row=WRONG "
        "swapped_order=WRONG ordered_shuffle=WRONG unordered_shuffle=CORRECT "
        "extra_column=CORRECT duplicated_row=WRONG"
    )
    return plants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--main", action="store_true")
    args = parser.parse_args(argv)
    run_self = args.self_test or not args.main
    run_main = args.main or not args.self_test
    if args.self_test and not args.main:
        run_main = False
    if args.main and not args.self_test:
        run_self = False
    if run_self:
        self_test()
    if run_main:
        grade_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
