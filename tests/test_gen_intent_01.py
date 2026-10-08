"""GEN-INTENT-01: list intent is not an invented stock-value aggregate.

The two baseline shapes (generate empty, then ranking; generate SQL that
fails validate:ungranted) must not come back L2_VALIDATED with
stock_value_myr. A grammar list is allowed. A query-skill row is not a
source for that list.

Must fail on 6f7139a3 for the two shapes. Measure-naming SQL stays the
compile output from that revision.
"""

from __future__ import annotations

import builtins
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology

_AGG = re.compile(r"\b(?:sum|count|avg|min|max)\s*\(|\bgroup\s+by\b", re.I)

ROOT = Path(__file__).resolve().parents[1]
_SCORED = (
    "curated_ceo",
    "oracles.yaml",
    "questions.yaml",
    "golden",
    "certified_queries",
    "dms_query_skills",
)
_SERVE_FILES = (
    "packages/executor/dms_executor/generative_ask.py",
    "packages/executor/dms_executor/semantic_retrieve.py",
    "packages/executor/dms_executor/ontology.py",
    "packages/executor/dms_executor/__init__.py",
)
# Captured on 6f7139a3. Questions that name a measure.
_STOCK_BY_CATEGORY_SQL = (
    'SELECT d0."category" AS "product_category", '
    'ROUND(SUM(f.quantity_kg * f.unit_cost_myr), 2) AS "stock_value_myr"\n'
    "FROM inventory f\n"
    "LEFT JOIN (SELECT sku, ANY_VALUE(category) AS category FROM inventory "
    'GROUP BY sku) d0 ON f."sku" = d0."sku"\n'
    'GROUP BY d0."category"\n'
    'ORDER BY "stock_value_myr" DESC\n'
    "LIMIT 50"
)
_STOCK_OF_CHEMICALS_SQL = (
    'SELECT d0."sku" AS "product_sku", '
    'ROUND(SUM(f.quantity_kg * f.unit_cost_myr), 2) AS "stock_value_myr"\n'
    "FROM inventory f\n"
    "LEFT JOIN (SELECT sku, ANY_VALUE(category) AS category FROM inventory "
    'GROUP BY sku) d0 ON f."sku" = d0."sku"\n'
    'WHERE f."category" = \'CHEMICALS\'\n'
    'GROUP BY d0."sku"\n'
    'ORDER BY "stock_value_myr" DESC\n'
    "LIMIT 50"
)


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_intent", hash="hash_intent_not_entry")


def _exec_submit(warehouse: Path) -> Any:
    def _run(sql: str) -> Any:
        con = connect_file(warehouse)
        try:
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(
            ok=True, status="ok", run_id="run_intent", output={"rows": rows}
        )

    return _run


def _rank(*metric_ids: str) -> dict[str, Any]:
    """Generate returned nothing. Ranking is the only candidate."""
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
        "ontology": {
            "ok": True,
            "metrics": [
                {"id": mid, "importance": {"rank": i + 1}}
                for i, mid in enumerate(metric_ids)
            ],
        },
        "values": [],
    }


def _ungranted_suppliers(*metric_ids: str) -> dict[str, Any]:
    sql = "SELECT s.supplier_id FROM suppliers s"
    return {
        "query_sql": sql,
        "plan_source": "ontology_plan",
        "phase": "generate",
        "generative": {"ok": True, "sql": sql},
        "ontology": {
            "ok": True,
            "metrics": [{"id": mid} for mid in metric_ids],
        },
    }


def _warehouse(tmp_path: Path) -> tuple[Path, Any]:
    """Test extract. Not the scored pack. Mixed category casings."""
    db = tmp_path / "intent.duckdb"
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        con.execute(
            """
            INSERT INTO inventory VALUES
              ('SKU-CHEM-B', 'WH-C', 10, 1, 2.0, 'SUP-04', 'Chemicals', NULL),
              ('SKU-CHEM-C', 'WH-C', 4, 1, 3.0, 'SUP-04', 'chemical', NULL),
              ('SKU-BIO', 'WH-C', 6, 1, 2.5, 'SUP-04', 'BIOCHEMICAL', NULL),
              ('SKU-NON', 'WH-C', 8, 1, 1.5, 'SUP-04', 'NON-CHEMICAL', NULL)
            """
        )
    finally:
        con.close()
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None and onto.verified
    return db, onto


def _ask(
    db: Path,
    onto: Any,
    question: str,
    payload: dict[str, Any],
    *,
    grantable: set[str],
) -> dict[str, Any] | None:
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=grantable,
        compute=lambda _c: payload,
        submit=_exec_submit(db),
        ledger_append=_ledger,
        ontology=onto,
        bind_on_miss=True,
    )


def _no_invented_measure(env: dict[str, Any]) -> None:
    """L2 with stock_value_myr is the baseline WRONG. A list or abstain is not."""
    assert_envelope_valid(env)
    sql = str(env.get("sql_used") or "")
    rows = list(env.get("rows") or [])
    text = str(env.get("text") or "")
    if env.get("badge") == "L2_VALIDATED" and not env.get("abstained"):
        assert not _AGG.search(sql)
        assert "stock_value_myr" not in sql
        for row in rows:
            assert "stock_value_myr" not in row
        assert "stock_value_myr" not in text
        assert env.get("plan_origin") != "ontology_ranking"
    else:
        assert env.get("badge") == "ABSTAIN"
        assert env.get("abstained") is True
        assert rows == []
        blob = text + " " + " ".join(str(a) for a in (env.get("assumptions") or []))
        assert (
            "generate_empty_no_ranking_answer" in blob
            or "unrequested_measure:" in blob
        )


def test_generate_empty_list_is_not_stock_value(tmp_path: Path) -> None:
    db, onto = _warehouse(tmp_path)
    opened: list[str] = []
    real_open = builtins.open

    def _spy(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    grants = {"inventory", "locations", "transactions", "suppliers", "shipments"}
    builtins.open = _spy
    try:
        env = _ask(
            db,
            onto,
            "List chemicals in inventory",
            _rank("cq_chemicals_list"),
            grantable=grants,
        )
    finally:
        builtins.open = real_open
    assert env is not None
    _no_invented_measure(env)
    blob = " ".join(opened).lower()
    for needle in _SCORED:
        assert needle not in blob


def test_show_all_and_which_are_in_are_not_aggregates(tmp_path: Path) -> None:
    db, onto = _warehouse(tmp_path)
    grants = {"inventory", "locations", "transactions", "suppliers", "shipments"}
    for question in (
        "show all chemicals in inventory",
        "which chemicals are in inventory",
    ):
        env = _ask(db, onto, question, _rank("cq_chemicals_list"), grantable=grants)
        assert env is not None
        _no_invented_measure(env)


_VARIANT_LIST_PHRASINGS = (
    "List chemicals in inventory",
    "show all chemicals in inventory",
    "which chemicals are in inventory",
)


@pytest.mark.xfail(strict=True, reason="LIST-MATCH-EXACT-01")
def test_list_rows_match_oracle_set_on_variant_lake(tmp_path: Path) -> None:
    """Oracle set is the exact CHEMICALS key. Variant rows stay in the lake."""
    db, onto = _warehouse(tmp_path)
    grants = {"inventory", "locations", "transactions", "suppliers", "shipments"}
    for question in _VARIANT_LIST_PHRASINGS:
        env = _ask(db, onto, question, _rank("cq_chemicals_list"), grantable=grants)
        assert env is not None
        got = {str(row.get("lot_sku")) for row in (env.get("rows") or [])}
        assert got == {"SKU-GAMMA"}, (question, got)


def test_ungranted_validate_does_not_answer_the_list(tmp_path: Path) -> None:
    db, onto = _warehouse(tmp_path)
    submitted: list[str] = []

    def _submit(sql: str) -> Any:
        submitted.append(sql)
        return _exec_submit(db)(sql)

    env = maybe_generative_ask(
        "List chemicals in inventory",
        warehouse=db,
        grantable={"inventory", "locations", "shipments"},
        compute=lambda _c: _ungranted_suppliers("cq_chemicals_list"),
        submit=_submit,
        ledger_append=_ledger,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env.get("rows") == []
    blob = str(env.get("text") or "") + " " + " ".join(
        str(a) for a in (env.get("assumptions") or [])
    )
    assert "validate:ungranted:suppliers" in blob
    assert "validate:ungranted:suppliers" in str(env.get("text") or "")
    assert not any("stock_value_myr" in sql for sql in submitted)


def test_named_measure_sql_is_unchanged(tmp_path: Path) -> None:
    """Same seed as 6f7139a3. Mixed casings are a different extract."""
    db = tmp_path / "measure.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    grants = {"inventory", "locations", "transactions", "suppliers", "shipments"}
    captured: list[str] = []

    def _submit(sql: str) -> Any:
        captured.append(sql)
        return SimpleNamespace(
            ok=True,
            status="ok",
            run_id="run_measure",
            output={"rows": [{"product_category": "RAW", "stock_value_myr": 1.0}]},
        )

    pairs = (
        (
            "What is total stock value by category?",
            ("stock_value_by_category",),
            _STOCK_BY_CATEGORY_SQL,
        ),
        (
            "What is the stock value of chemicals?",
            ("cq_chemicals_list",),
            _STOCK_OF_CHEMICALS_SQL,
        ),
    )
    for question, ids, expected in pairs:
        captured.clear()
        env = maybe_generative_ask(
            question,
            warehouse=db,
            grantable=grants,
            compute=lambda _c, ids=ids: _rank(*ids),
            submit=_submit,
            ledger_append=_ledger,
            ontology=onto,
            bind_on_miss=True,
        )
        assert env is not None
        assert env["badge"] == "L2_VALIDATED"
        assert captured == [expected]


def test_query_skill_row_is_not_served() -> None:
    """Normalized skill match is not a DMS serve path. 1d84117c is not read."""
    for rel in _SERVE_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "dms_query_skills" not in text
        assert "possible_query_skill" not in text
        assert "1d84117c" not in text
