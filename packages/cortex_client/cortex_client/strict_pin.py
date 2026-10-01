"""Strict model pin for the generate path (dms#317 Part A).

Contract: OpenVault #81 at ``0d0ef3f0``. ``pop_strict`` accepts only JSON
boolean true. ``X-OpenVault-Strict`` is on for ``1`` / ``true`` / ``yes``.
A 503 body is ``error.type=pin_unavailable``, ``error.reason``, ``error.model``
(the pin that failed), ``served_provider`` null, ``served_model`` null,
``served_local`` false. A 200 stamps ``served_provider``, ``served_model``,
and ``served_local`` in the JSON body, and sends
``X-OpenVault-Served-Provider``, ``X-OpenVault-Served-Model``, and
``X-OpenVault-Served-Local``.

The pin is config (``DMS_STRICT_MODEL`` / ``DMS_STRICT_PROVIDER``). Call sites
stamp that pair onto the request. They do not name a model. OpenVault keeps
the provider key. This module never reads a vault file and never retries.
A 503 ``pin_unavailable`` is not a 429, so it is not RATE_LIMIT.
Retry-After is recorded and not slept on.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

STRICT_HEADER = "X-OpenVault-Strict"
SERVED_MODEL_HEADER = "X-OpenVault-Served-Model"
SERVED_PROVIDER_HEADER = "X-OpenVault-Served-Provider"
DEFAULT_STRICT_MODEL = "openai/gpt-oss-120b"
DEFAULT_STRICT_PROVIDER = "groq"
# Exact ids from OpenVault #81 0d0ef3f0
# OpenMW/openmw/openvault/vault/providers.py
#   131: id="openrouter",
#   160: id="groq",
#   188: id="google",
# nvidia is providers.py line 247. Not an allowed pin.
# Compared with ==. No case fold and no rewrite.
PROVIDER_IDS = frozenset({"groq", "google", "openrouter"})
PIN_UNAVAILABLE = "pin_unavailable"
# OpenVault #81. quota_exhausted stays here. It is not a 429.
PIN_VAULT_REASONS = frozenset(
    {"parked", "quota_exhausted", "circuit_open", "no_hop", "not_in_catalog"}
)
_BAD_MODELS = frozenset({"", "auto", "default"})
# Cerebras catalogs the bare id. It is not the Groq pin.
_BARE_SUBSTITUTE = "gpt-oss-120b"
_CHAT_PATH = "/v1/chat/completions"
# One completion. At least 512, per the Groq-pinned budget. No second call.
_MAX_TOKENS = 512
_DEMO_VIEWER_KEY = "dms-demo-viewer-key"


@dataclass(frozen=True)
class PinConfig:
    provider: str
    model: str
    refusal: str | None


@dataclass(frozen=True)
class PinShot:
    """One vault answer. ``kind`` is ok, unavailable, mismatch, or caller_error.

    Body and header are kept apart. When both are present and they disagree,
    neither side is the served model.
    """

    kind: str
    name: str = ""
    vault_reason: str = ""
    served_provider: str | None = None
    served_model: str | None = None
    body_provider: str | None = None
    body_model: str | None = None
    header_provider: str | None = None
    header_model: str | None = None


@dataclass(frozen=True)
class PinRound:
    active: bool
    blocked: bool = False
    reason: str | None = None
    vault_reason: str | None = None


def pin_config() -> PinConfig:
    """The pin. Env overrides the default. Empty, auto, default, and the bare
    Cerebras id are caller errors: nothing is sent.
    """
    model = os.environ.get("DMS_STRICT_MODEL", DEFAULT_STRICT_MODEL).strip()
    provider = os.environ.get("DMS_STRICT_PROVIDER", DEFAULT_STRICT_PROVIDER).strip()
    if not provider:
        provider = DEFAULT_STRICT_PROVIDER
    return PinConfig(provider=provider, model=model, refusal=_refusal(model))


def _refusal(model: str) -> str | None:
    token = model.strip()
    if token.lower() in _BAD_MODELS:
        shown = token.lower() or "empty"
        return f"pin_caller_error:{shown}"
    if token == _BARE_SUBSTITUTE:
        return f"pin_caller_error:{token}"
    return None


def _base_url() -> str | None:
    raw = os.environ.get("OPENVAULT_URL", "").strip()
    return raw.rstrip("/") or None


def stamp_generate_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """Copy ``body`` and, when the pin is sendable, set model + JSON true strict."""
    out = dict(body)
    cfg = pin_config()
    if cfg.refusal:
        out["pin_refusal"] = cfg.refusal
        return out
    out["model"] = cfg.model
    out["strict"] = True
    return out


def stamp_generate_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    """Add the strict header when the pin is sendable. Preserves auth headers."""
    if pin_config().refusal:
        return dict(headers) if headers else None
    merged = dict(headers or {})
    merged[STRICT_HEADER] = "true"
    return merged


def _provider_text(value: object) -> str | None:
    """Provider id as sent. Empty is absent. No strip and no case fold."""
    if not isinstance(value, str) or value == "":
        return None
    return value


def _body_provider(body: Mapping[str, Any]) -> str | None:
    if "served_provider" not in body:
        return None
    return _provider_text(body.get("served_provider"))


def _header_raw(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Header name matches in any case. The value is not rewritten."""
    if not headers:
        return None
    want = name.lower()
    for key, value in headers.items():
        if str(key).lower() == want:
            return value if isinstance(value, str) else None
    return None


def _header_provider(headers: Mapping[str, str] | None) -> str | None:
    """Name is case-insensitive. The id is not folded or stripped.

    OpenVault #81 0d0ef3f0 sends this from ``served_response_headers``.
    A live hop used the lowercase name ``x-openvault-served-provider``.
    """
    return _provider_text(_header_raw(headers, SERVED_PROVIDER_HEADER))


def _body_field(body: Mapping[str, Any], key: str) -> str | None:
    if key not in body:
        return None
    value = body.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _sides(
    body: Mapping[str, Any] | None,
    headers: Mapping[str, str] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Body fields and header fields. A null body field is absent."""
    payload = body if isinstance(body, Mapping) else {}
    return (
        _body_provider(payload),
        _body_field(payload, "served_model"),
        _header_provider(headers),
        _provider_text(_header_raw(headers, SERVED_MODEL_HEADER)),
    )


def _disagree(
    body_provider: str | None,
    body_model: str | None,
    header_provider: str | None,
    header_model: str | None,
) -> bool:
    if body_model and header_model and body_model != header_model:
        return True
    if body_provider and header_provider and body_provider != header_provider:
        return True
    return False


def _mismatch_name(
    provider: str | None,
    model: str | None,
) -> str:
    return f"pin_mismatch:{provider or 'missing'}/{model or 'missing'}"


def interpret(
    status: int,
    body: Any,
    headers: Mapping[str, str] | None = None,
    cfg: PinConfig | None = None,
) -> PinShot:
    """Classify one OpenVault chat response. Does not map 503 onto RATE_LIMIT.

    A 200 is ok only when body and headers are both present, agree, and match
    the pin exactly. One side missing, or the two sides different, is INVALID
    ``pin_mismatch``. A 503 ``pin_unavailable`` is ABSTAIN only when that
    refusal carries neither served headers nor served body fields.
    """
    pin = cfg or pin_config()
    payload = body if isinstance(body, Mapping) else {}
    err = payload.get("error") if isinstance(payload, Mapping) else None
    err_d = err if isinstance(err, Mapping) else {}
    body_provider, body_model, header_provider, header_model = _sides(payload, headers)
    served_absent = (
        body_provider is None
        and body_model is None
        and header_provider is None
        and header_model is None
    )
    if (
        status == 503
        and str(err_d.get("type") or "") == PIN_UNAVAILABLE
        and served_absent
    ):
        vault_reason = str(err_d.get("reason") or "").strip()
        return PinShot(
            kind="unavailable",
            name=f"pin_unavailable:{pin.provider}/{pin.model}",
            vault_reason=vault_reason,
        )
    if _disagree(body_provider, body_model, header_provider, header_model):
        # Neither side is chosen. Both pairs stay in the name and on the shot.
        left = f"{body_provider or 'missing'}/{body_model or 'missing'}"
        right = f"{header_provider or 'missing'}/{header_model or 'missing'}"
        return PinShot(
            kind="mismatch",
            name=f"pin_mismatch:{left}+{right}",
            body_provider=body_provider,
            body_model=body_model,
            header_provider=header_provider,
            header_model=header_model,
        )
    served_model = body_model or header_model
    served_provider = body_provider or header_provider
    both = (
        body_provider is not None
        and body_model is not None
        and header_provider is not None
        and header_model is not None
    )
    # Both sides, exact, and the same pair. A missing side is not the pin.
    if (
        status == 200
        and both
        and body_provider == header_provider
        and body_model == header_model
        and served_provider in PROVIDER_IDS
        and pin.provider in PROVIDER_IDS
        and served_provider == pin.provider
        and served_model == pin.model
    ):
        return PinShot(
            kind="ok",
            served_provider=served_provider,
            served_model=served_model,
            body_provider=body_provider,
            body_model=body_model,
            header_provider=header_provider,
            header_model=header_model,
        )
    return PinShot(
        kind="mismatch",
        name=_mismatch_name(served_provider, served_model),
        served_provider=served_provider,
        served_model=served_model,
        body_provider=body_provider,
        body_model=body_model,
        header_provider=header_provider,
        header_model=header_model,
    )


def abstain_payload(shot: PinShot) -> dict[str, Any]:
    """Named ABSTAIN body for a 503 pin. No rows. Stops the generate retry."""
    return {
        "ok": False,
        "status": "ABSTAIN",
        "values": [],
        "insights_fail": shot.name,
        "pin_reason": shot.vault_reason,
        "pin_stop": True,
        "code": shot.name,
        "live_5000_ci": False,
        "served_provider": None,
        "served_model": None,
    }


def refusal_payload(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "ABSTAIN",
        "values": [],
        "insights_fail": reason,
        "pin_stop": True,
        "code": reason,
        "live_5000_ci": False,
    }


def _forward_bearer() -> str | None:
    """Relay a configured ov_ key. Never a demo viewer key and never a provider key."""
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if not raw or raw == _DEMO_VIEWER_KEY:
            continue
        return raw
    return None


def post_once(base_url: str) -> PinShot:
    """One pinned chat completion. No retry. Retry-After is not slept."""
    cfg = pin_config()
    if cfg.refusal:
        return PinShot(kind="caller_error", name=cfg.refusal)
    body = stamp_generate_body(
        {
            "messages": [{"role": "user", "content": "Reply with only: ok"}],
            "max_tokens": _MAX_TOKENS,
            "stream": False,
        }
    )
    headers: dict[str, str] = {}
    bearer = _forward_bearer()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    stamped = stamp_generate_headers(headers) or {}
    url = f"{base_url.rstrip('/')}{_CHAT_PATH}"
    try:
        with httpx.Client(timeout=5.0) as http:
            res = http.post(url, json=body, headers=stamped)
    except httpx.HTTPError:
        return PinShot(
            kind="unavailable",
            name=f"pin_unavailable:{cfg.provider}/{cfg.model}",
            vault_reason="vault_unreachable",
        )
    try:
        payload = res.json()
    except ValueError:
        payload = None
    return interpret(res.status_code, payload, res.headers, cfg)


def open_round() -> PinRound:
    """Preflight. No ``OPENVAULT_URL`` and a sendable pin: the round is not pinned.

    A caller-error pin blocks before any HTTP. A vault 503 with no served ids,
    or a partial or disagreeing served set, blocks the round. The caller
    leaves n at 0 and makes no case call.
    """
    cfg = pin_config()
    if cfg.refusal:
        return PinRound(active=True, blocked=True, reason=cfg.refusal)
    base = _base_url()
    if not base:
        return PinRound(active=False)
    shot = post_once(base)
    if shot.kind == "ok":
        return PinRound(active=True)
    return PinRound(
        active=True,
        blocked=True,
        reason=shot.name,
        vault_reason=shot.vault_reason or None,
    )


def next_answer() -> PinShot:
    """One pinned call for one question. Inactive when the round is not pinned."""
    base = _base_url()
    if not base:
        return PinShot(kind="inactive")
    return post_once(base)


def envelope_mismatch(env: Mapping[str, Any] | None) -> str | None:
    """INVALID name when a generate envelope's served_model is not the pin.

    No served field and no generate attribution: the question did not claim a
    model, so the vault shot already decided. A reported or missing attribution
    must carry the pin.
    """
    if not isinstance(env, Mapping):
        return None
    attr = str(env.get("served_attribution") or "")
    has_model = "served_model" in env
    if attr not in {"reported", "missing"} and not has_model:
        return None
    cfg = pin_config()
    model = env.get("served_model")
    provider = env.get("served_provider")
    model_s = model.strip() if isinstance(model, str) else ""
    provider_s = provider.strip() if isinstance(provider, str) else ""
    if model_s == cfg.model:
        return None
    return f"pin_mismatch:{provider_s or 'missing'}/{model_s or 'missing'}"
