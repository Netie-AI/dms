"""COMPILE-CHECK-01: a compile serve must pass the AI-lane checker.

A checker miss is not served. The question goes to the extract loop
(generate, execute, check, repair, escalate). If that also misses, the
envelope abstains and does not carry the compile figure. A compile SQL
the checker accepts is still served.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cortex_client.models import LedgerAppendResponse
from cortex_contract.execution import QueryResult
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import ai_lane_sql_check, maybe_generative_ask
from dms_executor.ontology import Ontology

_Q = "shipment cost by SKU and plant"
_MODEL_SQL = (
    "SELECT sku, plant_id, SUM(cost) AS shipping_cost_myr FROM shipments GROUP BY sku, plant_id"
)


def _seed(warehouse: Path) -> None:
    import duckdb

    con = duckdb.connect(str(warehouse))
    try:
        con.execute("CREATE TABLE plants (plant_id VARCHAR PRIMARY KEY, name VARCHAR)")
        con.execute("INSERT INTO plants VALUES ('P1','Plant 1'),('P2','Plant 2')")
        con.execute("CREATE TABLE products (sku VARCHAR PRIMARY KEY, category VARCHAR)")
        con.execute("INSERT INTO products VALUES ('SKU-A','RAW'),('SKU-B','PACK')")
        con.execute("CREATE TABLE suppliers (supplier_id VARCHAR PRIMARY KEY, name VARCHAR)")
        con.execute("INSERT INTO suppliers VALUES ('S1','North'),('S2','South')")
        con.execute("CREATE TABLE lanes (lane_id VARCHAR PRIMARY KEY, plant_id VARCHAR)")
        con.execute("INSERT INTO lanes VALUES ('L1','P1'),('L2','P2')")
        con.execute(
            "CREATE TABLE shipments ("
            "shipment_id VARCHAR PRIMARY KEY, sku VARCHAR, plant_id VARCHAR, "
            "lane_id VARCHAR, origin_id VARCHAR, supplier_id VARCHAR, cost DOUBLE)"
        )
        con.execute(
            "INSERT INTO shipments VALUES "
            "('H1','SKU-A','P1','L1','P2','S1',40),"
            "('H2','SKU-B','P1','L1','P2','S1',60),"
            "('H3','SKU-A','P2','L2','P1','S2',10)"
        )
    finally:
        con.close()


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("shipment", "shipments", ["shipment_id"])
    o.add_object("product", "products", ["sku"])
    o.add_object("plant", "plants", ["plant_id"])
    o.add_object("supplier", "suppliers", ["supplier_id"])
    o.add_object("lane", "lanes", ["lane_id"])
    o.add_link("ship_of_product", "shipment", ["sku"], "product", ["sku"])
    o.add_link("ship_to_plant", "shipment", ["plant_id"], "plant", ["plant_id"])
    o.add_link("ship_on_lane", "shipment", ["lane_id"], "lane", ["lane_id"])
    o.add_link("lane_to_plant", "lane", ["plant_id"], "plant", ["plant_id"])
    o.add_measure(
        "shipping_cost_myr",
        "shipment",
        "SUM(f.cost)",
        description="shipment cost / freight billed, one contribution per shipment",
    )
    return o


def _ask(
    tmp_path: Path,
    compute: Any,
    *,
    must_submit: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    warehouse = tmp_path / "sc.duckdb"
    _seed(warehouse)
    from dms_executor.generative_ask import load_verified_ontology

    onto = load_verified_ontology(warehouse, _ontology())
    assert onto is not None
    submits: list[str] = []

    def submit(sql: str) -> QueryResult:
        submits.append(sql)
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            cols = [d[0] for d in con.execute(sql).description]
            rows = [dict(zip(cols, r, strict=True)) for r in con.execute(sql).fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_cc01", output={"rows": rows})

    def boom(_sql: str) -> QueryResult:
        raise AssertionError("must not submit")

    env = maybe_generative_ask(
        _Q,
        warehouse=warehouse,
        grantable={"shipments", "products", "plants", "lanes", "suppliers"},
        compute=compute,
        submit=submit if must_submit else boom,
        ledger_append=(
            (lambda _b: LedgerAppendResponse(entry_id="led_cc01", hash="hash_cc01_not_entry"))
            if must_submit
            else (lambda _b: (_ for _ in ()).throw(AssertionError("must not append")))
        ),
        ontology=onto,
        bind_on_miss=False,
        dialect="duckdb",
    )
    assert env is not None
    assert_envelope_valid(env)
    return env, submits


def _reject_first_sql(monkeypatch: Any) -> dict[str, str | None]:
    """The first SQL the shared checker sees fails. Later SQL is checked for real."""
    import dms_executor.generative_ask as gen

    real = gen.ai_lane_sql_check
    seen: dict[str, str | None] = {"sql": None}

    def _check(sql: str, **kwargs: Any) -> str | None:
        if seen["sql"] is None:
            seen["sql"] = sql
            return "unverified_join:fan_trap"
        if sql == seen["sql"]:
            return "unverified_join:fan_trap"
        return real(sql, **kwargs)

    monkeypatch.setattr(gen, "ai_lane_sql_check", _check)
    return seen


def test_compile_checker_fail_is_not_served_ai_sql_is(tmp_path: Path, monkeypatch: Any) -> None:
    seen = _reject_first_sql(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"answer": "not a plan"}
        return {"query_sql": _MODEL_SQL, "plan_source": "ontology_plan"}

    env, submits = _ask(tmp_path, compute)
    refused = str(seen["sql"] or "")
    assert refused and "JOIN" in refused.upper()
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_origin") == "generate_sql"
    assert env.get("sql_used") == _MODEL_SQL
    assert refused not in submits
    assert submits == [_MODEL_SQL]
    rows = env["rows"]
    assert rows
    assert sum(float(r["shipping_cost_myr"]) for r in rows) == 110.0
    text = str(env.get("text") or "")
    assert "110" in text or "40" in text
    assert "fan_trap" not in text


def test_compile_checker_fail_and_ai_miss_is_not_served(tmp_path: Path, monkeypatch: Any) -> None:
    seen = _reject_first_sql(monkeypatch)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {"answer": "not a plan"}

    env, submits = _ask(tmp_path, compute, must_submit=False)
    assert seen["sql"] and "JOIN" in str(seen["sql"]).upper()
    assert submits == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env.get("sql_used") in (None, "")
    text = str(env.get("text") or "")
    assert "unverified_join" in text
    assert "110" not in text
    assert "gap: " in text


def test_compile_that_passes_the_checker_still_serves(tmp_path: Path) -> None:
    env, submits = _ask(tmp_path, lambda _ctx: {"answer": "not a plan"})
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_origin") == "ontology_compile"
    assert submits and "JOIN" in submits[0].upper()
    assert env.get("sql_used") == submits[0]
    rows = env["rows"]
    assert rows
    assert sum(float(r.get("shipping_cost_myr") or 0) for r in rows) == 110.0
    assert "Found" in str(env.get("text") or "")


def test_ai_lane_sql_check_is_the_function_the_loop_uses() -> None:
    """The compile gate and the extract loop call one function."""
    import inspect

    import dms_executor.generative_ask as gen

    src = inspect.getsource(gen._run_extract_loop)
    assert "ai_lane_sql_check(" in src
    assert ai_lane_sql_check is gen.ai_lane_sql_check
