"""DMS-CONNECT-03: S3, Azure, and Salesforce on connect-v2. Oracle ask cap.

Flags default off. Only the digit 1 turns one on. Unit tests use fakes.
Live asks run when CI has started the container. Salesforce uses the
recorded fixture in tests/fixtures/connect_salesforce.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.connect_store import ConnectStore, ConnectStoreError, allowed_kinds
from dms_executor.connectors import (
    ConnectQueryError,
    governed_ask,
    guarded_reason,
)
from dms_executor.drivers import azure_blob, azure_sql, flag_on, s3, salesforce
from dms_executor.drivers.file_sql import aggregate, run_bounded, sniff_csv
from dms_executor.drivers.limits import BYTE_CAP, clip_records
from dms_executor.drivers.s3_sign import sign
from dms_executor.sql_loop import UnknownConnectorDialect, dialect_for_connector

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "tests" / "fixtures" / "connect_salesforce"
_PASSWORD = "canary-connect03-secret"
_REF = "dms/connect03"
_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ASK = "SELECT category, SUM(CAST(amount AS DOUBLE)) AS total FROM sales GROUP BY category"
_BASE = frozenset({"postgresql", "mysql", "mariadb", "sqlserver", "sqlite"})
_FLAGS = (
    "DMS_CONNECT_S3",
    "DMS_CONNECT_AZURE",
    "DMS_CONNECT_SALESFORCE",
    "DMS_CONNECT_ORACLE",
)


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FLAGS:
        monkeypatch.delenv(name, raising=False)


def _evidence(kind: str, rows: list[dict[str, Any]]) -> None:
    line = "CONNECT-03 " + kind + " ask " + json.dumps(rows, default=str, sort_keys=True)
    print(line)
    path = os.environ.get("CONNECT03_EVIDENCE")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        lowered = {str(key).lower(): value for key, value in row.items()}
        category = lowered.get("category")
        total = lowered.get("total")
        if category is None or total is None:
            continue
        out[str(category)] = float(total)
    return out


def test_flags_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert s3.enabled() is False
    assert azure_sql.enabled() is False
    assert azure_blob.enabled() is False
    assert salesforce.enabled() is False
    assert allowed_kinds() == _BASE
    for raw in ("true", "yes", "on", "01"):
        monkeypatch.setenv("DMS_CONNECT_S3", raw)
        monkeypatch.setenv("DMS_CONNECT_AZURE", raw)
        monkeypatch.setenv("DMS_CONNECT_SALESFORCE", raw)
        assert flag_on("DMS_CONNECT_S3") is False
        assert allowed_kinds() == _BASE
    monkeypatch.setenv("DMS_CONNECT_S3", "1")
    monkeypatch.setenv("DMS_CONNECT_AZURE", "1")
    monkeypatch.setenv("DMS_CONNECT_SALESFORCE", "1")
    assert "s3" in allowed_kinds()
    assert "azure_sql" in allowed_kinds()
    assert "azure_blob" in allowed_kinds()
    assert "salesforce" in allowed_kinds()


def test_store_rejects_new_kinds_until_the_digit(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    store = ConnectStore("postgresql://127.0.0.1:1/nope", _TENANT)

    def _code(kind: str) -> str:
        with pytest.raises(ConnectStoreError) as caught:
            store.save_source(
                source_id="11111111-1111-1111-1111-111111111111",
                space_id="space",
                connector_id="c",
                kind=kind,
                host="db.example",
                port=443,
                database="sales",
                cred_ref=_REF,
                mode="live",
                server_version="",
                exposed=["sales"],
                chosen=["sales"],
                actor="actor",
                token_id="token",
            )
        return caught.value.code

    assert _code("s3") == _code("azure_sql") == _code("azure_blob") == "bad_request"
    assert _code("salesforce") == _code("snowflake") == "bad_request"
    assert _PASSWORD not in _code("s3")


def test_dialect_stays_unknown_until_the_digit(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    for kind in ("s3", "azure_sql", "azure_blob", "salesforce"):
        with pytest.raises(UnknownConnectorDialect):
            dialect_for_connector(kind)
    monkeypatch.setenv("DMS_CONNECT_S3", "1")
    monkeypatch.setenv("DMS_CONNECT_AZURE", "1")
    monkeypatch.setenv("DMS_CONNECT_SALESFORCE", "1")
    assert dialect_for_connector("s3") == "postgres"
    assert dialect_for_connector("azure_blob") == "postgres"
    assert dialect_for_connector("salesforce") == "postgres"
    assert dialect_for_connector("azure_sql") == "tsql"


def test_csv_schema_has_types_and_no_cell(tmp_path: Path) -> None:
    body = b"category,amount\nA,10\nA,20\nB,12\n"
    columns = sniff_csv(body)
    dumped = json.dumps(columns)
    assert columns == [
        {"name": "category", "type": "VARCHAR"},
        {"name": "amount", "type": "DOUBLE"},
    ]
    for cell in ('"A"', '"B"', "10", "20", "12"):
        assert cell not in dumped
    path = tmp_path / "sales.csv"
    path.write_bytes(body)
    names, rows, truncated = aggregate({"sales": path}, _ASK)
    assert truncated is False
    assert names == ["category", "total"]
    assert _totals([{names[i]: row[i] for i in range(len(names))} for row in rows]) == {
        "A": 30.0,
        "B": 12.0,
    }


def test_parquet_aggregate(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "sales.parquet"
    pq.write_table(
        pa.table({"category": ["A", "A", "B"], "amount": [10, 20, 12]}),
        path,
    )
    names, rows, truncated = aggregate(
        {"sales": path},
        "SELECT category, SUM(amount) AS total FROM sales GROUP BY category",
    )
    assert truncated is False
    got = _totals([{names[i]: row[i] for i in range(len(names))} for row in rows])
    assert got == {"A": 30.0, "B": 12.0}


def test_row_cap_is_visible() -> None:
    rows = [{"category": str(i), "total": 1} for i in range(1005)]
    kept, truncated, _used = clip_records(rows)
    assert truncated is True
    assert len(kept) == 1000


def test_byte_cap_constant_refuses_a_prefix_sum() -> None:
    huge = {"category": "A", "total": "x" * (BYTE_CAP + 10)}
    kept, truncated, _used = clip_records([huge])
    assert kept == []
    assert truncated is True


def test_timeout_helper_stops() -> None:
    import time

    holder = run_bounded(lambda: time.sleep(5), 0.2)
    assert holder.get("timed_out") is True


def test_s3_signature_matches_the_published_example() -> None:
    now = datetime(2013, 5, 24, tzinfo=UTC)
    headers = sign(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        {"Range": "bytes=0-9"},
        b"",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        now=now,
    )
    assert headers["Authorization"].endswith(
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    assert "wJalrXUtnFEMI" not in headers["Authorization"]


def test_write_is_refused_before_a_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_S3", "1")
    assert (
        guarded_reason("DELETE FROM sales", grantable={"sales"}, kind="s3")
        == "statement_not_allowed"
    )
    with pytest.raises(ConnectQueryError) as caught:
        governed_ask(
            kind="s3",
            host="127.0.0.1",
            port=9,
            database="sales",
            user="reader",
            password=_PASSWORD,
            sql="DELETE FROM sales",
            grantable={"sales"},
        )
    assert caught.value.code == "statement_not_allowed"
    assert _PASSWORD not in str(caught.value)


def test_azure_sql_schema_and_sum_use_a_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_AZURE", "1")
    seen: list[str] = []

    class _Cursor:
        def __init__(self) -> None:
            self._sql = ""
            self.description: list[tuple[Any, ...]] = [("category",), ("total",)]

        def execute(self, sql: str, *_args: Any) -> None:
            self._sql = sql
            seen.append(sql)
            if "SUM" in sql.upper():
                self.description = [("category",), ("total",)]

        def fetchall(self) -> list[tuple[Any, ...]]:
            sql = self._sql.upper()
            if "INFORMATION_SCHEMA.COLUMNS" in sql:
                return [
                    ("dbo", "sales", "category", "nvarchar"),
                    ("dbo", "sales", "amount", "int"),
                    ("sys", "objects", "name", "nvarchar"),
                ]
            if "INFORMATION_SCHEMA.TABLES" in sql:
                return [("dbo", "sales"), ("sys", "objects")]
            if "@@VERSION" in sql:
                return [("Microsoft SQL Server",)]
            if "SUM" in sql:
                return [("A", 30), ("B", 12)]
            return []

        def close(self) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def close(self) -> None:
            return None

    import sys
    import types

    module = types.ModuleType("pymssql")
    module.connect = lambda **_kwargs: _Conn()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymssql", module)
    body = governed_ask(
        kind="azure_sql",
        host="db.example",
        port=1433,
        database="sales",
        user="reader",
        password=_PASSWORD,
        sql="SELECT category, SUM(amount) AS total FROM dbo.sales GROUP BY category",
        grantable={"dbo.sales"},
    )
    dumped = json.dumps(body)
    assert _PASSWORD not in dumped
    assert body["schema_map"]["objects"][0]["name"] == "dbo.sales"
    assert "30" not in json.dumps(body["schema_map"])
    assert _totals(body["rows"]) == {"A": 30.0, "B": 12.0}
    assert any("FETCH NEXT" in sql for sql in seen)
    assert _PASSWORD not in "\n".join(seen)


def test_salesforce_fixture_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_SALESFORCE", "1")
    sobjects = json.loads((_FIXTURE / "sobjects.json").read_text(encoding="utf-8"))
    describe = json.loads((_FIXTURE / "describe.json").read_text(encoding="utf-8"))
    query = json.loads((_FIXTURE / "query.json").read_text(encoding="utf-8"))
    seen: list[str] = []

    def transport(
        method: str, url: str, headers: dict[str, str], body: bytes, timeout: int
    ) -> tuple[int, dict[str, Any]]:
        assert timeout > 0
        seen.append(url)
        if url.endswith("/services/oauth2/token"):
            assert method == "POST"
            assert _PASSWORD.encode() in body
            return 200, {
                "access_token": "fixture-token",
                "instance_url": "https://example.my.salesforce.com",
            }
        if url.endswith("/sobjects"):
            assert headers["Authorization"] == "Bearer fixture-token"
            return 200, sobjects
        if url.endswith("/sobjects/Sale/describe"):
            return 200, describe
        if "/query" in url:
            return 200, query
        raise AssertionError(url)

    salesforce.set_transport(transport)
    try:
        body = governed_ask(
            kind="salesforce",
            host="login.salesforce.com",
            port=443,
            database="v59.0",
            user="reader",
            password=_PASSWORD,
            client_id="client",
            client_secret="client-secret",
            sql="SELECT category, SUM(amount) AS total FROM sale GROUP BY category",
            grantable={"Sale"},
        )
    finally:
        salesforce.set_transport(None)
    dumped = json.dumps(body)
    schema = json.dumps(body["schema_map"])
    assert _PASSWORD not in dumped
    assert "fixture-token" not in dumped
    assert "client-secret" not in dumped
    assert "should-not-copy" not in schema
    assert "Secret__c" not in schema
    names = [obj["name"] for obj in body["schema_map"]["objects"]]
    assert names == ["Sale"]
    columns = {col["name"]: col["type"] for col in body["schema_map"]["objects"][0]["columns"]}
    assert columns == {"Category": "string", "Amount": "currency"}
    for cell in ("30", "12", '"A"', '"B"'):
        assert cell not in schema
    assert _totals(body["rows"]) == {"A": 30.0, "B": 12.0}
    assert body["truncated"] is False
    _evidence("salesforce", body["rows"])


def test_adls_list_maps_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_AZURE", "1")
    listed = {
        "paths": [
            {"name": "sales.csv", "contentLength": "40", "isDirectory": "false"},
            {"name": "raw/skip.csv", "contentLength": "4", "isDirectory": "false"},
        ]
    }
    csv_body = b"category,amount\nA,10\n"

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        payload: bytes = b"",
        *,
        timeout: int = 5,
        cap: int = 1,
    ) -> tuple[int, dict[str, str], bytes]:
        assert method == "GET"
        assert payload == b""
        assert timeout > 0
        assert cap > 0
        assert _PASSWORD not in url
        if "resource=filesystem" in url:
            return 200, {}, json.dumps(listed).encode()
        if url.endswith("/sales.csv"):
            return 200, {"content-length": str(len(csv_body))}, csv_body
        return 404, {}, b""

    monkeypatch.setattr(azure_blob, "exchange", transport)
    schema, tables = azure_blob._Client(
        "acct.dfs.core.windows.net",
        443,
        "acct",
        "a2V5",
        tls=True,
        timeout=5,
    ).introspect("lake")
    dumped = json.dumps(schema)
    assert tables == ["sales"]
    assert "10" not in dumped
    assert schema["objects"][0]["columns"][0]["name"] == "category"
    assert "raw/skip.csv" not in dumped


def _live(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


@pytest.mark.skipif(not _live("DMS_CONNECT_S3_LIVE"), reason="MinIO is not up")
def test_live_s3_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_S3", "1")
    host = os.environ.get("CONNECT_S3_HOST", "127.0.0.1")
    port = int(os.environ.get("CONNECT_S3_PORT", "9000"))
    access = os.environ["CONNECT_S3_ACCESS"]
    secret = os.environ["CONNECT_S3_SECRET"]
    _put_object(host, port, access, secret, "sales", "sales.csv", _CSV)
    body = governed_ask(
        kind="s3",
        host=host,
        port=port,
        database="sales",
        user=access,
        password=secret,
        sql=_ASK,
        grantable={"sales"},
    )
    dumped = json.dumps(body)
    assert secret not in dumped
    assert access not in dumped
    assert _totals(body["rows"]) == {"A": 30.0, "B": 12.0}
    assert "10" not in json.dumps(body["schema_map"])
    _evidence("s3", body["rows"])


@pytest.mark.skipif(not _live("DMS_CONNECT_AZURE_BLOB_LIVE"), reason="Azurite is not up")
def test_live_azure_blob_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_AZURE", "1")
    host = os.environ.get("CONNECT_BLOB_HOST", "127.0.0.1")
    port = int(os.environ.get("CONNECT_BLOB_PORT", "10000"))
    account = os.environ.get("CONNECT_BLOB_ACCOUNT", "devstoreaccount1")
    key = os.environ["CONNECT_BLOB_KEY"]
    _put_blob(host, port, account, key, "sales", "sales.csv", _CSV)
    body = governed_ask(
        kind="azure_blob",
        host=host,
        port=port,
        database="sales",
        user=account,
        password=key,
        sql=_ASK,
        grantable={"sales"},
    )
    dumped = json.dumps(body)
    assert key not in dumped
    assert _totals(body["rows"]) == {"A": 30.0, "B": 12.0}
    _evidence("azure-blob", body["rows"])


@pytest.mark.skipif(not _live("DMS_CONNECT_AZURE_SQL_LIVE"), reason="SQL Server is not up")
def test_live_azure_sql_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_AZURE", "1")
    host = os.environ.get("CONNECT_AZURE_SQL_HOST", "127.0.0.1")
    port = int(os.environ.get("CONNECT_AZURE_SQL_PORT", "1433"))
    user = os.environ.get("CONNECT_AZURE_SQL_USER", "sa")
    password = os.environ["CONNECT_AZURE_SQL_PASSWORD"]
    database = os.environ.get("CONNECT_AZURE_SQL_DB", "master")
    _seed_sql(host, port, user, password, database)
    body = governed_ask(
        kind="azure_sql",
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        sql="SELECT category, SUM(amount) AS total FROM dbo.sales GROUP BY category",
        grantable={"dbo.sales"},
    )
    dumped = json.dumps(body)
    assert password not in dumped
    assert _totals(body["rows"]) == {"A": 30.0, "B": 12.0}
    _evidence("azure-sql", body["rows"])


_CSV = b"category,amount\nA,10\nA,20\nB,12\n"


def _put_object(
    host: str, port: int, access: str, secret: str, bucket: str, key: str, body: bytes
) -> None:
    from dms_executor.drivers.http_cap import exchange
    from dms_executor.drivers.s3_sign import signed_url

    endpoint = f"{host}:{port}"
    for path, payload in ((f"/{bucket}", b""), (f"/{bucket}/{key}", body)):
        url, headers = signed_url(
            "PUT",
            endpoint,
            path,
            "",
            access_key=access,
            secret_key=secret,
            payload=payload,
            tls=False,
        )
        status, _headers, raw = exchange("PUT", url, headers, payload, timeout=10, cap=10000)
        if status not in {200, 204}:
            text = raw.decode("utf-8", "replace").replace(secret, "[redacted]")
            raise AssertionError(f"s3 setup {status} {text[:400]}")


def _put_blob(
    host: str, port: int, account: str, key: str, container: str, name: str, body: bytes
) -> None:
    from urllib.parse import urlencode

    from dms_executor.drivers.azure_sign import sign
    from dms_executor.drivers.http_cap import exchange

    root = f"http://{host}:{port}/{account}"
    for path, payload, query in (
        (f"/{container}", b"", {"restype": "container"}),
        (f"/{container}/{name}", body, {}),
    ):
        query_text = urlencode(query)
        url = root + path + (("?" + query_text) if query_text else "")
        signed, headers = sign(
            "PUT",
            url,
            account=account,
            key_b64=key,
            query=query,
            payload=payload,
            extra_headers={"x-ms-blob-type": "BlockBlob"} if payload else None,
            content_type="text/csv" if payload else "",
        )
        status, _headers, raw = exchange("PUT", signed, headers, payload, timeout=10, cap=10000)
        if status not in {200, 201, 204, 409}:
            text = raw.decode("utf-8", "replace").replace(key, "[redacted]")
            raise AssertionError(f"blob setup {status} {text[:400]}")


def _seed_sql(host: str, port: int, user: str, password: str, database: str) -> None:
    import pymssql

    try:
        con = pymssql.connect(
            server=host,
            port=port,
            user=user,
            password=password,
            database=database,
            login_timeout=10,
            timeout=30,
        )
    except Exception as exc:
        text = str(exc).replace(password, "[redacted]")
        raise AssertionError(text) from None
    cur = con.cursor()
    try:
        cur.execute("IF OBJECT_ID('dbo.sales', 'U') IS NOT NULL DROP TABLE dbo.sales")
        cur.execute("CREATE TABLE dbo.sales (category nvarchar(20), amount int)")
        cur.executemany(
            "INSERT INTO dbo.sales (category, amount) VALUES (%s, %s)",
            [("A", 10), ("A", 20), ("B", 12)],
        )
        con.commit()
    finally:
        cur.close()
        con.close()
