"""Tamper-evident hash over signing audit rows.

This chain is the e-sign evidence certificate on ``dms.sign_audit_events``.
``dms.ledger_ref`` stays a pointer to Cortex. A row also stores ``cortex_entry_id``
when the Cortex append succeeded. The product answer ledger is not this table.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64

# Fields that enter the hash. ``prev_hash`` is the prefix, not a JSON field,
# so a row cannot include its own event hash.
FIELDS = (
    "id",
    "document_id",
    "envelope_id",
    "event_type",
    "identity",
    "auth_method",
    "ip",
    "user_agent",
    "consent_text",
    "intent",
    "sha256_before",
    "sha256_after",
    "created_at",
)


def canon(row: dict[str, Any]) -> str:
    body = {key: "" if row.get(key) is None else str(row.get(key)) for key in FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def link(prev_hash: str, row: dict[str, Any]) -> str:
    text = prev_hash + "\n" + canon(row)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def intact(events: list[dict[str, Any]]) -> bool:
    """True when each row's hash matches the previous tip, from genesis."""
    prev = GENESIS
    for row in events:
        if str(row.get("prev_hash") or "") != prev:
            return False
        if str(row.get("event_hash") or "") != link(prev, row):
            return False
        prev = str(row["event_hash"])
    return True
