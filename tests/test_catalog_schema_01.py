"""CATALOG-SCHEMA-01: the catalog reads the schemas the grants name.

A file Space lands in ``bronze``. ``read_catalog`` used to read ``main`` only,
so the model was sent no columns. Grants name the schema. ``main`` stays a
bare name. Anything else keeps the grant's ``schema.table``. No table body
is read here. A granted join of those tables with no stored ontology is
not an abstain. ``DMS_SCHEMA_CONTEXT`` asks the user to confirm the catalog
keys and the SQL, and Yes files a proposed ontology link. The flag is off
by default. An ungranted name stays ``sql_relation_not_granted``.

Refs #518.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cortex_client.client import CortexClient
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, connect_serving, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import JOIN_CONFIRM_CODE, _no_ontology_file_join
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.schema_context import (
    INDEX_COLUMN_CAP,
    _forget_index,
    _serving_file,
    build_schema_context,
    build_space_index,
    join_key_facts,
    lookup_schema,
    read_catalog,
)

_LOOPBACK = "http://127.0.0.1:9"
_KEY = "ov_test_catalog_schema"
SPACE = "11111111-1111-4111-8111-111111111111"
CANARY_EMAIL = "ada@example.com"
CANARY_NAME = "Ada Lovelace"
ORDERS = b"order_id,customer_id,amount\n1,1,100\n2,1,50\n3,2,70\n4,2,30\n"
CUSTOMERS = (
    b"customer_id,customer_name,email\n"
    b"1,Ada Lovelace,ada@example.com\n"
    b"2,Bob Stone,bob@example.com\n"
)
ONE_TABLE_SQL = "SELECT SUM(amount) AS total FROM bronze.orders"
JOIN_SQL = (
    "SELECT SUM(o.amount) AS total FROM bronze.orders o "
    "JOIN bronze.customers c ON o.customer_id = c.customer_id"
)
QUESTION = "What is the sum of amount?"


def _exec(path: Path, sql: str) -> None:
    con = connect_file(path)
    try:
        con.execute(sql)
    finally:
        con.close()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.duckdb"
    _exec(path, "CREATE SCHEMA IF NOT EXISTS bronze")
    return path


def _names(body: dict[str, Any]) -> list[str]:
    return [str(item["name"]) for item in body["datasets"]]


def _columns(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    for item in body["datasets"]:
        if item["name"] == name:
            return list(item["columns"])
    raise AssertionError(name)


def test_no_ontology_file_join_ignores_main_and_one_table() -> None:
    assert _no_ontology_file_join(ONE_TABLE_SQL) is None
    assert (
        _no_ontology_file_join(
            "SELECT 1 FROM inventory JOIN transactions ON inventory.sku = transactions.sku"
        )
        is None
    )
    assert (
        _no_ontology_file_join(
            "SELECT 1 FROM main.inventory JOIN main.transactions USING (sku)"
        )
        is None
    )
    assert _no_ontology_file_join(
        "WITH c AS (SELECT * FROM bronze.orders) SELECT * FROM c"
    ) is None
    assert _no_ontology_file_join(JOIN_SQL) == JOIN_CONFIRM_CODE


def test_bronze_grant_lists_that_schema_and_not_the_main_twin(tmp_path: Path) -> None:
    path = _lake(tmp_path)
    _exec(
        path,
        """
        CREATE TABLE main.orders (sku VARCHAR);
        CREATE TABLE bronze.orders (
            amount INTEGER, _src INTEGER, _ingest_id VARCHAR
        );
        CREATE TABLE bronze.customers (
            customer_id INTEGER PRIMARY KEY, email VARCHAR
        );
        CREATE TABLE bronze.secret (id INTEGER);
        CREATE SCHEMA IF NOT EXISTS silver;
        CREATE TABLE silver.units (id INTEGER, qty INTEGER);
        """,
    )
    fingerprint, body = read_catalog(
        path, {"bronze.orders", "bronze.customers"}, "duckdb"
    )
    assert fingerprint
    assert _names(body) == ["bronze.customers", "bronze.orders"]
    orders = _columns(body, "bronze.orders")
    assert [col["name"] for col in orders] == ["amount"]
    for col in orders:
        assert col["values"] == []
        assert col["samples"] == []
    key = next(col for col in _columns(body, "bronze.customers") if col["name"] == "customer_id")
    assert key["primary_key"] is True
    bare, bare_body = read_catalog(path, None, "duckdb")
    assert bare
    assert "orders" in _names(bare_body)
    assert all("." not in name for name in _names(bare_body))
    silver_fp, silver = read_catalog(path, {"silver.units"}, "duckdb")
    assert silver_fp
    assert _names(silver) == ["silver.units"]
    empty_fp, empty = read_catalog(path, set(), "duckdb")
    assert empty_fp == ""
    assert empty == {"datasets": [], "relationships": [], "dialect": "duckdb"}


def test_demo_main_catalog_matches_the_main_only_read(tmp_path: Path) -> None:
    """A demo grant is bare ``main`` names. The fingerprint rows stay those."""
    path = tmp_path / "demo.duckdb"
    ensure_demo_warehouse(path)
    grant = {"locations", "inventory", "transactions", "suppliers"}
    fingerprint, body = read_catalog(path, grant, "duckdb")
    again, _again_body = read_catalog(path, grant, "duckdb")
    assert fingerprint == again
    assert _names(body) == sorted(grant)
    con = connect_serving(path)
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'main' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    finally:
        con.close()
    expected: dict[str, list[tuple[str, str]]] = {}
    for table_name, column_name, data_type in rows:
        table = str(table_name)
        if table not in grant:
            continue
        expected.setdefault(table, []).append((str(column_name), str(data_type or "")))
    got = {
        str(item["name"]): [(str(col["name"]), str(col["type"])) for col in item["columns"]]
        for item in body["datasets"]
    }
    assert got == expected


def test_index_reads_the_granted_schema_and_drops_personal_values(tmp_path: Path) -> None:
    path = _lake(tmp_path)
    _exec(
        path,
        """
        CREATE TABLE main.customers (customer_name VARCHAR, email VARCHAR, city VARCHAR);
        INSERT INTO main.customers VALUES ('Main Person', 'main@example.com', 'MAINONLY');
        CREATE TABLE bronze.customers (customer_name VARCHAR, email VARCHAR, city VARCHAR);
        INSERT INTO bronze.customers VALUES ('Ada Lovelace', 'ada@example.com', 'BRONZECITY');
        """,
    )
    grants = {"bronze.customers"}
    _forget_index(SPACE)
    try:
        assert build_space_index(path, SPACE, grants, "duckdb") == ""
        schema, stamp = lookup_schema(path, grants, "duckdb", SPACE)
        assert stamp == ""
        built = build_schema_context(
            "how many customers", schema, grantable=grants, space_id=SPACE
        )
        blob = " ".join(
            [
                built.prompt,
                built.envelope_prompt,
                str(schema),
                _serving_file(path, SPACE).read_text(encoding="utf-8"),
            ]
        )
        assert CANARY_EMAIL not in blob
        assert CANARY_NAME not in blob
        assert "main@example.com" not in blob
        assert "Main Person" not in blob
        assert "MAINONLY" not in blob
        assert "BRONZECITY" in str(schema)
        assert "bronze.customers" in built.prompt
    finally:
        _forget_index(SPACE)


def test_distinct_cap_still_holds(tmp_path: Path) -> None:
    assert INDEX_COLUMN_CAP == 500
    path = _lake(tmp_path)
    _exec(
        path,
        "CREATE TABLE bronze.codes (code VARCHAR); "
        "INSERT INTO bronze.codes SELECT 'v' || x::VARCHAR FROM generate_series(0, 500) AS t(x)",
    )
    grants = {"bronze.codes"}
    _forget_index(SPACE)
    try:
        assert build_space_index(path, SPACE, grants, "duckdb") == ""
        schema, stamp = lookup_schema(path, grants, "duckdb", SPACE)
        assert stamp == ""
        col = _columns(schema, "bronze.codes")[0]
        assert col.get("capped") is True
        assert col.get("values") == []
    finally:
        _forget_index(SPACE)


class _Capture:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.posts: list[dict[str, Any]] = []

    def client(self) -> type:
        stub = self

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def post(
                self,
                url: str,
                json: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None,
            ) -> Any:
                if str(url).rstrip("/").endswith("/v1/insights"):
                    stub.posts.append(dict(json or {}))
                    return _Resp(
                        200,
                        {
                            "ok": True,
                            "status": "ABSTAIN",
                            "phase": "generate",
                            "values": [],
                            "query_sql": stub.sql,
                            "sql": stub.sql,
                            "plan_origin": "generate_sql",
                            "model_called": True,
                            "generative": {"ok": True, "sql": stub.sql},
                        },
                    )
                return _Resp(200, {"ok": True, "status": "ABSTAIN", "values": []})

            def get(self, url: str, params: dict[str, Any] | None = None, **k: Any) -> Any:
                return _Resp(200, {"ok": True, "phase": "ontology", "ontology": {}})

            def close(self) -> None:
                return None

        return _Client


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.content = b"{}"

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _Submit:
    def __init__(self, db: Path, inner: CortexClient) -> None:
        self._db = db
        self._inner = inner

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        return self._inner.compute_insights(question, **kwargs)

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_catalog_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_serving(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_catalog_sql", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_catalog", hash="hash_catalog")


def _minter() -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint
    minter.fetch_intermediate = lambda: None
    minter.close = lambda: None
    minter.invalidate = lambda *_a, **_k: None
    return minter


def _prompts(body: dict[str, Any]) -> list[str]:
    found: list[str] = []
    if isinstance(body.get("schema_context"), str):
        found.append(body["schema_context"])
    onto = body.get("ontology")
    if isinstance(onto, dict) and isinstance(onto.get("schema_context"), str):
        found.append(onto["schema_context"])
    return found


def _allow_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.spaces as spaces_routes
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    def allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    import dms_api.routes.chat as chat_routes

    monkeypatch.setattr(studio_routes, "compliance_gate", allow)
    monkeypatch.setattr(spaces_routes, "compliance_gate", allow)
    monkeypatch.setattr(chat_routes, "compliance_gate", allow)


def _file_app(path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    """One app: the Space row and the bronze tables live together."""
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_CCA_CASCADE",
        "DMS_AI_FIRST",
        "DMS_ASK_LADDER",
        "DMS_AI_CLARIFY",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    _allow_writes(monkeypatch)
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)
    made = client.post("/v1/spaces", json={"name": "File orders"})
    assert made.status_code == 201, made.text
    space_id = str(made.json()["space"]["id"])
    other = client.post("/v1/spaces", json={"name": "Other secrets"})
    assert other.status_code == 201, other.text
    other_id = str(other.json()["space"]["id"])
    got = client.post(
        "/v1/studio/ingest-batch",
        data={"space_id": space_id},
        files=[
            ("files", ("orders.csv", ORDERS, "text/csv")),
            ("files", ("customers.csv", CUSTOMERS, "text/csv")),
        ],
    )
    assert got.status_code == 200, got.text
    assert got.json()["ingested"] == 2
    hidden = client.post(
        "/v1/studio/ingest-batch",
        data={"space_id": other_id},
        files=[("files", ("secrets.csv", b"id,note\n1,hidden\n", "text/csv"))],
    )
    assert hidden.status_code == 200, hidden.text
    return app, space_id


def _ask(
    app: Any,
    path: Path,
    sql: str,
    space_id: str,
    monkeypatch: pytest.MonkeyPatch,
    question: str = QUESTION,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from dms_api.settings import Settings, get_settings
    from fastapi.testclient import TestClient

    stub = _Capture(sql)
    monkeypatch.setattr("cortex_client.compute.httpx.Client", stub.client())
    inner = CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0)
    exe = Executor(cortex=_Submit(path, inner), minter=_minter(), warehouse_path=path)
    app.state.ask_service = exe
    app.state.cortex = inner
    app.dependency_overrides[get_settings] = lambda: Settings(
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    res = TestClient(app).post(
        "/v1/chat/ask",
        json={
            "question": question,
            "session_id": "ses_catalog_schema",
            "space_id": space_id,
            "ask_path": "generative",
        },
    )
    assert res.status_code == 200, res.text
    return res.json(), stub.posts


@pytest.fixture()
def file_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, str]:
    path = tmp_path / "file_space.duckdb"
    app, space_id = _file_app(path, monkeypatch)
    return app, path, space_id


def test_ask_sends_the_file_tables_and_answers_the_one_table_truth(
    file_space: tuple[Any, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, path, space_id = file_space
    grants = set(Executor(cortex=None, warehouse_path=path).grantable_tables(space_id=space_id))
    fingerprint, body = read_catalog(path, grants, "duckdb")
    assert fingerprint
    assert "bronze.orders" in _names(body)
    assert "bronze.customers" in _names(body)
    assert "bronze.secrets" not in _names(body)
    assert "inventory" not in _names(body)
    env, posts = _ask(app, path, ONE_TABLE_SQL, space_id, monkeypatch)
    assert_envelope_valid(env)
    assert env.get("abstained") is not True, env.get("text")
    rows = env.get("rows") or []
    assert len(rows) == 1
    assert int(rows[0]["total"]) == 250
    assert "250" in str(env.get("text") or "")
    assert posts
    listed = " ".join(prompt for post in posts for prompt in _prompts(post))
    assert "bronze.orders" in listed
    assert "bronze.customers" in listed
    assert "bronze.secrets" not in listed
    assert CANARY_EMAIL not in listed
    assert CANARY_NAME not in listed
    assert CANARY_EMAIL not in str(env.get("text") or "")


def test_catalog_foreign_key_is_a_proposed_join_key(tmp_path: Path) -> None:
    path = _lake(tmp_path)
    _exec(
        path,
        """
        CREATE TABLE bronze.customers (customer_id INTEGER PRIMARY KEY, email VARCHAR);
        CREATE TABLE bronze.orders (
            order_id INTEGER,
            customer_id INTEGER,
            amount INTEGER,
            FOREIGN KEY (customer_id) REFERENCES bronze.customers(customer_id)
        );
        """,
    )
    keys = join_key_facts(
        path,
        {"bronze.orders", "bronze.customers"},
        JOIN_SQL,
        "duckdb",
    )
    assert keys
    key = keys[0]
    assert key["left_table"] == "bronze.orders"
    assert key["left_column"] == "customer_id"
    assert key["right_table"] == "bronze.customers"
    assert key["right_column"] == "customer_id"
    assert key["right_key"] == "primary_key"
    assert key["reported"] == "foreign_key"
    assert "INT" in key["left_type"].upper()


def test_two_table_sql_is_served_when_schema_context_is_off(
    file_space: tuple[Any, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    app, path, space_id = file_space
    env, posts = _ask(app, path, JOIN_SQL, space_id, monkeypatch)
    assert_envelope_valid(env)
    assert env.get("abstained") is not True, env.get("text")
    assert env.get("reconfirm") in (None, {})
    rows = env.get("rows") or []
    assert len(rows) == 1
    assert int(rows[0]["total"]) == 250
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert "unverified_join" not in assumptions
    assert JOIN_CONFIRM_CODE not in assumptions
    assert posts
    listed = " ".join(str(post) for post in posts)
    assert CANARY_EMAIL not in listed


def test_schema_context_confirms_a_granted_join_and_files_the_link(
    file_space: tuple[Any, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_api.settings import Settings, get_settings
    from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
    from dms_executor.reconfirm import _reset_reconfirm
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.delenv("DMS_RECONFIRM_RUN_HELD", raising=False)
    reset_ontology_data_store()
    _reset_reconfirm()
    app, path, space_id = file_space
    env, _posts = _ask(app, path, JOIN_SQL, space_id, monkeypatch)
    assert_envelope_valid(env)
    assert env.get("abstained") is True
    assert not env.get("rows")
    assert not env.get("values")
    assert "250" not in str(env.get("text") or "")
    offer = env.get("reconfirm")
    assert isinstance(offer, dict)
    assert offer.get("token")
    definition = str(offer.get("definition") or "")
    assert "bronze.orders.customer_id" in definition
    assert "bronze.customers.customer_id" in definition
    assert "SELECT SUM(o.amount)" in definition
    assert "proposed ontology link" in definition
    app.dependency_overrides[get_settings] = lambda: Settings(
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    confirmed = TestClient(app).post(
        "/v1/chat/ask/confirm",
        json={"token": offer["token"], "yes": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert_envelope_valid(body)
    assert body.get("abstained") is not True, body.get("text")
    rows = body.get("rows") or []
    assert len(rows) == 1
    assert int(rows[0]["total"]) == 250
    proposed = ontology_data_store().diff().get("proposed_only") or []
    assert any(item.get("kind") == "link" and item.get("status") == "proposed" for item in proposed)
    assert all(item.get("status") != "approved" for item in proposed)
    reset_ontology_data_store()


def _confirm(app: Any, token: str) -> dict[str, Any]:
    from dms_api.settings import Settings, get_settings
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_settings] = lambda: Settings(
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    res = TestClient(app).post(
        "/v1/chat/ask/confirm",
        json={"token": token, "yes": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert_envelope_valid(body)
    return body


def test_confirmed_join_gate_miss_asks_back(
    file_space: tuple[Any, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yes runs the submit gates. A qualifier or currency miss is not a number."""
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.delenv("DMS_RECONFIRM_RUN_HELD", raising=False)
    app, path, space_id = file_space
    cases = (
        ("What is the sum of amount by month?", "unhonored_qualifier"),
        ("What is the sum of amount in USD and EUR?", "currency_mismatch"),
    )
    for question, needle in cases:
        env, _posts = _ask(app, path, JOIN_SQL, space_id, monkeypatch, question)
        assert_envelope_valid(env)
        offer = env.get("reconfirm")
        assert isinstance(offer, dict) and offer.get("token")
        body = _confirm(app, str(offer["token"]))
        assert body.get("abstained") is True
        assert not body.get("rows")
        assert not body.get("values")
        assert "250" not in str(body.get("text") or "")
        again = body.get("reconfirm")
        assert isinstance(again, dict) and again.get("token")
        blob = " ".join(
            [
                str(again.get("definition") or ""),
                str(again.get("reason_code") or ""),
                str(body.get("text") or ""),
                " ".join(str(item) for item in body.get("assumptions") or []),
            ]
        )
        assert needle in blob, blob


def test_ungranted_name_stays_a_grant_refusal_under_schema_context(
    file_space: tuple[Any, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    app, path, space_id = file_space
    sql = (
        "SELECT o.amount FROM bronze.orders o "
        "JOIN bronze.secrets s ON o.customer_id = s.id"
    )
    env, _posts = _ask(app, path, sql, space_id, monkeypatch)
    assert_envelope_valid(env)
    assert env.get("abstained") is True
    assert not env.get("rows")
    offer = env.get("reconfirm")
    assert not (isinstance(offer, dict) and offer.get("token"))
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert "sql_relation_not_granted" in assumptions or "ungranted" in assumptions
    assert "250" not in str(env.get("text") or "")
