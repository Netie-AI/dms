"""A2-03 / dms#259: a link declared on the child's own key is not blessed.

order_lines.line_id -> orders.order_id passes fk_intact when every line_id
happens to equal a real order id, and the join then attributes each line to
the wrong order. verify() refuses such a link as ``fk_is_child_key`` unless it
is declared ``one_to_one=True``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology, from_manifest

_SEED = (
    "CREATE TABLE customers (customer_id INTEGER, region VARCHAR)",
    "INSERT INTO customers VALUES (1,'North'),(2,'South'),(3,'North')",
    "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER)",
    "INSERT INTO orders VALUES (10,1),(11,2),(12,3),(13,2)",
    "CREATE TABLE order_lines (line_id INTEGER, order_id INTEGER, qty DOUBLE)",
    # line_id values coincide with real order ids on purpose.
    "INSERT INTO order_lines VALUES (10,11,1),(11,11,2),(12,10,5),(13,12,7)",
    # A genuine shared-primary-key subtype: every order has at most one invoice.
    "CREATE TABLE invoices (order_id INTEGER, invoice_no VARCHAR)",
    "INSERT INTO invoices VALUES (10,'I-10'),(11,'I-11'),(12,'I-12')",
)


def _con() -> Any:
    con = duckdb.connect(":memory:")
    for stmt in _SEED:
        con.execute(stmt)
    return con


def _ontology(line_fk: str, line_key: str = "line_id") -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"])
    o.add_object("order", "orders", ["order_id"])
    o.add_object("line", "order_lines", [line_key])
    o.add_link("order_customer", "order", ["customer_id"], "customer", ["customer_id"])
    o.add_link("line_order", "line", [line_fk], "order", ["order_id"])
    o.add_measure("units", "line", "SUM(f.qty)")
    return o


def test_link_on_child_key_is_refused_and_names_the_sibling_fk() -> None:
    o = _ontology("line_id")
    con = _con()
    try:
        violations = o.verify(con)
    finally:
        con.close()
    assert not o.verified
    [v] = violations
    assert v.check == "fk_is_child_key"
    assert v.subject == "line_order"
    assert "line_id" in v.detail
    assert "order_id" in v.detail and "likely foreign key" in v.detail
    assert o.links["line_order"].cardinality == "unverified"


def test_child_key_match_is_case_insensitive() -> None:
    """DuckDB folds identifier case, so key LINE_ID and link line_id are one column."""
    o = _ontology("line_id", line_key="LINE_ID")
    con = _con()
    try:
        [v] = o.verify(con)
    finally:
        con.close()
    assert v.check == "fk_is_child_key" and not o.verified
    assert "order_id" in v.detail and "likely foreign key" in v.detail


def test_sibling_hint_is_case_insensitive() -> None:
    o = Ontology()
    o.add_object("order", "orders", ["order_id"])
    o.add_object("line", "order_lines", ["line_id"])
    o.add_link("line_order", "line", ["line_id"], "order", ["ORDER_ID"])
    con = _con()
    try:
        [v] = o.verify(con)
    finally:
        con.close()
    assert v.check == "fk_is_child_key"
    assert "also has order_id" in v.detail


def test_one_to_one_off_the_child_key_is_rejected() -> None:
    o = Ontology()
    o.add_object("order", "orders", ["order_id"])
    o.add_object("line", "order_lines", ["line_id"])
    with pytest.raises(ValueError, match="one_to_one"):
        o.add_link("line_order", "line", ["order_id"], "order", ["order_id"], one_to_one=True)
    assert "line_order" not in o.links


def test_correct_fk_still_verifies() -> None:
    o = _ontology("order_id")
    con = _con()
    try:
        assert o.verify(con) == []
    finally:
        con.close()
    assert o.verified
    assert o.links["line_order"].cardinality == "many_to_one"


def test_explicit_one_to_one_link_still_verifies() -> None:
    o = Ontology()
    o.add_object("order", "orders", ["order_id"])
    o.add_object("invoice", "invoices", ["order_id"])
    o.add_link("invoice_order", "invoice", ["order_id"], "order", ["order_id"], one_to_one=True)
    con = _con()
    try:
        assert o.verify(con) == []
    finally:
        con.close()
    assert o.verified
    link = o.links["invoice_order"]
    assert link.cardinality == "many_to_one" and link.one_to_one is True


def test_one_to_one_is_opt_in_not_inferred() -> None:
    o = Ontology()
    o.add_object("order", "orders", ["order_id"])
    o.add_object("invoice", "invoices", ["order_id"])
    o.add_link("invoice_order", "invoice", ["order_id"], "order", ["order_id"])
    con = _con()
    try:
        [v] = o.verify(con)
    finally:
        con.close()
    assert v.check == "fk_is_child_key" and not o.verified
    # No sibling column exists beyond the key itself, so no hint is invented.
    assert "likely foreign key" not in v.detail


def test_from_manifest_passes_database_declared_shared_pk_as_one_to_one() -> None:
    entry = {
        "tables": [
            {"schema": "s", "table": "orders", "path": "x"},
            {"schema": "s", "table": "invoices", "path": "y"},
            {"schema": "s", "table": "order_lines", "path": "z"},
        ],
        "primary_keys": {
            "s.orders": ["order_id"],
            "s.invoices": ["order_id"],
            "s.order_lines": ["line_id"],
        },
        "foreign_keys": [
            {
                "name": "FK_inv",
                "from_table": "s.invoices",
                "from_column": "order_id",
                "to_table": "s.orders",
                "to_column": "order_id",
            },
            {
                "name": "FK_line",
                "from_table": "s.order_lines",
                "from_column": "order_id",
                "to_table": "s.orders",
                "to_column": "order_id",
            },
        ],
    }
    o = from_manifest(entry, relation_for=lambda _s, t: t)
    assert o.links["FK_inv"].one_to_one is True
    assert o.links["FK_line"].one_to_one is False
    con = _con()
    try:
        assert o.verify(con) == []
    finally:
        con.close()


def _submitter(path: Path) -> Any:
    def submit(sql: str) -> Any:
        con = duckdb.connect(str(path))
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_a203", output={"rows": rows})

    return submit


def _ask(tmp_path: Path, line_fk: str, line_key: str = "line_id") -> dict[str, Any]:
    lake = tmp_path / f"{line_fk}_{line_key}.duckdb"
    con = duckdb.connect(str(lake))
    try:
        for stmt in _SEED:
            con.execute(stmt)
    finally:
        con.close()
    env = maybe_generative_ask(
        "What is units by region?",
        warehouse=lake,
        grantable={"customers", "orders", "order_lines"},
        compute=lambda _ctx: {
            "query_plan": {"measure": "units", "group_by": [["customer", "region"]]}
        },
        submit=_submitter(lake),
        ledger_append=lambda _p: SimpleNamespace(entry_id="led_a203", hash="h"),
        ontology=_ontology(line_fk, line_key),
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


def test_envelope_abstains_instead_of_misattributing_lines(tmp_path: Path) -> None:
    """Before A2-03 this answered North=6, South=9: lines on the wrong orders (truth 12, 3)."""
    env = _ask(tmp_path, "line_id")
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "ontology_unverified" in env["text"]


def test_envelope_answers_with_the_correct_fk(tmp_path: Path) -> None:
    env = _ask(tmp_path, "order_id")
    assert env["abstained"] is False
    assert env["text"]
    got = {
        (
            next(v for v in r.values() if isinstance(v, str)),
            next(float(v) for v in r.values() if isinstance(v, (int, float))),
        )
        for r in env["rows"]
    }
    assert got == {("North", 12.0), ("South", 3.0)}


def test_envelope_abstains_when_key_case_differs_from_link(tmp_path: Path) -> None:
    """Verifier probe A1: key LINE_ID, link line_id shipped South=9, North=6 validated."""
    env = _ask(tmp_path, "line_id", line_key="LINE_ID")
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "ontology_unverified" in env["text"]
    assert "9.0" not in env["text"] and "6.0" not in env["text"]
