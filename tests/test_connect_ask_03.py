"""CONNECT-ASK-03 (dms#520, F-TBD-33): no ontology means zero verified links.

Before: the rule 16 join check ran only when the ask had a Space ontology
(``if space_onto is not None`` at three sites). A Space built from uploaded
files has none, so model-written SQL joining two uploads on any columns was
checked by grants and EXPLAIN only and was served ``L2_VALIDATED``.

Here a non-demo Space ingests two CSV files through the real route
(``POST /v1/studio/ingest-batch``) and has no ontology of any kind. The
generate leg is a stub that returns fixed SQL. Every answer-path case posts
``POST /v1/chat/ask`` and asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows). The Cortex fake
executes submits on the lake under the Cortex grant rule, so rows are real.

The stub does not assert which ontology the model was asked with: a file
Space is asked with the demo context today (dms#417, not this ticket).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor.demo_grants import is_demo_space
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import load_space_ontology
from fastapi.testclient import TestClient
from test_onto_derive_01 import (
    _assert_abstain,
    _gate_allows,
    _multiset,
    _oracle,
    _reasons,
    _RecordingCortex,
    make_minter,
)

ORDERS = "bronze.orders"
CUSTOMERS = "bronze.customers"
# order_id, customer_id, amount. Customer 10 and 12 are North, 11 is South.
ORDER_ROWS = [(1, 10, 100), (2, 10, 50), (3, 11, 70), (4, 12, 30)]
CUSTOMER_ROWS = [(10, "North"), (11, "South"), (12, "North")]
# One order date per order row: two in January, one in February, one in March.
ORDER_DATES = ["2026-01-05", "2026-01-20", "2026-02-11", "2026-03-02"]
ORDERS_CSV = "order_id,customer_id,amount,order_date\n" + "".join(
    f"{a},{b},{c},{d}\n" for (a, b, c), d in zip(ORDER_ROWS, ORDER_DATES, strict=True)
)
CUSTOMERS_CSV = "customer_id,region\n" + "".join(f"{a},{b}\n" for a, b in CUSTOMER_ROWS)

JOIN_Q = "What is the total order amount by region?"
# Parses, is granted, EXPLAINs, executes: North 180, South 70. Nothing ever
# measured that customer_id is a key of customers. That is the hole.
JOIN_SQL = (
    "SELECT c.region, SUM(CAST(o.amount AS INTEGER)) AS total "
    f"FROM {ORDERS} o JOIN {CUSTOMERS} c ON o.customer_id = c.customer_id "
    "GROUP BY c.region ORDER BY c.region"
)
JOIN_FIGURES = ("180", "70")
ONE_TABLE_Q = "What is the total order amount?"
ONE_TABLE_SQL = f"SELECT SUM(CAST(amount AS INTEGER)) AS total FROM {ORDERS}"
# Truth from the CSV constants, not from the lake and not from the SQL.
ONE_TABLE_TRUTH = sum(amount for _o, _c, amount in ORDER_ROWS)
NO_ONTOLOGY = "unverified_join:no_ontology"


@dataclass
class _StubCortex(_RecordingCortex):
    """The generate leg returns the fixed payload, whatever context it was sent."""

    suggestion: str = ""
    completions: list[str] = field(default_factory=list)

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any]:
        self.insights.append({"question": question, **kw})
        return dict(self.payload)

    def complete(self, prompt: str) -> str:
        """The reconfirm writer's model: the closest answerable question."""
        self.completions.append(prompt)
        return self.suggestion


class _Rig:
    def __init__(self, client: TestClient, cortex: _StubCortex, space_id: str | None, lake: Path):
        self.client = client
        self.cortex = cortex
        self.space_id = space_id
        self.lake = lake

    def ask(
        self, question: str, payload: dict[str, Any], session: str = "ses_connect_ask_03"
    ) -> dict[str, Any]:
        self.cortex.payload = payload
        body: dict[str, Any] = {"question": question, "session_id": session}
        if self.space_id is not None:
            body["space_id"] = self.space_id
        r = self.client.post("/v1/chat/ask", json=body)
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env

    def confirm(self, token: str, yes: bool = True) -> dict[str, Any]:
        r = self.client.post("/v1/chat/ask/confirm", json={"token": token, "yes": yes})
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case states its own flags. None leaks in from the shell."""
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_ONTO_FROM_DATA",
        "DMS_DEF_VALIDATE",
        "DMS_RECONFIRM_RUN_HELD",
        "DMS_AI_FIRST",
        "DMS_VQ_RAG",
        "DMS_SCHEMA_CONTEXT",
    ):
        monkeypatch.delenv(name, raising=False)
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()


def _app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> tuple[TestClient, _StubCortex, Any, Path]:
    lake = tmp_path / "connect_ask_03.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    cortex = _StubCortex(warehouse=lake, payload={})
    app = create_app()
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), cortex, app, lake


def _file_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> _Rig:
    """A non-demo Space holding two uploaded CSV tables and no ontology."""
    client, cortex, app, lake = _app(tmp_path, monkeypatch, minter)
    space = app.state.space_store.create("Two uploads")
    r = client.post(
        "/v1/studio/ingest-batch",
        data={"space_id": space.id},
        files=[
            ("files", ("orders.csv", ORDERS_CSV.encode(), "text/csv")),
            ("files", ("customers.csv", CUSTOMERS_CSV.encode(), "text/csv")),
        ],
    )
    assert r.status_code == 200, r.text
    assert [f["table"] for f in r.json()["files"]] == [ORDERS, CUSTOMERS], r.text
    assert not is_demo_space(space.id)
    assert load_space_ontology(space.id) is None, "the fixture Space must have no ontology"
    # The plausible answer really exists on the lake: that is the danger.
    assert _oracle(lake, JOIN_SQL) == [
        {"region": "North", "total": 180},
        {"region": "South", "total": 70},
    ]
    return _Rig(client, cortex, space.id, lake)


def _assert_no_ontology_abstain(
    env: dict[str, Any], rig: _Rig, gap: str = "unverified_join"
) -> None:
    text = _assert_abstain(env, gap)
    assert NO_ONTOLOGY in _reasons(env) or NO_ONTOLOGY in text, (text, env.get("assumptions"))
    # E9: nothing executed, so no figure from the refused join may be stated.
    for figure in JOIN_FIGURES:
        assert not re.search(rf"\b{figure}\b", text), text
    assert env.get("sql_used") in (None, "")
    assert rig.cortex.executed == [], "the refused SQL reached Cortex submit"


def _gen(sql: str) -> dict[str, Any]:
    return {"query_sql": sql, "plan_source": "ontology_plan"}


# --- clause 1: the reproduction ---------------------------------------------------


def test_two_table_join_in_a_file_space_abstains_no_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """On the parent commit this serves L2_VALIDATED with 2 rows (F-TBD-33)."""
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    assert len(rig.cortex.insights) == 1, "the generate stub was not asked"
    _assert_no_ontology_abstain(env, rig)


# --- clause 2: one table still answers --------------------------------------------


def test_one_table_sql_in_a_file_space_answers_the_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(ONE_TABLE_Q, _gen(ONE_TABLE_SQL))
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert env["rows"] == [{"total": ONE_TABLE_TRUTH}]
    assert [v["value"] for v in env["values"]] == [float(ONE_TABLE_TRUTH)]
    assert f"total={ONE_TABLE_TRUTH}" in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == [ONE_TABLE_SQL]


# --- clause 3: every other door ---------------------------------------------------


def test_extract_loop_retry_is_fed_the_reason_and_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """C-LOOP-B returns before the first-generate branch: its check must gate too."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    _assert_no_ontology_abstain(env, rig, f"loop_exhausted:checker:{NO_ONTOLOGY}")
    # Rule 15: the refusal is a ladder rung. The model was asked again, and the
    # retry prompt carried the reason.
    assert len(rig.cortex.insights) > 1
    retry = rig.cortex.insights[-1]
    assert NO_ONTOLOGY in str(retry), "the retry was not fed the refusal reason"

    env = rig.ask(ONE_TABLE_Q, _gen(ONE_TABLE_SQL), session="ses_loop_one_table")
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == [{"total": ONE_TABLE_TRUTH}]
    assert f"total={ONE_TABLE_TRUTH}" in str(env.get("text") or ""), env.get("text")


def _contract_join(rig: _Rig, monkeypatch: pytest.MonkeyPatch, sql: str = JOIN_SQL) -> None:
    def _ask(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer="North is 180 and South is 70.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=sql,
            rows=[{"region": "North", "total": 180}, {"region": "South", "total": 70}],
        )

    monkeypatch.setattr(rig.cortex, "ask", _ask)


def test_contract_ask_sql_used_abstains_no_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Generation misses; the contract ask's own SQL must pass the same rule."""
    rig = _file_space(tmp_path, monkeypatch, minter)
    _contract_join(rig, monkeypatch)
    env = rig.ask(JOIN_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    _assert_no_ontology_abstain(env, rig)


def test_contract_ask_fails_closed_under_onto_from_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """DMS_ONTO_FROM_DATA=1: this guard has no Space ontology, so it fails closed."""
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    rig = _file_space(tmp_path, monkeypatch, minter)
    _contract_join(rig, monkeypatch)
    env = rig.ask(JOIN_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    _assert_no_ontology_abstain(env, rig)


def test_reconfirm_yes_reruns_the_ladder_and_abstains_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Yes re-asks on the normal ladder. The same SQL meets the same gate."""
    rig = _file_space(tmp_path, monkeypatch, minter)
    rig.cortex.suggestion = ONE_TABLE_Q
    env = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    _assert_no_ontology_abstain(env, rig)
    block = env["reconfirm"]
    assert block["reason_code"] == NO_ONTOLOGY
    assert block["suggested_question"] == ONE_TABLE_Q
    assert NO_ONTOLOGY in rig.cortex.completions[-1], "the writer was not given the reason"
    # The model answers the suggested question with the same join. Same gate.
    again = rig.confirm(block["token"], yes=True)
    _assert_no_ontology_abstain(again, rig)


def test_held_sql_yes_abstains_no_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """DMS_RECONFIRM_RUN_HELD=1: Yes runs the parked SQL. It meets the same gate."""
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    block = env["reconfirm"]
    from dms_executor.reconfirm import held_ready, peek_pending

    pending = peek_pending(block["token"])
    assert pending is not None and held_ready(pending), "no SQL was parked for a Yes"
    assert pending["sql"] == JOIN_SQL
    again = rig.confirm(block["token"], yes=True)
    assert again["badge"] == "ABSTAIN", (again["badge"], again.get("text"), again.get("rows"))
    assert again["abstained"] is True
    assert again["rows"] == [] and again["values"] == []
    assert not again.get("drillthrough_token")
    assert NO_ONTOLOGY in _reasons(again), again.get("assumptions")
    for figure in JOIN_FIGURES:
        assert not re.search(rf"\b{figure}\b", str(again.get("text") or "")), again.get("text")


# --- clause 4: one allowlist, zero links ------------------------------------------

NORTH_Q = "What is the total order amount for the North region?"
# name -> (question, SQL). Each reads a second table without a plain JOIN ... ON.
SECOND_TABLE_SHAPES = {
    "comma_join": (
        JOIN_Q,
        f"SELECT c.region, SUM(CAST(o.amount AS INTEGER)) AS total FROM {ORDERS} o, {CUSTOMERS} c "
        "WHERE o.customer_id = c.customer_id GROUP BY c.region",
    ),
    "subquery_other_table": (
        NORTH_Q,
        f"SELECT SUM(CAST(amount AS INTEGER)) AS total FROM {ORDERS} WHERE customer_id IN "
        f"(SELECT customer_id FROM {CUSTOMERS} WHERE region = 'North')",
    ),
    "cte_two_tables": (
        NORTH_Q,
        f"WITH n AS (SELECT customer_id FROM {CUSTOMERS} WHERE region = 'North') "
        f"SELECT SUM(CAST(o.amount AS INTEGER)) AS total FROM {ORDERS} o "
        "JOIN n ON o.customer_id = n.customer_id",
    ),
    "union_two_tables": (
        "Which customer ids appear in orders or customers?",
        f"SELECT customer_id FROM {ORDERS} UNION SELECT customer_id FROM {CUSTOMERS}",
    ),
    "self_join": (
        "How many order pairs share a customer?",
        f"SELECT COUNT(*) AS pairs FROM {ORDERS} a JOIN {ORDERS} b "
        "ON a.customer_id = b.customer_id",
    ),
    "table_function": (
        ONE_TABLE_Q,
        f"SELECT SUM(CAST(amount AS INTEGER)) AS total FROM query('SELECT * FROM {ORDERS}')",
    ),
    "derived_table": (
        ONE_TABLE_Q,
        "SELECT SUM(t.amount) AS total FROM (SELECT CAST(o.amount AS INTEGER) AS amount "
        f"FROM {ORDERS} o JOIN {CUSTOMERS} c ON o.customer_id = c.customer_id) t",
    ),
}


@pytest.mark.parametrize("name", sorted(SECOND_TABLE_SHAPES))
def test_no_second_table_shape_answers(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    import dms_executor.generative_ask as ga

    seen: list[tuple[int, int]] = []
    real = ga.unverified_join_reason

    def _spy(sql: str, onto: Any, violations: Any, **kw: Any) -> str | None:
        seen.append((len(onto.objects), len(onto.links)))
        return real(sql, onto, violations, **kw)

    monkeypatch.setattr(ga, "unverified_join_reason", _spy)
    rig = _file_space(tmp_path, monkeypatch, minter)
    question, sql = SECOND_TABLE_SHAPES[name]
    env = rig.ask(question, _gen(sql))
    _assert_no_ontology_abstain(env, rig)
    # The existing allowlist decided, holding no object and no link.
    assert seen and set(seen) == {(0, 0)}, seen


# --- clause 5: the same ladder as a Space that has an ontology --------------------


@pytest.fixture()
def store() -> Any:
    from dms_executor.space_ontology import set_ontology_store

    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


def _code_head(block: dict[str, Any]) -> str:
    """The reason code up to and including ``unverified_join``; the tail names the why."""
    code = str(block.get("reason_code") or "")
    head, sep, _tail = code.partition("unverified_join")
    return head + sep


def _shape(env: dict[str, Any]) -> dict[str, Any]:
    block = env.get("reconfirm")
    return {
        "keys": sorted(env),
        "badge": env.get("badge"),
        "abstained": env.get("abstained"),
        "route": env.get("route"),
        "served_route": env.get("served_route"),
        "ladder_rung": env.get("ladder_rung"),
        "model_calls": env.get("model_calls"),
        "reconfirm_keys": sorted(block) if isinstance(block, dict) else None,
        "reconfirm_code": _code_head(block) if isinstance(block, dict) else None,
        "suggested_question": block.get("suggested_question") if isinstance(block, dict) else None,
    }


@pytest.mark.parametrize("cloop", ["0", "1"])
def test_refusal_follows_the_ladder_of_a_space_with_an_ontology(
    cloop: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    if cloop == "1":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    (tmp_path / "onto").mkdir()
    with_onto = od._space(tmp_path / "onto", monkeypatch, minter)
    monkeypatch.setattr(with_onto.cortex, "complete", lambda _p: ONE_TABLE_Q, raising=False)
    want = with_onto.ask(od.VERIFIED_Q, _gen(od.WRONG_JOIN_SQL))
    assert "unverified_join:" in _reasons(want) + str(want.get("text"))
    onto_calls = len(with_onto.cortex.insights)

    (tmp_path / "file").mkdir()
    rig = _file_space(tmp_path / "file", monkeypatch, minter)
    rig.cortex.suggestion = ONE_TABLE_Q
    got = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    _assert_no_ontology_abstain(
        got, rig, f"loop_exhausted:checker:{NO_ONTOLOGY}" if cloop == "1" else "unverified_join"
    )
    assert _shape(got) == _shape(want)
    assert got["reconfirm"]["reason_code"].endswith(NO_ONTOLOGY), got["reconfirm"]
    assert got["reconfirm"]["suggested_question"] == ONE_TABLE_Q
    # The same number of model calls: the same retries were spent.
    assert len(rig.cortex.insights) == onto_calls


# --- clause 6: demo Spaces are the parent commit ----------------------------------

DEMO_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
DEMO_JOIN_Q = "How many inventory lots does each warehouse hold?"
DEMO_JOIN_SQL = (
    "SELECT l.name, COUNT(*) AS lots FROM inventory i "
    "JOIN locations l ON i.location_id = l.location_id GROUP BY l.name ORDER BY l.name"
)


@pytest.mark.parametrize("space_id", [None, DEMO_FINANCE])
def test_demo_space_join_is_served_as_on_the_parent(
    space_id: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    client, cortex, _app_, lake = _app(tmp_path, monkeypatch, minter)
    assert is_demo_space(space_id)
    rig = _Rig(client, cortex, space_id, lake)
    oracle = _oracle(lake, DEMO_JOIN_SQL)
    assert len(oracle) > 1
    env = rig.ask(DEMO_JOIN_Q, _gen(DEMO_JOIN_SQL))
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset(oracle)
    first = oracle[0]
    assert f"name={first['name']}, lots={first['lots']}" in str(env.get("text") or "")
    assert cortex.executed == [DEMO_JOIN_SQL]


# --- clause 8: a SQL-source Space whose stored ontology has no keyed object -------


def test_sql_source_space_with_no_keyed_object_is_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """The source declared no primary key and no foreign key: nothing was verified."""
    monkeypatch.setattr(od, "PG_PKS", [])
    monkeypatch.setattr(od, "PG_FKS", [])
    rig = od._space(tmp_path, monkeypatch, minter)
    assert load_space_ontology(rig.space_id) is None
    cortex = _StubCortex(warehouse=rig.lake, payload={})
    rig.client.app.state.ask_service._cortex = cortex  # type: ignore[attr-defined]
    rig.client.app.state.cortex = cortex  # type: ignore[attr-defined]
    rig.cortex = cortex
    # The join over the link the source never declared: it executes (3 schools).
    assert _oracle(rig.lake, od.VERIFIED_SQL) == [{"school_count": 3}]
    env = rig.ask(od.VERIFIED_Q, _gen(od.VERIFIED_SQL))
    text = _assert_abstain(env, "unverified_join")
    assert NO_ONTOLOGY in _reasons(env) or NO_ONTOLOGY in text
    assert "school_count=3" not in text
    assert cortex.executed == []

    one = f"SELECT COUNT(*) AS school_count FROM {od.SCHOOLS}"
    env = rig.ask("How many schools are there?", _gen(one))
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == [{"school_count": len(od.SCHOOL_ROWS)}]
    assert f"school_count={len(od.SCHOOL_ROWS)}" in str(env.get("text") or "")


# --- clause 9: a check that cannot finish abstains --------------------------------


def test_unparsable_sql_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(JOIN_Q, _gen(f"SELECT region FROM {CUSTOMERS} JOIN ((( ON"))
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"))
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert rig.cortex.executed == []


@pytest.mark.parametrize("sql", [JOIN_SQL, ONE_TABLE_SQL])
def test_unreadable_lake_abstains_check_unavailable(
    sql: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Columns cannot be read: even one-table SQL is not served on an unfinished check."""
    import dms_executor.generative_ask as ga

    rig = _file_space(tmp_path, monkeypatch, minter)

    def _unreadable(*_a: Any, **_k: Any) -> dict[str, set[str]]:
        raise OSError("lake cannot be opened")

    monkeypatch.setattr(ga, "relation_columns", _unreadable)
    env = rig.ask(JOIN_Q, _gen(sql))
    text = _assert_abstain(env, "unverified_join")
    assert "unverified_join:check_unavailable" in _reasons(env) or (
        "unverified_join:check_unavailable" in text
    )
    assert rig.cortex.executed == []


def test_contract_ask_unreadable_lake_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    import dms_executor.generative_ask as ga

    rig = _file_space(tmp_path, monkeypatch, minter)
    _contract_join(rig, monkeypatch)

    def _unreadable(*_a: Any, **_k: Any) -> dict[str, set[str]]:
        raise OSError("lake cannot be opened")

    monkeypatch.setattr(ga, "relation_columns", _unreadable)
    env = rig.ask(JOIN_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    text = _assert_abstain(env, "unverified_join")
    assert "unverified_join:check_unavailable" in _reasons(env) or (
        "unverified_join:check_unavailable" in text
    )


# --- adversary round (dms#522): an analyser that raises abstains on every door ----


def _boom(*_a: Any, **_k: Any) -> str | None:
    raise RuntimeError("join analyser raised")


def _assert_check_unavailable(env: dict[str, Any], rig: _Rig) -> None:
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"))
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    assert "unverified_join:check_unavailable" in _reasons(env) + text, (
        text,
        env.get("assumptions"),
    )
    for figure in JOIN_FIGURES:
        assert not re.search(rf"\b{figure}\b", text), text
    assert rig.cortex.executed == []


@pytest.mark.parametrize("door", ["first_generate", "extract_loop", "contract_ask"])
def test_analyser_that_raises_abstains_check_unavailable(
    door: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Clause 9. Before the fix this was HTTP 503 ``live_ask_failed``, not an abstain."""
    import dms_executor.generative_ask as ga

    if door == "extract_loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = _file_space(tmp_path, monkeypatch, minter)
    monkeypatch.setattr(ga, "unverified_join_reason", _boom)
    if door == "contract_ask":
        _contract_join(rig, monkeypatch)
        env = rig.ask(JOIN_Q, {})
        assert rig.cortex.asks, "the contract ask was not reached"
    else:
        env = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    _assert_check_unavailable(env, rig)


def _parked_join(rig: _Rig) -> str:
    """Ask the join under def-validate; return the Yes token of the parked SQL."""
    from dms_executor.reconfirm import held_ready, peek_pending

    env = rig.ask(JOIN_Q, _gen(JOIN_SQL))
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    token = env["reconfirm"]["token"]
    pending = peek_pending(token)
    assert pending is not None and held_ready(pending), "no SQL was parked for a Yes"
    assert pending["sql"] == JOIN_SQL
    return str(token)


def test_held_sql_yes_with_an_analyser_that_raises_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    import dms_executor.generative_ask as ga

    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    rig = _file_space(tmp_path, monkeypatch, minter)
    token = _parked_join(rig)
    monkeypatch.setattr(ga, "unverified_join_reason", _boom)
    _assert_check_unavailable(rig.confirm(token, yes=True), rig)


def test_held_sql_yes_with_an_unreadable_ontology_store_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The store cannot say whether this Space has an ontology: a Yes never serves."""
    import dms_executor as de

    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    rig = _file_space(tmp_path, monkeypatch, minter)
    token = _parked_join(rig)

    def _unreadable(*_a: Any, **_k: Any) -> None:
        raise OSError("ontology store cannot be read")

    monkeypatch.setattr(de, "load_space_ontology", _unreadable)
    _assert_check_unavailable(rig.confirm(token, yes=True), rig)


# --- adversary round: the coverage cost, pinned ------------------------------------
#
# The allowlist counts table references, not distinct tables. With zero links a
# statement that names ONE table twice is refused like a join. Each case below
# answered on the parent and abstains here. The reason says ``no_ontology``
# although one table is read. Pinned so a later allowlist change shows up as a
# moved case. Routed to the PRD agent; not changed here.

ONE_TABLE_NOW_ABSTAINS = {
    "scalar_subquery_cast": (
        "Which orders are above the average amount?",
        f"SELECT order_id FROM {ORDERS} WHERE CAST(amount AS INTEGER) > "
        f"(SELECT AVG(CAST(amount AS INTEGER)) FROM {ORDERS}) ORDER BY order_id",
    ),
    "union_all_self": (
        "List every order id twice",
        f"SELECT order_id FROM {ORDERS} UNION ALL SELECT order_id FROM {ORDERS}",
    ),
    "exists_self": (
        "Which orders have another order from the same customer?",
        f"SELECT a.order_id FROM {ORDERS} a WHERE EXISTS (SELECT 1 FROM {ORDERS} b "
        "WHERE b.customer_id = a.customer_id AND b.order_id <> a.order_id) ORDER BY a.order_id",
    ),
    "self_join": SECOND_TABLE_SHAPES["self_join"],
    "generate_series": ("List the numbers one to three", "SELECT * FROM generate_series(1, 3)"),
}


@pytest.mark.parametrize("name", sorted(ONE_TABLE_NOW_ABSTAINS))
def test_one_table_shape_that_names_the_table_twice_abstains_today(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    question, sql = ONE_TABLE_NOW_ABSTAINS[name]
    env = rig.ask(question, _gen(sql))
    _assert_abstain(env, "unverified_join")
    assert NO_ONTOLOGY in _reasons(env), env.get("assumptions")
    assert rig.cortex.executed == []


_TOTAL_BY_CUSTOMER: dict[int, int] = {}
for _order, _customer, _amount in ORDER_ROWS:
    _TOTAL_BY_CUSTOMER[_customer] = _TOTAL_BY_CUSTOMER.get(_customer, 0) + _amount
_MEAN = sum(a for _o, _c, a in ORDER_ROWS) / len(ORDER_ROWS)
_LARGEST = max(ORDER_ROWS, key=lambda r: r[2])
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Monday"]  # of ORDER_DATES, by calendar

# name -> (question, SQL, truth rows computed from the CSV constants in Python).
ONE_TABLE_STILL_ANSWERS: dict[str, tuple[str, str, list[dict[str, Any]]]] = {
    "window": (
        "What is each order's customer total?",
        "SELECT order_id, SUM(CAST(amount AS INTEGER)) OVER (PARTITION BY customer_id) "
        f"AS customer_total FROM {ORDERS} ORDER BY order_id",
        [{"order_id": o, "customer_total": _TOTAL_BY_CUSTOMER[c]} for o, c, _a in ORDER_ROWS],
    ),
    "distinct": (
        "Which customers have ordered?",
        f"SELECT DISTINCT customer_id FROM {ORDERS} ORDER BY customer_id",
        [{"customer_id": c} for c in sorted(_TOTAL_BY_CUSTOMER)],
    ),
    "order_limit": (
        "Which order is the largest?",
        f"SELECT order_id, CAST(amount AS INTEGER) AS amount FROM {ORDERS} "
        "ORDER BY CAST(amount AS INTEGER) DESC LIMIT 1",
        [{"order_id": _LARGEST[0], "amount": _LARGEST[2]}],
    ),
    "qualify": (
        "What is each customer's largest order?",
        f"SELECT customer_id, order_id FROM {ORDERS} QUALIFY ROW_NUMBER() OVER "
        "(PARTITION BY customer_id ORDER BY CAST(amount AS INTEGER) DESC) = 1 ORDER BY customer_id",
        [
            {
                "customer_id": c,
                "order_id": max((r for r in ORDER_ROWS if r[1] == c), key=lambda r: r[2])[0],
            }
            for c in sorted(_TOTAL_BY_CUSTOMER)
        ],
    ),
    "date_function": (
        "On which weekday was each order placed?",
        f"SELECT order_id, dayname(order_date) AS weekday FROM {ORDERS} ORDER BY order_id",
        [{"order_id": r[0], "weekday": d} for r, d in zip(ORDER_ROWS, _WEEKDAYS, strict=True)],
    ),
    "cte_used_once": (
        "How many orders are above 40?",
        f"WITH big AS (SELECT * FROM (SELECT * FROM {ORDERS}) s "
        "WHERE CAST(amount AS INTEGER) > 40) "
        "SELECT COUNT(*) AS orders FROM big",
        [{"orders": sum(1 for r in ORDER_ROWS if r[2] > 40)}],
    ),
    # The allowlist's own one-table threshold shape: same column both sides.
    "threshold_same_column": (
        "Which orders are above the average amount?",
        f"SELECT order_id FROM {ORDERS} WHERE amount > (SELECT AVG(amount) FROM {ORDERS}) "
        "ORDER BY order_id",
        [{"order_id": r[0]} for r in ORDER_ROWS if r[2] > _MEAN],
    ),
    # One base table reference inside a CTE that is then used twice. The plain
    # self-join and UNION ALL above refuse; these pass the allowlist as it is.
    "cte_referenced_twice_self_join": (
        "How many order pairs share a customer?",
        f"WITH o AS (SELECT * FROM {ORDERS}) SELECT COUNT(*) AS pairs FROM o a "
        "JOIN o b ON a.customer_id = b.customer_id",
        [{"pairs": sum(1 for a in ORDER_ROWS for b in ORDER_ROWS if a[1] == b[1])}],
    ),
    "cte_referenced_twice_union_all": (
        "List every order id twice",
        f"WITH o AS (SELECT order_id FROM {ORDERS}) SELECT order_id FROM o "
        "UNION ALL SELECT order_id FROM o",
        [{"order_id": r[0]} for r in ORDER_ROWS] * 2,
    ),
}


@pytest.mark.parametrize("name", sorted(ONE_TABLE_STILL_ANSWERS))
def test_one_table_shape_still_answers_the_truth(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    question, sql, truth = ONE_TABLE_STILL_ANSWERS[name]
    env = rig.ask(question, _gen(sql))
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset(truth), env["rows"]
    text = str(env.get("text") or "")
    first = ", ".join(f"{k}={v}" for k, v in truth[0].items())
    assert first in text, text
    assert rig.cortex.executed == [sql]


# --- adversary round: DMS_ONTO_FROM_DATA=1 on a Space that HAS an ontology ---------


def test_onto_from_data_contract_ask_abstains_even_over_a_verified_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """A recorded decision, not an accident (dms#520 "fail closed there").

    Under ``DMS_ONTO_FROM_DATA=1`` the contract-ask guard never loads the Space
    ontology, so it cannot tell a verified link from a guessed one. The parent
    served this join (L2_VALIDATED, 1 row, 3 schools) with no check at all. It
    now abstains. The reason says ``no_ontology`` although this Space has one:
    that wording is untrue for this Space and is routed to the PRD agent.
    """
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    rig = od._space(tmp_path, monkeypatch, minter)
    assert load_space_ontology(rig.space_id) is not None
    assert _oracle(rig.lake, od.VERIFIED_SQL) == [{"school_count": 3}]

    def _ask(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer="There are 3 schools.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=od.VERIFIED_SQL,
            rows=[{"school_count": 3}],
        )

    monkeypatch.setattr(rig.cortex, "ask", _ask)
    monkeypatch.setattr(rig.cortex, "compute_insights", lambda *_a, **_k: {}, raising=True)
    env = rig.ask(od.VERIFIED_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    text = _assert_abstain(env, "unverified_join")
    assert NO_ONTOLOGY in _reasons(env) + text
    assert "3 schools" not in text
