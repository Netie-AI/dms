"""Ledger append facade — always via Cortex HTTP, never a local hash chain."""

from __future__ import annotations

from typing import Any

from cortex_client import CortexClient, LedgerAppendRequest, LedgerAppendResponse


def append_event(
    client: CortexClient,
    *,
    event_type: str,
    payload: dict[str, Any],
    actor: str | None = None,
) -> LedgerAppendResponse:
    return client.ledger_append(
        LedgerAppendRequest(event_type=event_type, payload=payload, actor=actor)
    )
