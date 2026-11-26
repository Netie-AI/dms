"""FDE-SCENARIOS-01: the multi-company eval packs are sound before they grade DMS.

Stack-free. Rebuilds all five company datasets in DuckDB, re-derives every gold
answer, and checks the grading rules the live runner applies. None of this is a
DMS accuracy score.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _runner() -> ModuleType:
    path = ROOT / "scripts" / "verify" / "fde_scenarios.py"
    spec = importlib.util.spec_from_file_location("fde_scenarios_runner", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fde = _runner()


def test_every_gold_answer_rederives_from_the_seeded_data() -> None:
    assert fde.self_test(list(fde.COMPANIES)) == fde.EXIT_PASS


def test_each_company_has_traps_and_15_to_25_questions() -> None:
    for company in fde.COMPANIES:
        qs = fde.load_questions(company)
        assert 15 <= len(qs) <= 25, company
        expects = {q["expect"] for q in qs}
        assert "clarify" in expects and "refuse" in expects, company
        assert all(q.get("gold_sql") for q in qs if q["expect"] == "answer"), company


def test_datasets_are_deterministic() -> None:
    a = fde.scenarios.build("finance_ar")
    b = fde.scenarios.build("finance_ar")
    assert [t.rows for t in a.tables] == [t.rows for t in b.tables]


def test_rows_match_rounds_numbers_and_ignores_order_unless_ordered() -> None:
    gold = [["A", 10.004], ["B", 2]]
    assert fde.rows_match([["B", "2.00"], ["A", 10.0]], gold, ordered=False)
    assert not fde.rows_match([["B", 2], ["A", 10]], gold, ordered=True)
    assert not fde.rows_match([["A", 10.0]], gold, ordered=False)
    assert not fde.rows_match([["A", 10.1], ["B", 2]], gold, ordered=False)


def _env(**kw: Any) -> dict[str, Any]:
    base = {"badge": "L2", "rows": [], "abstained": False}
    base.update(kw)
    return base


def test_live_judge_wrong_on_confident_trap_and_demo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fde, "_confident", lambda env: not env.get("abstained"))
    trap = {"id": "x", "expect": "refuse"}
    assert fde.judge_live(trap, _env(rows=[{"n": 0}]), None)[0] == "WRONG"
    assert fde.judge_live(trap, _env(abstained=True), None)[0] == "ABSTAIN"
    q = {"id": "y", "expect": "answer"}
    gold = {"rows": [["KL", 5]]}
    assert fde.judge_live(q, _env(rows=[{"w": "KL", "n": 5}]), gold)[0] == "CORRECT"
    assert fde.judge_live(q, _env(rows=[{"w": "KL", "n": 6}]), gold)[0] == "WRONG"
    assert (
        fde.judge_live(q, _env(rows=[{"w": "KL", "n": 5}], demo_fallback_used=True), gold)[0]
        == "WRONG"
    )
    assert fde.judge_live(q, _env(abstained=True), gold)[0] == "ABSTAIN"


def test_ddl_quotes_per_engine() -> None:
    t = fde.scenarios.build("retail").table("stock_levels")
    assert "`sku_code`" in fde.create_sql("mysql", t)
    assert "[sku_code]" in fde.create_sql("sqlserver", t)
    assert '"sku_code" VARCHAR(200)' in fde.create_sql("postgresql", t)
    assert "REFERENCES" in fde.create_sql("postgresql", t)
    assert "REFERENCES" not in fde.create_sql("duckdb", t, with_fks=False)


def test_sqlite_load_lands_every_row(tmp_path: Path) -> None:
    args = fde.main.__globals__["argparse"].Namespace(out_dir=str(tmp_path))
    assert fde.load_engine("sqlite", ["hr"], args) == fde.EXIT_PASS
    ds = fde.scenarios.build("hr")
    con = sqlite3.connect(tmp_path / "fde_hr.sqlite")
    try:
        for t in ds.tables:
            (n,) = con.execute(f'SELECT COUNT(*) FROM "{t.name}"').fetchone()
            assert n == len(t.rows), t.name
    finally:
        con.close()
    # A second load refuses to overwrite (no deletes).
    assert fde.load_engine("sqlite", ["hr"], args) == fde.EXIT_PASS
