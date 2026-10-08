"""Connect bodies carry an OpenVault reference, never a raw secret.

Detection is structural: secret field names, and string values that are a
DSN or URL with a password in the userinfo. Rejected text is not returned.
"""

from __future__ import annotations

import re

# Exact names after normalisation (case, hyphen, space). ``uri`` is special:
# the name alone is not a secret; a credential in the value is.
_SECRET_FIELDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "dsn",
        "connection_string",
    }
)
_URI_FIELDS = frozenset({"uri", "url"})

# scheme://user:password@host  (userinfo must contain a password)
_USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@", re.I)
# Server=...;Password=... and the same for pwd / passwd
_PWD_ASSIGN = re.compile(r"(?:password|passwd|pwd)\s*=", re.I)
# id, or service/kid. No scheme, no userinfo, no secret characters.
_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
)


class ConnectCredentialError(Exception):
    """OpenVault did not resolve the reference. The message is the code only."""

    def __init__(self) -> None:
        super().__init__("connect_credential_unresolved")


def norm_field(name: object) -> str:
    text = str(name).strip().casefold().replace("-", "_").replace(" ", "_")
    return re.sub(r"_+", "_", text)


def value_has_secret_shape(value: object) -> bool:
    """True for a DSN/URL with a password, or a connection-string assignment."""
    if not isinstance(value, str) or not value:
        return False
    return _USERINFO.search(value) is not None or _PWD_ASSIGN.search(value) is not None


def body_has_raw_secret(node: object) -> bool:
    """Walk a JSON value. True when a secret field or secret-shaped string is present."""
    if isinstance(node, dict):
        for key, val in node.items():
            name = norm_field(key)
            if name in _SECRET_FIELDS:
                return True
            if name in _URI_FIELDS and value_has_secret_shape(val):
                return True
            if body_has_raw_secret(val):
                return True
        return False
    if isinstance(node, list):
        return any(body_has_raw_secret(item) for item in node)
    return value_has_secret_shape(node)


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
