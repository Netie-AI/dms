"""FEAT-METRIC-CUBE-01: governed metric cubes.

The model names a cube plan. The compiler writes the SQL, and a TO_MANY
child is aggregated on the join key before it is joined. Order-grain revenue
is the proof: a naive join of order_items repeats the order amount, and a
sum of line qty would not, because that sum is associative.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.cube_store import cube_store, reset_cube_store
from dms_executor.cube.compile import REASON_UNKNOWN, CubePlanError, compile_plan
from dms_executor.cube.model import (
    Cube,
    CubeDimension,
    CubeJoin,
    CubeMeasure,
    CubePlan,
    CubeTimeDimension,
)
from dms_executor.cube.offer import accept_metric_cube, attach_catalog, catalog_text
from dms_executor.cube.propose import draft_cubes
from dms_executor.envelope import assert_envelope_valid
from dms_executor.route_stamp import route_of

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts" / "verify"))
import grade52  # noqa: E402


def _sales() -> Cube:
    return Cube(
        name="sales",
        base_table="orders",
        status="approved",
        measures=[
            CubeMeasure(name="revenue", expr="amount", table="orders", agg="sum"),
            CubeMeasure(name="units", expr="qty", table="order_items", agg="sum"),
        ],
        dimensions=[
            CubeDimension(name="region", expr="region", table="customers"),
            CubeDimension(name="sku", expr="sku", table="order_items"),
        ],
        time_dimensions=[
            CubeTimeDimension(name="ordered_at", expr="ordered_at", table="orders"),
        ],
        joins=[
            CubeJoin(
                from_table="orders",
                from_columns=["customer_id"],
                to_table="customers",
                to_columns=["id"],
                cardinality="many_to_one",
            ),
            CubeJoin(
                from_table="orders",
                from_columns=["id"],
                to_table="order_items",
                to_columns=["order_id"],
                cardinality="TO_MANY",
            ),
        ],
    )


def _seed(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE customers (id INTEGER, region VARCHAR)")
    con.execute(
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER, amount DOUBLE, ordered_at TIMESTAMP)"
    )
    con.execute("CREATE TABLE order_items (order_id INTEGER, qty INTEGER, sku VARCHAR)")
    con.execute("INSERT INTO customers VALUES (1, 'East'), (2, 'West')")
    con.execute(
        "INSERT INTO orders VALUES "
        "(10, 1, 100, TIMESTAMP '2024-01-15'), "
        "(11, 1, 40, TIMESTAMP '2024-01-20'), "
        "(12, 2, 50, TIMESTAMP '2024-04-01')"
    )
    con.execute(
        "INSERT INTO order_items VALUES "
        "(10, 1, 'A'), (10, 2, 'B'), (11, 1, 'A'), "
        "(12, 1, 'A'), (12, 1, 'B'), (12, 1, 'C')"
    )


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    return list(con.execute(sql).fetchall())


def test_fanout_guard_matches_the_correct_query_and_not_the_naive_join() -> None:
    cube = _sales()
    revenue = compile_plan(
        CubePlan(
            cube="sales",
            measures=["revenue"],
            dimensions=["region"],
            order=[{"member": "region", "direction": "asc"}],
        ),
        cube,
        dialect="duckdb",
    )
    assert "order_items" not in revenue.lower()
    both = compile_plan(
        CubePlan(
            cube="sales",
            measures=["revenue", "units"],
            dimensions=["region"],
            order=[{"member": "region", "direction": "asc"}],
        ),
        cube,
        dialect="duckdb",
    )
    lowered = both.lower()
    assert "order_items" in lowered
    assert "group by" in lowered

    con = duckdb.connect()
    _seed(con)
    correct = _rows(
        con,
        "SELECT customers.region, SUM(orders.amount) "
        "FROM orders JOIN customers ON orders.customer_id = customers.id "
        "GROUP BY customers.region ORDER BY customers.region",
    )
    naive = _rows(
        con,
        "SELECT customers.region, SUM(orders.amount) "
        "FROM orders JOIN customers ON orders.customer_id = customers.id "
        "JOIN order_items ON orders.id = order_items.order_id "
        "GROUP BY customers.region ORDER BY customers.region",
    )
    assert correct == [("East", 140.0), ("West", 50.0)]
    assert naive == [("East", 240.0), ("West", 150.0)]
    assert _rows(con, revenue) == correct
    assert [(row[0], row[1]) for row in _rows(con, both)] == correct
    assert [row[2] for row in _rows(con, both)] == [4.0, 3.0]
    con.close()


def test_time_grains_compile_for_every_serving_dialect() -> None:
    cube = _sales()
    markers = {
        "month": ("DATE_TRUNC('MONTH'", "MONTH("),
        "quarter": ("DATE_TRUNC('QUARTER'", "QUARTER("),
        "year": ("DATE_TRUNC('YEAR'", "YEAR("),
    }
    for dialect in ("postgres", "mysql", "tsql", "sqlite", "duckdb"):
        for grain, needles in markers.items():
            sql = compile_plan(
                CubePlan(
                    cube="sales",
                    measures=["revenue"],
                    time_dimension={"name": "ordered_at", "grain": grain},
                ),
                cube,
                dialect=dialect,
            )
            parsed = __import__("sqlglot").parse_one(sql, read=dialect)
            assert parsed is not None
            upper = sql.upper()
            assert any(needle in upper for needle in needles), (dialect, grain, sql)

    con = duckdb.connect()
    _seed(con)
    month = compile_plan(
        CubePlan(
            cube="sales",
            measures=["revenue"],
            time_dimension={"name": "ordered_at", "grain": "month"},
            order=[{"member": "ordered_at", "direction": "asc"}],
        ),
        cube,
        dialect="duckdb",
    )
    found = [(str(row[0])[:10], row[1]) for row in _rows(con, month)]
    assert found == [("2024-01-01", 140.0), ("2024-04-01", 50.0)]
    con.close()


def test_unknown_member_is_not_served() -> None:
    cube = _sales()
    with pytest.raises(CubePlanError) as info:
        compile_plan(
            CubePlan(cube="sales", measures=["no_such_measure"], dimensions=["region"]),
            cube,
            dialect="duckdb",
        )
    assert info.value.reason == REASON_UNKNOWN

    called: list[str] = []

    def serve_sql(sql: str) -> dict[str, Any]:
        called.append(sql)
        raise AssertionError("unknown member must not be served")

    payload = {"cube_plan": {"cube": "sales", "measures": ["no_such_measure"]}}
    assert (
        accept_metric_cube(
            payload,
            cubes=[cube],
            serve_sql=serve_sql,
            asked="revenue by region",
            space_id="s",
            session_id="sess",
            dialect="duckdb",
        )
        is None
    )
    assert called == []
    assert payload["cube_reject"] == REASON_UNKNOWN

    kept = {"query_sql": "SELECT 1", "cube_plan": {"cube": "missing", "measures": ["revenue"]}}
    assert (
        accept_metric_cube(
            kept,
            cubes=[cube],
            serve_sql=serve_sql,
            asked="revenue by region",
            space_id="s",
            session_id="sess",
            dialect="duckdb",
        )
        is None
    )
    assert kept["query_sql"] == "SELECT 1"

    cleared = {"query_sql": json.dumps({"cube": "sales", "measures": ["missing"]})}
    assert (
        accept_metric_cube(
            cleared,
            cubes=[cube],
            serve_sql=serve_sql,
            asked="revenue by region",
            space_id="s",
            session_id="sess",
            dialect="duckdb",
        )
        is None
    )
    assert cleared["query_sql"] == ""
    assert cleared["cube_reject"] == REASON_UNKNOWN


def test_parent_measure_by_child_dimension_is_a_reconfirm() -> None:
    called: list[str] = []
    env = accept_metric_cube(
        {"cube_plan": {"cube": "sales", "measures": ["revenue"], "dimensions": ["sku"]}},
        cubes=[_sales()],
        serve_sql=lambda sql: called.append(sql) or {},
        asked="revenue by sku",
        space_id="s",
        session_id="sess",
        dialect="duckdb",
    )
    assert called == []
    assert isinstance(env, dict)
    assert env.get("rows") == []
    assert not env.get("sql_used")
    block = env.get("reconfirm")
    assert isinstance(block, dict)
    assert block.get("reason_code") == "fanout_grain"


def test_model_clarify_is_returned_and_a_bad_count_is_not_guessed() -> None:
    cube = _sales()
    called: list[str] = []

    def serve_sql(sql: str) -> dict[str, Any]:
        called.append(sql)
        raise AssertionError("a clarify is not executed")

    env = accept_metric_cube(
        {
            "cube_plan": {
                "clarify": True,
                "readings": [
                    {"id": "a", "text": "Revenue by region"},
                    {"id": "b", "text": "Units by region"},
                ],
            }
        },
        cubes=[cube],
        serve_sql=serve_sql,
        asked="how did sales do",
        space_id="s",
        session_id="sess",
        dialect="duckdb",
    )
    assert called == []
    assert isinstance(env, dict)
    assert env.get("route") == "needs_clarification"
    assert not env.get("sql_used")
    assert_envelope_valid(env)
    block = env.get("clarify")
    assert isinstance(block, dict)
    readings = block.get("readings")
    assert isinstance(readings, list) and len(readings) == 2

    for count in (1, 5):
        payload = {
            "cube_plan": {
                "clarify": True,
                "readings": [{"id": str(i), "text": f"reading {i}"} for i in range(count)],
            }
        }
        assert (
            accept_metric_cube(
                payload,
                cubes=[cube],
                serve_sql=serve_sql,
                asked="how did sales do",
                space_id="s",
                session_id="sess",
                dialect="duckdb",
            )
            is None
        )
        assert payload["cube_reject"] == "cube_plan_incomplete"


def test_shape_misfit_becomes_a_reconfirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SHAPE_CHECK", "1")

    def misfit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        _ = args, kwargs
        return {"verdict": "misfit", "reasons": ["grain"]}

    monkeypatch.setattr("dms_executor.shape_check.check_answer_shape", misfit)
    served = {
        "abstained": False,
        "badge": "VERIFIED",
        "rows": [{"region": "East", "revenue": 1}],
        "sql_used": "SELECT 1",
        "route": "generated",
    }
    env = accept_metric_cube(
        {"cube_plan": {"cube": "sales", "measures": ["revenue"], "dimensions": ["region"]}},
        cubes=[_sales()],
        serve_sql=lambda sql: dict(served),
        asked="revenue by region",
        space_id="s",
        session_id="sess",
        dialect="duckdb",
    )
    assert isinstance(env, dict)
    assert env.get("rows") == []
    assert not env.get("sql_used")
    block = env.get("reconfirm")
    assert isinstance(block, dict)
    assert block.get("reason_code") == "shape_misfit"


def test_ai_cube_plan_counts_as_ai_without_a_model_id() -> None:
    env = {
        "served_route": "ai_cube_plan",
        "ladder_rung": "ai_cube_plan",
        "sql_used": "SELECT SUM(orders.amount) AS revenue FROM orders",
        "badge": "VERIFIED",
        "abstained": False,
        "rows": [{"revenue": 1}],
        "plan_origin": "ai_cube_plan",
    }
    assert route_of(env) == "ai_cube_plan"
    assert grade52.served_route(env, model_sql="SELECT 1") == "ai"
    abstained = {**env, "abstained": True, "badge": "ABSTAIN", "route": "abstain"}
    assert grade52.served_route(abstained) == "abstain"


def test_flag_off_catalog_is_the_same_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_METRIC_CUBE", raising=False)
    ctx = {"governed_definitions": "keep"}
    assert attach_catalog(ctx, [_sales()]) is ctx
    monkeypatch.setenv("DMS_METRIC_CUBE", "0")
    assert attach_catalog(ctx, [_sales()]) is ctx
    monkeypatch.setenv("DMS_METRIC_CUBE", "true")
    assert attach_catalog(ctx, [_sales()]) is ctx
    monkeypatch.setenv("DMS_METRIC_CUBE", "1")
    armed = attach_catalog(ctx, [_sales()])
    assert armed is not ctx
    assert "Approved metric cubes" in armed["governed_definitions"]
    assert "sales" in armed["governed_definitions"]
    assert ctx["governed_definitions"] == "keep"
    assert "Role: reason" in armed["governed_definitions"]
    assert "gpt" not in armed["governed_definitions"].lower()


def test_proposal_is_not_served_until_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DMS_METRIC_CUBE", raising=False)
    reset_cube_store()
    store = cube_store()
    row = store.store_proposal(
        _sales().model_dump(),
        space_id="space-a",
        created_by="steward",
    )
    assert row["status"] == "proposed"
    assert row["served"] is False
    assert catalog_text([]) == ""
    from dms_executor.cube.offer import load_cubes

    assert load_cubes("space-a") == []
    approved = store.approve(row["id"], actor="steward")
    assert approved is not None and approved["served"] is True
    loaded = load_cubes("space-a")
    assert [cube.name for cube in loaded] == ["sales"]
    assert load_cubes("space-b") == []
    text = catalog_text(loaded)
    assert "sales" in text
    reset_cube_store()


def test_drafts_use_relationships_and_flip_many_to_one() -> None:
    items = [
        {
            "kind": "object",
            "status": "approved",
            "payload": {
                "name": "orders",
                "relation": "orders",
                "attributes": [{"name": "ordered_at", "type": "timestamp"}],
            },
        },
        {
            "kind": "object",
            "status": "approved",
            "payload": {
                "name": "customers",
                "relation": "customers",
                "attributes": [{"name": "region", "type": "text"}],
            },
        },
        {
            "kind": "object",
            "status": "approved",
            "payload": {"name": "order_items", "relation": "order_items"},
        },
        {
            "kind": "measure",
            "status": "approved",
            "payload": {
                "name": "revenue",
                "expression": "SUM(orders.amount)",
                "grain": "orders",
            },
        },
        {
            "kind": "measure",
            "status": "draft",
            "payload": {
                "name": "hidden",
                "expression": "SUM(orders.tax)",
                "grain": "orders",
            },
        },
        {
            "kind": "link",
            "status": "approved",
            "payload": {
                "from_object": "orders",
                "to_object": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
                "cardinality": "many_to_one",
            },
        },
        {
            "kind": "link",
            "status": "approved",
            "payload": {
                "from_object": "order_items",
                "to_object": "orders",
                "from_columns": ["order_id"],
                "to_columns": ["id"],
                "cardinality": "many_to_one",
            },
        },
        {
            "kind": "link",
            "status": "approved",
            "payload": {
                "from_object": "orders",
                "to_object": "order_items",
                "from_columns": ["id"],
                "to_columns": ["order_id"],
                "cardinality": "unverified",
            },
        },
    ]
    drafts = draft_cubes(items)
    by_base = {cube.base_table: cube for cube in drafts}
    assert set(by_base) == {"orders"}
    orders = by_base["orders"]
    assert orders.status == "proposed"
    assert [item.name for item in orders.measures] == ["revenue"]
    cards = {(join.to_table, join.cardinality) for join in orders.joins}
    assert ("customers", "many_to_one") in cards
    assert ("order_items", "TO_MANY") in cards
    assert all(join.cardinality != "unverified" for join in orders.joins)
    assert any(item.name == "region" for item in orders.dimensions)

    forced = draft_cubes(
        items,
        [
            {
                "from_table": "orders",
                "from_columns": ["id"],
                "to_table": "order_items",
                "to_columns": ["order_id"],
                "cardinality": "TO_MANY",
            }
        ],
    )
    only = {cube.base_table: cube for cube in forced}["orders"]
    assert [(join.to_table, join.cardinality) for join in only.joins] == [
        ("order_items", "TO_MANY")
    ]


def test_flag_off_prompt_and_envelope_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DMS_METRIC_CUBE", raising=False)
    monkeypatch.delenv("DMS_SHAPE_CHECK", raising=False)
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)
    reset_cube_store()
    row = cube_store().store_proposal(
        _sales().model_dump(),
        space_id="cube-space",
        created_by="steward",
    )
    cube_store().approve(row["id"], actor="steward")

    def _ask(flag: str | None) -> tuple[str, str]:
        if flag is None:
            monkeypatch.delenv("DMS_METRIC_CUBE", raising=False)
        else:
            monkeypatch.setenv("DMS_METRIC_CUBE", flag)
        seen: list[dict[str, Any]] = []

        def compute(ctx: dict[str, Any]) -> dict[str, Any]:
            seen.append(dict(ctx))
            return {}

        from dms_executor.generative_ask import maybe_generative_ask

        env = maybe_generative_ask(
            "synthetic cube probe",
            tables=[],
            compute=compute,
            submit=lambda sql: None,
            ledger_append=lambda body: None,
            demo_gate=False,
            dialect="duckdb",
            grantable=set(),
            space_id="cube-space",
        )
        assert seen
        prompt = json.dumps(seen[0], sort_keys=True, default=str)
        view = env if isinstance(env, dict) else {}
        drop = {"as_of", "ticket_id", "answer_id", "audit_id", "timings"}
        stable = {key: value for key, value in view.items() if key not in drop}
        return prompt, json.dumps(stable, sort_keys=True, default=str)

    off_prompt, off_env = _ask(None)
    zero_prompt, zero_env = _ask("0")
    assert off_prompt == zero_prompt
    assert off_env == zero_env
    assert "Approved metric cubes" not in off_prompt
    on_prompt, on_env = _ask("1")
    assert "Approved metric cubes" in on_prompt
    assert on_prompt != off_prompt
    assert on_env == off_env
    reset_cube_store()
