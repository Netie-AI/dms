"""Connect bodies carry an OpenVault reference, never a raw secret.

The request is an allow-list. A field that was not declared, at any depth
and in any spelling, is rejected. A declared string is also rejected when it
carries URL userinfo (``user:pass@``), including after percent-decoding.
The error code is the only text returned.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

# scheme://user:password@host
_USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@", re.I)
# Server=...;Password=... on a declared string. Not a field-name list.
_PWD_ASSIGN = re.compile(r"(?:password|passwd|pwd)\s*=", re.I)
# id, or service/kid. No scheme, no userinfo.
_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
)

_RAW = "connect_raw_secret_rejected"
_BAD = "bad_request"

# POST /v1/connect/sources. Exact keys. ``options`` is the only nested object.
_SOURCES: dict[str, str] = {
    "credential_ref": "str",
    "connector_id": "str",
    "space_name": "str",
    "options": "options",
}
_OPTIONS: dict[str, str] = {"grant_tables": "str_list"}
# POST grant and revoke.
_TABLES: dict[str, str] = {"tables": "str_list"}
# POST /v1/connect/test. Used only when DMS_CONNECT_V2 is on. The off path
# keeps schema "sources", so these keys stay undeclared there.
_TEST: dict[str, str] = {
    "kind": "str",
    "host": "str",
    "port": "int",
    "db": "str",
    "cred_ref": "str",
    "tls": "bool",
}
_SOURCES_V2: dict[str, str] = {
    **_SOURCES,
    "kind": "str",
    "host": "str",
    "port": "int",
    "db": "str",
    "mode": "str",
    "tls": "bool",
}
_QUERY: dict[str, str] = {
    "sql": "str",
    "offset": "int",
    "page_size": "int",
    "confirm_runaway": "bool",
    "export": "bool",
}
_CANCEL: dict[str, str] = {"query_id": "str"}

_SCHEMAS = {
    "sources": _SOURCES,
    "tables": _TABLES,
    "options": _OPTIONS,
    "test": _TEST,
    "sources_v2": _SOURCES_V2,
    "query": _QUERY,
    "cancel": _CANCEL,
}


class ConnectCredentialError(Exception):
    """OpenVault did not resolve the reference. The message is the code only."""

    def __init__(self) -> None:
        super().__init__("connect_credential_unresolved")


def _unfold(value: str) -> str:
    """Percent-decode at most twice so ``%253A`` still becomes a colon."""
    cur = value
    for _ in range(2):
        nxt = unquote(cur)
        if nxt == cur:
            break
        cur = nxt
    return cur


def value_has_secret_shape(value: object) -> bool:
    """True when a string carries URL userinfo, raw or percent-decoded."""
    if not isinstance(value, str) or not value:
        return False
    for text in (value, _unfold(value)):
        if _USERINFO.search(text) is not None or _PWD_ASSIGN.search(text) is not None:
            return True
    return False


def _check(value: Any, kind: str) -> str | None:
    if kind == "int":
        # bool is a subclass of int. A JSON true is not a port.
        if isinstance(value, bool) or not isinstance(value, int):
            return _BAD
        return None
    if kind == "bool":
        if not isinstance(value, bool):
            return _BAD
        return None
    if kind == "str":
        if not isinstance(value, str):
            return _BAD
        if value_has_secret_shape(value):
            return _RAW
        return None
    if kind == "str_list":
        if not isinstance(value, list):
            return _BAD
        for item in value:
            if isinstance(item, (dict, list)):
                return _RAW
            if not isinstance(item, str):
                return _BAD
            if value_has_secret_shape(item):
                return _RAW
        return None
    if kind in _SCHEMAS:
        if not isinstance(value, dict):
            return _BAD
        return body_violation(value, schema=kind)
    return _BAD


def body_violation(payload: object, *, schema: str) -> str | None:
    """None when ``payload`` matches the named allow-list.

    An undeclared key, a nested object where a string was required, or a
    string with URL userinfo is ``connect_raw_secret_rejected``. A declared
    field of the wrong JSON type is ``bad_request``. Neither code carries
    the field or the value.
    """
    spec = _SCHEMAS.get(schema)
    if spec is None or not isinstance(payload, dict):
        return _BAD
    for key, val in payload.items():
        kind = spec.get(key)
        if kind is None:
            return _RAW
        code = _check(val, kind)
        if code:
            return code
    return None


def key_id_for_ref(ref: str) -> str | None:
    """Vault key id for a connect reference.

    A bare id is the key id. ``service/kid`` uses the kid. Both resolve through
    the existing OpenVault reveal ``GET /api/keys/{key_id}/secret``.
    """
    if not isinstance(ref, str) or _REF.fullmatch(ref) is None:
        return None
    if "/" in ref:
        return ref.split("/", 1)[1]
    return ref
