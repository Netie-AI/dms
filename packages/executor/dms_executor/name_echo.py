"""Drop ungranted relation names from user-visible abstain fields.

The ticket keeps the name. ``space_id``, ``session_id``, and the other
id fields are not rewritten. Matching is the whole identifier, any
length, so a name shorter than four characters is not a hole and a
name is not cut out of a longer identifier.

``ungranted:file`` and ``ungranted:unparsed`` are reason codes. They
are not relation names.
"""

from __future__ import annotations

import re
from typing import Any

# Reason-code tails. Not a table list.
_CODE_TAILS = frozenset({"file", "unparsed"})
_ID_KEYS = frozenset(
    {
        "answer_id",
        "ask_id",
        "audit_id",
        "issuer_key_id",
        "org_id",
        "run_id",
        "session_id",
        "space_id",
        "ticket_id",
    }
)
_NAME_KEYS = frozenset(
    {
        "abstain_reason",
        "assumptions",
        "demote_note",
        "message",
        "messages",
        "text",
    }
)
_TAIL = re.compile(
    r"(?i)(?<![A-Za-z0-9_])ungranted(?:_table)?:("
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r"(?:,[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)*"
    r")"
)


def echoed_names(text: str) -> list[str]:
    """Relation tails after ``ungranted:`` or ``ungranted_table:``."""
    found: list[str] = []
    for match in _TAIL.finditer(text or ""):
        for piece in match.group(1).split(","):
            name = piece.strip()
            if not name or name.casefold() in _CODE_TAILS:
                continue
            if name not in found:
                found.append(name)
    return found


def _idents(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for piece in names:
        for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", piece):
            key = ident.casefold()
            if key in _CODE_TAILS or key in seen:
                continue
            seen.add(key)
            out.append(ident)
    out.sort(key=len, reverse=True)
    return out


def scrub_text(text: str, names: list[str]) -> str:
    """Remove each name as a whole identifier. A longer token stays."""
    if not text or not names:
        return text
    qualified = sorted((n for n in names if "." in n), key=len, reverse=True)
    for piece in qualified:
        text = re.sub(
            rf"(?i)(?<![A-Za-z0-9_]){re.escape(piece)}(?![A-Za-z0-9_])",
            "",
            text,
        )
    for ident in _idents(names):
        text = re.sub(
            rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])",
            "",
            text,
        )
    return text


def _scrub_value(value: Any, names: list[str]) -> Any:
    if isinstance(value, str):
        return scrub_text(value, names)
    if isinstance(value, list):
        return [_scrub_value(item, names) for item in value]
    if isinstance(value, dict):
        return {
            str(key): item if str(key) in _ID_KEYS else _scrub_value(item, names)
            for key, item in value.items()
        }
    return value


def _collect(reason: str, env: dict[str, Any]) -> list[str]:
    blobs = [reason]
    for key in _NAME_KEYS:
        value = env.get(key)
        if isinstance(value, str):
            blobs.append(value)
        elif isinstance(value, list):
            blobs.extend(str(item) for item in value if isinstance(item, str))
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if isinstance(item, dict) and isinstance(item.get("outcome"), str):
                blobs.append(item["outcome"])
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict) and isinstance(unsure.get("why"), str):
            blobs.append(unsure["why"])
    found: list[str] = []
    for blob in blobs:
        for name in echoed_names(blob):
            if name not in found:
                found.append(name)
    return found


def hide_echo(env: dict[str, Any], reason: str) -> dict[str, Any]:
    """Scrub name-carrying fields. Id fields are left as they are."""
    names = _collect(reason, env)
    if not names:
        return env
    for key in _NAME_KEYS:
        if key not in env or key in _ID_KEYS:
            continue
        env[key] = _scrub_value(env[key], names)
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if isinstance(item, dict) and isinstance(item.get("outcome"), str):
                item["outcome"] = scrub_text(item["outcome"], names)
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict) and isinstance(unsure.get("why"), str):
            unsure["why"] = scrub_text(unsure["why"], names)
    return env
