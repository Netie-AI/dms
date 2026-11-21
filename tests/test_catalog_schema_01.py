"""CATALOG-SCHEMA-01 (dms#518): the catalog reads the schemas the grants name.

Before: ``read_catalog`` read schema ``main`` only. An uploaded file lands in
``bronze``, so a file Space's catalog was empty, no ``schema_context`` left on
the Insights POST, and Cortex refused before any model call.

Here a non-demo Space ingests CSV files through the real route
(``POST /v1/studio/ingest-batch``). The Insights HTTP client is replaced, so
the body asserted is the one DMS puts on the wire. The generate reply is a
stub that returns fixed SQL. Submits run on the lake under the Cortex grant
rule, so served rows are real. Every answer-path case posts
``POST /v1/chat/ask`` and asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows).

No live model and no live Cortex anywhere in this file.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.client import CortexClient
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor import schema_context as sc
from dms_executor.demo_grants import is_demo_space
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from dms_executor.ontology import demo_ontology
from dms_executor.schema_context import (
    INDEX_COLUMN_CAP,
    MAX_PROMPT_TOKENS,
    SCHEMA_CONTEXT_FIELD,
    _column_is_person,
    _column_positively_cleared,
    _forget_index,
    _serving_file,
    build_space_index,
    cached_value_index,
    estimate_tokens,
    prepare_generate_context,
    read_catalog,
)
from dms_executor.space_ontology import load_space_ontology
from fastapi.testclient import TestClient
from test_drop_route_fields_01 import _KEY, _LOOPBACK, _Ranked
from test_onto_derive_01 import (
    _assert_abstain,
    _gate_allows,
    _reasons,
    _RecordingCortex,
    make_minter,
)
from test_schema_context_no_empty_01 import _schema_prompts, _tables_in

ORDERS = "bronze.orders"
CUSTOMERS = "bronze.customers"
PAYROLL = "bronze.payroll"
GRANT = {ORDERS, CUSTOMERS}

# order_id, customer_id, amount, channel
ORDER_ROWS = [(1, 10, 100, "WEB"), (2, 10, 50, "STORE"), (3, 11, 70, "WEB"), (4, 12, 30, "WEB")]
ORDERS_HEADER = ["order_id", "customer_id", "amount", "channel"]
ORDERS_CSV = (
    ",".join(ORDERS_HEADER) + "\n" + "".join(f"{a},{b},{c},{d}\n" for a, b, c, d in ORDER_ROWS)
)
# Personal values. Each is a canary: it may appear nowhere outside the lake.
NAME_CANARIES = ["Zanthippe Quorrowmere", "Ottoline Vexhallow", "Barnabus Yewkindle"]
EMAIL_CANARIES = ["zq.canary@example.test", "ov.canary@example.test", "by.canary@example.test"]
CUSTOMER_ROWS = [
    (10, "North", NAME_CANARIES[0], EMAIL_CANARIES[0]),
    (11, "South", NAME_CANARIES[1], EMAIL_CANARIES[1]),
    (12, "North", NAME_CANARIES[2], EMAIL_CANARIES[2]),
]
CUSTOMERS_HEADER = ["customer_id", "region", "name", "email"]
CUSTOMERS_CSV = (
    ",".join(CUSTOMERS_HEADER)
    + "\n"
    + "".join(f"{a},{b},{c},{d}\n" for a, b, c, d in CUSTOMER_ROWS)
)
# Another Space's upload. Never granted to the Space under test.
PAYROLL_CSV = "staff_code,salary_band\nPAYROLLCANARY1,BANDCANARY9\n"
# A ``main`` table with the same bare name as the upload. Never granted.
MAIN_ORDERS_VALUES = ["MAINONLYCANARYA", "MAINONLYCANARYB"]
OUTSIDE = (
    "payroll",
    "staff_code",
    "salary_band",
    "PAYROLLCANARY1",
    "BANDCANARY9",
    "main_only_marker",
    *MAIN_ORDERS_VALUES,
    "_ingest_registry",
    "_land_row_log",
    "inventory",
    "transactions",
    "suppliers",
    "locations",
    "shipments",
    "alerts",
)

ONE_TABLE_Q = "What is the total order amount?"
ONE_TABLE_SQL = f"SELECT SUM(CAST(amount AS INTEGER)) AS total FROM {ORDERS}"
# Truth from the CSV constants, not from the lake and not from the SQL.
ONE_TABLE_TRUTH = sum(amount for _o, _c, amount, _ch in ORDER_ROWS)
JOIN_Q = "What is the total order amount by region?"
JOIN_SQL = (
    "SELECT c.region, SUM(CAST(o.amount AS INTEGER)) AS total "
    f"FROM {ORDERS} o JOIN {CUSTOMERS} c ON o.customer_id = c.customer_id "
    "GROUP BY c.region ORDER BY c.region"
)
JOIN_FIGURES = ("180", "70")
NO_ONTOLOGY = "unverified_join:no_ontology"

# Recorded on the parent commit (583377f, claude/integration-epic-178) with
# ``read_catalog(demo warehouse, grant, "duckdb")`` before read_catalog changed.
FINANCE_GRANT = {"locations", "inventory", "transactions", "suppliers"}
GOLDEN_FINANCE = ("05b0b25a7d53ee7e207a", "70873f04f19108108ebe")
GOLDEN_UNFILTERED = ("97972378d5a19b16c1de", "5d4469e7f6ab71b9ae7f")
GOLDEN_FINANCE_PROMPT = "c96e53f372e95b56b86a"
GOLDEN_QUESTION = "how many SKUs per category"


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


@dataclass
class _WireCortex(_RecordingCortex):
    """Insights goes through the real client, so the HTTP body is the real one."""

    inner: Any = None

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any]:
        self.insights.append({"question": question, **kw})
        got = self.inner.compute_insights(question, **kw)
        return dict(got or {})


class _Rig:
    def __init__(
        self,
        client: TestClient,
        cortex: _WireCortex,
        space_id: str,
        lake: Path,
        posts: list[dict[str, Any]],
        stub: _Ranked,
        exe: Executor,
    ) -> None:
        self.client = client
        self.cortex = cortex
        self.space_id = space_id
        self.lake = lake
        self.posts = posts
        self.stub = stub
        self.exe = exe

    def grants(self) -> set[str]:
        return set(self.exe.grantable_tables(space_id=self.space_id))

    def ask(
        self, question: str, sql: str, session: str = "ses_catalog_schema_01"
    ) -> dict[str, Any]:
        self.stub.payload["query_sql"] = sql
        self.stub.payload["sql"] = sql
        self.stub.payload["generative"] = {"ok": True, "sql": sql, "stamp": {"task": "freeroute"}}
        r = self.client.post(
            "/v1/chat/ask",
            json={"question": question, "session_id": session, "space_id": self.space_id},
        )
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env

    def prompts(self) -> list[str]:
        return [prompt for body in self.posts for prompt in _schema_prompts(body)]


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
        "DMS_CCA_CASCADE",
        "DMS_STRICT_MODEL",
        "DMS_STRICT_PROVIDER",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()


def _seed_main_orders(lake: Path) -> None:
    """A ``main.orders`` beside the upload: same bare name, other columns, other values."""
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE TABLE main.orders (main_only_marker VARCHAR, channel VARCHAR)")
        con.executemany(
            "INSERT INTO main.orders VALUES (?, ?)", [(v, v) for v in MAIN_ORDERS_VALUES]
        )
    finally:
        con.close()


def _file_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    extra: tuple[tuple[str, str], ...] = (),
) -> _Rig:
    """A non-demo Space with two uploads, beside tables it is not granted."""
    lake = tmp_path / "catalog_schema_01.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    _seed_main_orders(lake)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)

    posts: list[dict[str, Any]] = []
    stub = _Ranked(ONE_TABLE_SQL, "total")
    base = stub.client()

    class _Capture(base):  # type: ignore[misc, valid-type]
        def post(
            self,
            url: str,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            if str(url).rstrip("/").endswith("/v1/insights"):
                posts.append(dict(json or {}))
            return super().post(url, json=json, headers=headers)

    monkeypatch.setattr("cortex_client.compute.httpx.Client", _Capture)
    cortex = _WireCortex(
        warehouse=lake, payload={}, inner=CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0)
    )
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
    client = TestClient(app)

    other = app.state.space_store.create("Somebody else")
    r = client.post(
        "/v1/studio/ingest-batch",
        data={"space_id": other.id},
        files=[("files", ("payroll.csv", PAYROLL_CSV.encode(), "text/csv"))],
    )
    assert r.status_code == 200, r.text
    assert [f["table"] for f in r.json()["files"]] == [PAYROLL], r.text

    space = app.state.space_store.create("Two uploads")
    files = [("orders.csv", ORDERS_CSV), ("customers.csv", CUSTOMERS_CSV), *extra]
    r = client.post(
        "/v1/studio/ingest-batch",
        data={"space_id": space.id},
        files=[("files", (name, text.encode(), "text/csv")) for name, text in files],
    )
    assert r.status_code == 200, r.text
    assert [f["table"] for f in r.json()["files"]][:2] == [ORDERS, CUSTOMERS], r.text
    assert not is_demo_space(space.id)
    assert load_space_ontology(space.id) is None, "the fixture Space must have no ontology"
    # The ingest schedules a background index build. Wait it out, then start
    # every case without an index, so a ready one is only there when built here.
    sc.stop_index_builds()
    _forget_index(space.id)
    return _Rig(client, cortex, space.id, lake, posts, stub, exe)


def _columns(body: dict[str, Any], table: str) -> list[tuple[str, str]]:
    found = [d for d in body["datasets"] if d["name"] == table]
    assert len(found) == 1, [d["name"] for d in body["datasets"]]
    return [(str(c["name"]), str(c["type"])) for c in found[0]["columns"]]


def _assert_nothing_outside(text: str) -> None:
    for marker in OUTSIDE:
        assert not re.search(rf"\b{re.escape(marker)}\b", text), marker


# --- acceptance 1: the reproduction -----------------------------------------------


def test_file_space_catalog_names_its_tables_and_landed_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """On the parent commit the catalog is empty and the fingerprint is ''."""
    rig = _file_space(tmp_path, monkeypatch, minter)
    grant = rig.grants()
    assert grant == GRANT
    fingerprint, body = read_catalog(rig.lake, grant, "duckdb")
    assert fingerprint, "a granted file Space produced no catalog"
    assert [d["name"] for d in body["datasets"]] == [CUSTOMERS, ORDERS]
    assert _columns(body, ORDERS) == [
        ("order_id", "BIGINT"),
        ("customer_id", "BIGINT"),
        ("amount", "BIGINT"),
        ("channel", "VARCHAR"),
    ]
    assert _columns(body, CUSTOMERS) == [
        ("customer_id", "BIGINT"),
        ("region", "VARCHAR"),
        ("name", "VARCHAR"),
        ("email", "VARCHAR"),
    ]
    # Names, types and keys only: no value came with the shape.
    for dataset in body["datasets"]:
        for col in dataset["columns"]:
            assert col["values"] == [] and col["samples"] == [] and col["distinct"] is None


def test_ask_sends_the_space_tables_to_cortex_and_answers_the_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """On the parent commit the Insights body carries no schema_context at all."""
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(ONE_TABLE_Q, ONE_TABLE_SQL)
    assert rig.posts, "the ask never reached Insights"
    prompts = rig.prompts()
    assert prompts, "no schema_context on the Insights body"
    for prompt in prompts:
        assert set(_tables_in(prompt)) == GRANT, prompt
        for column in (*ORDERS_HEADER, *CUSTOMERS_HEADER):
            assert re.search(rf"^- {column} ", prompt, re.M), (column, prompt)
        assert "- amount BIGINT" in prompt, prompt
        assert estimate_tokens(prompt) <= MAX_PROMPT_TOKENS
    # The customer's answer: text and rows equal to the precomputed truth.
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert env["rows"] == [{"total": ONE_TABLE_TRUTH}]
    assert [v["value"] for v in env["values"]] == [float(ONE_TABLE_TRUTH)]
    assert f"total={ONE_TABLE_TRUTH}" in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == [ONE_TABLE_SQL]


# --- acceptance 2: only what the grant names --------------------------------------


def test_nothing_outside_the_grant_leaves_on_the_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Another Space's upload, the registry, demo tables, a same-named main table."""
    rig = _file_space(tmp_path, monkeypatch, minter)
    assert build_space_index(rig.lake, rig.space_id, rig.grants(), "duckdb") == ""
    env = rig.ask(ONE_TABLE_Q, ONE_TABLE_SQL)
    assert rig.prompts()
    for body in rig.posts:
        for prompt in _schema_prompts(body):
            assert set(_tables_in(prompt)) == GRANT, prompt
            _assert_nothing_outside(prompt)
    _assert_nothing_outside(json.dumps(env))
    record = _serving_file(rig.lake, rig.space_id).read_text(encoding="utf-8")
    _assert_nothing_outside(record)
    _assert_nothing_outside(json.dumps(cached_value_index(rig.space_id)))


def _two_schema_lake(tmp_path: Path) -> Path:
    """No app: one bare name in three schemas, each with its own columns and values."""
    lake = tmp_path / "two_schema.duckdb"
    dw._SEEDED.discard(str(lake.resolve()))
    ensure_demo_warehouse(lake)
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE SCHEMA bronze")
        con.execute("CREATE SCHEMA landing_zone_7")
        con.execute("CREATE TABLE main.orders (main_only_marker VARCHAR, status VARCHAR)")
        con.execute("CREATE TABLE bronze.orders (amount BIGINT, status VARCHAR)")
        con.execute("CREATE TABLE bronze.unticked (hidden_col VARCHAR)")
        con.execute("CREATE TABLE landing_zone_7.orders (third_marker VARCHAR, status VARCHAR)")
        con.execute("INSERT INTO main.orders VALUES ('m', 'MAINONLYCANARYA')")
        con.execute("INSERT INTO bronze.orders VALUES (5, 'WEB'), (6, 'STORE')")
        con.execute("INSERT INTO bronze.unticked VALUES ('UNTICKEDCANARY')")
        con.execute("INSERT INTO landing_zone_7.orders VALUES ('t', 'THIRDCANARY')")
    finally:
        con.close()
    return lake


@pytest.mark.parametrize(
    ("grant", "expected"),
    [
        ({"bronze.orders"}, {"bronze.orders": ["amount", "status"]}),
        ({"orders"}, {"orders": ["main_only_marker", "status"]}),
        ({"main.orders"}, {"orders": ["main_only_marker", "status"]}),
        ({"landing_zone_7.orders"}, {"landing_zone_7.orders": ["third_marker", "status"]}),
        (
            {"orders", "bronze.orders"},
            {"orders": ["main_only_marker", "status"], "bronze.orders": ["amount", "status"]},
        ),
        (
            {"bronze.orders", "inventory"},
            {
                "bronze.orders": ["amount", "status"],
                "inventory": None,
            },
        ),
        ({"bronze.nope"}, {}),
        ({"nope.orders"}, {}),
        (set(), {}),
    ],
    ids=[
        "bronze-only",
        "bare-is-main",
        "main-qualified",
        "a-schema-no-code-names",
        "both-kept-apart",
        "bronze-grant-does-not-grant-main-twin",
        "missing-table",
        "missing-schema",
        "empty-grant",
    ],
)
def test_same_bare_name_in_two_schemas_is_kept_apart(
    tmp_path: Path, grant: set[str], expected: dict[str, list[str] | None]
) -> None:
    """Each table appears only if granted, under its own name, with its own columns."""
    lake = _two_schema_lake(tmp_path)
    fingerprint, body = read_catalog(lake, grant, "duckdb")
    got = {d["name"]: [c["name"] for c in d["columns"]] for d in body["datasets"]}
    assert set(got) == set(expected)
    for name, columns in expected.items():
        if columns is not None:
            assert got[name] == columns
    assert bool(fingerprint) is bool(expected)


def test_two_grants_with_one_bare_name_have_different_fingerprints(tmp_path: Path) -> None:
    """The index is keyed by fingerprint: two schemas may not share one."""
    lake = _two_schema_lake(tmp_path)
    prints = {
        read_catalog(lake, grant, "duckdb")[0]
        for grant in ({"orders"}, {"bronze.orders"}, {"landing_zone_7.orders"})
    }
    assert len(prints) == 3 and "" not in prints


def test_index_reads_values_from_the_granted_schema_only(tmp_path: Path) -> None:
    """``status`` is in three schemas and the mask clears that name, so it may hint.

    Only the granted table's values are read.
    """
    lake = _two_schema_lake(tmp_path)
    space = "space_catalog_schema_index"
    _forget_index(space)
    try:
        assert build_space_index(lake, space, {"bronze.orders"}, "duckdb") == ""
        index = cached_value_index(space)
        assert index == {"bronze.orders.status": ("STORE", "WEB")}
        ctx = prepare_generate_context(
            {},
            question="total amount for the WEB status",
            serving=lake,
            grantable={"bronze.orders"},
            dialect="duckdb",
            space_id=space,
        )
        prompt = str(ctx[SCHEMA_CONTEXT_FIELD])
        assert _tables_in(prompt) == ["bronze.orders"]
        # Rule 12: the hint names the stored value on the right table and column.
        assert "- bronze.orders.status = WEB" in prompt, prompt
        for canary in ("MAINONLYCANARYA", "THIRDCANARY", "UNTICKEDCANARY", "hidden_col"):
            assert canary not in prompt
            assert canary not in json.dumps(index)
    finally:
        _forget_index(space)


class _SpyCon:
    def __init__(self, con: Any, log: list[tuple[str, list[Any]]], seen: list[str]) -> None:
        self._con = con
        self._log = log
        self._seen = seen
        self.rows: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> Any:
        self._log.append((sql, list(params or [])))
        cur = self._con.execute(sql, params) if params else self._con.execute(sql)
        self.rows = cur.fetchall()
        self._seen.extend(str(row[0]) for row in self.rows)
        return self

    def fetchall(self) -> list[Any]:
        return self.rows

    def close(self) -> None:
        self._con.close()


@pytest.mark.parametrize(
    ("grant", "schemas"),
    [
        ({"bronze.orders"}, {"bronze"}),
        (set(FINANCE_GRANT), {"main"}),
        ({"landing_zone_7.orders", "inventory"}, {"landing_zone_7", "main"}),
        (None, {"main"}),
    ],
    ids=["file", "demo", "mixed", "unfiltered"],
)
def test_catalog_reads_only_the_schemas_the_grants_name_and_no_table_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    grant: set[str] | None,
    schemas: set[str],
) -> None:
    """Structural: the schema set is bound from the grants, and no body is read."""
    lake = _two_schema_lake(tmp_path)
    log: list[tuple[str, list[Any]]] = []
    real = sc._open_serving
    seen: list[str] = []
    monkeypatch.setattr(sc, "_open_serving", lambda path: _SpyCon(real(path), log, seen))
    fingerprint, _body = read_catalog(lake, grant, "duckdb")
    assert fingerprint and log
    # Every statement selects the schema first. No row of any other schema
    # reached Python: the engine filtered, not a later grant check alone.
    assert set(seen) == schemas, set(seen)
    for sql, params in log:
        sources = re.findall(r"\bFROM\s+([A-Za-z_][\w.]*(?:\(\))?)", sql, re.I)
        assert sources and set(sources) <= {
            "information_schema.columns",
            "duckdb_tables()",
            "duckdb_constraints()",
        }, sql
        # The schemas are bound values from the grant, never text in the SQL.
        assert set(params) == schemas, (sql, params)
        for name in ("bronze", "landing_zone_7", "'main'"):
            assert name not in sql, sql


# --- acceptance 3: the join gate holds with the catalog visible -------------------


def test_two_table_question_still_abstains_no_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(JOIN_Q, JOIN_SQL)
    # The model was shown both tables. The gate is what refuses the join.
    prompts = rig.prompts()
    assert prompts and all(set(_tables_in(p)) == GRANT for p in prompts)
    text = _assert_abstain(env, "unverified_join")
    assert NO_ONTOLOGY in _reasons(env) or NO_ONTOLOGY in text, (text, env.get("assumptions"))
    for figure in JOIN_FIGURES:
        assert not re.search(rf"\b{figure}\b", text), text
    assert env["rows"] == [] and env["values"] == []
    assert env.get("sql_used") in (None, "")
    assert rig.cortex.executed == [], "the refused SQL reached Cortex submit"


def test_one_table_date_function_question_abstains_today_base_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Pinned, not wanted: a correct one-table answer is refused in a file Space.

    The no-ontology one-read rule (JOIN-HOLE-01, dms#527) refuses a function
    sqlglot cannot type, here ``dayname``. The same case is red on the base
    (``test_connect_ask_03::test_one_table_shape_still_answers_the_truth
    [date_function]``). With the catalog visible a real model will write this
    SQL, so the false abstain is now reachable. This ticket does not change
    the gate. When the gate is fixed this test goes red: assert the weekdays.
    """
    visits = "visit_id,visit_date\n1,2026-01-05\n2,2026-01-20\n"
    rig = _file_space(tmp_path, monkeypatch, minter, extra=(("visits.csv", visits),))
    assert "bronze.visits" in rig.grants()
    sql = "SELECT visit_id, dayname(visit_date) AS weekday FROM bronze.visits ORDER BY visit_id"
    env = rig.ask("On which weekday was each visit?", sql)
    assert any("bronze.visits" in _tables_in(p) for p in rig.prompts())
    text = _assert_abstain(env, "unverified_join")
    assert NO_ONTOLOGY in _reasons(env) or NO_ONTOLOGY in text, (text, env.get("assumptions"))
    # No figure and no row: a refusal, never a wrong answer.
    assert env["rows"] == [] and env["values"] == []
    assert rig.cortex.executed == []


# --- personal values stay in the lake ---------------------------------------------


def test_personal_values_in_a_bronze_column_never_leave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The same functions decide for ``bronze.customers`` as for ``customers``."""
    assert _column_is_person(CUSTOMERS, "name") is _column_is_person("customers", "name") is True
    assert _column_positively_cleared(CUSTOMERS, "name", tagged=False) is False
    assert _column_positively_cleared(CUSTOMERS, "email", tagged=False) is False
    rig = _file_space(tmp_path, monkeypatch, minter)
    assert build_space_index(rig.lake, rig.space_id, rig.grants(), "duckdb") == ""
    envs = [
        rig.ask(ONE_TABLE_Q, ONE_TABLE_SQL),
        rig.ask(f"orders for {NAME_CANARIES[0]}", ONE_TABLE_SQL, session="ses_catalog_named"),
    ]
    assert rig.prompts()
    record = _serving_file(rig.lake, rig.space_id).read_text(encoding="utf-8")
    index = json.dumps(cached_value_index(rig.space_id))
    samples = json.dumps(sc._space_samples(rig.space_id))
    prompts = "\n".join(rig.prompts())
    for canary in (*NAME_CANARIES, *EMAIL_CANARIES):
        for part in (canary, *canary.split()):
            assert part not in prompts, part
            assert part not in record, part
            assert part not in index, part
            assert part not in samples, part
    # The second question itself names the first canary, and an envelope may
    # echo its own question. Every other canary is in the lake only.
    for canary in (*NAME_CANARIES[1:], *EMAIL_CANARIES):
        for env in envs:
            assert canary not in json.dumps(env)
    # The index did read the Space's own non-personal column, from bronze.
    assert cached_value_index(rig.space_id).get(f"{ORDERS}.channel") == ("STORE", "WEB")


# --- caps hold on a wide and a high-cardinality upload ----------------------------


def test_wide_and_high_cardinality_uploads_hold_the_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    wide_header = [f"metric_col_{i:03d}" for i in range(120)]
    wide = ",".join(wide_header) + "\n" + ",".join(str(i) for i in range(120)) + "\n"
    tall = "code,qty\n" + "".join(f"CODE{i:05d},{i}\n" for i in range(INDEX_COLUMN_CAP + 50))
    rig = _file_space(tmp_path, monkeypatch, minter, extra=(("wide.csv", wide), ("tall.csv", tall)))
    grant = rig.grants()
    assert grant == GRANT | {"bronze.wide", "bronze.tall"}
    assert build_space_index(rig.lake, rig.space_id, grant, "duckdb") == ""
    # Past the column cap a text column indexes nothing.
    assert "bronze.tall.code" not in cached_value_index(rig.space_id)
    env = rig.ask(ONE_TABLE_Q, ONE_TABLE_SQL)
    prompts = rig.prompts()
    assert prompts
    for prompt in prompts:
        assert estimate_tokens(prompt) <= MAX_PROMPT_TOKENS, estimate_tokens(prompt)
        assert set(_tables_in(prompt)) <= grant
        assert "CODE00001" not in prompt
    assert env["rows"] == [{"total": ONE_TABLE_TRUTH}]


# --- acceptance 4: Spaces that worked before --------------------------------------


def test_demo_catalog_is_byte_for_byte_the_parent_commit(tmp_path: Path) -> None:
    lake = tmp_path / "golden.duckdb"
    dw._SEEDED.discard(str(lake.resolve()))
    ensure_demo_warehouse(lake)
    fingerprint, body = read_catalog(lake, set(FINANCE_GRANT), "duckdb")
    assert (fingerprint, _sha(body)) == GOLDEN_FINANCE
    fingerprint, body = read_catalog(lake, None, "duckdb")
    assert (fingerprint, _sha(body)) == GOLDEN_UNFILTERED
    ctx = prepare_generate_context(
        {},
        question=GOLDEN_QUESTION,
        serving=lake,
        grantable=set(FINANCE_GRANT),
        ontology=demo_ontology(lake),
        dialect="duckdb",
        space_id=None,
    )
    prompt = str(ctx[SCHEMA_CONTEXT_FIELD])
    assert hashlib.sha256(prompt.encode()).hexdigest()[:20] == GOLDEN_FINANCE_PROMPT


_ANSWER_KEYS = ("badge", "abstained", "text", "rows", "values", "sql_used", "assumptions")


def test_sql_source_space_with_an_ontology_serves_the_same_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """On the parent this Space's catalog was empty, so flag on equalled flag off.

    The answer-path fields must still be equal with the catalog now visible:
    a verified-link join serves, and an unverified one abstains.
    """
    import test_onto_derive_01 as od

    served_sql = (
        f"SELECT d.name AS district, COUNT(*) AS schools FROM {od.SCHOOLS} s "
        f"JOIN {od.DISTRICTS} d ON s.district_id = d.district_id GROUP BY d.name ORDER BY d.name"
    )
    refused_sql = (
        f"SELECT COUNT(*) AS n FROM {od.ENROLL} e JOIN {od.SCHOOLS} s ON e.school_id = s.school_id"
    )
    seen: dict[str, list[dict[str, Any]]] = {}
    for flag in ("0", "1"):
        monkeypatch.setenv("DMS_SCHEMA_CONTEXT", flag)
        space = od._space(tmp_path / f"flag{flag}", monkeypatch, minter)
        sc.stop_index_builds()
        _forget_index(space.space_id)
        envs = [
            space.ask("How many schools per district?", {"query_sql": served_sql}),
            space.ask("How many enrollments have a school?", {"query_sql": refused_sql}),
        ]
        seen[flag] = [{key: env.get(key) for key in _ANSWER_KEYS} for env in envs]
        if flag == "1":
            sent = [str(kw.get("ontology")) for kw in space.cortex.insights]
            assert any(od.SCHOOLS in text for text in sent)
    assert seen["0"][0]["badge"] == "L2_VALIDATED" and seen["0"][0]["rows"], seen["0"][0]
    assert seen["0"][1]["abstained"] is True and seen["0"][1]["rows"] == [], seen["0"][1]
    assert seen["1"] == seen["0"]
