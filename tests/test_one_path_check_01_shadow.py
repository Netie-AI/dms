"""ONE-PATH-CHECK-01: flag-off envelopes stay the f9ffc3e1 bytes.

The 52-question replay is the existing flag-off fixture
(``tests/fixtures/ask_guide``). ``as_of`` is the wall clock, so both sides
replace it. The only other allowed difference is ``served_check_shadow``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from dms_executor import sql_grounds
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from sqlglot import exp

from tests.fixtures.ask_guide.capture_flag_off_52 import HERE, dump_rows, replay_pack

_AS_OF = "<as_of>"
_SHADOW = "served_check_shadow"
# Capture day of flag_off_52_f9ffc3e1.json. semantic_retrieve uses
# date.today() - 90 days, which was 2026-07-10 on this day.
_CAPTURE_DAY = (2026, 10, 8)
GOLDEN = HERE / "flag_off_52_f9ffc3e1.json"
# One executing capture of main 57d85c52. as_of is masked at compare time.
EXEC_MAIN = HERE / "flag_off_52_exec_main.json"


def _pin_capture_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin date.today() to the capture day.

    typed_filters imports date inside the function, and datetime.date is
    immutable, so the class on the datetime module is swapped. A later
    system clock still yields the golden literal 2026-07-10.
    """
    base = dt.date

    class _CaptureDate(base):
        @classmethod
        def today(cls) -> dt.date:
            return base(*_CAPTURE_DAY)

    monkeypatch.setattr(dt, "date", _CaptureDate)


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    out.pop(_SHADOW, None)
    if "as_of" in out:
        out["as_of"] = _AS_OF
    return out


def _stable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": row["id"], "env": _stable(row["env"])} for row in rows]


def _diff_paths(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list):
        if len(left) != len(right):
            return [path or "$"]
        paths = []
        for index, (item, other) in enumerate(zip(left, right, strict=True)):
            paths.extend(_diff_paths(item, other, f"{path}[{index}]"))
        return paths
    if left != right:
        return [path or "$"]
    return []


def _row_bag(rows: list[Any]) -> Counter[str]:
    return Counter(json.dumps(row, sort_keys=True, default=str) for row in rows)


def _order_specs(sql: str) -> list[tuple[str | int, bool]] | None:
    """Top-level ORDER BY columns. [] means none. None means the order is not a column."""
    text = (sql or "").strip()
    if not text:
        return []
    try:
        tree = sqlglot.parse_one(text, read=SERVING_DIALECT)
    except sqlglot.errors.SqlglotError:
        return None
    order = tree.args.get("order")
    if order is None:
        return []
    specs: list[tuple[str | int, bool]] = []
    for ordered in order.expressions:
        this = ordered.this
        desc = bool(ordered.args.get("desc"))
        if isinstance(this, exp.Literal) and this.is_int:
            specs.append((int(this.this), desc))
        elif isinstance(this, exp.Column):
            specs.append((this.name, desc))
        else:
            return None
    return specs


def _order_key(row: dict[str, Any], specs: list[tuple[str | int, bool]]) -> tuple[Any, ...]:
    keys: list[Any] = []
    values = list(row.values())
    for name, _desc in specs:
        if isinstance(name, int):
            keys.append(values[name - 1] if 1 <= name <= len(values) else None)
            continue
        found = None
        for key, val in row.items():
            if str(key).lower() == name.lower():
                found = val
                break
        keys.append(found)
    return tuple(keys)


def rows_problem(sql: str, left: list[Any], right: list[Any]) -> str | None:
    """Point-4 rows. No top-level ORDER BY: multiset, duplicates kept.

    A top-level ORDER BY keeps the key sequence exact. Rows that share a key
    stay a multiset, because the engine does not order ties. A value change
    or a dropped duplicate fails either way.
    """
    if not isinstance(left, list) or not isinstance(right, list):
        return "rows"
    specs = _order_specs(sql)
    if specs is None:
        if left != right:
            return "rows exact"
        return None
    if _row_bag(left) != _row_bag(right):
        return "rows multiset"
    if not specs:
        return None
    if [_order_key(r, specs) for r in left if isinstance(r, dict)] != [
        _order_key(r, specs) for r in right if isinstance(r, dict)
    ]:
        return "rows order"
    return None


def _text_problem(left: Any, right: Any, *, rows_relaxed: bool) -> str | None:
    if left == right:
        return None
    if not rows_relaxed:
        return "text"

    def split(text: Any) -> tuple[list[str], Counter[str]]:
        head: list[str] = []
        bullets: list[str] = []
        for line in str(text).split("\n"):
            if line.startswith("  - "):
                bullets.append(line)
            else:
                head.append(line)
        return head, Counter(bullets)

    left_head, left_bullets = split(left)
    right_head, right_bullets = split(right)
    if left_head != right_head or left_bullets != right_bullets:
        return "text"
    return None


def _without_row_order(env: dict[str, Any]) -> dict[str, Any]:
    """Drop fields the row rule already judged. as_of stays masked. checker_version may differ."""
    out = json.loads(json.dumps(env, default=str))
    out.pop("as_of", None)
    out.pop("rows", None)
    out.pop("text", None)
    shadow = out.get("served_check_shadow")
    if isinstance(shadow, dict):
        shadow.pop("checker_version", None)
    receipt = out.get("audit_receipt")
    if isinstance(receipt, dict):
        include = receipt.get("include")
        if isinstance(include, dict):
            include.pop("rows", None)
    return out


def flag_off_problems(main: dict[str, Any], got: dict[str, Any]) -> list[str]:
    sql = str(got.get("sql_used") or main.get("sql_used") or "")
    problems: list[str] = []
    row_hit = rows_problem(sql, list(main.get("rows") or []), list(got.get("rows") or []))
    if row_hit:
        problems.append(row_hit)
    relaxed = row_hit is None and main.get("rows") != got.get("rows")
    text_hit = _text_problem(main.get("text"), got.get("text"), rows_relaxed=relaxed)
    if text_hit:
        problems.append(text_hit)
    main_inc = ((main.get("audit_receipt") or {}).get("include") or {}).get("rows")
    got_inc = ((got.get("audit_receipt") or {}).get("include") or {}).get("rows")
    if main_inc is not None or got_inc is not None:
        inc_hit = rows_problem(sql, list(main_inc or []), list(got_inc or []))
        if inc_hit:
            problems.append(f"include {inc_hit}")
    problems.extend(_diff_paths(_without_row_order(main), _without_row_order(got)))
    return problems


def test_planted_row_value_or_dropped_duplicate_fails() -> None:
    """A changed cell or a dropped duplicate is drift. An unordered shuffle is not."""
    sql = "SELECT category, COUNT(*) AS sku_count FROM inventory GROUP BY category"
    base = [
        {"category": "RAW", "sku_count": 2},
        {"category": "RAW", "sku_count": 2},
        {"category": "PARTS", "sku_count": 1},
    ]
    changed = [dict(base[0], sku_count=9), base[1], base[2]]
    dropped = [base[0], base[2]]
    shuffled = [base[2], base[0], base[1]]
    assert rows_problem(sql, base, changed) == "rows multiset"
    assert rows_problem(sql, base, dropped) == "rows multiset"
    assert rows_problem(sql, base, shuffled) is None
    ordered = sql + " ORDER BY sku_count DESC"
    tied = [base[0], base[1], base[2]]
    tie_swap = [base[1], base[0], base[2]]
    key_swap = [base[2], base[0], base[1]]
    assert rows_problem(ordered, tied, tie_swap) is None
    assert rows_problem(ordered, tied, key_swap) == "rows order"


def test_flag_off_executing_matches_main_except_row_order_and_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Executing 52 vs main 57d85c52. as_of masked. checker_version is the live file hash.

    Served rows are an exact ORDER BY key sequence, or a multiset when the
    served SQL has no top-level ORDER BY. Every other field is byte-equal.
    """
    _pin_capture_day(monkeypatch)
    db = tmp_path / "flag52.duckdb"
    ensure_demo_warehouse(db)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    live = replay_pack(execute_sql=True)
    golden = json.loads(EXEC_MAIN.read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in golden]
    live_hash = hashlib.sha256(Path(sql_grounds.__file__).read_bytes()).hexdigest()
    problems: list[str] = []
    stamped = 0
    for got_row, main_row in zip(live, golden, strict=True):
        got = got_row["env"]
        main = main_row["env"]
        hit = flag_off_problems(main, got)
        if hit:
            problems.append(f"{got_row['id']}: {hit}")
        shadow = got.get("served_check_shadow")
        if isinstance(shadow, dict) and "checker_version" in shadow:
            stamped += 1
            assert shadow["checker_version"] == live_hash, got_row["id"]
    assert problems == [], "\n".join(problems)
    assert stamped > 0


def test_flag_off_envelopes_match_f9ffc3e1_except_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_capture_day(monkeypatch)
    live = replay_pack()
    assert len(live) == 52
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in golden]
    left = dump_rows(_stable_rows(live))
    right = dump_rows(_stable_rows(golden))
    if left != right:
        main_rows = {row["id"]: row["env"] for row in golden}
        problems: list[str] = []
        for row in live:
            paths = _diff_paths(_stable(row["env"]), _stable(main_rows[row["id"]]))
            if paths:
                problems.append(f"{row['id']}: {paths}")
        pytest.fail("flag-off envelopes differ from f9ffc3e1:\n" + "\n".join(problems))
    served = 0
    for row in live:
        env = row["env"]
        shadow = env.get(_SHADOW)
        if env.get("badge") == "L2_VALIDATED" and env.get("route") == "generated":
            served += 1
            assert isinstance(shadow, dict), row["id"]
            assert shadow.get("checker_version")
            assert "error" not in shadow
            assert "unclear" in shadow and "conjuncts" in shadow
        else:
            assert shadow is None, row["id"]
    assert served > 0
