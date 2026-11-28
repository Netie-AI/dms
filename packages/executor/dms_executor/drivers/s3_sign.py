"""AWS Signature Version 4 for S3 GET and PUT.

The secret is an argument. It is not stored and it is not part of the
canonical request. Swap: boto3, if a deploy already depends on it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit, urlunsplit


def _quote(value: str, *, safe: str = "") -> str:
    return quote(value, safe=safe).replace("+", "%20")


def _canonical_query(query: str) -> str:
    if not query:
        return ""
    parts: list[tuple[str, str]] = []
    for piece in query.split("&"):
        if not piece:
            continue
        key, _, raw = piece.partition("=")
        parts.append((_quote(key), _quote(raw)))
    parts.sort()
    return "&".join(f"{key}={value}" for key, value in parts)


def _canonical_path(path: str) -> str:
    if not path:
        return "/"
    segments = [_quote(segment, safe="") for segment in path.split("/")]
    rendered = "/".join(segments)
    if path.startswith("/") and not rendered.startswith("/"):
        rendered = "/" + rendered
    return rendered or "/"


def sign(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: bytes,
    *,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    service: str = "s3",
    now: datetime | None = None,
) -> dict[str, str]:
    """Return headers to send, including Authorization. ``payload`` is hashed, not logged."""
    stamped = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    amz_date = stamped.strftime("%Y%m%dT%H%M%SZ")
    day = stamped.strftime("%Y%m%d")
    split = urlsplit(url)
    payload_hash = hashlib.sha256(payload).hexdigest()
    signed_headers_in = {key.lower(): " ".join(value.split()) for key, value in headers.items()}
    signed_headers_in["host"] = split.netloc
    signed_headers_in["x-amz-date"] = amz_date
    signed_headers_in["x-amz-content-sha256"] = payload_hash
    names = sorted(signed_headers_in)
    canonical_headers = "".join(f"{name}:{signed_headers_in[name]}\n" for name in names)
    signed_header_names = ";".join(names)
    canonical = "\n".join(
        [
            method.upper(),
            _canonical_path(split.path),
            _canonical_query(split.query),
            canonical_headers,
            signed_header_names,
            payload_hash,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        ]
    )
    key = ("AWS4" + secret_key).encode("utf-8")
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_header_names}, Signature={signature}"
    )
    out = {key: value for key, value in headers.items()}
    out["Host"] = split.netloc
    out["x-amz-date"] = amz_date
    out["x-amz-content-sha256"] = payload_hash
    out["Authorization"] = authorization
    return out


def signed_url(
    method: str,
    endpoint: str,
    path: str,
    query: str,
    *,
    access_key: str,
    secret_key: str,
    payload: bytes = b"",
    extra_headers: Mapping[str, str] | None = None,
    now: datetime | None = None,
    tls: bool = False,
) -> tuple[str, dict[str, str]]:
    """Path-style URL plus signed headers. ``endpoint`` is host or host:port."""
    scheme = "https" if tls else "http"
    base = endpoint if "://" in endpoint else f"{scheme}://{endpoint}"
    split = urlsplit(base)
    url = urlunsplit((split.scheme, split.netloc, path, query, ""))
    headers = sign(
        method,
        url,
        dict(extra_headers or {}),
        payload,
        access_key=access_key,
        secret_key=secret_key,
        now=now,
    )
    return url, headers
