"""Azure Blob and ADLS Gen2 files, read-only, behind DMS_CONNECT_AZURE.

``user`` is the storage account. ``password`` is the account key from
OpenVault. ``database`` is the container or filesystem. A host containing
``.dfs.`` uses the Data Lake list API. Anything else, including Azurite,
uses the Blob REST API. The query path is the same as S3: a granted file
under the byte cap is registered as a table and aggregated locally.
"""

from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from dms_executor.drivers import DriverError, flag_on
from dms_executor.drivers.azure_sign import sign
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
from dms_executor.drivers.schema_map import compact_schema, prompt_lines

KIND = "azure_blob"
DIALECT = "postgres"
DEFAULT_PORT = 443
FLAG = "DMS_CONNECT_AZURE"


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
    product = "adls" if client.dfs else "azure-blob"
    return _Session(product, tables, schema)


def inspect(con: _Session) -> dict[str, str]:
    return {"version": con.version, "mode": "", "product": con.version, "dialect": DIALECT}


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
        account: str,
        key_b64: str,
        *,
        tls: bool,
        timeout: int,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._account = account
        self._key = key_b64
        self._tls = tls or int(port) == 443
        self._timeout = timeout
        self.dfs = ".dfs." in host.lower()

    def _root(self) -> str:
        scheme = "https" if self._tls else "http"
        netloc = self._host if self._port in {80, 443} else f"{self._host}:{self._port}"
        if self._path_style():
            return f"{scheme}://{netloc}/{self._account}"
        return f"{scheme}://{netloc}"

    def _path_style(self) -> bool:
        if self._port not in {80, 443}:
            return True
        host = self._host.lower()
        if host in {"localhost", "azurite"}:
            return True
        return host.replace(".", "").isdigit()

    def _call(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        *,
        extra: dict[str, str] | None = None,
        cap: int = BYTE_CAP,
    ) -> tuple[int, dict[str, str], bytes]:
        query_text = urlencode(query)
        url = self._root() + path + (("?" + query_text) if query_text else "")
        try:
            signed, headers = sign(
                method,
                url,
                account=self._account,
                key_b64=self._key,
                query=query,
                extra_headers=extra,
            )
            return exchange(method, signed, headers, timeout=self._timeout, cap=cap)
        except DriverError:
            raise
        except Exception:
            raise DriverError("network") from None

    def introspect(self, container: str) -> tuple[dict[str, Any], list[str]]:
        if self.dfs:
            query = {"resource": "filesystem", "recursive": "true"}
        else:
            query = {"restype": "container", "comp": "list", "maxresults": str(CATALOG_CAP)}
        status, _headers, body = self._call("GET", f"/{container}", query)
        _raise_for_status(status, body)
        objects, truncated = _objects(body, self.dfs)
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
                entry["columns"] = self._columns(container, key, suffix)
            built.append(entry)
            tables.append(stem)
        schema = compact_schema(DIALECT, built)
        if truncated:
            schema["truncated_catalog"] = True
        return schema, tables

    def _columns(self, container: str, key: str, suffix: str) -> list[dict[str, str]]:
        if suffix == ".csv":
            status, _headers, body = self._call(
                "GET",
                f"/{container}/{key}",
                {},
                extra={"Range": "bytes=0-8191"},
                cap=8192,
            )
            if status not in {200, 206}:
                return []
            return sniff_csv(body)
        status, headers, body = self._call("GET", f"/{container}/{key}", {})
        if status != 200:
            return []
        if _over(headers, body):
            return []
        return sniff_parquet(body)

    def aggregate(
        self,
        container: str,
        sql: str,
        grantable: set[str],
        schema: dict[str, Any],
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        wanted = _referenced(sql)
        granted = {_bare(name) for name in grantable}
        by_name = {str(obj["name"]): obj for obj in schema.get("objects") or []}
        folder = tempfile.TemporaryDirectory(prefix="dms-blob-")
        files: dict[str, Path] = {}
        try:
            for name in sorted(wanted):
                if name not in granted or name not in by_name:
                    continue
                if by_name[name].get("over_byte_cap") is True:
                    raise DriverError("byte_limit")
                path = self._download(container, name, Path(folder.name))
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

    def _download(self, container: str, stem: str, folder: Path) -> Path | None:
        found: list[tuple[str, bytes]] = []
        for suffix in (".csv", ".parquet"):
            status, headers, body = self._call("GET", f"/{container}/{stem}{suffix}", {})
            if status == 404:
                continue
            _raise_for_status(status, body)
            if _over(headers, body):
                return None
            found.append((suffix, body))
        if len(found) != 1:
            raise DriverError("bad_request")
        suffix, body = found[0]
        path = folder / f"{stem}{suffix}"
        path.write_bytes(body)
        return path


def _over(headers: dict[str, str], body: bytes) -> bool:
    raw = headers.get("content-length")
    if raw is not None and str(raw).isdigit() and int(raw) > BYTE_CAP:
        return True
    return len(body) > BYTE_CAP


def _raise_for_status(status: int, body: bytes) -> None:
    if status < 400:
        return
    code = _xml_code(body)
    if status in {401, 403} or code in {"AuthenticationFailed", "AuthorizationFailure"}:
        if code == "AuthorizationFailure":
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


def _objects(body: bytes, dfs: bool) -> tuple[list[tuple[str, int | None]], bool]:
    if dfs:
        return _dfs_objects(body)
    return _blob_objects(body)


def _dfs_objects(body: bytes) -> tuple[list[tuple[str, int | None]], bool]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise DriverError("network") from None
    found: list[tuple[str, int | None]] = []
    for item in payload.get("paths") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("isDirectory", "")).lower() == "true":
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        raw = item.get("contentLength")
        size = int(raw) if isinstance(raw, str) and raw.isdigit() else None
        found.append((name, size))
    return found, False


def _blob_objects(body: bytes) -> tuple[list[tuple[str, int | None]], bool]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise DriverError("network") from None
    found: list[tuple[str, int | None]] = []
    truncated = False
    for node in root.iter():
        if node.tag.endswith("NextMarker") and (node.text or "").strip():
            truncated = True
        if not node.tag.endswith("Blob"):
            continue
        name = ""
        size: int | None = None
        for child in node.iter():
            if child is node:
                continue
            if child.tag.endswith("Name") and child.text and not name:
                name = child.text.strip()
            if child.tag.endswith("Content-Length") and child.text and child.text.strip().isdigit():
                size = int(child.text.strip())
        if name:
            found.append((name, size))
    return found, truncated


def _referenced(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:
        return set()
    return {str(table.name).lower() for table in tree.find_all(exp.Table) if table.name}


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
