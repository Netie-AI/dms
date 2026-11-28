"""HMAC for pipeline webhooks. Secret bytes never enter the store.

The store keeps an OpenVault reference. The caller resolves that reference
and passes the bytes here. A payload is ids and an outcome. Row values are
added only when the Space owner sets include_values.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from dms_core.control_plane.connect_secrets import key_id_for_ref

EVENTS = frozenset({"answer.served", "clarify.needed", "benchmark.finished"})
HEADER = "X-Dms-Signature"


class WebhookSecretError(Exception):
    """The OpenVault reference did not resolve. The message is the code only."""

    def __init__(self) -> None:
        super().__init__("webhook_secret_unresolved")


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Stable signature bytes. ASCII, keys sorted, no extra space."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sign_body(secret: bytes, body: bytes) -> str:
    """Hex HMAC-SHA256. The secret is not part of the return value."""
    if not isinstance(secret, (bytes, bytearray)) or not secret:
        raise WebhookSecretError()
    return hmac.new(bytes(secret), body, hashlib.sha256).hexdigest()


def signature_header(secret: bytes, body: bytes) -> str:
    return "sha256=" + sign_body(secret, body)


def verify_body(secret: bytes, body: bytes, signature: str) -> bool:
    """True only when ``signature`` matches ``body``. A changed body is false."""
    if not isinstance(signature, str) or not signature.startswith("sha256="):
        return False
    try:
        expected = signature_header(secret, body)
    except WebhookSecretError:
        return False
    return hmac.compare_digest(expected, signature)


def outcome_for_envelope(env: Mapping[str, Any]) -> str:
    """Typed outcome from envelope fields. Does not read the question."""
    current = env.get("outcome")
    if current in {"served", "clarify", "reconfirm", "refused", "error"}:
        return str(current)
    if isinstance(env.get("clarify"), dict) or env.get("route") == "needs_clarification":
        return "clarify"
    if isinstance(env.get("reconfirm"), dict):
        return "reconfirm"
    if env.get("abstained") is True or str(env.get("badge") or "").upper() == "ABSTAIN":
        return "refused"
    if env.get("route") or env.get("served_route") or env.get("badge"):
        return "served"
    return "error"


def event_for_envelope(env: Mapping[str, Any]) -> str | None:
    """Webhook name for a finished envelope, from typed fields only.

    Reads route, clarify, reconfirm, badge, and abstained. Does not read
    the question text.
    """
    if isinstance(env.get("clarify"), dict) or env.get("route") == "needs_clarification":
        return "clarify.needed"
    if isinstance(env.get("reconfirm"), dict):
        return None
    if env.get("abstained") is True or str(env.get("badge") or "").upper() == "ABSTAIN":
        return None
    return "answer.served"


def build_payload(
    *,
    event: str,
    space_id: str,
    outcome: str,
    route: str | None,
    audit_id: str | None = None,
    run_id: str | None = None,
    include_values: bool = False,
    values: list[Any] | None = None,
) -> dict[str, Any]:
    if event not in EVENTS:
        raise ValueError("webhook_event")
    payload: dict[str, Any] = {
        "audit_id": audit_id,
        "event": event,
        "outcome": outcome,
        "route": route,
        "run_id": run_id,
        "space_id": space_id,
    }
    if include_values:
        payload["values"] = list(values or [])
    return payload


def ref_key_id(secret_ref: str) -> str | None:
    """OpenVault key id for a webhook reference. None when the shape is wrong."""
    return key_id_for_ref(secret_ref)
