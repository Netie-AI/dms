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
  statement goes through;

and the places that append a ledger entry record its id and seq
(``Executor._ledger_verified_query``), so the audit row can say where in the chain
its pointer sits.

The trace is per thread and per ask: ``Executor.live_ask`` starts it and the
caller reads it once with ``take`` right after ``live_ask`` returns or raises. The
caller also clears it before a request starts (``chat_ask``), so a trace left by a
path that never took it cannot be read by the next request on that thread. It is
not part of the returned envelope, so nothing here reaches the customer or the
scorers that copy envelopes. Paths that run SQL locally (bronze sheets, the demo)
record nothing; the recorder then falls back to the envelope's own SQL.
"""

from __future__ import annotations

import threading

from dms_core.ask import ExecutedTrace

_LOCAL = threading.local()


def begin() -> None:
    """Start an empty trace for the ask this thread is about to run."""
    _LOCAL.statements = []
    _LOCAL.ledger = []


def record(sql: str | None, row_count: int) -> None:
    """Note one executed statement. A no-op outside a traced ask."""
    items = getattr(_LOCAL, "statements", None)
    if items is not None and sql is not None and str(sql).strip():
        items.append((str(sql), max(0, int(row_count))))


def record_ledger(entry_id: str | None, seq: int | None) -> None:
    """Note one ledger entry DMS appended. A no-op outside a traced ask."""
    items = getattr(_LOCAL, "ledger", None)
    if items is not None and entry_id:
        real_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        items.append((str(entry_id), real_seq))


def take() -> ExecutedTrace:
    """Return this thread's trace and clear it. Empty when no ask was traced."""
    trace = ExecutedTrace(
        statements=tuple(getattr(_LOCAL, "statements", None) or ()),
        ledger=tuple(getattr(_LOCAL, "ledger", None) or ()),
    )
    _LOCAL.statements = None
    _LOCAL.ledger = None
    return trace
