"""NL19-COMPILE-WRONG. Honour check before a compiled measure is served.

Flag default off. Off, no OpenVault call is made and the envelope, the
prompt, and the served route stay the bytes they were. On, a model reply
that does not honour the question is not served. A reply that does is
served. A destructive ask, from the model or from a non-SELECT AST, never
reaches compile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cortex_client.models import LedgerAppendResponse
from cortex_contract.execution import QueryResult
from dms_executor.compile_honour import (
    ENV,
    HONOUR_SCHEMA,
    ROLE_ALIAS,
    ast_non_select,
    bind_compile_honour_transport,
    reset_compile_honour_transport,
    shape_guard_hit,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology

_Q = "shipment cost by SKU and plant"
_TOTAL_Q = "What is the total shipping cost?"
_DROP_NL = "Drop the suppliers table"
_DROP_SQL = "DROP TABLE suppliers"
_MODEL_SQL = (
    "SELECT sku, plant_id, SUM(cost) AS shipping_cost_myr "
    "FROM shipments GROUP BY sku, plant_id"
)
_SCALAR_SQL = "SELECT SUM(cost) AS shipping_cost_myr FROM shipments"
_MODEL_KEYS = frozenset(
    {"model", "model_id", "model_name", "served_model", "served_provider", "provider", "strict"}
)
_HONOUR_TRUE = {
    "grouping": True,
    "filters": True,
    "entity": True,
    "aggregation": True,
    "time": True,
    "reasons": [],
}


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
    onto = Ontology()
    onto.add_object("shipment", "shipments", ["shipment_id"])
    onto.add_object("product", "products", ["sku"])
    onto.add_object("plant", "plants", ["plant_id"])
    onto.add_object("supplier", "suppliers", ["supplier_id"])
    onto.add_object("lane", "lanes", ["lane_id"])
    onto.add_link("ship_of_product", "shipment", ["sku"], "product", ["sku"])
    onto.add_link("ship_to_plant", "shipment", ["plant_id"], "plant", ["plant_id"])
    onto.add_link("ship_on_lane", "shipment", ["lane_id"], "lane", ["lane_id"])
    onto.add_link("lane_to_plant", "lane", ["plant_id"], "plant", ["plant_id"])
    onto.add_measure(
        "shipping_cost_myr",
        "shipment",
        "SUM(f.cost)",
        description="shipping cost, one contribution per shipment",
    )
    return onto


def _flag(monkeypatch: Any, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)


def _spy(monkeypatch: Any) -> dict[str, int]:
    import dms_executor.generative_ask as gen
    import dms_executor.ontology as onto_mod

    calls = {"compile": 0, "multi": 0}
    real = onto_mod.Ontology.compile
    real_multi = gen.try_compile_multi_grain

    def wrapped(self: Ontology, *args: Any, **kwargs: Any) -> Any:
        calls["compile"] += 1
        return real(self, *args, **kwargs)

    def wrapped_multi(*args: Any, **kwargs: Any) -> Any:
        calls["multi"] += 1
        return real_multi(*args, **kwargs)

    monkeypatch.setattr(onto_mod.Ontology, "compile", wrapped)
    monkeypatch.setattr(gen, "try_compile_multi_grain", wrapped_multi)
    return calls


def _ask(
    tmp_path: Path,
    monkeypatch: Any,
    question: str,
    compute: Any,
    *,
    flag: str | None,
    transport: Any | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    warehouse = tmp_path / "sc.duckdb"
    if not warehouse.is_file():
        _seed(warehouse)
    from dms_executor.generative_ask import load_verified_ontology

    onto = load_verified_ontology(warehouse, _ontology())
    assert onto is not None
    submits: list[str] = []
    prompts: list[str] = []

    def submit(sql: str) -> QueryResult:
        submits.append(sql)
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_nl19", output={"rows": rows})

    def wrapped(ctx: dict[str, Any]) -> Any:
        prompts.append(json.dumps(ctx, sort_keys=True, default=str))
        return compute(ctx)

    _flag(monkeypatch, flag)
    token = None
    if transport is not None:
        token = bind_compile_honour_transport(transport)
    try:
        env = maybe_generative_ask(
            question,
            warehouse=warehouse,
            grantable={"shipments", "products", "plants", "lanes", "suppliers"},
            compute=wrapped,
            submit=submit,
            ledger_append=(
                lambda _body: LedgerAppendResponse(entry_id="led_nl19", hash="hash_nl19_not_entry")
            ),
            ontology=onto,
            bind_on_miss=False,
            dialect="duckdb",
        )
    finally:
        if token is not None:
            reset_compile_honour_transport(token)
    assert env is not None
    assert_envelope_valid(env)
    return env, submits, prompts


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(env, sort_keys=True, default=str))
    out.pop("as_of", None)
    return out


def _keys(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key).casefold())
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


def _reply(destructive: bool, honour: dict[str, Any] | None):
    def transport(body: dict[str, Any]) -> dict[str, Any]:
        assert body["role"] == ROLE_ALIAS
        found: list[str] = []
        _keys(body, found)
        assert _MODEL_KEYS.isdisjoint(found)
        if body["schema"] == "DestructiveIntentReply":
            return {"destructive": destructive, "reasons": []}
        assert body["schema"] == HONOUR_SCHEMA
        user = json.loads(body["messages"][1]["content"])
        semantics = user["semantics"]
        for key in ("group_by", "filters", "entity", "aggregation", "time"):
            assert key in semantics
        assert honour is not None
        return honour

    return transport


def test_ast_non_select_is_the_statement_type() -> None:
    assert ast_non_select(_DROP_SQL) is True
    assert ast_non_select("UPDATE suppliers SET risk_score = 0") is True
    assert ast_non_select("SELECT SUM(cost) FROM shipments") is False
    assert ast_non_select(_DROP_NL) is False
    assert ast_non_select(_Q) is False
    env = {
        "abstained": True,
        "assumptions": [
            "scalar ask answered by a grouped ranking: shape mismatch (E12/ANS-02)"
        ],
    }
    assert shape_guard_hit(env) is True
    assert shape_guard_hit({"abstained": False, "assumptions": env["assumptions"]}) is False


def test_flag_off_is_byte_identical(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls: list[int] = []

    def transport(_body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise AssertionError("honour call")

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {"answer": "not a plan"}

    unset, submits_a, prompts_a = _ask(
        tmp_path, monkeypatch, _Q, compute, flag=None, transport=transport
    )
    zero, submits_b, prompts_b = _ask(
        tmp_path, monkeypatch, _Q, compute, flag="0", transport=transport
    )
    assert calls == []
    assert prompts_a == prompts_b
    assert prompts_a
    assert _stable(unset) == _stable(zero)
    assert unset["served_route"] == "ontology_compile"
    assert zero["served_route"] == "ontology_compile"
    assert unset["plan_origin"] == "ontology_compile"
    assert unset["sql_used"] == submits_a[0]
    assert submits_a == submits_b
    assert unset["abstained"] is False


def test_not_honoured_is_not_served(tmp_path: Path, monkeypatch: Any) -> None:
    seen: list[str] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        if body["schema"] == HONOUR_SCHEMA:
            seen.append(json.loads(body["messages"][1]["content"])["sql"])
        return _reply(False, {**_HONOUR_TRUE, "grouping": False})(body)

    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"answer": "not a plan"}
        return {"query_sql": _MODEL_SQL, "plan_source": "ontology_plan"}

    env, submits, _prompts = _ask(
        tmp_path, monkeypatch, _Q, compute, flag="1", transport=transport
    )
    assert seen
    compile_sql = seen[0]
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["sql_used"] == _MODEL_SQL
    assert env["sql_used"] != compile_sql
    assert compile_sql not in submits
    assert submits == [_MODEL_SQL]
    assert env.get("plan_origin") == "generate_sql"
    text = str(env.get("text") or "")
    assert "gap:" not in text


def test_honoured_is_served(tmp_path: Path, monkeypatch: Any) -> None:
    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {"answer": "not a plan"}

    env, submits, _prompts = _ask(
        tmp_path,
        monkeypatch,
        _Q,
        compute,
        flag="1",
        transport=_reply(False, dict(_HONOUR_TRUE)),
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["served_route"] == "ontology_compile"
    assert env["plan_origin"] == "ontology_compile"
    assert submits
    assert env["sql_used"] == submits[0]
    assert env["rows"]


def test_destructive_ask_never_reaches_compile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls = _spy(monkeypatch)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("compile refused before the AI leg")

    env, submits, _prompts = _ask(
        tmp_path,
        monkeypatch,
        _DROP_NL,
        compute,
        flag="1",
        transport=_reply(True, None),
    )
    assert calls == {"compile": 0, "multi": 0}
    assert submits == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert "hostile_sql" in assumptions
    assert env.get("sql_used") in (None, "")


def test_ast_non_select_refuses_before_compile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls = _spy(monkeypatch)
    schemas: list[str] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        schemas.append(str(body["schema"]))
        return _reply(False, dict(_HONOUR_TRUE))(body)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("non-SELECT refused before compile")

    env, submits, _prompts = _ask(
        tmp_path,
        monkeypatch,
        _DROP_SQL,
        compute,
        flag="1",
        transport=transport,
    )
    assert "DestructiveIntentReply" in schemas
    assert HONOUR_SCHEMA not in schemas
    assert calls == {"compile": 0, "multi": 0}
    assert submits == []
    assert env["abstained"] is True
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert "hostile_sql" in assumptions


def test_shape_guard_falls_to_ai_sql(tmp_path: Path, monkeypatch: Any) -> None:
    plan = {
        "query_plan": {
            "measure": "shipping_cost_myr",
            "group_by": [["product", "sku"]],
        },
        "plan_source": "ontology_plan",
    }
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return plan
        return {"query_sql": _SCALAR_SQL, "plan_source": "ontology_plan"}

    off, _off_submits, _off_prompts = _ask(
        tmp_path, monkeypatch, _TOTAL_Q, compute, flag=None
    )
    assert off["abstained"] is True
    assert "You asked for one total" in str(off.get("text") or "")

    calls["n"] = 0
    on, submits, _prompts = _ask(
        tmp_path,
        monkeypatch,
        _TOTAL_Q,
        compute,
        flag="1",
        transport=_reply(False, dict(_HONOUR_TRUE)),
    )
    assert "You asked for one total" not in str(on.get("text") or "")
    assert on["abstained"] is False
    assert on["sql_used"] == _SCALAR_SQL
    assert submits[-1] == _SCALAR_SQL
    block = on.get("reconfirm")
    assert not isinstance(block, dict) or str(block.get("suggested_question") or "").strip()
