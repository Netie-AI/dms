"""FANOUT-GUARD-01 (dms#277 / dms#231): an aggregate over a join counts each row once.

Typed ingest made ``SUM(orders.amount)`` over ``orders JOIN items`` bind. The join
repeats an order once per item, so the ask came back L2_VALIDATED at twice the
orders total. Parent (79a03aa): every "ABSTAIN fan_out" case below is an
L2_VALIDATED envelope carrying the inflated figure.

The Space is a typed SQL source landed by ``ingest_source_database`` (the
psycopg-shaped fake from the typed-ingest suite): ``orders`` (order_id primary
key, amount), ``items`` (order_id foreign key, qty; order 1 has 3 items),
``customers`` (a truly unique dimension) and ``segments`` (a dimension whose key
is duplicated in the data, whatever its name suggests). The source declares no
keys: uniqueness is read from the data, never from a catalog claim.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor import db_connector as dbc
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import customer_abstain_text, customer_gap_label
from dms_executor.manifest import ManifestMinter
from dms_executor.sql_fanout import clear_fan_out_cache, fan_out_reason
from test_space_gen_01 import _ask, _reasons, _rig, minter  # noqa: F401

ORDERS_TOTAL = Decimal("200.00")
JOINED_TOTAL = Decimal("400.00")  # order 1 (100.00) counted three times
ITEMS_QTY = 15

#: name -> (psycopg cursor.description, rows)
TABLES: dict[str, tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]] = {
    "orders": (
        [
            ("order_id", 23, None, 4, None, None, None),
            ("customer_id", 23, None, 4, None, None, None),
            ("amount", 1700, None, None, 12, 2, None),
        ],
        [
            (1, 10, Decimal("100.00")),
            (2, 20, Decimal("60.00")),
            (3, 10, Decimal("40.00")),
        ],
    ),
    "items": (
        [
            ("order_id", 23, None, 4, None, None, None),
            ("qty", 23, None, 4, None, None, None),
        ],
        [(1, 1), (1, 2), (1, 3), (2, 4), (3, 5)],
    ),
    "customers": (
        [
            ("customer_id", 23, None, 4, None, None, None),
            ("region", 25, None, None, None, None, None),
        ],
        [(10, "North"), (20, "South")],
    ),
    "segments": (
        [
            ("customer_id", 23, None, 4, None, None, None),
            ("segment", 25, None, None, None, None, None),
        ],
        [(10, "Retail"), (10, "Wholesale"), (20, "Retail")],
    ),
    "products": (
        [
            ("product_id", 23, None, 4, None, None, None),
            ("price", 1700, None, None, 12, 2, None),
        ],
        [(1, Decimal("10.00")), (2, Decimal("5.00"))],
    ),
    "lines": (
        [
            ("order_id", 23, None, 4, None, None, None),
            ("product_id", 23, None, 4, None, None, None),
            ("qty", 23, None, 4, None, None, None),
        ],
        [(1, 1, 2), (1, 2, 1), (2, 1, 3)],
    ),
}
ORDERS = "bronze.public_orders"
ITEMS = "bronze.public_items"
CUSTOMERS = "bronze.public_customers"
SEGMENTS = "bronze.public_segments"
PRODUCTS = "bronze.public_products"
LINES = "bronze.public_lines"
LINE_REVENUE = Decimal("55.00")  # 2 x 10.00 + 1 x 5.00 + 3 x 10.00


class _Cursor:
    def __init__(self) -> None:
        self._rows: list[tuple[Any, ...]] = []
        self.description: list[tuple[Any, ...]] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self._rows = [("public", name) for name in sorted(TABLES)]
            return
        if "INFORMATION_SCHEMA" in sql:
            self._rows = []  # no declared keys: the guard must read the data
            return
        m = re.search(r'"public"\."(\w+)"', sql)
        assert m, sql
        desc, rows = TABLES[m.group(1)]
        if sql.startswith("SELECT COUNT(*)"):
            self._rows = [(len(rows),)]
            return
        self.description = list(desc)
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        out, self._rows = self._rows, []
        return out

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        return None


class _Conn:
    def cursor(self) -> _Cursor:
        return _Cursor()

    def close(self) -> None:
        return None


@contextmanager
def _source(monkeypatch: pytest.MonkeyPatch) -> Iterator[dbc.SourceConfig]:
    @contextmanager
    def _connect(cfg: dbc.SourceConfig) -> Iterator[Any]:
        yield _Conn()

    monkeypatch.setattr(dbc, "connect", _connect)
    yield dbc.SourceConfig(kind="postgresql", host="pg.example.net", database="shop", user="r")


def _space(tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, sql: str):  # noqa: F811
    clear_fan_out_cache()
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    with _source(monkeypatch) as cfg:
        extract = dbc.ingest_source_database(cfg, path=rig.lake, space_id=rig.space_id)
    landed = {p.bronze_table: p.column_types for p in extract.pulls}
    assert landed[ORDERS]["amount"] == "DECIMAL(12,2)", landed
    assert landed[ITEMS]["qty"] == "INTEGER", landed
    return rig


def _truth(lake: Path, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


# --- the customer envelope: fan-out abstains, with no figure --------------------------

FAN_OUT_CASES = [
    pytest.param(
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"JOIN {ITEMS} i ON o.order_id = i.order_id",
        f"fan_out:{ITEMS}.order_id",
        id="sum_over_join",
    ),
    pytest.param(
        f"WITH lines AS (SELECT o.amount FROM {ORDERS} o "
        f"JOIN {ITEMS} i ON o.order_id = i.order_id) "
        "SELECT SUM(amount) AS total_amount FROM lines",
        f"fan_out:{ITEMS}.order_id",
        id="sum_over_join_inside_cte",
    ),
    pytest.param(
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"JOIN {SEGMENTS} s ON o.customer_id = s.customer_id",
        f"fan_out:{SEGMENTS}.customer_id",
        id="dimension_key_duplicated_in_data",
    ),
    # Round-3 verifier bypass: padding an order-level measure with a line
    # column let the line side count as the "start" relation (L2 400 vs 200).
    pytest.param(
        f"SELECT SUM(o.amount + i.qty * 0) AS total_amount FROM {ORDERS} o "
        f"JOIN {ITEMS} i ON i.order_id = o.order_id",
        f"fan_out:{ITEMS}.order_id",
        id="one_side_measure_padded_with_many_side_column",
    ),
]


@pytest.mark.parametrize(("sql", "reason"), FAN_OUT_CASES)
def test_sum_over_a_join_that_repeats_orders_abstains_with_no_figure(
    tmp_path: Path,
    minter: ManifestMinter,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
    reason: str,
) -> None:
    rig = _space(tmp_path, minter, monkeypatch, sql)
    # The inflated figure the parent certified at L2 is really what this SQL returns.
    [(inflated,)] = _truth(rig.lake, sql)
    assert Decimal(str(inflated)) > ORDERS_TOTAL

    env = _ask(rig, "What is the total order amount?")

    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("rows"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    text = str(env.get("text") or "")
    assert f"gap: {reason}" in text, text
    assert "more than once" in text
    for figure in (str(inflated), str(JOINED_TOTAL), "400", str(ORDERS_TOTAL)):
        assert figure not in text, (figure, text)
    assert reason in _reasons(env)
    # Refused before submit: nothing executed, nothing appended to the ledger.
    assert rig.cortex.executed == []


def test_count_star_over_a_join_counts_the_from_relation_and_abstains(
    tmp_path: Path,
    minter: ManifestMinter,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = (
        f"SELECT COUNT(*) AS order_count FROM {ORDERS} o "
        f"JOIN {ITEMS} i ON o.order_id = i.order_id"
    )
    rig = _space(tmp_path, minter, monkeypatch, sql)
    env = _ask(rig, "How many orders are there?")
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("rows"))
    assert env["rows"] == [] and env["values"] == []
    text = str(env.get("text") or "")
    assert f"gap: fan_out:{ITEMS}.order_id" in text, text
    assert "order_count=5" not in text
    assert rig.cortex.executed == []


#: A derived table the join reads as unique, in a shape that is not provably so.
#: The guard names each ``derived_shape``. Parent (5e48639): the first two were
#: L2_VALIDATED with the inflated figure shown (400.00; 3 customers for 2); the
#: grain gate, which runs first, already abstains on the other three, so their
#: envelope gap is the grain one and the guard's own verdict is asserted too.
_SHAPE = "fan_out_unanalysable:derived_shape"
DERIVED_SHAPE_CASES = [
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"JOIN (SELECT customer_id, unnest([1, 2]) AS u FROM {CUSTOMERS}) c "
        "ON c.customer_id = o.customer_id",
        ORDERS_TOTAL,
        _SHAPE,
        id="unnest_repeats_a_unique_dimension",
    ),
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"JOIN (SELECT order_id, unnest([1, 2]) AS u FROM {ITEMS} GROUP BY order_id) t "
        "ON t.order_id = o.order_id",
        ORDERS_TOTAL,
        "grain_unanalysable:nested_grouping",
        id="unnest_repeats_a_grouped_side",
    ),
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"CROSS JOIN (SELECT SUM(qty) AS s, unnest([1, 2]) AS u FROM {ITEMS}) t",
        ORDERS_TOTAL,
        "grain_unanalysable:nested_grouping",
        id="unnest_repeats_a_one_row_aggregate",
    ),
    pytest.param(
        "How many customers are there?",
        f"SELECT COUNT(*) AS customer_count FROM {CUSTOMERS} c "
        f"JOIN (SELECT customer_id AS order_id FROM {ORDERS} "
        "GROUP BY order_id, customer_id) t ON t.order_id = c.customer_id",
        2,
        _SHAPE,
        id="group_by_binds_the_input_not_the_shadowing_alias",
    ),
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"CROSS JOIN (SELECT SUM(qty) OVER () AS s FROM {ITEMS}) w",
        ORDERS_TOTAL,
        "grain_unanalysable:window_function",
        id="window_aggregate_is_not_one_row",
    ),
]


@pytest.mark.parametrize(("question", "sql", "oracle", "gap"), DERIVED_SHAPE_CASES)
def test_derived_side_not_provably_unique_abstains_with_no_figure(
    tmp_path: Path,
    minter: ManifestMinter,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    sql: str,
    oracle: Any,
    gap: str,
) -> None:
    rig = _space(tmp_path, minter, monkeypatch, sql)
    # The SQL really repeats rows: its figure is not the oracle.
    [(wrong,)] = _truth(rig.lake, sql)
    assert Decimal(str(wrong)) != Decimal(str(oracle))
    # The guard's own verdict on the landed Space, whichever gate speaks first.
    assert fan_out_reason(sql, rig.lake) == _SHAPE

    env = _ask(rig, question)
    assert_envelope_valid(env)

    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("rows"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    text = str(env.get("text") or "")
    assert f"gap: {gap}" in text, text
    assert "I am not showing" in text
    assert str(wrong) not in text, (wrong, text)
    assert gap in _reasons(env)
    assert rig.cortex.executed == []


# --- the customer envelope: safe joins still answer at L2 with the oracle ---------------

L2_CASES = [
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"JOIN {CUSTOMERS} c ON o.customer_id = c.customer_id",
        "total_amount",
        ORDERS_TOTAL,
        id="unique_dimension",
    ),
    pytest.param(
        "What is the total item quantity?",
        f"SELECT SUM(i.qty) AS total_qty FROM {ITEMS} i "
        f"JOIN {ORDERS} o ON o.order_id = i.order_id",
        "total_qty",
        ITEMS_QTY,
        id="many_to_one_items_to_orders",
    ),
    pytest.param(
        "What is the total item quantity?",
        f"SELECT SUM(i.qty) AS total_qty FROM {ORDERS} o "
        f"JOIN {ITEMS} i ON o.order_id = i.order_id",
        "total_qty",
        ITEMS_QTY,
        id="many_to_one_written_orders_first",
    ),
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {ORDERS} o "
        f"JOIN (SELECT order_id FROM {ITEMS} GROUP BY order_id) i "
        "ON o.order_id = i.order_id",
        "total_amount",
        ORDERS_TOTAL,
        id="pre_aggregated_items",
    ),
    pytest.param(
        "How many orders are there?",
        f"SELECT COUNT(DISTINCT o.order_id) AS order_count FROM {ORDERS} o "
        f"JOIN {ITEMS} i ON o.order_id = i.order_id",
        "order_count",
        3,
        id="count_distinct_over_join",
    ),
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(amount) AS total_amount FROM {ORDERS}",
        "total_amount",
        ORDERS_TOTAL,
        id="single_table",
    ),
    # Parent (5e48639): fan_out_unanalysable:non_equi_join. Unmatched customers
    # carry only NULL amounts, which SUM skips; each order meets one customer.
    pytest.param(
        "What is the total order amount?",
        f"SELECT SUM(o.amount) AS total_amount FROM {CUSTOMERS} c "
        f"LEFT JOIN {ORDERS} o ON o.customer_id = c.customer_id",
        "total_amount",
        ORDERS_TOTAL,
        id="dimension_left_join_fact",
    ),
]


@pytest.mark.parametrize(("question", "sql", "column", "oracle"), L2_CASES)
def test_join_that_keeps_each_row_once_answers_at_l2_with_the_oracle(
    tmp_path: Path,
    minter: ManifestMinter,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    sql: str,
    column: str,
    oracle: Any,
) -> None:
    rig = _space(tmp_path, minter, monkeypatch, sql)
    [(truth,)] = _truth(rig.lake, sql)
    assert Decimal(str(truth)) == Decimal(str(oracle))

    env = _ask(rig, question)
    assert_envelope_valid(env)

    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert len(env["rows"]) == 1
    assert Decimal(str(env["rows"][0][column])) == Decimal(str(oracle))
    text = str(env.get("text") or "")
    shown = re.search(rf"\b{column}=(-?[0-9.]+)", text)
    assert shown is not None, text
    assert Decimal(shown.group(1)) == Decimal(str(oracle)), text
    assert rig.cortex.executed == [sql]


# --- the analysis itself -------------------------------------------------------------


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    clear_fan_out_cache()
    db = tmp_path / "fan.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE SCHEMA bronze")
        con.execute(
            "CREATE TABLE bronze.orders AS SELECT * FROM (VALUES (1, 10, 100.00), "
            "(2, 20, 60.00), (3, 10, 40.00)) t(order_id, customer_id, amount)"
        )
        con.execute(
            "CREATE TABLE bronze.items AS SELECT * FROM (VALUES (1, 1), (1, 2), (1, 3), "
            "(2, 4), (3, 5)) t(order_id, qty)"
        )
        con.execute(
            "CREATE TABLE bronze.customers AS SELECT * FROM (VALUES (10, 'N'), (20, 'S'))"
            " t(customer_id, region)"
        )
        con.execute(
            "CREATE TABLE bronze.nullkey AS SELECT * FROM (VALUES (10, 'N'), (NULL, 'S'), "
            "(NULL, 'W'), (20, 'E')) t(customer_id, region)"
        )
        con.execute(
            "CREATE TABLE bronze.nulldup AS SELECT * FROM (VALUES (10, 'N'), (NULL, 'S'), "
            "(10, 'W')) t(customer_id, region)"
        )
        # Unique as text; DuckDB casts VARCHAR to INTEGER against an INTEGER key,
        # so '10', '010' and ' 10' all match customer 10.
        con.execute(
            "CREATE TABLE bronze.products AS SELECT * FROM (VALUES (1, 10.00), (2, 5.00)) "
            "t(product_id, price)"
        )
        con.execute(
            "CREATE TABLE bronze.lines AS SELECT * FROM (VALUES (1, 1, 2), (1, 2, 1), "
            "(2, 1, 3)) t(order_id, product_id, qty)"
        )
        con.execute(
            "CREATE TABLE bronze.custv AS SELECT * FROM (VALUES ('10', 'N'), ('010', 'N2'), "
            "(' 10', 'N3'), ('20', 'S')) t(customer_id, region)"
        )
    finally:
        con.close()
    return db


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        # Fan-out: named by the relation and key that repeat.
        ("SELECT SUM(amount) FROM bronze.orders o, bronze.items i "
         "WHERE o.order_id = i.order_id", "fan_out:bronze.items.order_id"),
        ("SELECT SUM(amount) FROM bronze.orders JOIN bronze.items USING (order_id)",
         "fan_out:bronze.items.order_id"),
        ("SELECT AVG(o.amount) FROM bronze.orders o LEFT JOIN bronze.items i "
         "ON o.order_id = i.order_id", "fan_out:bronze.items.order_id"),
        ("SELECT SUM(x.amount) FROM (SELECT * FROM bronze.orders o "
         "JOIN bronze.items i USING (order_id)) x", "fan_out:bronze.items.order_id"),
        ("SELECT COUNT(o.amount) FROM bronze.orders o JOIN bronze.items i "
         "ON o.order_id = i.order_id", "fan_out:bronze.items.order_id"),
        # A NULL key never matches, so it cannot repeat a row; a repeated one can.
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN bronze.nullkey n "
         "ON n.customer_id = o.customer_id", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN bronze.nulldup n "
         "ON o.customer_id = n.customer_id", "fan_out:bronze.nulldup.customer_id"),
        # A key compared through a cast is not the key the data check proved.
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN bronze.custv c "
         "ON o.customer_id = c.customer_id", "fan_out_unanalysable:key_type"),
        ("SELECT SUM(o.amount) FROM bronze.orders o, bronze.custv c "
         "WHERE c.customer_id = o.customer_id", "fan_out_unanalysable:key_type"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN bronze.custv c "
         "USING (customer_id)", "fan_out_unanalysable:key_type"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT customer_id FROM "
         "bronze.custv GROUP BY customer_id) c ON o.customer_id = c.customer_id",
         "fan_out_unanalysable:key_type"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN bronze.custv c "
         "ON c.customer_id = 10", "fan_out_unanalysable:key_type"),
        # A grouped wrapper finer than the summed column's rows repeats them.
        ("WITH j AS (SELECT i.qty, o.order_id, o.amount FROM bronze.orders o "
         "JOIN bronze.items i ON o.order_id = i.order_id "
         "GROUP BY i.qty, o.order_id, o.amount) SELECT SUM(amount) FROM j",
         "fan_out_unanalysable:grouped_passthrough"),
        ("SELECT SUM(amount) FROM (SELECT DISTINCT i.qty, o.amount FROM bronze.orders o "
         "JOIN bronze.items i ON o.order_id = i.order_id) j",
         "fan_out_unanalysable:grouped_passthrough"),
        # A LEFT JOIN's ON equality keys only its own relation, never a cross join.
        ("SELECT SUM(o.amount) FROM bronze.orders o CROSS JOIN bronze.customers c2 "
         "LEFT JOIN bronze.customers c3 ON c2.customer_id = o.customer_id "
         "AND c3.customer_id = 10", "fan_out_unanalysable:non_equi_join"),
        # Joins the analysis cannot bound fail closed, by name.
        ("SELECT SUM(o.amount) FROM bronze.orders o CROSS JOIN bronze.customers c",
         "fan_out_unanalysable:non_equi_join"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN bronze.customers c "
         "ON o.customer_id < c.customer_id", "fan_out_unanalysable:non_equi_join"),
        ("SELECT SUM(o.amount) FROM bronze.orders o RIGHT JOIN bronze.customers c "
         "ON o.customer_id = c.customer_id", "fan_out_unanalysable:outer_join"),
        ("SELECT SUM(o.amount) FROM bronze.orders o NATURAL JOIN bronze.customers c",
         "fan_out_unanalysable:join_method"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id, qty "
         "FROM bronze.items GROUP BY order_id, qty) i ON o.order_id = i.order_id",
         "fan_out_unanalysable:derived_key"),
        # A derived side is unique only in an allowlisted shape; else derived_shape.
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT customer_id, "
         "unnest([1, 2]) AS u FROM bronze.customers) c ON c.customer_id = o.customer_id",
         "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id, "
         "unnest([1, 2]) AS u FROM bronze.items GROUP BY order_id) t "
         "ON t.order_id = o.order_id", "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o CROSS JOIN (SELECT SUM(qty) AS s, "
         "unnest([1, 2]) AS u FROM bronze.items) t", "fan_out_unanalysable:derived_shape"),
        ("SELECT COUNT(*) FROM bronze.customers c JOIN (SELECT customer_id AS order_id "
         "FROM bronze.orders GROUP BY order_id, customer_id) t "
         "ON t.order_id = c.customer_id", "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT customer_id AS order_id "
         "FROM bronze.orders GROUP BY customer_id) t ON t.order_id = o.order_id",
         "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT customer_id, "
         "MIN(region) AS customer_id FROM bronze.customers GROUP BY customer_id) c "
         "ON c.customer_id = o.customer_id", "fan_out_unanalysable:derived_shape"),
        # The ontology compiler's dimension: an aggregate aliased like its input,
        # neither grouped on nor joined on, keeps one row per key.
        ("SELECT SUM(o.amount) FROM bronze.orders o LEFT JOIN (SELECT customer_id, "
         "ANY_VALUE(region) AS region FROM bronze.customers GROUP BY customer_id) c "
         "ON o.customer_id = c.customer_id", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o CROSS JOIN (SELECT SUM(qty) OVER () "
         "AS s FROM bronze.items) w", "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id, SUM(qty) OVER "
         "(PARTITION BY order_id) AS q FROM bronze.items) w ON w.order_id = o.order_id",
         "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT c.customer_id FROM "
         "bronze.customers c JOIN bronze.customers c2 ON c.customer_id = c2.customer_id) c "
         "ON c.customer_id = o.customer_id", "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id FROM "
         "bronze.items GROUP BY ROLLUP (order_id)) i ON i.order_id = o.order_id",
         "fan_out_unanalysable:derived_shape"),
        ("SELECT SUM(amount) FROM (SELECT amount, unnest([1, 2]) AS u "
         "FROM bronze.orders) x", "fan_out_unanalysable:derived_shape"),
        # A window aggregate does not collapse a wrapper: COUNT(*) sees the join.
        ("SELECT COUNT(*) FROM (SELECT o.order_id, SUM(i.qty) OVER () AS s "
         "FROM bronze.orders o JOIN bronze.items i ON o.order_id = i.order_id) x",
         "fan_out:bronze.items.order_id"),
        # Dimension LEFT JOIN fact: the ON keys the dimension from the fact only
        # for an aggregate that skips the NULL rows of unmatched customers.
        ("SELECT SUM(o.amount) FROM bronze.customers c LEFT JOIN bronze.orders o "
         "ON o.customer_id = c.customer_id", None),
        ("SELECT COUNT(*) FROM bronze.customers c LEFT JOIN bronze.orders o "
         "ON o.customer_id = c.customer_id", "fan_out:bronze.orders.customer_id"),
        ("SELECT COUNT(COALESCE(o.amount, 0)) FROM bronze.customers c "
         "LEFT JOIN bronze.orders o ON o.customer_id = c.customer_id",
         "fan_out_unanalysable:non_equi_join"),
        # Every relation the argument reads must be un-repeated on its own, so
        # line revenue over a product dimension is a named over-refusal.
        ("SELECT SUM(l.qty * p.price) FROM bronze.lines l JOIN bronze.products p "
         "ON p.product_id = l.product_id", "fan_out:bronze.lines.product_id"),
        ("SELECT SUM(o.amount + i.qty) FROM bronze.orders o JOIN bronze.items i "
         "ON i.order_id = o.order_id", "fan_out:bronze.items.order_id"),
        ("SELECT SUM(c.customer_id) FROM bronze.orders o JOIN bronze.customers c "
         "ON c.customer_id = o.customer_id", "fan_out:bronze.orders.customer_id"),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id, SUM(qty) AS q "
         "FROM bronze.items GROUP BY 1) i ON o.order_id = i.order_id", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT customer_id, region "
         "FROM bronze.customers) c ON c.customer_id = o.customer_id", None),
        # Safe: many-to-one, pre-aggregated, DISTINCT, one-row side, semi-join,
        # insensitive aggregates, a single table.
        ("SELECT SUM(i.qty) FROM bronze.orders o JOIN bronze.items i "
         "ON o.order_id = i.order_id JOIN bronze.customers c "
         "ON c.customer_id = o.customer_id", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id, SUM(qty) AS q "
         "FROM bronze.items GROUP BY order_id) i ON o.order_id = i.order_id", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT DISTINCT order_id "
         "FROM bronze.items) i ON o.order_id = i.order_id", None),
        # DISTINCT dimension joined on part of its outputs: the data decides.
        ("SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT DISTINCT customer_id, "
         "region FROM bronze.customers) c ON o.customer_id = c.customer_id", None),
        ("SELECT SUM(i.qty) FROM bronze.items i JOIN (SELECT DISTINCT order_id, qty "
         "FROM bronze.items) d ON i.order_id = d.order_id", "fan_out:bronze.items.order_id"),
        ("SELECT SUM(o.amount) / MAX(t.n) FROM bronze.orders o "
         "CROSS JOIN (SELECT COUNT(*) AS n FROM bronze.items) t", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o SEMI JOIN bronze.items i "
         "ON o.order_id = i.order_id", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o "
         "WHERE o.order_id IN (SELECT order_id FROM bronze.items)", None),
        ("SELECT MAX(o.amount), MIN(o.amount) FROM bronze.orders o "
         "JOIN bronze.items i ON o.order_id = i.order_id", None),
        ("SELECT SUM(amount) FROM bronze.orders", None),
        ("SELECT SUM(o.amount) FROM bronze.orders o LEFT JOIN bronze.customers c "
         "ON c.customer_id = o.customer_id", None),
        ("SELECT SUM(amount) FROM (SELECT order_id, amount FROM bronze.orders "
         "GROUP BY order_id, amount) g", None),
        ("SELECT COUNT(*) FROM (SELECT o.customer_id FROM bronze.orders o "
         "JOIN bronze.items i ON o.order_id = i.order_id GROUP BY o.customer_id) g", None),
        ("SELECT SUM(g.q) FROM (SELECT o.customer_id, SUM(i.qty) AS q FROM bronze.orders o "
         "JOIN bronze.items i ON o.order_id = i.order_id GROUP BY o.customer_id) g", None),
    ],
)
def test_fan_out_reason_on_shapes(lake: Path, sql: str, reason: str | None) -> None:
    assert fan_out_reason(sql, lake) == reason


def test_without_a_warehouse_a_join_is_unproven_and_a_single_table_is_not(tmp_path: Path) -> None:
    join = (
        "SELECT SUM(o.amount) FROM bronze.orders o "
        "JOIN bronze.customers c ON o.customer_id = c.customer_id"
    )
    assert fan_out_reason(join, None) == "fan_out_unanalysable:no_warehouse"
    assert fan_out_reason(join, tmp_path / "missing.duckdb") == "fan_out_unanalysable:no_warehouse"
    assert fan_out_reason("SELECT SUM(amount) FROM bronze.orders", None) is None
    # Even a pre-aggregated side needs the warehouse: the key types decide
    # whether the equality compares the grouped key or a cast of it.
    assert fan_out_reason(
        "SELECT SUM(o.amount) FROM bronze.orders o JOIN (SELECT order_id FROM bronze.items "
        "GROUP BY order_id) i ON o.order_id = i.order_id",
        None,
    ) == "fan_out_unanalysable:no_warehouse"


def test_the_data_check_follows_the_data_not_a_stale_cache(lake: Path) -> None:
    sql = (
        "SELECT SUM(o.amount) FROM bronze.orders o "
        "JOIN bronze.customers c ON o.customer_id = c.customer_id"
    )
    assert fan_out_reason(sql, lake) is None
    con = duckdb.connect(str(lake))
    try:
        con.execute("INSERT INTO bronze.customers VALUES (10, 'X')")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    assert fan_out_reason(sql, lake) == "fan_out:bronze.customers.customer_id"


def test_customer_text_names_the_gap_and_hides_nothing_unsafe() -> None:
    for reason, shown in (
        ("fan_out:bronze.items.order_id", "fan_out:bronze.items.order_id"),
        ("fan_out_unanalysable:outer_join", "fan_out_unanalysable:outer_join"),
        ("fan_out_unanalysable:some model text", "fan_out_unanalysable"),
    ):
        text = customer_abstain_text(reason)
        assert f"gap: {shown}" in text, (reason, text)
        assert customer_gap_label(reason) == shown
    assert "model text" not in customer_abstain_text("fan_out_unanalysable:some model text")
