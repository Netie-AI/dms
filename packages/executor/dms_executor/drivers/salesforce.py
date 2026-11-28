"""Salesforce SOQL, read-only, behind DMS_CONNECT_SALESFORCE.

Objects and field types come from the describe API and become the schema
map. Picklist values and record bodies are not copied into that map. The
query is one guarded SELECT, sent as SOQL. Credentials are the OpenVault
JSON: ``user`` and ``password`` plus ``client_id`` and ``client_secret``,
or a ``token`` that skips the password grant. None of those are stored or
returned.

``set_transport`` is the test seam for a recorded fixture. Production
leaves it unset.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

from dms_executor.drivers import DriverError, flag_on
from dms_executor.drivers.guard import refuse
from dms_executor.drivers.http_cap import exchange
from dms_executor.drivers.limits import CONNECT_TIMEOUT_S, result
from dms_executor.drivers.schema_map import compact_schema, prompt_lines

KIND = "salesforce"
DIALECT = "postgres"
DEFAULT_PORT = 443
FLAG = "DMS_CONNECT_SALESFORCE"
_VERSION = re.compile(r"^v\d{1,3}\.\d{1,3}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

Transport = Callable[[str, str, dict[str, str], bytes, int], tuple[int, dict[str, Any]]]
_transport: Transport | None = None


def enabled() -> bool:
    return flag_on(FLAG)


def set_transport(fn: Transport | None) -> None:
    """Test seam. Production leaves this unset."""
    global _transport
    _transport = fn


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
    client_id: str = "",
    client_secret: str = "",
    token: str = "",
) -> _Session:
    if not enabled():
        raise DriverError("network")
    version = _api_version(database)
    access, instance = _session_token(
        host, user, password, client_id, client_secret, token, int(timeout)
    )
    schema, tables = _describe(instance, version, access, int(timeout))
    return _Session("salesforce", tables, schema)


def inspect(con: _Session) -> dict[str, str]:
    return {"version": con.version, "mode": "", "product": "salesforce", "dialect": DIALECT}


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
    return statement


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
    client_id: str = "",
    client_secret: str = "",
    token: str = "",
    **_extra: Any,
) -> dict[str, Any]:
    if not enabled():
        raise DriverError("network")
    refuse(KIND, sql, grantable)
    version = _api_version(database)
    access, instance = _session_token(
        host, user, password, client_id, client_secret, token, CONNECT_TIMEOUT_S
    )
    schema, _tables = _describe(instance, version, access, CONNECT_TIMEOUT_S)
    columns, rows, truncated = _query(instance, version, access, _one(sql), CONNECT_TIMEOUT_S)
    body = result(columns, rows, schema=schema)
    if truncated:
        body["truncated"] = True
        body["bounded"] = True
        body["complete"] = False
    return body


def _api_version(database: str) -> str:
    text = (database or "").strip()
    if _VERSION.fullmatch(text):
        return text
    return "v59.0"


def _session_token(
    host: str,
    user: str,
    password: str,
    client_id: str,
    client_secret: str,
    token: str,
    timeout: int,
) -> tuple[str, str]:
    if token:
        return token, f"https://{host}"
    if not client_id or not client_secret or not user or not password:
        raise DriverError("auth")
    body = urlencode_login(user, password, client_id, client_secret)
    status, payload = _json(
        "POST",
        f"https://{host}/services/oauth2/token",
        {"Content-Type": "application/x-www-form-urlencoded"},
        body,
        timeout,
    )
    if status != 200:
        raise DriverError("auth")
    access = payload.get("access_token")
    instance = payload.get("instance_url")
    if not isinstance(access, str) or not access:
        raise DriverError("auth")
    if not isinstance(instance, str):
        raise DriverError("auth")
    split = urlsplit(instance)
    if split.scheme != "https" or not split.hostname:
        raise DriverError("auth")
    return access, f"https://{split.hostname}"


def urlencode_login(user: str, password: str, client_id: str, client_secret: str) -> bytes:
    from urllib.parse import urlencode

    return urlencode(
        {
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": client_secret,
            "username": user,
            "password": password,
        }
    ).encode("utf-8")


def _describe(
    instance: str, version: str, access: str, timeout: int
) -> tuple[dict[str, Any], list[str]]:
    status, payload = _json(
        "GET",
        f"{instance}/services/data/{version}/sobjects",
        _auth(access),
        b"",
        timeout,
    )
    if status == 401:
        raise DriverError("auth")
    if status == 403:
        raise DriverError("permission")
    if status != 200:
        raise DriverError("network")
    objects: list[dict[str, Any]] = []
    listed = payload.get("sobjects") or []
    if not isinstance(listed, list):
        listed = []
    for item in listed:
        if not isinstance(item, dict) or item.get("queryable") is not True:
            continue
        name = item.get("name")
        if not isinstance(name, str) or _IDENT.fullmatch(name) is None:
            continue
        columns = _fields(instance, version, access, name, timeout)
        objects.append({"name": name, "columns": columns})
    schema = compact_schema(DIALECT, objects)
    tables = [str(obj["name"]) for obj in schema["objects"]]
    return schema, tables


def _fields(
    instance: str, version: str, access: str, name: str, timeout: int
) -> list[dict[str, str]]:
    status, payload = _json(
        "GET",
        f"{instance}/services/data/{version}/sobjects/{name}/describe",
        _auth(access),
        b"",
        timeout,
    )
    if status != 200:
        return []
    columns: list[dict[str, str]] = []
    for field in payload.get("fields") or []:
        if not isinstance(field, dict):
            continue
        col = field.get("name")
        typ = field.get("type")
        if isinstance(col, str) and isinstance(typ, str) and _IDENT.fullmatch(col):
            columns.append({"name": col, "type": typ})
    return columns


def _query(
    instance: str, version: str, access: str, sql: str, timeout: int
) -> tuple[list[str], list[dict[str, Any]], bool]:
    status, payload = _json(
        "GET",
        f"{instance}/services/data/{version}/query?q={quote(sql)}",
        _auth(access),
        b"",
        timeout,
    )
    if status in {400, 404}:
        raise DriverError("bad_request")
    if status == 401:
        raise DriverError("auth")
    if status == 403:
        raise DriverError("permission")
    if status != 200:
        raise DriverError("network")
    records = payload.get("records") or []
    if not isinstance(records, list):
        records = []
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = {key: _cell(value) for key, value in record.items() if key != "attributes"}
        for key in row:
            if key not in columns:
                columns.append(key)
        rows.append(row)
    truncated = payload.get("done") is False
    return columns, rows, truncated


def _auth(access: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access}", "Accept": "application/json"}


def _json(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: int
) -> tuple[int, dict[str, Any]]:
    transport = _transport
    try:
        if transport is not None:
            status, payload = transport(method, url, headers, body, timeout)
        else:
            status, payload = _urllib_json(method, url, headers, body, timeout)
    except DriverError:
        raise
    except Exception:
        raise DriverError("network") from None
    if not isinstance(payload, dict):
        raise DriverError("network")
    return status, payload


def _urllib_json(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: int
) -> tuple[int, dict[str, Any]]:
    status, _headers, raw = exchange(method, url, headers, body, timeout=timeout)
    if not raw:
        return status, {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise DriverError("network") from None
    if not isinstance(payload, dict):
        raise DriverError("network")
    return status, payload


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _one(sql: str) -> str:
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text
