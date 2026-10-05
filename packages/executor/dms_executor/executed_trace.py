"""BANK-02 (dms#269): what actually ran for one ask, kept apart from the envelope.

The customer envelope is post-processed for the customer: an abstain carries
``sql_used=None`` and no rows, a document answer carries the placeholder
``-- document retrieval (no SQL)``. None of that is a record of what the engine
executed, and the audit export must not be derived from it.

So the places that execute SQL record the statement and its row count here, as
it happens and before any envelope post-processing:

- the Cortex contract ask, from the engine's own ``sql_used`` and ``rows``
  (``map_ask_response_to_envelope``);
- ``Executor.submit_sql``, which every certified, governed-metric and generated
  statement goes through.

The trace is per thread and per ask: ``Executor.live_ask`` starts it and the
caller reads it once with ``take`` right after ``live_ask`` returns or raises.
It is not part of the returned envelope, so nothing here reaches the customer or
the scorers that copy envelopes. Paths that run SQL locally (bronze sheets, the
demo) record nothing; the recorder then falls back to the envelope's own SQL.
"""

from __future__ import annotations

import threading

_LOCAL = threading.local()


def begin() -> None:
    """Start an empty trace for the ask this thread is about to run."""
    _LOCAL.items = []


def record(sql: str | None, row_count: int) -> None:
    """Note one executed statement. A no-op outside a traced ask."""
    items = getattr(_LOCAL, "items", None)
    if items is not None and sql is not None and str(sql).strip():
        items.append((str(sql), max(0, int(row_count))))


def take() -> list[tuple[str, int]]:
    """Return this thread's executed statements, in order, and clear the trace."""
    items = getattr(_LOCAL, "items", None) or []
    _LOCAL.items = None
    return list(items)
