"""FEAT-CONNECT-GAUSS-01: GaussDB / openGauss and Oracle, flags default off.

Flag off rejects both kinds with the same error an unknown kind gets, and the
ask prompt, envelope, and route stay byte-identical. Flag on, recorded cursors
cover introspect, keys, and typed errors. The live openGauss round-trip runs
only when CI has started the server.
"""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.connect_store import (
    ConnectStore,
    ConnectStoreError,
    allowed_kinds,
)
from dms_executor import db_connector as dbc
from dms_executor.connectors import (
    ConnectQueryError,
    guarded_reason,
    probe_source,
    run_live_select,
)
from dms_executor.drivers import gauss, oracle
from dms_executor.sql_loop import UnknownConnectorDialect, dialect_for_connector
from test_connect_v2 import (  # noqa: E402
    REF,
    _auth,
    _client,
    _close,
    _prompt_bytes,
    _stable_envelope,
)

_ROOT = Path(__file__).resolve().parents[1]
_BASE_KINDS = frozenset({"postgresql", "mysql", "mariadb", "sqlserver", "sqlite"})
_PASSWORD = "canary-gauss-secret"
_ORA_PASSWORD = "canary-oracle-secret"


def _flag(monkeypatch: pytest.MonkeyPatch, name: str, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)


class _Cursor:
    def __init__(self, owner: _Connection) -> None:
        self._owner = owner
        self._sql = ""
        self.description: list[tuple[Any, ...]] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self._sql = sql
        self._owner.executed.append(sql)
        self.description = [("a",)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        sql = self._sql
        if "CONSTRAINT_TYPE = 'P'" in sql or "contype = 'p'" in sql:
            return list(self._owner.pks)
        if "CONSTRAINT_TYPE = 'R'" in sql or "contype = 'f'" in sql:
            return list(self._owner.fks)
        if "INFORMATION_SCHEMA.TABLES" in sql or sql.startswith("SELECT OWNER, TABLE_NAME"):
            return list(self._owner.tables)
        return []

    def fetchone(self) -> tuple[Any, ...] | None:
        lowered = self._sql.lower()
        if "sql_compatibility" in lowered:
            return (self._owner.mode,)
        if "v$version" in lowered:
            return (self._owner.banner,) if self._owner.banner else None
        if "sys_context" in lowered:
            return (self._owner.db_name,) if self._owner.db_name else None
        if "version()" in lowered:
            return (self._owner.version,)
        rows = self.fetchall()
        return rows[0] if rows else None

    def close(self) -> None:
        return None


class _Connection:
    def __init__(
        self,
        *,
        tables: list[tuple[str, str]] | None = None,
        pks: list[tuple[Any, ...]] | None = None,
        fks: list[tuple[Any, ...]] | None = None,
        version: str = "",
        mode: str = "",
        banner: str = "",
        db_name: str = "",
    ) -> None:
        self.tables = tables or []
        self.pks = pks or []
        self.fks = fks or []
        self.version = version
        self.mode = mode
        self.banner = banner
        self.db_name = db_name
        self.executed: list[str] = []
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _cfg(kind: str, password: str = _PASSWORD) -> dbc.SourceConfig:
    return dbc.SourceConfig(
        kind=kind,  # type: ignore[arg-type]
        host="db.example",
        database="sales",
        user="reader",
        password=password,
    )


def test_flags_default_off_and_kinds_match_the_base_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", None)
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", None)
    assert allowed_kinds() == _BASE_KINDS
    assert gauss.enabled() is False
    assert oracle.enabled() is False


def test_word_flags_stay_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.drivers import flag_on

    for raw in ("true", "yes", "on"):
        _flag(monkeypatch, "DMS_CONNECT_GAUSS", raw)
        _flag(monkeypatch, "DMS_CONNECT_ORACLE", raw)
        assert flag_on("DMS_CONNECT_GAUSS") is False
        assert flag_on("DMS_CONNECT_ORACLE") is False
        assert allowed_kinds() == _BASE_KINDS
        assert gauss.enabled() is False
        assert oracle.enabled() is False
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", "1")
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", "1")
    assert flag_on("DMS_CONNECT_GAUSS") is True
    assert "gauss" in allowed_kinds()
    assert "oracle" in allowed_kinds()


def test_flag_off_source_config_error_matches_an_unknown_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", None)
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", None)
    with pytest.raises(ValueError) as unknown:
        dbc.SourceConfig(
            kind="snowflake",  # type: ignore[arg-type]
            host="db.example",
            database="sales",
            user="reader",
            password=_PASSWORD,
        )
    with pytest.raises(ValueError) as gauss_off:
        _cfg("gauss")
    with pytest.raises(ValueError) as oracle_off:
        _cfg("oracle")
    assert str(unknown.value) == "unsupported source kind: 'snowflake'"
    assert str(gauss_off.value) == "unsupported source kind: 'gauss'"
    assert str(oracle_off.value) == "unsupported source kind: 'oracle'"
    assert type(unknown.value) is type(gauss_off.value) is type(oracle_off.value)
    assert _PASSWORD not in str(gauss_off.value)


def test_store_rejects_new_kinds_with_the_same_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", None)
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", None)
    store = ConnectStore("postgresql://127.0.0.1:1/nope", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def _save(kind: str) -> str:
        with pytest.raises(ConnectStoreError) as caught:
            store.save_source(
                source_id="11111111-1111-1111-1111-111111111111",
                space_id="space",
                connector_id="c",
                kind=kind,
                host="db.example",
                port=5432,
                database="sales",
                cred_ref=REF,
                mode="live",
                server_version="",
                exposed=["public.orders"],
                chosen=["public.orders"],
                actor="actor",
                token_id="token",
            )
        return caught.value.code

    assert _save("snowflake") == _save("gauss") == _save("oracle") == "bad_request"


def test_flag_off_kind_error_bytes_match_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", None)
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", None)
    client, _cortex = _client(monkeypatch, v2="1")
    try:

        def _post(kind: str) -> Any:
            return client.post(
                "/v1/connect/test",
                headers=_auth(),
                json={
                    "kind": kind,
                    "host": "127.0.0.1",
                    "port": 5432,
                    "db": "sales",
                    "cred_ref": REF,
                },
            )

        unknown = _post("snowflake")
        gauss_res = _post("gauss")
        oracle_res = _post("oracle")
        assert unknown.status_code == 400
        assert unknown.content == gauss_res.content == oracle_res.content
        assert _PASSWORD.encode() not in unknown.content
    finally:
        _close(client)


def test_flag_off_prompt_envelope_and_route_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.connectors import register_connector

    def _one(raw: str | None) -> tuple[bytes, dict[str, Any], str]:
        _flag(monkeypatch, "DMS_CONNECT_GAUSS", raw)
        _flag(monkeypatch, "DMS_CONNECT_ORACLE", raw)
        client, cortex = _client(monkeypatch, v2=None)
        try:
            register_connector("cv2-src", lambda credential: ["cv2_left"])
            created = client.post(
                "/v1/connect/sources",
                headers=_auth(),
                json={
                    "connector_id": "cv2-src",
                    "space_name": "Gauss flag off",
                    "credential_ref": REF,
                },
            )
            assert created.status_code == 201, created.text
            body = created.json()
            asked = client.post(
                "/v1/chat/ask",
                json={
                    "question": "cv2 marker tally please",
                    "space_id": body["space"]["id"],
                    "session_id": "ses_gauss_off",
                    "grounded_tables": ["cv2_left"],
                },
            )
            assert asked.status_code == 200, asked.text
            env = asked.json()
            stable = _stable_envelope(env)
            return _prompt_bytes(cortex.asks[-1]), stable, str(stable.get("route"))
        finally:
            _close(client)

    unset = _one(None)
    explicit = _one("0")
    assert unset[0] == explicit[0]
    assert unset[1] == explicit[1]
    assert unset[2] == explicit[2] == "sql"
    assert b"DMS_CONNECT_GAUSS" not in unset[0]
    assert b"DMS_CONNECT_ORACLE" not in unset[0]


def test_flag_off_dialect_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", None)
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", None)
    with pytest.raises(ConnectQueryError) as gauss_off:
        guarded_reason(
            "SELECT a FROM public.orders", grantable={"public.orders"}, kind="gauss"
        )
    with pytest.raises(ConnectQueryError) as oracle_off:
        guarded_reason("SELECT a FROM hr.orders", grantable={"hr.orders"}, kind="oracle")
    assert gauss_off.value.code == oracle_off.value.code == "sql_dialect_unknown"
    with pytest.raises(UnknownConnectorDialect):
        dialect_for_connector("gauss")
    with pytest.raises(UnknownConnectorDialect):
        dialect_for_connector("oracle")


def test_gauss_auth_method_error_hides_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    import psycopg

    def _boom(**kwargs: Any) -> Any:
        raise RuntimeError(
            f"authentication method 10 not supported password={kwargs.get('password')}"
        )

    monkeypatch.setattr(psycopg, "connect", _boom)
    with pytest.raises(dbc.SourceConnectionError) as caught:
        gauss.open_source(_cfg("gauss"))
    text = "".join(traceback.format_exception(caught.value))
    assert caught.value.code == "auth_method_unsupported"
    assert caught.value.doc == gauss.AUTH_DOC
    assert _PASSWORD not in str(caught.value)
    assert _PASSWORD not in text
    assert "authentication method" not in str(caught.value)


def test_gauss_bad_password_is_not_the_auth_method_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    import psycopg

    def _bad(**kwargs: Any) -> Any:
        raise RuntimeError(f"password authentication failed for {kwargs.get('password')}")

    monkeypatch.setattr(psycopg, "connect", _bad)
    with pytest.raises(dbc.SourceConnectionError) as caught:
        gauss.open_source(_cfg("gauss"))
    text = "".join(traceback.format_exception(caught.value))
    assert caught.value.code == ""
    assert _PASSWORD not in str(caught.value)
    assert _PASSWORD not in text


def test_gauss_refuses_b_mode_and_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    cfg = _cfg("gauss")
    cases = (
        ("B", "compat_mode_unsupported"),
        ("", "compat_mode_unknown"),
        ("C", "compat_mode_unknown"),
    )
    for mode, code in cases:
        con = _Connection(version="(openGauss 5.0.0 build abc)", mode=mode)
        monkeypatch.setattr(gauss, "connect_login", lambda **_k: con)
        with pytest.raises(dbc.SourceConnectionError) as caught:
            gauss.open_source(cfg)
        assert caught.value.code == code
        assert con.closed is True
        assert _PASSWORD not in str(caught.value)


def test_gauss_accepts_pg_and_a_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    pg = _Connection(version="(openGauss 5.0.0 build abc)", mode="PG")
    monkeypatch.setattr(gauss, "connect_login", lambda **_k: pg)
    opened = gauss.open_source(_cfg("gauss"))
    assert opened is pg
    assert any("READ ONLY" in sql for sql in pg.executed)
    a_mode = _Connection(version="PostgreSQL 9.2.4 (GaussDB Kernel V500)", mode="A")
    assert gauss.inspect(a_mode)["dialect"] == "postgres"
    assert gauss.inspect(a_mode)["mode"] == "A"
    # psycopg 3 can hand this GUC back as bytes. b'A' is A mode, not unknown.
    byte_mode = _Connection(version="(openGauss 5.0.0 build abc)", mode=b"A")
    assert gauss.inspect(byte_mode)["mode"] == "A"


def test_gauss_refuses_plain_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _Connection(version="PostgreSQL 16.1", mode="PG")
    with pytest.raises(gauss.DriverError) as caught:
        gauss.inspect(plain)
    assert caught.value.code == "product_not_gauss"


def test_gauss_keys_use_the_pg_catalog_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    pk_sql, fk_sql = gauss.key_sql()
    assert pk_sql == dbc._PK_POSTGRES
    assert fk_sql == dbc._FK_POSTGRES
    assert "pg_catalog.pg_constraint" in pk_sql
    con = _Connection(
        tables=[("public", "orders"), ("pg_catalog", "pg_class")],
        pks=[("public", "orders", "id", 1), ("pg_catalog", "pg_class", "oid", 1)],
        fks=[
            (
                "orders_customer_fkey",
                "public",
                "orders",
                "customer_id",
                "public",
                "customers",
                "id",
                1,
            )
        ],
    )
    cfg = _cfg("gauss")
    tables = dbc.list_source_tables(cfg, con=con)
    assert [table.qualified for table in tables] == ["public.orders"]
    keys = dbc.list_source_keys(cfg, con=con)
    assert keys.primary_keys == {"public.orders": ("id",)}
    assert keys.foreign_keys[0].from_column == "customer_id"
    assert keys.foreign_keys[0].to_table == "public.customers"
    assert _PASSWORD not in "\n".join(con.executed)


def test_oracle_keys_come_from_all_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_ORACLE", "1")
    assert "ALL_CONSTRAINTS" in oracle.PK_SQL
    assert "ALL_CONS_COLUMNS" in oracle.PK_SQL
    assert "ALL_TABLES" in oracle.FK_SQL
    con = _Connection(
        tables=[("HR", "ORDERS"), ("SYS", "DUAL")],
        pks=[("HR", "ORDERS", "ID", 1), ("SYS", "DUAL", "DUMMY", 1)],
        fks=[("ORDERS_FK", "HR", "ORDERS", "CUST_ID", "HR", "CUSTOMERS", "ID", 1)],
    )
    cfg = _cfg("oracle", _ORA_PASSWORD)
    tables = dbc.list_source_tables(cfg, con=con)
    assert [table.qualified for table in tables] == ["HR.ORDERS"]
    keys = dbc.list_source_keys(cfg, con=con)
    assert keys.primary_keys == {"HR.ORDERS": ("ID",)}
    assert "SYS.DUAL" not in keys.primary_keys
    assert keys.foreign_keys[0].to_column == "ID"
    joined = "\n".join(con.executed)
    assert "ALL_CONSTRAINTS" in joined
    assert "ALL_CONS_COLUMNS" in joined
    assert _ORA_PASSWORD not in joined


def test_oracle_thin_connect_never_loads_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (_ROOT / "packages" / "executor" / "dms_executor" / "drivers" / "oracle.py").read_text(
        encoding="utf-8"
    )
    assert "init_oracle_client" not in text
    assert "oracledb.connect" in text
    monkeypatch.setenv("DMS_CONNECT_ORACLE", "1")
    import sys
    import types

    captured: dict[str, Any] = {}

    def _connect(**kwargs: Any) -> _Connection:
        captured.update(kwargs)
        return _Connection(banner="Oracle Database 23ai Free")

    module = types.ModuleType("oracledb")
    module.connect = _connect  # type: ignore[attr-defined]
    module.is_thin_mode = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "oracledb", module)
    opened = oracle.open_source(_cfg("oracle", _ORA_PASSWORD))
    assert captured["service_name"] == "sales"
    assert captured["user"] == "reader"
    assert captured["password"] == _ORA_PASSWORD
    assert "dsn" not in captured
    assert opened.banner == "Oracle Database 23ai Free"
    assert any("SET TRANSACTION READ ONLY" in sql for sql in opened.executed)


def test_oracle_banner_that_is_not_oracle_is_refused() -> None:
    con = _Connection(banner="Some Other Database")
    with pytest.raises(oracle.DriverError) as caught:
        oracle.inspect(con)
    assert caught.value.code == "product_not_oracle"


def test_dialect_gates_accept_reads_and_refuse_dml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    monkeypatch.setenv("DMS_CONNECT_ORACLE", "1")
    assert dialect_for_connector("gauss") == "postgres"
    assert dialect_for_connector("oracle") == "oracle"
    assert (
        guarded_reason(
            "SELECT a FROM public.orders", grantable={"public.orders"}, kind="gauss"
        )
        is None
    )
    assert (
        guarded_reason(
            "DELETE FROM public.orders", grantable={"public.orders"}, kind="gauss"
        )
        == "statement_not_allowed"
    )
    assert (
        guarded_reason("UPDATE public.orders SET a = 1", grantable={"public.orders"}, kind="gauss")
        == "statement_not_allowed"
    )
    assert (
        guarded_reason("SELECT a FROM hr.orders", grantable={"hr.orders"}, kind="oracle") is None
    )
    for sql in (
        "INSERT INTO hr.orders (a) VALUES (1)",
        "UPDATE hr.orders SET a = 1",
        "DELETE FROM hr.orders",
    ):
        assert (
            guarded_reason(sql, grantable={"hr.orders"}, kind="oracle") == "statement_not_allowed"
        )


def test_pages_are_a_window_and_cancel_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    monkeypatch.setenv("DMS_CONNECT_ORACLE", "1")
    import dms_executor.connectors as connectors

    gauss_con = _Connection(version="(openGauss 6.0.0 build abc)", mode="PG")
    monkeypatch.setattr(connectors, "_open", lambda **_k: gauss_con)
    page = run_live_select(
        kind="gauss",
        host="db.example",
        port=5432,
        database="sales",
        user="reader",
        password=_PASSWORD,
        sql="SELECT a FROM public.orders",
        grantable={"public.orders"},
        confirm_runaway=True,
        page_size=2,
        offset=0,
    )
    assert page["truncated"] is False
    assert page["bounded"] is False
    assert page["cancelled"] is False
    assert any("LIMIT 3 OFFSET 0" in sql for sql in gauss_con.executed)
    assert _PASSWORD not in "\n".join(gauss_con.executed)

    import threading

    event = threading.Event()
    event.set()
    cancelled = run_live_select(
        kind="gauss",
        host="db.example",
        port=5432,
        database="sales",
        user="reader",
        password=_PASSWORD,
        sql="SELECT a FROM public.orders",
        grantable={"public.orders"},
        confirm_runaway=True,
        cancel=event,
    )
    assert cancelled["cancelled"] is True
    assert cancelled["error"] == "cancelled"
    assert _PASSWORD not in json.dumps(cancelled)

    ora = _Connection(banner="Oracle Database 23ai Free")
    monkeypatch.setattr(connectors, "_open", lambda **_k: ora)
    ora_page = run_live_select(
        kind="oracle",
        host="db.example",
        port=1521,
        database="sales",
        user="reader",
        password=_ORA_PASSWORD,
        sql="SELECT a FROM hr.orders",
        grantable={"hr.orders"},
        confirm_runaway=True,
        page_size=2,
        offset=4,
    )
    assert ora_page["truncated"] is False
    assert any("FETCH NEXT 3 ROWS ONLY" in sql for sql in ora.executed)
    assert any("OFFSET 4 ROWS" in sql for sql in ora.executed)
    assert _ORA_PASSWORD not in "\n".join(ora.executed)


def test_prompt_names_the_source_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    monkeypatch.setenv("DMS_CONNECT_ORACLE", "1")
    gauss_prompt = gauss.prompt(["public.orders"])
    oracle_prompt = oracle.prompt(["HR.ORDERS"])
    assert gauss_prompt == "DIALECT: postgres\nSCHEMA\n- public.orders"
    assert oracle_prompt == "DIALECT: oracle\nSCHEMA\n- HR.ORDERS"
    _flag(monkeypatch, "DMS_CONNECT_GAUSS", None)
    _flag(monkeypatch, "DMS_CONNECT_ORACLE", None)
    assert gauss.prompt(["public.orders"]).startswith("DIALECT: postgres")
    with pytest.raises(UnknownConnectorDialect):
        dialect_for_connector("gauss")


def test_extras_are_declared() -> None:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "\ngauss = [" in text
    assert "oracledb>=2.0" in text


def test_drivers_do_not_branch_on_question_text() -> None:
    for name in ("gauss.py", "oracle.py"):
        text = (_ROOT / "packages" / "executor" / "dms_executor" / "drivers" / name).read_text(
            encoding="utf-8"
        )
        assert "in question" not in text
        assert "question.lower" not in text


def test_gauss_catalog_falls_back_when_the_server_rejects_ordinality() -> None:
    """openGauss 5 errors on WITH ORDINALITY. The probe still lists the table."""

    class _Cur:
        def __init__(self, owner: _Srv) -> None:
            self._owner = owner
            self._sql = ""

        def execute(self, sql: str, _params: Any = None) -> None:
            self._sql = sql
            self._owner.executed.append(sql)
            if "INFORMATION_SCHEMA" in sql or "WITH ORDINALITY" in sql:
                raise RuntimeError("syntax")

        def fetchall(self) -> list[tuple[Any, ...]]:
            if "relkind" in self._sql:
                return [("public", "dms_probe")]
            if "contype = 'p'" in self._sql:
                return [("public", "dms_probe", "n", 1)]
            return []

        def close(self) -> None:
            return None

    class _Srv:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.rolled = 0

        def cursor(self) -> _Cur:
            return _Cur(self)

        def rollback(self) -> None:
            self.rolled += 1

    con = _Srv()
    assert gauss.visible_tables(con, "postgres") == ["public.dms_probe"]
    assert gauss.keys_payload(con)["primary_keys"]["public.dms_probe"] == ["n"]
    assert con.rolled >= 1
    assert any("relkind" in sql for sql in con.executed)
    assert any("ANY (con.conkey)" in sql for sql in con.executed)


def _live(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


@pytest.mark.skipif(not _live("DMS_CONNECT_GAUSS_LIVE"), reason="openGauss image unavailable")
def test_live_gauss_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema introspect plus one read. CI creates public.dms_probe first."""
    monkeypatch.setenv("DMS_CONNECT_GAUSS", "1")
    password = os.environ["CONNECT_GAUSS_PASSWORD"]
    import psycopg

    with psycopg.connect(
        host=os.environ.get("CONNECT_GAUSS_HOST", "127.0.0.1"),
        port=int(os.environ.get("CONNECT_GAUSS_PORT", "5432")),
        user=os.environ.get("CONNECT_GAUSS_USER", "gaussdb"),
        password=password,
        dbname=os.environ.get("CONNECT_GAUSS_DB", "postgres"),
        connect_timeout=10,
    ) as raw:
        shown = raw.execute("SHOW sql_compatibility").fetchone()
    assert shown is not None
    assert password not in str(shown[0])
    print("sql_compatibility", shown[0])
    common = {
        "kind": "gauss",
        "host": os.environ.get("CONNECT_GAUSS_HOST", "127.0.0.1"),
        "port": int(os.environ.get("CONNECT_GAUSS_PORT", "5432")),
        "database": os.environ.get("CONNECT_GAUSS_DB", "postgres"),
        "user": os.environ.get("CONNECT_GAUSS_USER", "gaussdb"),
        "password": password,
    }
    probed = probe_source(**common)
    dumped = json.dumps(probed)
    assert password not in dumped
    assert probed["dialect"] == "postgres"
    assert probed["prompt"].startswith("DIALECT: postgres")
    version = str(probed["server_version"]).lower()
    assert "opengauss" in version or "gaussdb" in version
    assert "public.dms_probe" in probed["tables"]
    page = run_live_select(
        **common,
        sql="SELECT n FROM public.dms_probe",
        grantable={"public.dms_probe"},
        confirm_runaway=True,
        page_size=10,
    )
    body = json.dumps(page)
    assert password not in body
    assert page["ok"] is True
    assert len(page["rows"]) == 1
    assert list(page["rows"][0].values()) == [1]
    with pytest.raises(ConnectQueryError) as raised:
        run_live_select(
            **common,
            sql="DELETE FROM public.dms_probe",
            grantable={"public.dms_probe"},
            confirm_runaway=True,
        )
    assert raised.value.code == "statement_not_allowed"
    assert password not in str(raised.value)


def _oracle_driver_error(password: str) -> str:
    """The driver exception, with the password removed. Printed when the probe says network."""
    try:
        import oracledb
    except ImportError as exc:
        return f"ImportError: {exc}"
    try:
        con = oracledb.connect(
            user=os.environ.get("CONNECT_ORACLE_USER", "system"),
            password=password,
            host=os.environ.get("CONNECT_ORACLE_HOST", "127.0.0.1"),
            port=int(os.environ.get("CONNECT_ORACLE_PORT", "1521")),
            service_name=os.environ.get("CONNECT_ORACLE_DB", "FREEPDB1"),
            tcp_connect_timeout=10,
        )
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        return text.replace(password, "[redacted]")
    try:
        cur = con.cursor()
        cur.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
        row = cur.fetchone()
        banner = "" if not row else str(row[0])
        return f"driver login ok banner={banner}".replace(password, "[redacted]")
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        return text.replace(password, "[redacted]")
    finally:
        con.close()


@pytest.mark.skipif(not _live("DMS_CONNECT_ORACLE_LIVE"), reason="Oracle Free image unavailable")
def test_live_oracle_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_ORACLE", "1")
    password = os.environ["CONNECT_ORACLE_PASSWORD"]
    try:
        probed = probe_source(
            kind="oracle",
            host=os.environ.get("CONNECT_ORACLE_HOST", "127.0.0.1"),
            port=int(os.environ.get("CONNECT_ORACLE_PORT", "1521")),
            database=os.environ.get("CONNECT_ORACLE_DB", "FREEPDB1"),
            user=os.environ.get("CONNECT_ORACLE_USER", "system"),
            password=password,
        )
    except Exception as exc:
        detail = _oracle_driver_error(password)
        print(detail)
        assert password not in detail
        raise AssertionError(f"{exc}: {detail}") from None
    dumped = json.dumps(probed)
    assert password not in dumped
    assert probed["dialect"] == "oracle"
    assert probed["prompt"].startswith("DIALECT: oracle")
