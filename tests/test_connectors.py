"""CONNECT-01 (dms#366) - read-only connectors behind one interface.

What each source is tested against, stated so nobody reads this file as more:

- SQLite, DuckDB, CSV, Excel: real engines on local fixtures built in ``tmp_path``.
- Snowflake: CONTRACT-LEVEL MOCK ONLY. A fake ``snowflake.connector`` module records
  what the connector sends. No Snowflake account is reached or claimed.
- PostgreSQL, MySQL, SQL Server: a fake DB-API connection here for secret hygiene;
  the live engines are ``tests/test_connectors_live.py`` (CI service containers).

OpenVault is an ``httpx.MockTransport``. No real OV is reached.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
import traceback
import types
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from dms_executor import connectors as c
from dms_executor import db_connector as dbc
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
ROWS = [(i, f"SKU-{i:03d}", i * 10) for i in range(1, 51)]


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(c.FLAG, "1")


@pytest.fixture()
def fixtures(tmp_path: Path) -> dict[str, Path]:
    sq = tmp_path / "src.sqlite"
    con = sqlite3.connect(sq)
    con.execute("CREATE TABLE orders (id INTEGER, sku TEXT, qty INTEGER)")
    con.executemany("INSERT INTO orders VALUES (?, ?, ?)", ROWS)
    con.commit()
    con.close()

    dd = tmp_path / "src.duckdb"
    dcon = duckdb.connect(str(dd))
    dcon.execute("CREATE TABLE orders (id INTEGER, sku VARCHAR, qty INTEGER)")
    dcon.executemany("INSERT INTO orders VALUES (?, ?, ?)", ROWS)
    dcon.close()

    cv = tmp_path / "orders.csv"
    cv.write_text(
        "id,sku,qty\n" + "".join(f"{i},{s},{q}\n" for i, s, q in ROWS), encoding="utf-8"
    )

    # Test fixture only: the product never writes a workbook (Excel is source-only).
    xl = tmp_path / "book.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["id", "sku", "qty"])
    for row in ROWS:
        ws.append(list(row))
    wb.save(xl)
    return {"sqlite": sq, "duckdb": dd, "csv": cv, "excel": xl}


FILE_KINDS = ["sqlite", "duckdb", "csv", "excel"]


def _open(fixtures: dict[str, Path], kind: str, **kw: Any) -> c.ReadOnlyConnector:
    return c.open_connector(c.ConnectionRef(kind=kind, path=str(fixtures[kind])), **kw)  # type: ignore[arg-type]


# --- flag ----------------------------------------------------------------------------


def test_flag_is_off_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(c.FLAG, raising=False)
    assert c.connectors_enabled() is False
    with pytest.raises(c.ConnectorsDisabled):
        c.open_connector(c.ConnectionRef(kind="csv", path=str(tmp_path / "x.csv")))


@pytest.mark.parametrize("value", ["0", "", "false", "no", "off"])
def test_flag_off_values_refuse(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(c.FLAG, value)
    with pytest.raises(c.ConnectorsDisabled):
        c.open_connector(c.ConnectionRef(kind="csv", path="x.csv"))


def test_env_example_documents_the_flag_off() -> None:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert f"{c.FLAG}=0" in lines


def test_nothing_in_api_or_ui_reaches_the_connectors() -> None:
    """Off means unreachable: no route, no Studio call, no package re-export."""
    needles = ("dms_executor.connectors", "open_connector", c.FLAG, "ReadOnlyConnector")
    hits = [
        f"{p.relative_to(ROOT)}: {n}"
        for base in (ROOT / "apps" / "api", ROOT / "apps" / "ui" / "src")
        for p in base.rglob("*")
        if p.is_file() and p.suffix in {".py", ".ts", ".tsx"}
        for n in needles
        if n in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not hits, hits
    init = ast.parse((ROOT / "packages/executor/dms_executor/__init__.py").read_text("utf-8"))
    imported = {
        a.name
        for node in ast.walk(init)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for a in node.names
    } | {
        node.module or ""
        for node in ast.walk(init)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("connectors" in name for name in imported)


# --- the interface on real local engines -------------------------------------------------


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_introspect_schema(fixtures: dict[str, Path], kind: str) -> None:
    tables = _open(fixtures, kind).introspect_schema()
    assert len(tables) == 1
    assert [col.name for col in tables[0].columns] == ["id", "sku", "qty"]
    assert tables[0].name == "orders"


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_sample_returns_n_rows(fixtures: dict[str, Path], kind: str) -> None:
    res = _open(fixtures, kind).sample("orders", 3)
    assert res.columns == ["id", "sku", "qty"]
    assert [tuple(r) for r in res.rows] == ROWS[:3]


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_sample_refuses_a_table_not_in_the_catalog(fixtures: dict[str, Path], kind: str) -> None:
    with pytest.raises(dbc.UnknownSourceTable):
        _open(fixtures, kind).sample('orders"; DROP TABLE orders; --', 3)


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_run_select_returns_the_rows(fixtures: dict[str, Path], kind: str) -> None:
    res = _open(fixtures, kind).run_select(
        "WITH big AS (SELECT sku, qty FROM orders WHERE qty >= 480) "
        "SELECT sku, qty FROM big ORDER BY qty DESC"
    )
    assert res.columns == ["sku", "qty"]
    assert res.rows == [("SKU-050", 500), ("SKU-049", 490), ("SKU-048", 480)]
    assert res.truncated is False


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_row_cap_truncates_and_says_so(fixtures: dict[str, Path], kind: str) -> None:
    res = _open(fixtures, kind, max_rows=7).run_select("SELECT id FROM orders ORDER BY id")
    assert [r[0] for r in res.rows] == list(range(1, 8))
    assert res.truncated is True and res.row_cap == 7


def test_row_cap_has_a_ceiling(fixtures: dict[str, Path]) -> None:
    assert _open(fixtures, "sqlite", max_rows=10**9).max_rows == c.MAX_ROWS_CEILING


_SLOW = {
    "sqlite": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) "
    "SELECT count(*) FROM r",
    "duckdb": "SELECT count(*) FROM range(1000000) a, range(1000000) b "
    "WHERE a.range + b.range = -1",
}


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_timeout_cancels(fixtures: dict[str, Path], kind: str) -> None:
    with pytest.raises(c.SelectTimeout):
        _open(fixtures, kind, timeout_s=0.5).run_select(_SLOW.get(kind, _SLOW["duckdb"]))


# --- guard ----------------------------------------------------------------------------

REJECTS = [
    ("", "postgresql", "empty_sql"),
    ("  ;  ", "postgresql", "empty_sql"),
    ("DELETE FROM orders", "postgresql", "not_select"),
    ("UPDATE orders SET qty = 0", "mysql", "not_select"),
    ("INSERT INTO orders VALUES (1)", "sqlserver", "not_select"),
    ("DROP TABLE orders", "sqlite", "not_select"),
    ("CREATE TABLE x (a int)", "duckdb", "not_select"),
    ("ALTER TABLE orders ADD c int", "snowflake", "not_select"),
    ("TRUNCATE TABLE orders", "postgresql", "not_select"),
    ("GRANT ALL ON orders TO public", "postgresql", "not_select"),
    ("EXEC xp_cmdshell 'dir'", "sqlserver", "not_select"),
    ("VALUES (1)", "postgresql", "not_select"),
    ("SELECT 1; DROP TABLE orders", "postgresql", "multi_statement"),
    ("SELECT 1; SELECT 2", "mysql", "multi_statement"),
    ("SELECT 1 -- trailing", "postgresql", "comment_smuggling"),
    ("SELECT 1 /* x */ FROM orders", "sqlserver", "comment_smuggling"),
    ("SELECT 1 # hash", "mysql", "comment_smuggling"),
    ("/*! DROP TABLE orders */ SELECT 1", "mysql", "comment_smuggling"),
    ("SELECT 1 /*; DROP TABLE orders */", "duckdb", "comment_smuggling"),
    ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "postgresql", "ddl_dml"),
    ("SELECT * INTO copy_t FROM orders", "sqlserver", "select_into"),
    ("SELECT * FROM orders FOR UPDATE", "postgresql", "locking_read"),
    ("SELECT * FROM read_csv('/etc/passwd')", "duckdb", "forbidden_function"),
    ("SELECT pg_read_file('/etc/passwd')", "postgresql", "forbidden_function"),
    ("SELECT nextval('s')", "postgresql", "forbidden_function"),
    ("SELECT LOAD_FILE('/etc/passwd')", "mysql", "forbidden_function"),
    ("SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'y')", "sqlserver", "forbidden_function"),
    ("SELECT load_extension('x')", "sqlite", "forbidden_function"),
    ("SELECT SYSTEM$WAIT(10)", "snowflake", "forbidden_function"),
    ("SELECT (", "postgresql", "parse_error"),
]


@pytest.mark.parametrize(("sql", "kind", "code"), REJECTS)
def test_guard_rejects_with_a_named_code(sql: str, kind: str, code: str) -> None:
    with pytest.raises(c.SelectRejected) as info:
        c.guard_select(sql, kind)
    assert info.value.code == code


ACCEPTS = [
    ("SELECT 1", "postgresql"),
    ("SELECT 1;", "mysql"),
    ("WITH x AS (SELECT 1 AS a) SELECT a FROM x", "sqlserver"),
    ("SELECT a FROM t UNION ALL SELECT b FROM u", "snowflake"),
    ("(SELECT 1)", "duckdb"),
    ("SELECT '-- not a comment', '/* nor this */' FROM t", "postgresql"),
    ("SELECT TOP (5) * FROM [dbo].[orders]", "sqlserver"),
    ("SELECT `delete` FROM `update`", "mysql"),
]


@pytest.mark.parametrize(("sql", "kind"), ACCEPTS)
def test_guard_accepts_one_select(sql: str, kind: str) -> None:
    """R-0005: a guard that refuses legitimate reads is a failure too."""
    assert c.guard_select(sql, kind) == sql.strip().rstrip(";")


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_run_select_rejection_leaves_the_source_unchanged(
    fixtures: dict[str, Path], kind: str
) -> None:
    con = _open(fixtures, kind)
    for sql in ("DELETE FROM orders", "SELECT 1; DELETE FROM orders"):
        with pytest.raises(c.SelectRejected):
            con.run_select(sql)
    assert con.run_select("SELECT count(*) FROM orders").rows == [(50,)]


@pytest.mark.parametrize(
    ("kind", "sql"),
    [
        ("sqlite", "DELETE FROM orders"),
        # mode=ro alone allows this; the authorizer is what refuses it.
        ("sqlite", "ATTACH DATABASE ':memory:' AS side"),
        ("duckdb", "DELETE FROM orders"),
        ("duckdb", "SELECT * FROM read_text('/etc/passwd')"),
        ("csv", "SELECT * FROM read_text('/etc/passwd')"),
    ],
)
def test_engine_session_is_read_only_even_past_the_guard(
    fixtures: dict[str, Path], kind: str, sql: str
) -> None:
    """Defense in depth: SQL that skips ``guard_select`` still meets a locked session."""
    con = _open(fixtures, kind)
    with con._session() as (raw, secret):
        with pytest.raises(c.ConnectorError):
            con._select(raw, secret, sql, 5)
    assert con.run_select("SELECT count(*) FROM orders").rows == [(50,)]


# --- connection reference carries no secret --------------------------------------------


@pytest.mark.parametrize("key", ["password", "PWD", "api_key", "token", "client_secret"])
def test_ref_refuses_credential_options(key: str) -> None:
    with pytest.raises(ValueError, match="OpenVault"):
        c.ConnectionRef(kind="snowflake", service_id="sf", options={key: "x"})


def test_server_ref_needs_a_service_id() -> None:
    with pytest.raises(ValueError, match="service_id"):
        c.ConnectionRef(kind="postgresql", host="db", database="d", user="u")


def test_ref_has_no_secret_field() -> None:
    assert not any(
        c._CREDENTIAL_KEY.search(f) for f in c.ConnectionRef.__dataclass_fields__
    )


# --- OpenVault resolve path ------------------------------------------------------------


def _ov(status: int, body: dict[str, Any], seen: list[httpx.Request]) -> c.OpenVaultSecrets:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json=body)

    return c.OpenVaultSecrets(
        openvault_url="http://ov.test", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_openvault_resolves_by_service_id_without_admin_token() -> None:
    seen: list[httpx.Request] = []
    assert _ov(200, {"token": "tok"}, seen).get("src-erp") == "tok"
    (req,) = seen
    assert req.url.path == "/keys/services"
    assert req.read() == b'{"service_id":"src-erp"}'
    assert "authorization" not in req.headers
    assert not any("admin" in k.lower() for k in req.headers)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_openvault_refusal_is_named(status: int) -> None:
    with pytest.raises(c.SecretUnavailable, match=str(status)):
        _ov(status, {"detail": "openvault_unauthenticated"}, []).get("src-erp")


def test_missing_secret_refuses_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_: Any, **__: Any) -> Any:
        raise AssertionError("connected without a secret")

    monkeypatch.setattr(dbc, "connect", never)
    ref = c.ConnectionRef(kind="postgresql", service_id="s", host="h", database="d", user="u")
    with pytest.raises(c.SecretUnavailable):
        c.open_connector(ref, secrets=_ov(404, {}, [])).introspect_schema()


# --- server kinds: fake DB-API, secret hygiene ---------------------------------------


class _Cursor:
    def __init__(self, con: _Con) -> None:
        self.con = con
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.con.executed.append(sql)
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            self.description = [("TABLE_SCHEMA",), ("TABLE_NAME",), ("COLUMN_NAME",), ("T",)]
            self._rows = [
                (self.con.schema, "ORDERS", "ID", "NUMBER"),
                (self.con.schema, "ORDERS", "SKU", "TEXT"),
                ("INFORMATION_SCHEMA", "TABLES", "X", "TEXT"),
            ]
            return
        if self.con.fail_with:
            raise RuntimeError(self.con.fail_with)
        self.description = [("ID",), ("SKU",)]
        self._rows = [(r[0], r[1]) for r in ROWS]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        return self._rows[:n]

    def close(self) -> None:
        pass


class _Con:
    def __init__(self, schema: str = "PUBLIC", fail_with: str = "") -> None:
        self.schema = schema
        self.fail_with = fail_with
        self.executed: list[str] = []
        self.autocommit: Any = False
        self.timeout = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def close(self) -> None:
        self.closed = True


class _Secrets:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[str] = []

    def get(self, name: str) -> str | None:
        self.calls.append(name)
        return self.value


def _secret() -> str:
    return f"pw-{uuid.uuid4().hex}"


def _leaks(secret: str, *objs: Any) -> list[str]:
    out = []
    for o in objs:
        blobs = [repr(o), str(o)]
        if isinstance(o, BaseException):
            blobs.append("".join(traceback.format_exception(o)))
        if hasattr(o, "__dict__"):
            blobs.append(repr(vars(o)))
        out += [b for b in blobs if secret in b]
    return out


def test_snowflake_contract_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONTRACT-LEVEL MOCK ONLY. Asserts what DMS sends; no Snowflake is reached."""
    calls: list[dict[str, Any]] = []
    con = _Con(schema="SALES")

    def connect(**kw: Any) -> _Con:
        calls.append(kw)
        return con

    pkg = types.ModuleType("snowflake")
    mod = types.ModuleType("snowflake.connector")
    mod.connect = connect  # type: ignore[attr-defined]
    pkg.connector = mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", pkg)
    monkeypatch.setitem(sys.modules, "snowflake.connector", mod)

    secret = _secret()
    secrets = _Secrets(secret)
    ref = c.ConnectionRef(
        kind="snowflake", service_id="sf-prod", user="DMS_RO", database="ANALYTICS",
        options={"account": "acme-xy123", "warehouse": "WH_XS", "role": "DMS_READER"},
    )
    k = c.open_connector(ref, secrets=secrets, max_rows=5, timeout_s=7)

    tables = k.introspect_schema()
    assert [t.qualified for t in tables] == ["SALES.ORDERS"]
    sample = k.sample("orders", 2)
    assert con.executed[-1] == 'SELECT * FROM "SALES"."ORDERS" LIMIT 2'
    assert sample.rows == [(1, "SKU-001"), (2, "SKU-002")]
    res = k.run_select("SELECT ID, SKU FROM SALES.ORDERS")
    assert res.truncated is True and len(res.rows) == 5
    with pytest.raises(c.SelectRejected):
        k.run_select("DELETE FROM SALES.ORDERS")

    assert secrets.calls == ["sf-prod"] * 3
    kw = calls[0]
    assert kw["password"] == secret and kw["account"] == "acme-xy123"
    assert kw["role"] == "DMS_READER" and kw["warehouse"] == "WH_XS"
    assert kw["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 7
    assert con.closed
    assert not _leaks(secret, k, ref, res, sample)


def test_snowflake_without_driver_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "snowflake", None)
    ref = c.ConnectionRef(kind="snowflake", service_id="sf", options={"account": "a"})
    with pytest.raises(c.ConnectorError, match="not installed"):
        c.open_connector(ref, secrets=_Secrets("x")).introspect_schema()


@pytest.mark.parametrize("kind", ["postgresql", "mysql", "sqlserver"])
def test_secret_is_never_logged_stored_or_raised(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    """The secret reaches the driver and nowhere else - including hostile driver errors."""
    caplog.set_level("DEBUG")
    secret = _secret()
    seen_cfg: list[dbc.SourceConfig] = []
    mode = {"fail": ""}

    @contextmanager
    def fake_connect(cfg: dbc.SourceConfig) -> Iterator[Any]:
        seen_cfg.append(cfg)
        if mode["fail"] == "connect":
            try:
                raise RuntimeError(f"auth failed for PWD={cfg.password}")
            except RuntimeError as exc:
                raise dbc.SourceConnectionError(f"could not connect to {cfg.describe()}") from exc
        con = _Con(fail_with=f"driver echoed password {cfg.password}" if mode["fail"] else "")
        if kind == "mysql":
            con.autocommit = lambda on: None  # pymysql's autocommit is a method
        yield con

    monkeypatch.setattr(dbc, "connect", fake_connect)
    ref = c.ConnectionRef(kind=kind, service_id="erp", host="db.local", database="erp", user="ro")  # type: ignore[arg-type]
    k = c.open_connector(ref, secrets=_Secrets(secret))

    tables = k.introspect_schema()
    res = k.run_select("SELECT ID, SKU FROM PUBLIC.ORDERS")
    assert seen_cfg and all(cfg.password == secret for cfg in seen_cfg)

    errors: list[BaseException] = []
    for fail in ("connect", "query"):
        mode["fail"] = fail
        with pytest.raises(c.ConnectorError) as info:
            k.run_select("SELECT ID FROM PUBLIC.ORDERS")
        errors.append(info.value)
        assert "[REDACTED]" in str(info.value)

    out = capsys.readouterr()
    assert secret not in caplog.text and secret not in out.out + out.err
    assert not _leaks(secret, k, ref, res, *tables, *errors)
    assert all(secret not in repr(cfg) for cfg in seen_cfg)
