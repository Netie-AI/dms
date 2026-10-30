"""Join questions compile from a confirmed measure. Free SQL may not join.

Each test goes red if that rule is removed: unconfirmed measures do not
answer, a compiled count is the grain and not the fan-out, and an L2 join
with no plan abstains.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from cortex_client.models import AskResponse
from dms_executor import Executor, map_ask_response_to_envelope
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.ontology import Ontology, demo_ontology
from dms_executor.typed_plan import (
    TypedPlan,
    compile_confirmed,
    confirm_measure,
    maybe_compiled_measure_ask,
    resolve_cortex_join,
)


def _warehouse(path: Path) -> Ontology:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE orders (order_id INTEGER, region_id INTEGER)")
        con.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")
        con.execute("CREATE TABLE regions (region_id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO regions VALUES (10, 'North'), (20, 'South')")
        con.execute("CREATE TABLE lines (line_id INTEGER, order_id INTEGER)")
        con.execute("INSERT INTO lines VALUES (1, 1), (2, 1), (3, 2), (4, 2)")
        onto = Ontology()
        onto.add_object("order", "orders", ["order_id"])
        onto.add_object("region", "regions", ["region_id"])
        onto.add_link(
            "order_in_region", "order", ["region_id"], "region", ["region_id"]
        )
        onto.add_measure("order_count", "order", "COUNT(*)")
        assert onto.verify(con) == []
        return onto
    finally:
        con.close()


def _joined_response() -> AskResponse:
    return AskResponse(
        answer="There are 4 orders.",
        badge="l2_validated",
        route="sql",
        sql_used=(
            "SELECT r.name, COUNT(*) AS order_count FROM orders o "
            "JOIN regions r ON o.region_id = r.region_id "
            "JOIN lines l ON l.order_id = o.order_id GROUP BY r.name"
        ),
        rows=[
            {"name": "North", "order_count": 2},
            {"name": "South", "order_count": 2},
        ],
        audit_id="aud_join",
        ontology_plan={
            "measure": "order_count",
            "group_by": [["region", "name"]],
        },
    )


def test_seeded_demo_verifies_and_refuses_the_model_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh demo seed must verify. The compiled count replaces the model's."""
    db = tmp_path / "demo.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    onto = demo_ontology(db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        violations = onto.verify(con)
    finally:
        con.close()
    assert violations == [], violations
    resp = AskResponse(
        answer="99 shipments.",
        badge="l2_validated",
        route="sql",
        sql_used=(
            "SELECT COUNT(*) AS shipment_count FROM shipments s "
            "JOIN inventory i ON s.sku = i.sku"
        ),
        rows=[{"shipment_count": 99}],
        audit_id="aud_demo",
        ontology_plan={"measure": "shipment_count"},
    )
    out = resolve_cortex_join(resp, warehouse=db)
    assert out.compiled is True, out.response.answer
    assert int(out.response.rows[0]["shipment_count"]) == 5


def test_how_many_shipments_compiles_without_cortex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "demo.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    env = maybe_compiled_measure_ask(
        "How many shipments?", warehouse=db, space_id="sp_x", session_id="ses_ship"
    )
    assert env is not None
    assert env["abstained"] is False
    assert int(env["rows"][0]["shipment_count"]) == 5
    assert_envelope_valid(env)
    assert maybe_compiled_measure_ask("What is the meaning of life?", warehouse=db) is None


def test_shipment_count_does_not_need_a_cortex_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "demo.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    exe = Executor(cortex=None, warehouse_path=db)
    env = exe.live_ask("How many shipments?", session_id="ses_ship")
    assert env["abstained"] is False
    assert int(env["rows"][0]["shipment_count"]) == 5
    with pytest.raises(RuntimeError, match="CortexClient required"):
        exe.live_ask("What is the meaning of life?", session_id="ses_ship")


def test_unconfirmed_measure_does_not_answer(tmp_path: Path) -> None:
    db = tmp_path / "lake.duckdb"
    onto = _warehouse(db)
    refused = compile_confirmed(
        onto, TypedPlan("order_count", group_by=(("region", "name"),)), ()
    )
    assert getattr(refused, "reason", None) == "unconfirmed_measure"
    out = resolve_cortex_join(
        _joined_response(), warehouse=db, onto=onto, confirmed=set()
    )
    assert out.compiled is False
    assert out.response.rows == []
    assert "unconfirmed_measure" in (out.response.answer or "")


def test_confirmed_measure_counts_orders_not_lines(tmp_path: Path) -> None:
    db = tmp_path / "lake.duckdb"
    onto = _warehouse(db)
    out = resolve_cortex_join(
        _joined_response(), warehouse=db, onto=onto, confirmed={"order_count"}
    )
    assert out.compiled is True
    assert sum(int(row["order_count"]) for row in out.response.rows) == 2
    env = map_ask_response_to_envelope(
        out.response, space_id="sp_x", session_id="ses_plan", compiled_join=True
    )
    assert env["abstained"] is False
    assert env["rows"]
    assert_envelope_valid(env)


def test_confirm_does_not_invent_a_measure(tmp_path: Path) -> None:
    db = tmp_path / "lake.duckdb"
    onto = _warehouse(db)
    with pytest.raises(KeyError, match="does not invent"):
        confirm_measure(db, "made_up_total", onto)


def test_l2_join_without_a_plan_abstains() -> None:
    resp = AskResponse(
        answer="4 schools.",
        badge="l2_validated",
        route="sql",
        sql_used="SELECT COUNT(*) AS n FROM schools a JOIN schools b ON a.id = b.id",
        rows=[{"n": 9}],
        audit_id="aud_free_join",
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_free")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env["values"] == []
    assert "join_needs_compile" in env["text"]
    assert_envelope_valid(env)


def test_comma_from_is_a_join_too() -> None:
    resp = AskResponse(
        answer="9.",
        badge="l2_validated",
        route="sql",
        sql_used="SELECT COUNT(*) AS n FROM schools a, schools b WHERE a.id = b.id",
        rows=[{"n": 9}],
        audit_id="aud_comma",
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_comma")
    assert env["abstained"] is True
    assert "join_needs_compile" in env["text"]


def test_single_table_sql_still_answers() -> None:
    resp = AskResponse(
        answer="2 orders.",
        badge="l2_validated",
        route="sql",
        sql_used="SELECT COUNT(*) AS n FROM orders",
        rows=[{"n": 2}],
        audit_id="aud_one",
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_one")
    assert env["abstained"] is False
    assert env["rows"] == [{"n": 2}]
    assert_envelope_valid(env)


def test_compiled_flag_is_required_for_a_join_to_ship() -> None:
    resp = AskResponse(
        answer="Compiled order_count at grain order.",
        badge="governed_metric",
        route="compiled_join",
        sql_used=(
            'SELECT "region_name" AS region_name, COUNT(*) AS "order_count" '
            "FROM orders f LEFT JOIN regions d0 ON f.region_id = d0.region_id"
        ),
        rows=[{"region_name": "North", "order_count": 1}],
        audit_id="aud_flag",
    )
    blocked = map_ask_response_to_envelope(
        resp, space_id="sp_x", session_id="ses_flag"
    )
    assert blocked["abstained"] is True
    allowed = map_ask_response_to_envelope(
        resp, space_id="sp_x", session_id="ses_flag", compiled_join=True
    )
    assert allowed["abstained"] is False
