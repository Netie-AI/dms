"""Postgres advisory locks keyed on (tenant_id, target_table)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg


class AdvisoryLockTimeout(TimeoutError):
    """Second writer timed out waiting for (tenant_id, target_table) lock."""


def _lock_keys(tenant_id: UUID | str, target_table: str) -> tuple[int, int]:
    digest = hashlib.sha256(f"{tenant_id}:{target_table}".encode()).digest()
    k1 = int.from_bytes(digest[:4], "big", signed=True)
    k2 = int.from_bytes(digest[4:8], "big", signed=True)
    return k1, k2


@contextmanager
def advisory_lock(
    conn: psycopg.Connection,
    tenant_id: UUID | str,
    target_table: str,
    *,
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.05,
) -> Iterator[None]:
    """Serialize amend-applies against the single-writer serving DB.

    Uses ``pg_try_advisory_lock`` so a contending writer times out cleanly
    instead of blocking forever.
    """
    k1, k2 = _lock_keys(tenant_id, target_table)
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    while time.monotonic() < deadline:
        row = conn.execute("SELECT pg_try_advisory_lock(%s, %s)", (k1, k2)).fetchone()
        if row and row[0]:
            acquired = True
            break
        time.sleep(poll_interval)
    if not acquired:
        raise AdvisoryLockTimeout(
            f"advisory lock timeout tenant={tenant_id} table={target_table}"
        )
    try:
        yield
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s, %s)", (k1, k2))
