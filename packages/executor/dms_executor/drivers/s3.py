"""Amazon S3 CSV and Parquet, read-only, behind DMS_CONNECT_S3.

Objects are listed and typed into a compact schema map. A governed query
downloads a granted object only when it fits in the byte cap, registers it
as a table, and runs the aggregation in local DuckDB. The model is given
the map, not the file and not an object-store URI.

``user`` is the access key. ``password`` is the secret key. Both come from
the OpenVault JSON the connect book already resolves. They are not stored.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from dms_executor.drivers import DriverError, flag_on
from dms_executor.drivers.file_sql import (
    FileSqlError,
    aggregate,
    object_stem,
    sniff_csv,
    sniff_parquet,
)
from dms_executor.drivers.guard import refuse
from dms_executor.drivers.http_cap import exchange
from dms_executor.drivers.limits import BYTE_CAP, CATALOG_CAP, CONNECT_TIMEOUT_S
from dms_executor.drivers.s3_sign import signed_url
from dms_executor.drivers.schema_map import compact_schema, prompt_lines

KIND = "s3"
DIALECT = "postgres"
DEFAULT_PORT = 443
FLAG = "DMS_CONNECT_S3"
_AUTH_CODES = frozenset({"InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied"})


def enabled() -> bool:
    return flag_on(FLAG)


class _Session:
    def __init__(self, version: str, tables: list[str], schema: dict[str, Any]) -> None:
        self.version = version
        self.tables = tables
        self.schema = schema

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def connect_login(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    timeout: int,
    tls: bool = False,
) -> _Session:
    if not enabled():
        raise DriverError("network")
    client = _Client(host, int(port), user, password, tls=tls, timeout=int(timeout))
    schema, tables = client.introspect(database)
    return _Session("s3", tables, schema)


def inspect(con: _Session) -> dict[str, str]:
    return {"version": con.version, "mode": "", "product": "s3", "dialect": DIALECT}


def server_version(con: _Session) -> str:
    return con.version


def visible_tables(con: _Session, _database: str) -> list[str]:
    return list(con.tables)


def keys_payload(_con: _Session) -> dict[str, Any]:
    return {"primary_keys": {}, "foreign_keys": []}


def schema_map(con: _Session) -> dict[str, Any]:
    return con.schema


def prompt(tables: list[str], schema: dict[str, Any] | None = None) -> str:
    return prompt_lines(DIALECT, tables, schema)


def page_sql(statement: str, page_size: int, offset: int) -> str:
    return (
        f"SELECT * FROM ({statement}) AS _dms_page "
        f"LIMIT {int(page_size) + 1} OFFSET {int(offset)}"
    )


def governed_ask(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sql: str,
    grantable: set[str],
    tls: bool = False,
    **_extra: Any,
) -> dict[str, Any]:
    from dms_executor.drivers.limits import result

    if not enabled():
        raise DriverError("network")
    refuse(KIND, sql, grantable)
    client = _Client(host, int(port), user, password, tls=tls, timeout=CONNECT_TIMEOUT_S)
    schema, _tables = client.introspect(database)
    columns, rows, truncated = client.aggregate(database, sql, grantable, schema)
    body = result(columns, [_pair(columns, row) for row in rows], schema=schema)
    if truncated:
        body["truncated"] = True
        body["bounded"] = True
        body["complete"] = False
    return body


def _pair(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return {columns[i]: _cell(row[i]) for i in range(min(len(columns), len(row)))}


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class _Client:
    def __init__(
        self,
        host: str,
        port: int,
        access_key: str,
        secret_key: str,
        *,
        tls: bool,
        timeout: int,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._access = access_key
        self._secret = secret_key
        self._tls = tls or int(port) == 443
        self._timeout = timeout

    def _endpoint(self) -> str:
        if self._port in {80, 443}:
            return self._host
        return f"{self._host}:{self._port}"

    def _call(
        self,
        method: str,
        path: str,
        query: str,
        *,
        extra: dict[str, str] | None = None,
        payload: bytes = b"",
        cap: int = BYTE_CAP,
    ) -> tuple[int, dict[str, str], bytes]:
        url, headers = signed_url(
            method,
            self._endpoint(),
            path,
            query,
            access_key=self._access,
            secret_key=self._secret,
            payload=payload,
            extra_headers=extra,
            tls=self._tls,
        )
        try:
            return exchange(method, url, headers, payload, timeout=self._timeout, cap=cap)
        except DriverError:
            raise
        except Exception:
            raise DriverError("network") from None

    def introspect(self, bucket: str) -> tuple[dict[str, Any], list[str]]:
        status, _headers, body = self._call(
            "GET",
            f"/{bucket}",
            f"list-type=2&max-keys={CATALOG_CAP}",
            cap=BYTE_CAP,
        )
        _raise_for_status(status, body)
        objects, truncated = _objects_from_list(body)
        built: list[dict[str, Any]] = []
        tables: list[str] = []
        for key, size in objects:
            stem, suffix = object_stem(key)
            if stem is None or suffix not in {".csv", ".parquet"}:
                continue
            entry: dict[str, Any] = {"name": stem, "columns": []}
            if size is not None and size > BYTE_CAP:
                entry["over_byte_cap"] = True
            else:
                entry["columns"] = self._columns(bucket, key, suffix)
            built.append(entry)
            tables.append(stem)
        schema = compact_schema(DIALECT, built)
        if truncated:
            schema["truncated_catalog"] = True
        return schema, tables

    def _columns(self, bucket: str, key: str, suffix: str) -> list[dict[str, str]]:
        if suffix == ".csv":
            status, _headers, body = self._call(
                "GET",
                f"/{bucket}/{key}",
                "",
                extra={"Range": "bytes=0-8191"},
                cap=8192,
            )
            if status not in {200, 206}:
                return []
            return sniff_csv(body)
        status, headers, body = self._call("GET", f"/{bucket}/{key}", "")
        if status != 200:
            return []
        length = _length(headers)
        if (length is not None and length > BYTE_CAP) or len(body) > BYTE_CAP:
            return []
        return sniff_parquet(body)

    def aggregate(
        self,
        bucket: str,
        sql: str,
        grantable: set[str],
        schema: dict[str, Any],
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        import tempfile

        wanted = _referenced(sql)
        granted = {_bare(name) for name in grantable}
        by_name = {str(obj["name"]): obj for obj in schema.get("objects") or []}
        files: dict[str, Path] = {}
        folder = tempfile.TemporaryDirectory(prefix="dms-s3-")
        try:
            for name in sorted(wanted):
                if name not in granted or name not in by_name:
                    continue
                if by_name[name].get("over_byte_cap") is True:
                    raise DriverError("byte_limit")
                path = self._download(bucket, name, Path(folder.name))
                if path is None:
                    raise DriverError("byte_limit")
                files[name] = path
            if not files:
                raise DriverError("bad_request")
            try:
                return aggregate(files, _one(sql))
            except FileSqlError as exc:
                raise DriverError(exc.code) from None
        finally:
            folder.cleanup()

    def _download(self, bucket: str, stem: str, folder: Path) -> Path | None:
        found: list[tuple[str, bytes]] = []
        for suffix in (".csv", ".parquet"):
            status, headers, body = self._call("GET", f"/{bucket}/{stem}{suffix}", "")
            if status == 404:
                continue
            _raise_for_status(status, body)
            length = _length(headers)
            if (length is not None and length > BYTE_CAP) or len(body) > BYTE_CAP:
                return None
            found.append((suffix, body))
        if len(found) != 1:
            raise DriverError("bad_request")
        suffix, body = found[0]
        path = folder / f"{stem}{suffix}"
        path.write_bytes(body)
        return path


def _length(headers: dict[str, str]) -> int | None:
    raw = headers.get("content-length")
    if raw is None or not str(raw).isdigit():
        return None
    return int(raw)


def _raise_for_status(status: int, body: bytes) -> None:
    if status < 400:
        return
    code = _xml_code(body)
    if status in {401, 403} or code in _AUTH_CODES:
        if code == "AccessDenied":
            raise DriverError("permission")
        raise DriverError("auth")
    raise DriverError("network")


def _xml_code(body: bytes) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return ""
    for node in root.iter():
        if node.tag.endswith("Code") and node.text:
            return node.text.strip()
    return ""


def _objects_from_list(body: bytes) -> tuple[list[tuple[str, int | None]], bool]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise DriverError("network") from None
    found: list[tuple[str, int | None]] = []
    truncated = False
    for node in root.iter():
        if node.tag.endswith("IsTruncated") and (node.text or "").strip().lower() == "true":
            truncated = True
        if not node.tag.endswith("Contents"):
            continue
        key = ""
        size: int | None = None
        for child in node:
            if child.tag.endswith("Key") and child.text:
                key = child.text.strip()
            if child.tag.endswith("Size") and child.text and child.text.strip().isdigit():
                size = int(child.text.strip())
        if key:
            found.append((key, size))
    return found, truncated


def _referenced(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:
        return set()
    found: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = str(table.name or "")
        if name:
            found.add(name.lower())
    return found


def _bare(name: str) -> str:
    text = name.strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _one(sql: str) -> str:
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text
