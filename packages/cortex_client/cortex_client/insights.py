"""Off-contract Cortex Insights — GET|POST /v1/insights (Cortex #213 @ 6f701e93).

Sibling to ``/v1/contract/*``; cortex-contract stays 1.2.0. DMS forwards the
already-configured OpenVault ``ov_`` / viewer key and never invents LIVE_KEY.
``live_5000_ci`` is always forced false. Unreachable Cortex fails closed — no
invented values.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

import httpx

INSIGHTS_PATH = "/v1/insights"
#: sha256 of Cortex's published demo viewer key. The key is not a secret, but
#: KEY-01 (dms#273) keeps its text out of apps/ and packages/ entirely: DMS has
#: no default and no fallback for it, and a refusal needs only to recognise it.
#: Compared on the stripped key, exactly as before. tests/test_key_01_fail_closed.py
#: pins this digest to the real value, so it cannot drift silently.
_DEMO_VIEWER_KEY_SHA256 = "76ecf0a7405d1368942588172e70fab3c396c269ae48e90bab33851e226eef08"
INSIGHTS_FAIL_BEARER_MISSING = "insights_bearer_missing"
INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT = "insights_bearer_insecure_transport"

_TOKEN_RE = re.compile(
    r"(?:Bearer\s+)?(?:ov_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9_-]{8,})",
    re.I,
)
_LIVE_KEY_RE = re.compile(r"\bLIVE_KEY(?:_ID)?\b")
_AUTH_HEADER_RE = re.compile(
    r"(?i)((?:Authorization\s*:\s*)?Bearer\s+|X-API-Key\s*:\s+)\S+"
)


class InsightsError(Exception):
    """Cortex insights transport or HTTP failure. Payload is a real refuse body."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        payload: dict[str, Any] | None = None,
        code: str = "cortex_unavailable",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.code = code


def redact_secrets(text: str, extra: str | None = None) -> str:
    """Strip bearer/provider-looking tokens from anything that might be logged or returned."""
    out = text or ""
    if extra:
        out = out.replace(extra, "[redacted]")
    out = _AUTH_HEADER_RE.sub(r"\1[redacted]", out)
    out = _TOKEN_RE.sub("[redacted]", out)
    return _LIVE_KEY_RE.sub("[redacted]", out)


def generate_transport_is_safe(base_url: str) -> bool:
    """True when a bearer may travel: https, or http to loopback / localhost."""
    raw = (base_url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if scheme == "https":
        return True
    if scheme != "http":
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback)


def is_published_demo_key(api_key: str | None) -> bool:
    """True when ``api_key`` is Cortex's published demo viewer key.

    A recogniser for refusals only. Nothing in DMS may default to, fall back to
    or send this value.
    """
    if api_key is None:
        return False
    digest = hashlib.sha256(str(api_key).strip().encode("utf-8")).hexdigest()
    return digest == _DEMO_VIEWER_KEY_SHA256


def generate_bearer_refuse(api_key: str | None, base_url: str) -> str | None:
    """Named generate=true refuse, or None if the call may go out.

    A missing key (``None``), an empty key and the published demo viewer key
    never generate. Plain http to a non-loopback host never generates. There is
    no ``None`` exception: KEY-01 (dms#273) removed ``missing_none=False``, so an
    unconfigured Python default is a refusal like any other, never a call.
    """
    if api_key is None or not str(api_key).strip() or is_published_demo_key(api_key):
        return INSIGHTS_FAIL_BEARER_MISSING
    if not generate_transport_is_safe(base_url):
        return INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT
    return None


def generate_abstain_payload(reason: str) -> dict[str, Any]:
    """Customer-visible ABSTAIN body. Never includes a key."""
    return honest_envelope(
        {
            "ok": False,
            "status": "ABSTAIN",
            "values": [],
            "insights_fail": reason,
            "code": reason,
            "live_5000_ci": False,
        }
    )


def honest_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Pass Cortex through; never claim live :5000 green on the consume path."""
    out = dict(payload)
    out["live_5000_ci"] = False
    api = dict(out["api"]) if isinstance(out.get("api"), dict) else {}
    api["live_5000_ci"] = False
    out["api"] = api
    return out


def auth_headers(api_key: str | None) -> dict[str, str] | None:
    """Forward a configured key. Empty stays empty — never invent LIVE_KEY / ov_."""
    if not api_key:
        return None
    key = str(api_key)
    return {"X-API-Key": key, "Authorization": f"Bearer {key}"}


def _json_body(res: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = res.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _request(
    method: str,
    base_url: str,
    path: str,
    *,
    api_key: str | None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = auth_headers(api_key)
    if extra_headers:
        merged = dict(headers or {})
        merged.update(extra_headers)
        headers = merged
    try:
        with httpx.Client(timeout=timeout) as http:
            res = http.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
            )
    except httpx.HTTPError as exc:
        raise InsightsError(
            redact_secrets(f"unreachable: {exc.__class__.__name__}", api_key),
            status_code=503,
            code="cortex_unavailable",
        ) from None

    body = _json_body(res)
    if body is not None:
        from cortex_client.strict_pin import abstain_payload, interpret

        shot = interpret(res.status_code, body, getattr(res, "headers", None))
        if shot.kind == "unavailable":
            return honest_envelope(abstain_payload(shot))
    if res.status_code == 200 and body is not None:
        return honest_envelope(body)
    if body is not None:
        stamped = honest_envelope(body)
        hint = body.get("refused") or body.get("answer") or body.get("error")
        if hint is None:
            hint = f"http_{res.status_code}"
        raise InsightsError(
            redact_secrets(str(hint)[:400], api_key),
            status_code=res.status_code if res.status_code >= 400 else 502,
            payload=stamped,
            code="insights_refused" if res.status_code in {401, 403} else "insights_http_error",
        )
    raise InsightsError(
        redact_secrets(f"http_{res.status_code}", api_key),
        status_code=503 if res.status_code >= 500 else 502,
        code="insights_unavailable",
    )


def insights_get(
    base_url: str,
    suffix: str = "",
    *,
    api_key: str | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    tail = suffix if suffix.startswith("/") or suffix == "" else f"/{suffix}"
    return _request(
        "GET",
        base_url,
        f"{INSIGHTS_PATH}{tail}",
        api_key=api_key,
        params=params,
        timeout=timeout,
    )


def insights_post(
    base_url: str,
    *,
    intent: str = "",
    question: str = "",
    ask: bool = True,
    generate: bool = False,
    session_id: str = "demo",
    space_id: str | None = None,
    consumer: str = "dms",
    api_key: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    if generate:
        refuse = generate_bearer_refuse(api_key, base_url)
        if refuse:
            return generate_abstain_payload(refuse)
    body: dict[str, Any] = {
        "intent": intent,
        "question": question,
        "ask": ask,
        "generate": generate,
        "session_id": session_id,
        "space_id": space_id,
        "consumer": consumer,
    }
    extra_headers: dict[str, str] | None = None
    if generate:
        from cortex_client.strict_pin import stamp_generate_body, stamp_generate_headers

        body = stamp_generate_body(body)
        if body.get("pin_refusal"):
            return generate_abstain_payload(str(body["pin_refusal"]))
        extra_headers = stamp_generate_headers({})
    return _request(
        "POST",
        base_url,
        INSIGHTS_PATH,
        api_key=api_key,
        json_body=body,
        timeout=timeout,
        extra_headers=extra_headers,
    )


__all__ = [
    "INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT",
    "INSIGHTS_FAIL_BEARER_MISSING",
    "INSIGHTS_PATH",
    "InsightsError",
    "auth_headers",
    "generate_abstain_payload",
    "generate_bearer_refuse",
    "generate_transport_is_safe",
    "honest_envelope",
    "insights_get",
    "insights_post",
    "is_published_demo_key",
    "redact_secrets",
]
