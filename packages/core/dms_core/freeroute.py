"""SCALE-FREE-AI-01 — FreeRoute consumption plan from OpenVault API metadata.

Prove/ask stay on ``free+normal``. This module names which free providers that
preference would attempt vs skip. It never holds provider tokens, never POSTs
chat completions, and never reads a local vault directory.

Platform/Free Keys owns mint. LIVE_KEY_ID is not rotated here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

FREEROUTE_PREFERENCE = "free+normal"
WRONG_DISCIPLINE = "WRONG=0"

# Groq-first, matching OpenVault onboard order. Not a second catalog.
_GROQ_FIRST: tuple[str, ...] = (
    "groq",
    "google",
    "openrouter",
    "cerebras",
    "mistral",
    "huggingface",
    "cloudflare",
)
_FREE_ROLES = frozenset({"free", "cheap"})
_FREE_TIERS = frozenset({"free", "freemium", "local"})
_PAID_TIERS = frozenset({"paid"})
_RETIRED_IDS = frozenset({"github_models"})
_SECRET_KEYS = frozenset(
    {
        "secret",
        "token",
        "api_key",
        "apikey",
        "password",
        "authorization",
        "live_key",
        "live_key_id",
        "key_material",
        "plaintext",
        "bearer",
        "value",
    }
)
_TOKEN_RE = re.compile(
    r"(?:Bearer\s+)?(?:ov_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9_-]{8,})",
    re.I,
)
_LIVE_KEY_RE = re.compile(r"\bLIVE_KEY(?:_ID)?\b")
_WS_RE = re.compile(r"\s+")

_CATALOG_PATHS: tuple[str, ...] = (
    "/api/freeroute/status",
    "/api/freeroute/onboard",
    "/api/tool/register",
    "/api/keys",
)


def redact_text(text: str) -> str:
    """Strip provider-looking tokens. Never keep LIVE_KEY / ov_ / sk- / gsk_."""
    out = _TOKEN_RE.sub("[redacted]", text or "")
    return _LIVE_KEY_RE.sub("[redacted]", out)


def normalize_label(raw: Any) -> str:
    return _WS_RE.sub(" ", str(raw or "").strip()).casefold()


def _first_str(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _str_list(raw: Any) -> list[str]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy catalog fields only. Drop secrets even if a vault row leaked them."""
    out: dict[str, Any] = {}
    for key, val in row.items():
        low = str(key).strip().casefold()
        if low in _SECRET_KEYS or "token" in low or "secret" in low:
            continue
        if isinstance(val, str):
            out[key] = redact_text(val)
        elif isinstance(val, (int, float, bool)) or val is None:
            out[key] = val
        elif isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
            if all(isinstance(x, (str, int, float, bool)) or x is None for x in val):
                out[key] = [
                    redact_text(x) if isinstance(x, str) else x for x in val
                ]
    label = _first_str(out, "label", "name", "id", "provider")
    provider = _first_str(out, "provider", "id", "add_key_provider")
    if label:
        out["label"] = label
    if provider:
        out["provider"] = provider
    models = _str_list(out.get("chat_models"))
    if models:
        out["chat_models"] = models
    return out


def _eligible_free_normal(row: Mapping[str, Any]) -> str | None:
    """None = attempt. Else skip reason under free+normal."""
    provider = _first_str(row, "provider", "id").casefold()
    if provider in _RETIRED_IDS:
        return "retired"
    if not normalize_label(_first_str(row, "label", "name", "id", "provider")):
        return "empty_label"
    if row.get("spendable") is False or row.get("openai_compatible") is False:
        return "not_spendable"
    tier = _first_str(row, "tier").casefold()
    role = _first_str(row, "role", "default_role").casefold()
    if tier in _PAID_TIERS and role not in _FREE_ROLES:
        return "paid_not_free_normal"
    free_role = role in _FREE_ROLES or role == ""
    free_tier = tier in _FREE_TIERS or tier == ""
    if not free_role and not free_tier:
        return "paid_not_free_normal"
    return None


def _sort_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    pid = _first_str(row, "provider", "id").casefold()
    if pid in _GROQ_FIRST:
        return (0, _GROQ_FIRST.index(pid), pid)
    return (1, 50, pid)


def _iter_maps(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, Mapping):
        for key in ("hops", "spendable", "providers", "keys", "items", "data", "results"):
            inner = raw.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, Mapping)]
        if raw.get("label") or raw.get("provider") or raw.get("id") or raw.get("name"):
            return [raw]
        return []
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, Mapping)]
    return []


def records_from_payloads(
    *,
    status: Any = None,
    onboard: Any = None,
    register: Any = None,
    keys: Any = None,
) -> list[dict[str, Any]]:
    """Flatten OpenVault API JSON into public rows. Later sources still participate
    so duplicate labels can be recorded as skips."""
    ordered: list[tuple[str, Any]] = [
        ("hops", status),
        ("keys", keys),
        ("onboard", onboard),
        ("spendable", status),
        ("register", register),
    ]
    rows: list[dict[str, Any]] = []
    for kind, payload in ordered:
        if payload is None:
            continue
        if kind == "hops" and isinstance(payload, Mapping):
            blob: Any = payload.get("hops")
        elif kind == "spendable" and isinstance(payload, Mapping):
            blob = payload.get("spendable")
        else:
            blob = payload
        for row in _iter_maps(blob):
            pub = public_row(row)
            pub["_source"] = kind
            rows.append(pub)
    return rows


def plan_free_providers(
    records: Sequence[Mapping[str, Any]],
    *,
    preference: str = FREEROUTE_PREFERENCE,
) -> dict[str, Any]:
    """Attempt unique free+normal labels; skip duplicates and paid/retired.

    First matching label wins (Groq-first). A second row with the same label is
    skipped so climb/ask does not burn extra keys for the same hop name.
    WRONG=0 is unchanged: this does not add providers to generate retries.
    """
    pref = (preference or FREEROUTE_PREFERENCE).strip() or FREEROUTE_PREFERENCE
    attempted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    ranked = sorted((public_row(r) for r in records), key=_sort_key)
    for row in ranked:
        label = _first_str(row, "label", "name", "id", "provider")
        norm = normalize_label(label)
        reason = _eligible_free_normal(row)
        entry: dict[str, Any] = {
            "label": label or "(none)",
            "provider": _first_str(row, "provider", "id") or "",
            "tier": _first_str(row, "tier"),
            "role": _first_str(row, "role", "default_role"),
            "source": str(row.get("_source") or ""),
        }
        models = _str_list(row.get("chat_models"))
        if models:
            entry["chat_models"] = models
        if reason:
            entry["reason"] = reason
            skipped.append(entry)
            continue
        if norm in seen_labels:
            entry["reason"] = "dup_label"
            skipped.append(entry)
            continue
        seen_labels.add(norm)
        attempted.append(entry)
    return {
        "ok": True,
        "preference": pref,
        "source": "openvault_api",
        "second_vault": False,
        "chat_tokens": False,
        "scrape_local_vault": False,
        "live_5000_ci": False,
        "live_key_rotate": False,
        "wrong_discipline": WRONG_DISCIPLINE,
        "attempted": attempted,
        "skipped": skipped,
        "attempted_count": len(attempted),
        "skipped_count": len(skipped),
        "dup_labels_skipped": sum(1 for row in skipped if row.get("reason") == "dup_label"),
    }


def empty_plan(*, openvault_ok: bool = False) -> dict[str, Any]:
    plan = plan_free_providers([])
    plan["ok"] = False
    plan["openvault_ok"] = openvault_ok
    return plan


def render_harness_md(plan: Mapping[str, Any]) -> str:
    """Attempt vs skip table. No tokens. Not a live vault dump."""
    raw_attempted = plan.get("attempted")
    raw_skipped = plan.get("skipped")
    attempted_rows: list[Any] = list(raw_attempted) if isinstance(raw_attempted, list) else []
    skipped_rows: list[Any] = list(raw_skipped) if isinstance(raw_skipped, list) else []
    lines = [
        "# FreeRoute providers (SCALE-FREE-AI-01)",
        "",
        f"- preference: `{plan.get('preference') or FREEROUTE_PREFERENCE}`",
        f"- source: `{plan.get('source') or 'openvault_api'}`",
        f"- attempted: **{plan.get('attempted_count', len(attempted_rows))}**",
        f"- skipped: **{plan.get('skipped_count', len(skipped_rows))}**",
        f"- dup labels skipped: **{plan.get('dup_labels_skipped', 0)}**",
        f"- WRONG discipline: `{plan.get('wrong_discipline') or WRONG_DISCIPLINE}`",
        f"- chat tokens: `{plan.get('chat_tokens', False)}`",
        f"- scrape local vault: `{plan.get('scrape_local_vault', False)}`",
        f"- second vault: `{plan.get('second_vault', False)}`",
        "",
        "## Attempted (free+normal)",
        "",
        "| label | provider | tier | role |",
        "|---|---|---|---|",
    ]
    if attempted_rows:
        for row in attempted_rows:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                f"| {row.get('label') or '?'} | {row.get('provider') or ''} | "
                f"{row.get('tier') or ''} | {row.get('role') or ''} |"
            )
    else:
        lines.append("| _none_ |  |  |  |")
    lines += [
        "",
        "## Skipped",
        "",
        "| label | provider | reason |",
        "|---|---|---|",
    ]
    if skipped_rows:
        for row in skipped_rows:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                f"| {row.get('label') or '?'} | {row.get('provider') or ''} | "
                f"{row.get('reason') or ''} |"
            )
    else:
        lines.append("| _none_ |  |  |")
    lines += [
        "",
        "Prove/ask generate still sends `model_preference=free+normal` only.",
        "Do not add extra hops to burn more keys. Do not invent COMPLETE.",
        "",
    ]
    return "\n".join(lines)


def candidate_models(plan: Mapping[str, Any]) -> list[str]:
    """Chat model ids from attempted providers. Empty if the vault named none."""
    out: list[str] = []
    seen: set[str] = set()
    raw_rows = plan.get("attempted")
    rows: list[Any] = list(raw_rows) if isinstance(raw_rows, list) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for model in _str_list(row.get("chat_models")):
            if model not in seen:
                seen.add(model)
                out.append(model)
    return out


def catalog_paths() -> tuple[str, ...]:
    return _CATALOG_PATHS


__all__ = [
    "FREEROUTE_PREFERENCE",
    "WRONG_DISCIPLINE",
    "candidate_models",
    "catalog_paths",
    "empty_plan",
    "normalize_label",
    "plan_free_providers",
    "public_row",
    "records_from_payloads",
    "redact_text",
    "render_harness_md",
]
