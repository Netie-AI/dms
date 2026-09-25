"""ORACLE-FIX-02 / dms#308: cq_audit_overdue binds the run date, not CURRENT_DATE.

Harness-only. Seeded tmp lake via ensure_demo_warehouse. No network, no Cortex,
no keys. Expected supplier_id rows are derived from demo_warehouse.py source
plus the certified 90-day rule (ontology.py audit_overdue), never from an
answer. CI fixtures, not live. Not COMPLETE.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oracle_row_match import run_oracle_select  # noqa: E402
from score_curated import (  # noqa: E402
    read_engine_clock_from_con,
    round_date_label,
    stamp_round_clock,
)

ORACLES_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"
SEED_PATH = ROOT / "packages" / "executor" / "dms_executor" / "demo_warehouse.py"

# Certified window: packages/executor/dms_executor/ontology.py:2036-2044
# CAST(f.last_audit_date AS DATE) < CURRENT_DATE - INTERVAL 90 DAY
_AUDIT_WINDOW_DAYS = 90

# Two dates that the seed last_audit_date values split differently.
_AS_OF_EARLY = date(2025, 3, 1)
_AS_OF_LATE = date(2026, 11, 15)

_SUPPLIER_ROW = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,\s*\d+\s*,\s*[0-9.]+\s*,"
    r"\s*'(\d{4}-\d{2}-\d{2})'\s*\)"
)


def _load_demo_warehouse() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dms_demo_warehouse_harness", SEED_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_supplier_last_audit() -> dict[str, date]:
    """supplier_id -> last_audit_date from the seed INSERT string. Not a query."""
    tree = ast.parse(SEED_PATH.read_text(encoding="utf-8"))
    blob = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "INSERT INTO suppliers" in node.value:
                blob = node.value
                break
    assert blob, f"no INSERT INTO suppliers in {SEED_PATH}"
    found: dict[str, date] = {}
    for match in _SUPPLIER_ROW.finditer(blob):
        found[match.group(1)] = date.fromisoformat(match.group(2))
    assert found, f"no supplier last_audit_date literals in seed ({SEED_PATH})"
    return found


def _overdue_ids(as_of: date, audits: dict[str, date]) -> set[str]:
    cutoff = as_of - timedelta(days=_AUDIT_WINDOW_DAYS)
    return {sid for sid, last in audits.items() if last < cutoff}


def _audit_overdue_sql() -> str:
    raw = yaml.safe_load(ORACLES_PATH.read_text(encoding="utf-8")) or {}
    spec = (raw.get("oracles") or {}).get("cq_audit_overdue") or {}
    sql = str(spec.get("sql") or "").strip()
    assert sql, "cq_audit_overdue missing sql"
    return sql


def test_cq_audit_overdue_bound_as_of_two_dates(tmp_path: Path) -> None:
    sql = _audit_overdue_sql()
    assert "$as_of" in sql, (
        "cq_audit_overdue must bind $as_of so the oracle and the run share one date"
    )
    assert "CURRENT_DATE" not in sql.upper(), (
        "cq_audit_overdue must not use CURRENT_DATE"
    )

    audits = _seed_supplier_last_audit()
    want_early = _overdue_ids(_AS_OF_EARLY, audits)
    want_late = _overdue_ids(_AS_OF_LATE, audits)
    assert want_early and want_late and want_early != want_late, (
        "seed last_audit_date must split differently on the two as_of dates"
    )

    warehouse = _load_demo_warehouse()
    db = tmp_path / "dms_demo.duckdb"
    warehouse.ensure_demo_warehouse(db)

    for as_of, want in ((_AS_OF_EARLY, want_early), (_AS_OF_LATE, want_late)):
        rows, err = run_oracle_select(db, sql, params={"as_of": as_of.isoformat()})
        assert err is None, f"cq_audit_overdue ORACLE_ERROR at as_of={as_of.isoformat()}: {err}"
        assert rows is not None
        got = {str(row.get("supplier_id")) for row in rows}
        assert got == want, (
            f"cq_audit_overdue rows at as_of={as_of.isoformat()} "
            f"must match seed-derived overdue supplier_id set"
        )


def test_round_invalid_when_engine_date_crosses_midnight() -> None:
    """Mismatched before/after is INVALID, never WRONG. Matching dates are not."""
    mismatched = round_date_label("2026-09-24", "2026-09-25")
    assert mismatched == "INVALID"
    assert mismatched != "WRONG"

    report = {"passed": True, "wrong": 3}
    label = stamp_round_clock(report, "2026-09-24", "2026-09-25", "UTC")
    assert label == "INVALID"
    assert report["round_label"] == "INVALID"
    assert report["round_label"] != "WRONG"
    assert report["passed"] is False
    assert report["wrong"] == 3
    assert report["oracle_as_of"] == "2026-09-24"
    assert report["oracle_as_of_after"] == "2026-09-25"
    assert report["oracle_timezone"] == "UTC"

    ok = {"passed": True, "wrong": 0}
    match = stamp_round_clock(ok, "2026-09-25", "2026-09-25", "Asia/Kuala_Lumpur")
    assert match is None
    assert ok.get("round_label") != "INVALID"
    assert ok["passed"] is True
    assert ok["wrong"] == 0


def test_engine_date_comes_from_answer_connection(tmp_path: Path) -> None:
    """CURRENT_DATE is read on the same connection, not datetime.now()."""
    warehouse = _load_demo_warehouse()
    db = tmp_path / "dms_demo.duckdb"
    warehouse.ensure_demo_warehouse(db)
    con = warehouse.connect_file(db)
    try:
        con.execute("SET TimeZone = 'UTC'")
        as_of, tz = read_engine_clock_from_con(con)
        row = con.execute(
            "SELECT CAST(CURRENT_DATE AS VARCHAR), current_setting('TimeZone')"
        ).fetchone()
        assert row is not None
        assert tz == "UTC"
        assert as_of == str(row[0])
        assert tz == str(row[1])
    finally:
        con.close()
