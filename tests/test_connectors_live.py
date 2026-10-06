"""CONNECT-01 (dms#366) - PostgreSQL, MySQL, SQL Server against real engines.

Runs in CI against throwaway service containers started by ``.github/workflows/ci.yml``
(step "Start connector source containers"). That step generates a random password per
run, masks it in the log, and exports ``DMS_CONNECTOR_CI_PASSWORD``. The connector never
reads that variable. It reaches the connector only as an OpenVault response, through an
``httpx.MockTransport`` that stands in for OV. No real OV is reached.

Locally, with ``DMS_CONNECTOR_LIVE`` unset, the module skips and says why. With it set,
a missing driver or an unreachable container FAILS (R-0002: a skip is not a pass).
"""

from __future__ import annotations

import os
import time
import traceback
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from dms_executor import connectors as c

LIVE = os.environ.get("DMS_CONNECTOR_LIVE") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE, reason="DMS_CONNECTOR_LIVE!=1: no source containers (CI runs these)"
)

PASSWORD = os.environ.get("DMS_CONNECTOR_CI_PASSWORD", "")
HOST = os.environ.get("DMS_CONNECTOR_HOST", "127.0.0.1")
ROWS = [(i, f"SKU-{i:03d}", i * 10) for i in range(1, 51)]


def _wait(connect: Callable[[], Any], what: str, budget_s: float = 180.0) -> Any:
    deadline = time.monotonic() + budget_s
    while True:
        try:
            return connect()
        except Exception as exc:  # noqa: BLE001 - containers boot at their own pace
            if time.monotonic() > deadline:
                raise AssertionError(f"{what} not reachable: {type(exc).__name__}") from None
            time.sleep(2)


def _seed_postgres() -> None:
    import psycopg

    con = _wait(
        lambda: psycopg.connect(
            host=HOST, port=5432, user="postgres", password=PASSWORD, dbname="postgres",
            autocommit=True, connect_timeout=5,
        ),
        "postgres",
    )
    with con:
        con.execute("DROP TABLE IF EXISTS public.orders")
        con.execute("CREATE TABLE public.orders (id int, sku text, qty int)")
        with con.cursor() as cur:
            cur.executemany("INSERT INTO public.orders VALUES (%s, %s, %s)", ROWS)


def _seed_mysql() -> None:
    import pymysql

    con = _wait(
        lambda: pymysql.connect(
            host=HOST, port=3306, user="root", password=PASSWORD, database="connect01",
            autocommit=True, connect_timeout=5,
        ),
        "mysql",
    )
    try:
        with con.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS orders")
            cur.execute("CREATE TABLE orders (id int, sku varchar(20), qty int)")
            cur.executemany("INSERT INTO orders VALUES (%s, %s, %s)", ROWS)
    finally:
        con.close()


def _seed_sqlserver() -> None:
    import pyodbc

    def dsn(db: str) -> str:
        return (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={HOST},1433;DATABASE={db};UID=sa;PWD={{{PASSWORD}}};"
            "Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=5;"
        )

    con = _wait(lambda: pyodbc.connect(dsn("master"), autocommit=True), "sqlserver")
    con.execute("IF DB_ID('connect01') IS NULL CREATE DATABASE connect01")
    con.close()
    con = pyodbc.connect(dsn("connect01"), autocommit=True)
    con.execute("IF OBJECT_ID('dbo.orders') IS NOT NULL DROP TABLE dbo.orders")
    con.execute("CREATE TABLE dbo.orders (id int, sku varchar(20), qty int)")
    con.cursor().executemany("INSERT INTO dbo.orders VALUES (?, ?, ?)", ROWS)
    con.close()


SOURCES: dict[str, dict[str, Any]] = {
    "postgresql": {
        "seed": _seed_postgres, "user": "postgres", "database": "postgres", "port": 5432,
        "schema": "public", "slow": "SELECT pg_sleep(10)", "options": {},
    },
    "mysql": {
        "seed": _seed_mysql, "user": "root", "database": "connect01", "port": 3306,
        "schema": "connect01", "slow": "SELECT BENCHMARK(5000000000, SHA2('x', 256))",
        "options": {},
    },
    "sqlserver": {
        "seed": _seed_sqlserver, "user": "sa", "database": "connect01", "port": 1433,
        "schema": "dbo",
        "slow": "SELECT COUNT_BIG(*) FROM sys.all_objects a CROSS JOIN sys.all_objects b "
        "CROSS JOIN sys.all_objects c",
        "options": {"trust_server_certificate": "true"},
    },
}
KINDS = list(SOURCES)
_SEEDED: set[str] = set()


def _ov(secret: str, seen: list[str] | None = None) -> c.OpenVaultSecrets:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req.url.path)
        return httpx.Response(200, json={"token": secret})

    return c.OpenVaultSecrets(
        openvault_url="http://ov.test", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(c.FLAG, "1")


def _connector(kind: str, *, secret: str | None = None, **kw: Any) -> c.ReadOnlyConnector:
    assert PASSWORD, "DMS_CONNECTOR_LIVE=1 but DMS_CONNECTOR_CI_PASSWORD is empty"
    src = SOURCES[kind]
    if kind not in _SEEDED:
        src["seed"]()
        _SEEDED.add(kind)
    ref = c.ConnectionRef(
        kind=kind,  # type: ignore[arg-type]
        service_id=f"ci-{kind}",
        host=HOST,
        port=src["port"],
        database=src["database"],
        user=src["user"],
        options=src["options"],
    )
    return c.open_connector(ref, secrets=_ov(PASSWORD if secret is None else secret), **kw)


@pytest.mark.parametrize("kind", KINDS)
def test_live_introspect(kind: str) -> None:
    tables = {t.qualified.lower(): t for t in _connector(kind).introspect_schema()}
    orders = tables[f"{SOURCES[kind]['schema']}.orders"]
    assert [col.name.lower() for col in orders.columns] == ["id", "sku", "qty"]


@pytest.mark.parametrize("kind", KINDS)
def test_live_sample(kind: str) -> None:
    res = _connector(kind).sample(f"{SOURCES[kind]['schema']}.orders", 3)
    assert [col.lower() for col in res.columns] == ["id", "sku", "qty"]
    assert len(res.rows) == 3 and res.truncated is False
    assert {tuple(r) for r in res.rows} <= set(ROWS)


@pytest.mark.parametrize("kind", KINDS)
def test_live_run_select(kind: str) -> None:
    res = _connector(kind).run_select(
        "WITH big AS (SELECT sku, qty FROM orders WHERE qty >= 480) "
        "SELECT sku, qty FROM big ORDER BY qty DESC"
    )
    assert [tuple(r) for r in res.rows] == [("SKU-050", 500), ("SKU-049", 490), ("SKU-048", 480)]


@pytest.mark.parametrize("kind", KINDS)
def test_live_row_cap(kind: str) -> None:
    res = _connector(kind, max_rows=7).run_select("SELECT id FROM orders ORDER BY id")
    assert [r[0] for r in res.rows] == list(range(1, 8)) and res.truncated is True


@pytest.mark.parametrize("kind", KINDS)
def test_live_timeout(kind: str) -> None:
    started = time.monotonic()
    with pytest.raises(c.SelectTimeout):
        _connector(kind, timeout_s=1).run_select(SOURCES[kind]["slow"])
    assert time.monotonic() - started < 30


@pytest.mark.parametrize("kind", KINDS)
def test_live_guard_leaves_rows_unchanged(kind: str) -> None:
    k = _connector(kind)
    for sql in (
        "DELETE FROM orders",
        "SELECT 1; DELETE FROM orders",
        "SELECT id FROM orders /* ; DELETE FROM orders */",
    ):
        with pytest.raises(c.SelectRejected):
            k.run_select(sql)
    assert k.run_select("SELECT COUNT(*) FROM orders").rows[0][0] == 50


@pytest.mark.parametrize("kind", ["postgresql", "mysql"])
def test_live_session_is_read_only_past_the_guard(kind: str) -> None:
    """SQL Server is not in this list: its ODBC read-only flag is advisory, so SQL
    Server relies on the guard plus a db_datareader login. Not claimed here."""
    k = _connector(kind)
    with k._session() as (raw, secret):
        with pytest.raises(c.ConnectorError):
            k._select(raw, secret, "DELETE FROM orders", 5)
    assert k.run_select("SELECT COUNT(*) FROM orders").rows[0][0] == 50


@pytest.mark.parametrize("kind", KINDS)
def test_live_secret_comes_from_ov_and_never_leaks(
    kind: str, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level("DEBUG")
    _connector(kind).introspect_schema()  # seeds; proves the OV secret opens the engine
    wrong = f"wrong-{PASSWORD[::-1]}"
    with pytest.raises(c.ConnectorError) as info:
        _connector(kind, secret=wrong).run_select("SELECT 1")
    blob = "".join(traceback.format_exception(info.value)) + repr(info.value)
    out = capsys.readouterr()
    for secret in (PASSWORD, wrong):
        assert secret not in blob
        assert secret not in caplog.text
        assert secret not in out.out + out.err
