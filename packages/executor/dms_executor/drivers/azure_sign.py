"""Azure Storage shared-key signing for Blob and ADLS.

The account key is an argument. It is not stored. Swap: the Azure SDK, if a
deploy already depends on it. This signer is what Azurite checks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import urlsplit


def _ms_headers(headers: Mapping[str, str]) -> str:
    folded: dict[str, str] = {}
    for key, value in headers.items():
        name = key.lower()
        if not name.startswith("x-ms-"):
            continue
        folded[name] = " ".join(value.split())
    return "".join(f"{name}:{folded[name]}\n" for name in sorted(folded))


def string_to_sign(
    method: str,
    *,
    account: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    content_length: str = "",
) -> str:
    """The shared-key string.

    The account is always prefixed. A host-style URL path starts at the
    container, so the resource is ``/account/container``. Azurite's path
    already contains the account, and the same rule makes that
    ``/account/account/container``, which is what Azurite checks.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    raw = path if path.startswith("/") else "/" + path
    resource = "/" + account + raw
    if query:
        lines = [f"{key.lower()}:{query[key]}" for key in sorted(query)]
        resource = resource + "\n" + "\n".join(lines)
    fields = [
        method.upper(),
        lowered.get("content-encoding", ""),
        lowered.get("content-language", ""),
        content_length,
        lowered.get("content-md5", ""),
        lowered.get("content-type", ""),
        "",
        lowered.get("if-modified-since", ""),
        lowered.get("if-match", ""),
        lowered.get("if-none-match", ""),
        lowered.get("if-unmodified-since", ""),
        lowered.get("range", ""),
    ]
    return "\n".join(fields) + "\n" + _ms_headers(headers) + resource


def sign(
    method: str,
    url: str,
    *,
    account: str,
    key_b64: str,
    query: Mapping[str, str] | None = None,
    extra_headers: Mapping[str, str] | None = None,
    payload: bytes = b"",
    now: datetime | None = None,
    content_type: str = "",
) -> tuple[str, dict[str, str]]:
    """Return the URL and the headers to send. The key is not a header."""
    stamped = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    split = urlsplit(url)
    headers = {key: value for key, value in (extra_headers or {}).items()}
    headers["x-ms-date"] = format_datetime(stamped, usegmt=True)
    headers["x-ms-version"] = "2020-10-02"
    if content_type:
        headers["Content-Type"] = content_type
    # 2015-02-21 and later sign a zero Content-Length as an empty field.
    length = "" if not payload else str(len(payload))
    text = string_to_sign(
        method,
        account=account,
        path=split.path or "/",
        query=dict(query or {}),
        headers=headers,
        content_length=length,
    )
    digest = hmac.new(base64.b64decode(key_b64), text.encode("utf-8"), hashlib.sha256).digest()
    headers["Authorization"] = f"SharedKey {account}:{base64.b64encode(digest).decode('ascii')}"
    return url, headers
