"""R2-CONNECT-CORE-01: persisted connections, probe, introspect, live read.

Flag off stays on the old connect book. Engine cases run when
``DMS_CONNECT_V2_ENGINES=1`` (the connect-v2 CI job). The pytest shards
leave that unset and skip those cases on purpose.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.control_plane.connect_grants import reset as reset_grants
from dms_core.control_plane.connect_secrets import body_violation
from dms_core.control_plane.connect_store import (
    ConnectStore,
    ConnectStoreError,
    memory_audit_rows,
    reset_memory_audit,
)
from dms_executor.connectors import (
    classify_driver_error,
    clear_connectors,
    guarded_reason,
    iter_live_export,
    open_cancel,
    probe_source,
    run_live_select,
    set_auto_onto_hook,
    set_probe_override,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

TOKEN = "cv2-harness-bearer"
REF = "dms/cv2-kid"
SECRET = "ov-mem-only"
QUESTION = "cv2 marker tally please"
LEFT = "cv2_left"

_ROOT = Path(__file__).resolve().parents[1]


def _engines_on() -> bool:
    return os.environ.get("DMS_CONNECT_V2_ENGINES", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _shown(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _no_secret(text: str, *secrets: str) -> None:
    """A short CI password can be a substring of a reference. Scan the long ones."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            assert secret not in text


def _allow(*, action: str, **_: Any) -> ComplianceDecision:
    return ComplianceDecision(allowed=True, reason="test_allow", action=action)


class _OvResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _OvHttp:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self.secrets = secrets or {"cv2-kid": SECRET}
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> _OvResponse:
        self.calls.append(url)
        kid = url.rstrip("/").split("/")[-2]
        if kid not in self.secrets:
            return _OvResponse(404, {})
        return _OvResponse(200, {"id": kid, "secret": self.secrets[kid]})


@dataclass
class _Cortex:
    asks: list[Any] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-cv2")

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Connected marker is 7.",
            abstained=False,
            badge="certified",
            sql_used=f"SELECT marker FROM {LEFT}",
            rows=[{"marker": 7}],
            audit_id="aud-cv2",
            route="sql",
        )


def _minter(
    monkeypatch: pytest.MonkeyPatch, secrets: dict[str, str] | None = None
) -> ManifestMinter:
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
            issued_at="2026-08-02T00:00:00+00:00",
            expires_at="2026-08-02T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    minter._http = _OvHttp(secrets)  # type: ignore[assignment]
    return minter


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    v2: str | None,
    secrets: dict[str, str] | None = None,
    database_url: str | None = None,
) -> tuple[TestClient, _Cortex]:
    import dms_api.app as app_mod
    from dms_api import settings as settings_mod
    from dms_api.routes import connect as connect_routes
    from dms_api.routes import spaces as spaces_routes
    from dms_api.routes import studio as studio_routes
    from dms_executor import Executor

    monkeypatch.setenv("DMS_CONNECT_API", "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("DMS_CCA_CASCADE", "0")
    if v2 is None:
        monkeypatch.delenv("DMS_CONNECT_V2", raising=False)
    else:
        monkeypatch.setenv("DMS_CONNECT_V2", v2)
    if database_url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)
    settings_mod.get_settings.cache_clear()
    reset_grants()
    clear_connectors()
    reset_memory_audit()
    cortex = _Cortex()
    minter = _minter(monkeypatch, secrets)

    def _build(*_a: Any, **_k: Any) -> Executor:
        return Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "build_ask_service", _build)
    monkeypatch.setattr(connect_routes, "compliance_gate", _allow)
    monkeypatch.setattr(spaces_routes, "compliance_gate", _allow)
    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    app = app_mod.create_app()
    client = TestClient(app)
    client.__enter__()
    return client, cortex


def _close(client: TestClient) -> None:
    client.__exit__(None, None, None)
    from dms_api import settings as settings_mod

    reset_grants()
    clear_connectors()
    reset_memory_audit()
    settings_mod.get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _audit_shape(raw: bytes) -> list[dict[str, Any]]:
    rows = json.loads(raw)["rows"]
    stable = []
    for row in rows:
        stable.append(
            {
                "action": row.get("action"),
                "tables": row.get("tables"),
                "persisted": json.loads(raw).get("persisted"),
            }
        )
    return stable


def _prompt_bytes(req: Any) -> bytes:
    if hasattr(req, "model_dump"):
        data = req.model_dump()
    else:
        data = {key: value for key, value in vars(req).items() if not key.startswith("_")}
    for key in ("session_id", "space_id", "as_of", "issued_at", "expires_at"):
        data.pop(key, None)
    return json.dumps(data, sort_keys=True, default=str).encode()


def _stable_envelope(env: dict[str, Any]) -> dict[str, Any]:
    return {
        key: env.get(key)
        for key in ("text", "rows", "badge", "abstained", "route", "sql_used")
    }


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_api import settings as settings_mod

    monkeypatch.delenv("DMS_CONNECT_V2", raising=False)
    settings_mod.get_settings.cache_clear()
    assert settings_mod.get_settings().dms_connect_v2 is False
    settings_mod.get_settings.cache_clear()


def test_off_schema_rejects_v2_keys_and_v2_schema_accepts_them() -> None:
    old = {
        "credential_ref": REF,
        "connector_id": "c",
        "space_name": "s",
        "kind": "postgresql",
        "host": "127.0.0.1",
        "port": 5432,
        "db": "connect_src",
        "mode": "live",
    }
    assert body_violation(old, schema="sources") == "connect_raw_secret_rejected"
    assert body_violation(old, schema="sources_v2") is None


def test_migration_chains_without_a_secret_column() -> None:
    text = (_ROOT / "alembic" / "versions" / "0010_connections.py").read_text(encoding="utf-8")
    assert 'revision: str = "0010_connections"' in text
    assert 'down_revision: str | None = "0009_action_writeback"' in text
    lowered = text.lower()
    for banned in ("password", "passwd", "secret", "pwd"):
        assert banned not in lowered
    decision = (
        _ROOT / "docs" / "decisions" / "0005-extract-only-is-the-answer-to-sql-sources.md"
    ).read_text(encoding="utf-8")
    assert "status: superseded" in decision
    assert "DR-0010" in decision


def test_live_path_sets_no_statement_timeout_or_row_cap() -> None:
    text = (_ROOT / "packages" / "executor" / "dms_executor" / "connectors.py").read_text(
        encoding="utf-8"
    )
    assert "statement_timeout" not in text
    assert "max_rows" not in text


def test_store_rejects_userinfo_before_any_connection() -> None:
    store = ConnectStore(
        "postgresql://127.0.0.1:1/nope", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    with pytest.raises(ConnectStoreError) as caught:
        store.save_source(
            source_id=str(uuid4()),
            space_id="space",
            connector_id="c",
            kind="postgresql",
            host="http://user:secret@db.example",
            port=5432,
            database="connect_src",
            cred_ref=REF,
            mode="live",
            server_version="",
            exposed=["public.widget"],
            chosen=["public.widget"],
            actor="actor",
            token_id="token",
        )
    assert caught.value.code == "connect_raw_secret_rejected"


def test_driver_errors_are_typed_without_using_the_text_as_the_code() -> None:
    class _State(Exception):
        def __init__(self, sqlstate: str) -> None:
            super().__init__("driver text")
            self.sqlstate = sqlstate

    class _Errno(Exception):
        def __init__(self, errno: int) -> None:
            super().__init__(errno, "driver text")

    assert classify_driver_error(_State("28P01")) == "auth"
    assert classify_driver_error(_State("42501")) == "permission"
    assert classify_driver_error(_State("08001")) == "network"
    assert classify_driver_error(_Errno(1045)) == "auth"
    assert classify_driver_error(_Errno(1044)) == "permission"
    assert classify_driver_error(_Errno(2003)) == "network"
    assert classify_driver_error(_Errno(2026)) == "tls"
    assert classify_driver_error(ConnectionRefusedError(111, "refused")) == "network"
    assert "driver text" not in classify_driver_error(_State("28P01"))


def test_sql_guard_allows_one_granted_read_and_refuses_writes() -> None:
    grants = {"public.widget"}
    assert (
        guarded_reason(
            "SELECT n FROM public.widget", grantable=grants, kind="postgresql"
        )
        is None
    )
    assert (
        guarded_reason("DELETE FROM public.widget", grantable=grants, kind="postgresql")
        == "statement_not_allowed"
    )
    assert (
        guarded_reason(
            "SELECT n FROM public.widget; DELETE FROM public.widget",
            grantable=grants,
            kind="postgresql",
        )
        == "multi_statement"
    )
    assert (
        guarded_reason(
            "WITH d AS (DELETE FROM public.widget RETURNING n) SELECT n FROM d",
            grantable=grants,
            kind="postgresql",
        )
        == "statement_not_allowed"
    )
    assert (
        guarded_reason(
            "SELECT n INTO public.other FROM public.widget",
            grantable=grants,
            kind="postgresql",
        )
        == "statement_not_allowed"
    )
    refused = guarded_reason(
        "SELECT n FROM public.other", grantable=grants, kind="postgresql"
    )
    assert refused is not None


def test_flag_off_route_prompt_and_envelope_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.connectors import register_connector

    def _one(flag: str | None) -> tuple[bytes, dict[str, Any], bytes, set[str]]:
        client, cortex = _client(monkeypatch, v2=flag)
        try:
            register_connector("cv2-src", lambda credential: [LEFT] if credential == SECRET else [])
            created = client.post(
                "/v1/connect/sources",
                headers=_auth(),
                json={
                    "connector_id": "cv2-src",
                    "space_name": "Cv2 off",
                    "credential_ref": REF,
                },
            )
            assert created.status_code == 201, created.text
            body = created.json()
            keys = set(body)
            asked = client.post(
                "/v1/chat/ask",
                json={
                    "question": QUESTION,
                    "space_id": body["space"]["id"],
                    "session_id": "ses_cv2",
                    "grounded_tables": [LEFT],
                },
            )
            assert asked.status_code == 200, asked.text
            env = asked.json()
            assert_envelope_valid(env)
            audit = client.get("/v1/connect/audit", headers=_auth())
            assert audit.status_code == 200
            assert cortex.asks
            return (
                _prompt_bytes(cortex.asks[-1]),
                _stable_envelope(env),
                _audit_shape(audit.content),
                keys,
            )
        finally:
            _close(client)

    unset = _one(None)
    explicit = _one("0")
    assert unset[0] == explicit[0]
    assert unset[1] == explicit[1]
    assert unset[2] == explicit[2]
    assert unset[2] and unset[2][0]["action"] == "register"
    assert unset[3] == explicit[3]
    assert unset[3] == {
        "space",
        "source_id",
        "connector_id",
        "exposed_tables",
        "granted_tables",
        "persisted",
        "storage",
        "hint",
    }
    assert b"DMS_CONNECT_V2" not in unset[0]


def test_flag_off_does_not_enter_v2_and_new_routes_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dms_api.wiring as wiring

    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("v2 register entered")

    monkeypatch.setattr(wiring, "connect_v2_register", _boom)
    client, _cortex = _client(monkeypatch, v2=None)
    try:
        from dms_executor.connectors import register_connector

        register_connector("cv2-src", lambda _credential: [LEFT])
        created = client.post(
            "/v1/connect/sources",
            headers=_auth(),
            json={
                "connector_id": "cv2-src",
                "space_name": "Cv2 still off",
                "credential_ref": REF,
            },
        )
        assert created.status_code == 201, created.text
        extra = client.post(
            "/v1/connect/sources",
            headers=_auth(),
            json={
                "connector_id": "cv2-src",
                "space_name": "Cv2 extra",
                "credential_ref": REF,
                "kind": "postgresql",
                "host": "127.0.0.1",
                "port": 5432,
                "db": "connect_src",
                "mode": "live",
            },
        )
        assert extra.status_code == 400
        assert extra.json()["detail"] == "connect_raw_secret_rejected"
        listed = client.get("/v1/connect/sources", headers=_auth())
        assert listed.status_code == 404
        assert listed.json()["detail"] == "connect_v2_disabled"
        probed = client.post(
            "/v1/connect/test",
            headers=_auth(),
            json={
                "kind": "postgresql",
                "host": "127.0.0.1",
                "port": 5432,
                "db": "connect_src",
                "cred_ref": REF,
            },
        )
        assert probed.status_code == 404
        assert probed.json()["detail"] == "connect_v2_disabled"
    finally:
        _close(client)

    monkeypatch.setenv("DMS_CONNECT_API", "0")
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    client_off, _cortex = _client(monkeypatch, v2="1")
    # _client forces DMS_CONNECT_API=1. Hit the disabled flag directly.
    _close(client_off)
    monkeypatch.setenv("DMS_CONNECT_API", "0")
    monkeypatch.setenv("DMS_CONNECT_V2", "1")
    settings_mod.get_settings.cache_clear()
    import dms_api.app as app_mod

    app = app_mod.create_app()
    with TestClient(app) as bare:
        res = bare.get("/v1/connect/sources", headers=_auth())
    assert res.status_code == 404
    assert res.json()["detail"] == "connect_api_disabled"
    settings_mod.get_settings.cache_clear()


def test_probe_error_does_not_echo_the_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "canary-" + uuid4().hex
    caplog.set_level(logging.DEBUG)

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(canary)

    client, _cortex = _client(
        monkeypatch,
        v2="1",
        secrets={"cv2-kid": json.dumps({"user": "reader", "password": canary})},
    )
    set_probe_override(_boom)
    try:
        res = client.post(
            "/v1/connect/test",
            headers=_auth(),
            json={
                "kind": "postgresql",
                "host": "127.0.0.1",
                "port": 5432,
                "db": "connect_src",
                "cred_ref": REF,
            },
        )
        assert res.status_code == 200, _shown(res.text, canary)
        body = res.json()
        assert body["ok"] is False
        assert body["error"] in {"auth", "network", "permission", "tls"}
        assert canary not in res.text
        assert canary not in caplog.text
        dumped = json.dumps(memory_audit_rows())
        assert canary not in dumped
        assert body["error"] in dumped or "probe" in dumped
    finally:
        set_probe_override(None)
        _close(client)


def _sqlite_db(tmp_path: Path) -> Path:
    path = tmp_path / "src.sqlite"
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE widget (n INTEGER)")
        con.executemany("INSERT INTO widget (n) VALUES (?)", [(1,), (2,), (3,)])
        con.commit()
    finally:
        con.close()
    return path


def test_sqlite_pages_and_export_return_every_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SQLITE_SOURCE_ROOT", str(tmp_path))
    path = _sqlite_db(tmp_path)
    probed = probe_source(
        kind="sqlite",
        host="localhost",
        port=0,
        database=str(path),
        user="reader",
        password="unused",
    )
    assert probed["tables_visible"] == 1
    grantable = set(probed["tables"])
    sql = f"SELECT n FROM {probed['tables'][0]} ORDER BY n"
    assert guarded_reason(sql, grantable=grantable, kind="sqlite") is None
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(10):
        page = run_live_select(
            kind="sqlite",
            host="localhost",
            port=0,
            database=str(path),
            user="reader",
            password="unused",
            sql=sql,
            grantable=grantable,
            offset=offset,
            page_size=1,
            confirm_runaway=True,
        )
        assert page["truncated"] is False
        assert page["bounded"] is False
        rows.extend(page["rows"])
        if page["complete"]:
            break
        offset = int(page["next_offset"])
    assert [row["n"] for row in rows] == [1, 2, 3]
    exported = list(
        iter_live_export(
            kind="sqlite",
            host="localhost",
            port=0,
            database=str(path),
            user="reader",
            password="unused",
            sql=sql,
            grantable=grantable,
            confirm_runaway=True,
        )
    )
    assert [item["row"]["n"] for item in exported if item.get("event") == "row"] == [1, 2, 3]
    end = exported[-1]
    assert end["complete"] is True
    assert end["cancelled"] is False
    assert end["truncated"] is False
    assert end["bounded"] is False


def test_cancel_and_runaway_do_not_pretend_the_result_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SQLITE_SOURCE_ROOT", str(tmp_path))
    path = _sqlite_db(tmp_path)
    probed = probe_source(
        kind="sqlite",
        host="localhost",
        port=0,
        database=str(path),
        user="reader",
        password="unused",
    )
    grantable = set(probed["tables"])
    sql = f"SELECT n FROM {probed['tables'][0]} ORDER BY n"
    blocked = run_live_select(
        kind="sqlite",
        host="localhost",
        port=0,
        database=str(path),
        user="reader",
        password="unused",
        sql=sql,
        grantable=grantable,
        confirm_runaway=False,
    )
    assert blocked["error"] == "runaway"
    assert blocked["rows"] == []
    assert blocked["truncated"] is False
    event = threading.Event()
    event.set()
    cancelled = run_live_select(
        kind="sqlite",
        host="localhost",
        port=0,
        database=str(path),
        user="reader",
        password="unused",
        sql=sql,
        grantable=grantable,
        confirm_runaway=True,
        cancel=event,
    )
    assert cancelled["cancelled"] is True
    assert cancelled["complete"] is False
    query_id, live = open_cancel()
    from dms_executor.connectors import cancel_live

    assert cancel_live(query_id) is True
    assert live.is_set()


def test_cancel_route_marks_the_open_query(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _cortex = _client(monkeypatch, v2="1")
    try:
        query_id, event = open_cancel()
        res = client.post(
            f"/v1/connect/sources/{uuid4()}/query/cancel",
            headers=_auth(),
            json={"query_id": query_id},
        )
        assert res.status_code == 200, res.text
        assert res.json()["cancelled"] is True
        assert event.is_set()
        missing = client.post(
            f"/v1/connect/sources/{uuid4()}/query/cancel",
            headers=_auth(),
            json={"query_id": "no-such-query"},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "query_not_found"
    finally:
        _close(client)


@pytest.fixture(scope="module")
def engines() -> dict[str, Any]:
    if not _engines_on():
        pytest.skip("DMS_CONNECT_V2_ENGINES is off; the connect-v2 CI job runs these")
    required = (
        "DATABASE_URL",
        "CONNECT_V2_PG_HOST",
        "CONNECT_V2_PG_PORT",
        "CONNECT_V2_PG_USER",
        "CONNECT_V2_PG_PASSWORD",
        "CONNECT_V2_PG_DB",
        "CONNECT_V2_MY_HOST",
        "CONNECT_V2_MY_PORT",
        "CONNECT_V2_MY_USER",
        "CONNECT_V2_MY_PASSWORD",
        "CONNECT_V2_MY_DB",
        "CONNECT_V2_NOPERM_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.fail("connect-v2 job is missing " + ",".join(missing))
    _prepare_postgres()
    _prepare_mariadb()
    return {
        "pg": {
            "kind": "postgresql",
            "host": os.environ["CONNECT_V2_PG_HOST"],
            "port": int(os.environ["CONNECT_V2_PG_PORT"]),
            "db": os.environ["CONNECT_V2_PG_DB"],
            "user": os.environ["CONNECT_V2_PG_USER"],
            "password": os.environ["CONNECT_V2_PG_PASSWORD"],
        },
        "my": {
            "kind": "mariadb",
            "host": os.environ["CONNECT_V2_MY_HOST"],
            "port": int(os.environ["CONNECT_V2_MY_PORT"]),
            "db": os.environ["CONNECT_V2_MY_DB"],
            "user": os.environ["CONNECT_V2_MY_USER"],
            "password": os.environ["CONNECT_V2_MY_PASSWORD"],
        },
        "noperm": os.environ["CONNECT_V2_NOPERM_PASSWORD"],
    }


def _prepare_postgres() -> None:
    import psycopg
    from psycopg import sql

    admin = os.environ["DATABASE_URL"]
    db_name = os.environ["CONNECT_V2_PG_DB"]
    noperm = os.environ["CONNECT_V2_NOPERM_PASSWORD"]
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        ).fetchone()
        if exists is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'noperm'").fetchone()
        password = sql.Literal(noperm)
        if role is None:
            conn.execute(
                sql.SQL("CREATE ROLE noperm LOGIN PASSWORD {}").format(password)
            )
        else:
            conn.execute(sql.SQL("ALTER ROLE noperm PASSWORD {}").format(password))
        conn.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name))
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(os.environ["CONNECT_V2_PG_USER"])
            )
        )
    source = admin.rsplit("/", 1)[0] + "/" + db_name
    with psycopg.connect(source, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS widget (n integer)")
        conn.execute("DELETE FROM widget")
        conn.execute("INSERT INTO widget (n) VALUES (1), (2), (3)")


def _prepare_mariadb() -> None:
    import pymysql

    db_name = os.environ["CONNECT_V2_MY_DB"]
    noperm = os.environ["CONNECT_V2_NOPERM_PASSWORD"]
    con = pymysql.connect(
        host=os.environ["CONNECT_V2_MY_HOST"],
        port=int(os.environ["CONNECT_V2_MY_PORT"]),
        user=os.environ["CONNECT_V2_MY_USER"],
        password=os.environ["CONNECT_V2_MY_PASSWORD"],
        autocommit=True,
    )
    try:
        cur = con.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
        cur.execute(f"CREATE TABLE IF NOT EXISTS `{db_name}`.widget (n INT)")
        cur.execute(f"DELETE FROM `{db_name}`.widget")
        cur.execute(f"INSERT INTO `{db_name}`.widget (n) VALUES (1),(2),(3)")
        cur.execute("CREATE USER IF NOT EXISTS 'noperm'@'%%' IDENTIFIED BY %s", (noperm,))
        cur.execute("ALTER USER 'noperm'@'%%' IDENTIFIED BY %s", (noperm,))
    finally:
        con.close()


def _secret(user: str, password: str) -> str:
    return json.dumps({"user": user, "password": password})


def test_connection_survives_a_new_app(
    monkeypatch: pytest.MonkeyPatch, engines: dict[str, Any]
) -> None:
    import psycopg

    pg = engines["pg"]
    canary = pg["password"]
    secrets = {"pg-kid": _secret(pg["user"], pg["password"])}
    seen: list[tuple[str, tuple[str, ...]]] = []
    client, _cortex = _client(
        monkeypatch,
        v2="1",
        secrets=secrets,
        database_url=os.environ["DATABASE_URL"],
    )
    # _client clears connectors, which drops a hook set before the app exists.
    set_auto_onto_hook(lambda source_id, tables: seen.append((source_id, tables)))
    try:
        saved = client.post(
            "/v1/connect/sources",
            headers=_auth(),
            json={
                "connector_id": "pg",
                "space_name": "Cv2 persist",
                "credential_ref": "dms/pg-kid",
                "kind": "postgresql",
                "host": pg["host"],
                "port": pg["port"],
                "db": pg["db"],
                "mode": "live",
            },
        )
        assert saved.status_code == 201, _shown(saved.text, canary)
        body = saved.json()
        _no_secret(saved.text, canary)
        assert "password" not in body
        assert body["mode"] == "live"
        assert body["read_only"] is True
        assert body["onto_job_id"]
        assert seen and seen[0][0] == body["source_id"]
        source_id = body["source_id"]
    finally:
        _close(client)
    reset_grants()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cols = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'dms' AND table_name = 'connect_sources'
                """
            ).fetchall()
        }
        assert "cred_ref" in cols
        for banned in ("password", "passwd", "secret", "pwd"):
            assert banned not in cols
        row = conn.execute(
            "SELECT id::text, cred_ref, mode FROM dms.connect_sources WHERE id = %s::uuid",
            (source_id,),
        ).fetchone()
        assert row is not None
        assert row[1] == "dms/pg-kid"
        assert row[2] == "live"
        job = conn.execute(
            "SELECT status FROM dms.connect_onto_jobs WHERE source_id = %s::uuid",
            (source_id,),
        ).fetchone()
        assert job is not None and job[0] == "requested"
    client2, _cortex = _client(
        monkeypatch,
        v2="1",
        secrets=secrets,
        database_url=os.environ["DATABASE_URL"],
    )
    try:
        listed = client2.get("/v1/connect/sources", headers=_auth())
        assert listed.status_code == 200, _shown(listed.text, canary)
        ids = [item["source_id"] for item in listed.json()["sources"]]
        assert source_id in ids
        _no_secret(listed.text, canary)
        schema = client2.get(f"/v1/connect/sources/{source_id}/schema", headers=_auth())
        assert schema.status_code == 200, _shown(schema.text, canary)
        text = schema.text
        assert "widget" in text
        assert '"rows"' not in text
        _no_secret(text, canary)
    finally:
        _close(client2)


@pytest.mark.parametrize("engine", ["pg", "my"])
def test_probe_ok_and_typed_failures(
    monkeypatch: pytest.MonkeyPatch,
    engines: dict[str, Any],
    engine: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = engines[engine]
    canary_auth = "wrong-" + uuid4().hex
    noperm = engines["noperm"]
    secrets = {
        "ok-kid": _secret(spec["user"], spec["password"]),
        "auth-kid": _secret(spec["user"], canary_auth),
        "perm-kid": _secret("noperm", noperm),
    }
    hidden = (spec["password"], canary_auth, noperm)
    caplog.set_level(logging.DEBUG)
    client, _cortex = _client(
        monkeypatch,
        v2="1",
        secrets=secrets,
        database_url=os.environ["DATABASE_URL"],
    )
    try:
        def _post(body: dict[str, Any]) -> Any:
            res = client.post("/v1/connect/test", headers=_auth(), json=body)
            assert res.status_code == 200, _shown(res.text, *hidden)
            _no_secret(res.text, *hidden)
            _no_secret(caplog.text, *hidden)
            return res.json()

        base = {
            "kind": spec["kind"],
            "host": spec["host"],
            "port": spec["port"],
            "db": spec["db"],
        }
        ok = _post({**base, "cred_ref": "dms/ok-kid"})
        assert ok["ok"] is True
        assert ok["tables_visible"] >= 1
        assert ok["server_version"]
        assert "error" not in ok
        auth = _post({**base, "cred_ref": "dms/auth-kid"})
        assert auth["ok"] is False and auth["error"] == "auth"
        network = _post({**base, "port": 1, "cred_ref": "dms/ok-kid"})
        assert network["ok"] is False and network["error"] == "network"
        permission = _post({**base, "cred_ref": "dms/perm-kid"})
        assert permission["ok"] is False and permission["error"] == "permission"
        tls = _post({**base, "cred_ref": "dms/ok-kid", "tls": True})
        if spec["kind"] == "postgresql":
            # The CI Postgres image does not serve TLS. The failure stays typed.
            assert tls["ok"] is False and tls["error"] == "tls"
        elif tls["ok"]:
            assert tls["tables_visible"] >= 1
        else:
            assert tls["error"] == "tls"
        dumped = json.dumps(memory_audit_rows())
        _no_secret(dumped, *hidden)
    finally:
        _close(client)


def test_live_query_returns_every_postgres_row(
    monkeypatch: pytest.MonkeyPatch, engines: dict[str, Any]
) -> None:
    pg = engines["pg"]
    canary = pg["password"]
    secrets = {"pg-kid": _secret(pg["user"], pg["password"])}
    client, _cortex = _client(
        monkeypatch,
        v2="1",
        secrets=secrets,
        database_url=os.environ["DATABASE_URL"],
    )
    try:
        saved = client.post(
            "/v1/connect/sources",
            headers=_auth(),
            json={
                "connector_id": "pg",
                "space_name": "Cv2 query " + uuid4().hex[:8],
                "credential_ref": "dms/pg-kid",
                "kind": "postgresql",
                "host": pg["host"],
                "port": pg["port"],
                "db": pg["db"],
                "mode": "live",
            },
        )
        assert saved.status_code == 201, _shown(saved.text, canary)
        source_id = saved.json()["source_id"]
        exposed = saved.json()["exposed_tables"]
        qualified = next(name for name in exposed if name.endswith("widget"))
        assert qualified.replace(".", "").replace("_", "").isalnum()
        sql = f"SELECT n FROM {qualified} ORDER BY n"
        page = client.post(
            f"/v1/connect/sources/{source_id}/query",
            headers=_auth(),
            json={"sql": sql, "page_size": 1, "offset": 0, "confirm_runaway": True},
        )
        assert page.status_code == 200, _shown(page.text, canary)
        body = page.json()
        assert body["truncated"] is False
        assert body["bounded"] is False
        _no_secret(page.text, canary)
        rows = list(body["rows"])
        offset = body.get("next_offset")
        guard = 0
        while body.get("complete") is False and offset is not None and guard < 10:
            guard += 1
            page = client.post(
                f"/v1/connect/sources/{source_id}/query",
                headers=_auth(),
                json={"sql": sql, "page_size": 1, "offset": offset, "confirm_runaway": True},
            )
            assert page.status_code == 200, _shown(page.text, canary)
            body = page.json()
            assert body["truncated"] is False
            rows.extend(body["rows"])
            offset = body.get("next_offset")
        assert [row["n"] for row in rows] == [1, 2, 3]
        exported = client.post(
            f"/v1/connect/sources/{source_id}/query",
            headers=_auth(),
            json={"sql": sql, "export": True, "confirm_runaway": True},
        )
        assert exported.status_code == 200, _shown(exported.text, canary)
        _no_secret(exported.text, canary)
        lines = [json.loads(line) for line in exported.text.splitlines() if line.strip()]
        got = [item["row"]["n"] for item in lines if item.get("event") == "row"]
        assert got == [1, 2, 3]
        assert lines[-1]["truncated"] is False
        assert lines[-1]["bounded"] is False
        assert lines[-1]["complete"] is True
    finally:
        _close(client)
