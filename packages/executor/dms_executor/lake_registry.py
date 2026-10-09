"""One DuckDB parent per lake file, and never two configurations at once.

Serving, schema reads, and value reads take cursors from one parent opened
``read_only=True`` with ``enable_external_access`` false. DuckDB then rejects
INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, and the other write statements
on that connection. Ingest uses a different parent, opened read-write, and
only after every read cursor has been returned and the read-only parent has
been closed. DuckDB refuses a read-only connect beside an open read-write
one (and the reverse), which is the HTTP 503 this registry exists to stop.

Callers get a cursor (``conn.cursor()``). Cursors from the read-only parent
run concurrently. The parent closes when the last lease returns, so the other
mode, or a later ``duckdb.connect`` in a test, can open the file.

ponytail: readers share one read-only parent. Once a writer is waiting, new
reads wait behind it and in-flight reads finish, then that writer holds one
read-write parent. The wait for that handoff is bounded
(``DMS_LAKE_INGEST_WAIT_S``, default 5s). Reads that already queued for the
writer run before the next writer. Ceiling: the pause is one ingest's own
write. Upgrade: a second file snapshot if that pause is too long.
"""

from __future__ import annotations

import contextvars
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import duckdb

#: Named reason when a queued serving lease waits out its budget.
SERVING_LEASE_CAP_REASON = "serving_lease_cap"
#: Named reason when an ask arrives and the waiter queue is already full.
SERVING_LEASE_QUEUE_FULL_REASON = "serving_lease_queue_full"
#: Named reason when the lease was granted and the ask deadline still passed.
SERVING_DEADLINE_EXCEEDED_REASON = "serving_deadline_exceeded"
#: How long ingest waits for in-flight reads to finish. ``DMS_LAKE_INGEST_WAIT_S``.
DEFAULT_INGEST_WAIT_S = 5.0
#: Concurrent serving cursors on one lake. ``DMS_LAKE_SERVING_LEASE_CAP``.
DEFAULT_SERVING_LEASE_CAP = 32
#: How long a serving lease waits for a free slot. ``DMS_LAKE_SERVING_LEASE_WAIT_S``.
DEFAULT_SERVING_LEASE_WAIT_S = 2.0
#: Queued asks that do not yet hold a cursor. ``DMS_LAKE_SERVING_LEASE_QUEUE_MAX``.
#: 32 holds Lead's short burst (cap plus 16 asks) twice over. A larger default
#: would track every thread the process can start, which is the unbound queue.
DEFAULT_SERVING_LEASE_QUEUE_MAX = 32
#: Seconds kept for work after the lease is granted (model, SQL, check).
#: Case 1 (cap 4, four leases held 0.3s, 16 asks): served work is the time
#: from the first serving grant on that ask to ``live_ask`` returning.
#: n=16, p50=0.498s, p95=0.547s. 0.55 is that p95 rounded up to 0.01s.
DEFAULT_SERVING_LEASE_RESERVE_S = 0.55
#: How long before the ask deadline a running query is interrupted.
#: ``DMS_LAKE_SERVING_DEADLINE_DELIVER_S``. The client timeout stays 8s.
#: Lead's bar is client p95 <= 7.5s. The watcher polls every 0.05s.
#: Under (c) load, 20 sequential deadline probes, this margin: client
#: p50=7.185s, p95=7.256s, max=7.262s, 0 bare timeouts. The interrupt is
#: scheduled at 8.0-0.90=7.10s, so receipt lags that schedule by p95=0.156s.
#: 0.90 = 0.50s (8.0 down to 7.5) + 0.16s overhead + 0.05s poll + 0.19s so
#: a slower runner still clears 7.5, rounded up to 0.01s. Threshold 7.10s,
#: so the interrupt fires by about 7.15s, before 7.3s. The same probes with
#: the margin put back to 0.2s: p50=7.867s p95=7.917s max=7.973s, over 7.5s.
DEFAULT_SERVING_DEADLINE_DELIVER_S = 0.90

_INGEST_WAIT_ENV = "DMS_LAKE_INGEST_WAIT_S"
_LEASE_CAP_ENV = "DMS_LAKE_SERVING_LEASE_CAP"
_LEASE_WAIT_ENV = "DMS_LAKE_SERVING_LEASE_WAIT_S"
_LEASE_QUEUE_ENV = "DMS_LAKE_SERVING_LEASE_QUEUE_MAX"
_LEASE_RESERVE_ENV = "DMS_LAKE_SERVING_LEASE_RESERVE_S"
_DEADLINE_DELIVER_ENV = "DMS_LAKE_SERVING_DEADLINE_DELIVER_S"
_serving_block: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dms_serving_lease_block",
    default=None,
)
_ask_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "dms_ask_deadline",
    default=None,
)
_leases_this_ask: contextvars.ContextVar[int] = contextvars.ContextVar(
    "dms_leases_this_ask",
    default=0,
)
# How often a queued waiter looks for a dropped client. Under the 0.5s bar.
# The deadline watcher uses the same slice, so an interrupt can lag the
# deliver threshold by this much.
_DISCONNECT_POLL_S = 0.05


class IngestWaitTimeout(Exception):
    """Ingest did not acquire the lake within ``DMS_LAKE_INGEST_WAIT_S``."""

    code = "ingest_wait_timeout"

    def __init__(self, waited_s: float) -> None:
        self.waited_s = waited_s
        super().__init__(f"ingest_wait_timeout after {waited_s:.3f}s")


class ServingLeaseCap(Exception):
    """A queued serving lease waited out ``DMS_LAKE_SERVING_LEASE_WAIT_S``."""

    code = SERVING_LEASE_CAP_REASON

    def __init__(self, cap: int, waited_s: float) -> None:
        self.cap = cap
        self.waited_s = waited_s
        super().__init__(f"serving_lease_cap cap={cap} waited_s={waited_s:.3f}")


class ServingLeaseQueueFull(Exception):
    """The ask arrived when the waiter queue was already at its bound."""

    code = SERVING_LEASE_QUEUE_FULL_REASON

    def __init__(self, bound: int) -> None:
        self.bound = bound
        super().__init__(f"serving_lease_queue_full bound={bound}")


class ServingWaitCancelled(Exception):
    """The waiter was dropped before a slot was granted."""

    code = "serving_wait_cancelled"


class ServingDeadlineExceeded(Exception):
    """A serving lease was granted and the ask deadline still passed."""

    code = SERVING_DEADLINE_EXCEEDED_REASON


def ingest_wait_s() -> float:
    return _env_float(_INGEST_WAIT_ENV, DEFAULT_INGEST_WAIT_S)


def serving_lease_cap() -> int:
    return _env_int(_LEASE_CAP_ENV, DEFAULT_SERVING_LEASE_CAP)


def serving_lease_queue_max() -> int:
    return _env_int(_LEASE_QUEUE_ENV, DEFAULT_SERVING_LEASE_QUEUE_MAX)


def serving_lease_reserve_s() -> float:
    """Time left for model, SQL, and the check after a lease is granted."""
    return _env_float(_LEASE_RESERVE_ENV, DEFAULT_SERVING_LEASE_RESERVE_S)


def serving_deadline_deliver_s() -> float:
    """Interrupt a running query this long before the ask deadline.

    This is not the lease reserve. The reserve decides whether a slot may be
    taken. This margin is how early ``interrupt()`` runs so the named abstain
    reaches a client whose timeout is still 8s, with p95 at or under 7.5s.
    """
    return _env_float(_DEADLINE_DELIVER_ENV, DEFAULT_SERVING_DEADLINE_DELIVER_S)


def serving_lease_wait_s() -> float:
    """How long this acquire may block.

    With no ask clock, that is ``DMS_LAKE_SERVING_LEASE_WAIT_S`` (default 2s).
    There is no fixed ceiling. With an ask clock, the wait is the time left
    before that ask times out, minus ``DMS_LAKE_SERVING_LEASE_RESERVE_S``.
    If that budget is already gone, the wait is 0 and the acquire abstains
    with ``serving_lease_cap`` without taking a slot.
    """
    configured = _env_float(_LEASE_WAIT_ENV, DEFAULT_SERVING_LEASE_WAIT_S)
    deadline = _ask_deadline.get()
    if deadline is None:
        return configured
    budget = deadline - time.monotonic() - serving_lease_reserve_s()
    if budget <= 0:
        return 0.0
    return min(configured, budget)


def bind_ask_deadline(deadline: float) -> tuple[
    contextvars.Token[float | None], contextvars.Token[int]
]:
    """This ask times out at ``deadline`` (``time.monotonic`` seconds)."""
    return _ask_deadline.set(deadline), _leases_this_ask.set(0)


def reset_ask_deadline(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[int]],
) -> None:
    _ask_deadline.reset(tokens[0])
    _leases_this_ask.reset(tokens[1])


def note_serving_lease_granted() -> None:
    if _ask_deadline.get() is None:
        return
    _leases_this_ask.set(_leases_this_ask.get() + 1)


def serving_deadline_missed() -> bool:
    """True when this ask took a lease and is now past its deadline."""
    deadline = _ask_deadline.get()
    if deadline is None or _leases_this_ask.get() < 1:
        return False
    return time.monotonic() > deadline


def ask_took_serving_lease() -> bool:
    return _leases_this_ask.get() >= 1


class AskControl:
    """One ask's cancel flag and the connection a running query can interrupt.

    The worker thread is never killed. A dropped client sets ``disconnected``.
    A query still inside ``execute`` is stopped with ``interrupt()`` then, or
    at the ask deadline at the latest. ``finally`` on the lease releases it.
    """

    def __init__(self) -> None:
        self.disconnected = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.deadline: float | None = None
        self.con: Any = None
        self._watcher: threading.Thread | None = None

    def arm(self, con: Any) -> None:
        with self.lock:
            self.con = con

    def disarm(self, con: Any) -> None:
        with self.lock:
            if self.con is con:
                self.con = None

    def start(self) -> None:
        with self.lock:
            if self._watcher is not None:
                return
            self._watcher = threading.Thread(
                target=self._run, name="dms-ask-deadline", daemon=True
            )
            self._watcher.start()

    def _run(self) -> None:
        # The ask thread keeps running. This daemon only calls interrupt().
        while not self.stop.wait(_DISCONNECT_POLL_S):
            if not self._interrupt_due():
                continue
            with self.lock:
                con = self.con
            if con is None:
                continue
            try:
                con.interrupt()
            except Exception:  # noqa: BLE001 - interrupt is best-effort
                pass

    def _interrupt_due(self) -> bool:
        if self.disconnected.is_set():
            return True
        deadline = self.deadline
        if deadline is None:
            return False
        return time.monotonic() >= deadline - serving_deadline_deliver_s()

    def close(self) -> None:
        self.stop.set()
        watcher = self._watcher
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(0.2)


_ask_control: contextvars.ContextVar[AskControl | None] = contextvars.ContextVar(
    "dms_ask_control",
    default=None,
)


def bind_ask_control(ctrl: AskControl) -> contextvars.Token[AskControl | None]:
    return _ask_control.set(ctrl)


def reset_ask_control(token: contextvars.Token[AskControl | None]) -> None:
    ctrl = _ask_control.get()
    _ask_control.reset(token)
    if ctrl is not None:
        ctrl.close()


def current_ask_control() -> AskControl | None:
    return _ask_control.get()


def ask_disconnected() -> bool:
    ctrl = _ask_control.get()
    return ctrl is not None and ctrl.disconnected.is_set()


def raise_if_ask_stopped() -> None:
    """Step boundary. A running query is not polled here; ``interrupt`` stops it."""
    if ask_disconnected():
        raise ServingWaitCancelled()
    if serving_deadline_missed():
        raise ServingDeadlineExceeded()


def block_serving_lease(reason: str) -> contextvars.Token[str | None]:
    """While an ask is already abstaining, further serving opens must not wait."""
    return _serving_block.set(reason)


def reset_serving_lease_block(token: contextvars.Token[str | None]) -> None:
    _serving_block.reset(token)


def serving_lease_blocked() -> str | None:
    return _serving_block.get()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = float(raw)
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


class _RWLock:
    """Many readers, one writer. A waiting writer goes ahead of new readers.

    The writer thread may re-enter. Reads that were already queued when a
    writer releases run before the next writer, so a tight ingest loop does
    not pin serving for the whole loop.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer: int | None = None
        self._write_depth = 0
        self._waiting_writers = 0
        self._next_ticket = 0
        self._queued: set[int] = set()
        self._handoff: set[int] = set()
        # Serving-queue tickets waiting on a writer. They are not ``_readers``.
        self._serving_blocked: set[int] = set()
        self.max_read_wait_s = 0.0
        self.max_readers_seen = 0

    def acquire_read(self) -> bool:
        """Take a shared hold. False when this thread already holds the write."""
        me = threading.get_ident()
        started = time.monotonic()
        with self._cond:
            if self._writer == me:
                return False
            ticket = self._next_ticket
            self._next_ticket += 1
            self._queued.add(ticket)
            acquired = False
            try:
                while self._writer is not None or (
                    self._waiting_writers > 0 and ticket not in self._handoff
                ):
                    self._cond.wait()
                self._handoff.discard(ticket)
                self._readers += 1
                if self._readers > self.max_readers_seen:
                    self.max_readers_seen = self._readers
                acquired = True
                if not self._handoff:
                    self._cond.notify_all()
            finally:
                self._queued.discard(ticket)
                if not acquired:
                    self._handoff.discard(ticket)
                    if not self._handoff:
                        self._cond.notify_all()
            waited = time.monotonic() - started
            if waited > self.max_read_wait_s:
                self.max_read_wait_s = waited
            return True

    def holds_write(self) -> bool:
        with self._cond:
            return self._writer == threading.get_ident()

    def note_serving_wait(self, ticket: int) -> bool:
        """True when a writer is ahead. The ticket joins the post-write handoff.

        The ticket is not a reader. Ingest is not pinned by someone still in
        the serving queue.
        """
        with self._cond:
            busy = self._writer is not None or self._waiting_writers > 0
            if busy:
                self._serving_blocked.add(ticket)
            else:
                self._serving_blocked.discard(ticket)
            return busy

    def try_acquire_read(self, ticket: int) -> bool:
        """Shared hold if it will not wait. False when a writer should go first."""
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:
                return False
            writer_busy = self._writer is not None or self._waiting_writers > 0
            handed = ticket in self._handoff
            if writer_busy and not handed:
                self._serving_blocked.add(ticket)
                return False
            self._serving_blocked.discard(ticket)
            self._handoff.discard(ticket)
            self._readers += 1
            if self._readers > self.max_readers_seen:
                self.max_readers_seen = self._readers
            if not self._handoff:
                self._cond.notify_all()
            return True

    def unmark_serving(self, ticket: int) -> None:
        with self._cond:
            self._serving_blocked.discard(ticket)
            dropped = ticket in self._handoff
            self._handoff.discard(ticket)
            if dropped and not self._handoff:
                self._cond.notify_all()

    def note_pause(self, waited: float) -> None:
        with self._cond:
            if waited > self.max_read_wait_s:
                self.max_read_wait_s = waited

    def release_read(self, held: bool) -> None:
        if not held:
            return
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self, timeout: float) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:
                self._write_depth += 1
                return
            deadline = time.monotonic() + timeout
            self._waiting_writers += 1
            # Readers blocked in wait() must see the waiting writer and stay there.
            self._cond.notify_all()
            try:
                while self._writer is not None or self._readers or self._handoff:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise IngestWaitTimeout(timeout)
                    self._cond.wait(remaining)
                self._writer = me
                self._write_depth = 1
            except BaseException:
                self._waiting_writers -= 1
                self._cond.notify_all()
                raise
            else:
                self._waiting_writers -= 1

    def release_write(self) -> None:
        with self._cond:
            self._write_depth -= 1
            if self._write_depth == 0:
                self._writer = None
                # Reads already queued for this ingest go before the next writer.
                # Serving-queue tickets are included so a tight ingest loop does
                # not skip them, and they still are not ``_readers`` until granted.
                self._handoff = set(self._queued) | set(self._serving_blocked)
                self._cond.notify_all()


class _Lake:
    def __init__(self) -> None:
        self.mu = threading.Condition(threading.Lock())
        self.rw = _RWLock()
        self.conn: duckdb.DuckDBPyConnection | None = None
        self.mode: str | None = None
        self.refs = 0
        # FIFO of waiters that do not hold a cursor yet. Bounded.
        self.queue: deque[int] = deque()
        self.waiters = 0
        self.max_waiters = 0
        self._next_ticket = 0
        self.cancelled: set[int] = set()


class _Lease:
    """Cursor plus the shared-or-exclusive hold. ``close`` does not drop the parent
    while another lease is still out.

    Use it as a context manager. A ``with`` block releases the write on the
    way out, including when the body raises, so serving is not left blocked.
    """

    def __init__(self, lake: _Lake, cur: duckdb.DuckDBPyConnection, *, mode: str) -> None:
        self._lake = lake
        self._cur = cur
        self._mode = mode
        self._closed = False

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._run(self._cur.execute, *args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return self._run(self._cur.executemany, *args, **kwargs)

    def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Step boundary, then the call. A query already inside DuckDB is stopped
        with ``interrupt()`` (disconnect now, ask deadline at the latest).
        ``close`` still releases the lease and the read lock.
        """
        raise_if_ask_stopped()
        ctrl = _ask_control.get()
        if ctrl is not None:
            ctrl.arm(self._cur)
        try:
            result = fn(*args, **kwargs)
        except ServingWaitCancelled:
            raise
        except ServingDeadlineExceeded:
            raise
        except Exception as exc:
            if ask_disconnected():
                raise ServingWaitCancelled() from exc
            interrupted = type(exc).__name__ == "InterruptException"
            if interrupted or serving_deadline_missed():
                raise ServingDeadlineExceeded() from exc
            raise
        finally:
            if ctrl is not None:
                ctrl.disarm(self._cur)
        raise_if_ask_stopped()
        if result is self._cur:
            return self
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _give_back(self._lake, self._cur)
        finally:
            if self._mode == "write":
                self._lake.rw.release_write()
            elif self._mode == "read":
                self._lake.rw.release_read(True)
            # "nested": this thread already holds the write lease.
            # Wake the serving queue after the writer flag and handoff are set.
            # give_back notifies while the writer may still be held.
            with self._lake.mu:
                self._lake.mu.notify_all()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)

    def __enter__(self) -> _Lease:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


_GUARD = threading.Lock()
_LAKES: dict[str, _Lake] = {}


def _key(path: Path | str) -> str:
    text = str(path)
    if text == ":memory:":
        raise ValueError("open_lake does not own :memory: databases")
    return str(Path(text).expanduser().resolve())


def _lake_for(key: str) -> _Lake:
    with _GUARD:
        lake = _LAKES.get(key)
        if lake is None:
            lake = _Lake()
            _LAKES[key] = lake
        return lake


def _open_parent(key: str, *, write: bool) -> duckdb.DuckDBPyConnection:
    if write:
        # Ingest path. Not the connection chat, schema, or value reads use.
        return duckdb.connect(key)
    # Serving instance. External access is off, then the config is locked so a
    # later SET on this parent cannot turn it back on. Cursors inherit both.
    # The lock is this connection only: after it closes, ingest may open the
    # file read-write and read a CSV. The two opens are never at the same time.
    con = duckdb.connect(key, read_only=True)
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return con


def _borrow(lake: _Lake, key: str, *, write: bool) -> duckdb.DuckDBPyConnection:
    want = "write" if write else "read"
    with lake.mu:
        if lake.conn is None:
            lake.conn = _open_parent(key, write=write)
            lake.mode = want
        elif lake.mode != want:
            raise RuntimeError(f"lake is open {lake.mode}, refused {want}")
        cur = lake.conn.cursor()
        lake.refs += 1
        return cur


def _leave_queue(lake: _Lake, ticket: int) -> None:
    try:
        lake.queue.remove(ticket)
    except ValueError:
        pass
    else:
        lake.waiters -= 1
    lake.cancelled.discard(ticket)
    lake.mu.notify_all()


def _acquire_serving(lake: _Lake, key: str) -> duckdb.DuckDBPyConnection:
    """FIFO wait for a serving slot, then the read lock.

    The read lock is taken only after a slot is granted. A waiter that times
    out, is cancelled, or raises leaves the queue in ``finally``. An arrival
    that finds the queue full does not join it.
    """
    if lake.rw.holds_write():
        raise RuntimeError("serving read cannot share the ingest write connection")
    blocked = serving_lease_blocked()
    if blocked == SERVING_LEASE_QUEUE_FULL_REASON:
        raise ServingLeaseQueueFull(serving_lease_queue_max())
    if blocked:
        raise ServingLeaseCap(serving_lease_cap(), 0.0)
    cap = serving_lease_cap()
    wait_s = serving_lease_wait_s()
    if _ask_deadline.get() is not None and wait_s <= 0:
        raise ServingLeaseCap(cap, 0.0)
    qmax = serving_lease_queue_max()
    in_queue = False
    ticket = -1
    started = time.monotonic()
    saw_writer = False
    try:
        with lake.mu:
            if lake.waiters >= qmax:
                raise ServingLeaseQueueFull(qmax)
            ticket = lake._next_ticket
            lake._next_ticket += 1
            lake.queue.append(ticket)
            lake.waiters += 1
            in_queue = True
            if lake.waiters > lake.max_waiters:
                lake.max_waiters = lake.waiters
            deadline = time.monotonic() + wait_s
            while True:
                if ticket in lake.cancelled or ask_disconnected():
                    raise ServingWaitCancelled()
                if _ask_deadline.get() is not None and serving_lease_wait_s() <= 0:
                    raise ServingLeaseCap(cap, time.monotonic() - started)
                if lake.rw.note_serving_wait(ticket):
                    saw_writer = True
                head = bool(lake.queue) and lake.queue[0] == ticket
                if head and lake.refs < cap and lake.rw.try_acquire_read(ticket):
                    try:
                        if lake.conn is not None and lake.mode != "read":
                            raise RuntimeError(f"lake is open {lake.mode}, refused read")
                        if lake.conn is None:
                            lake.conn = _open_parent(key, write=False)
                            lake.mode = "read"
                        cur = lake.conn.cursor()
                        lake.refs += 1
                    except BaseException:
                        lake.rw.release_read(True)
                        raise
                    _leave_queue(lake, ticket)
                    in_queue = False
                    if saw_writer:
                        lake.rw.note_pause(time.monotonic() - started)
                    return cur
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ServingLeaseCap(cap, wait_s)
                lake.mu.wait(min(remaining, _DISCONNECT_POLL_S))
    finally:
        if in_queue:
            with lake.mu:
                _leave_queue(lake, ticket)
            lake.rw.unmark_serving(ticket)


def _give_back(lake: _Lake, cur: duckdb.DuckDBPyConnection) -> None:
    with lake.mu:
        try:
            cur.close()
        except Exception:  # noqa: BLE001 - parent may already be shut down
            pass
        if lake.conn is None:
            lake.mu.notify_all()
            return
        if lake.refs > 0:
            lake.refs -= 1
        if lake.refs == 0:
            try:
                lake.conn.close()
            except Exception:  # noqa: BLE001
                pass
            lake.conn = None
            lake.mode = None
        lake.mu.notify_all()


def open_lake(path: Path | str, *, write: bool = False) -> _Lease:
    """Cursor for ``path``.

    ``write=False`` is the serving connection: DuckDB opened it read-only.
    ``write=True`` is ingest. It waits, bounded by ``DMS_LAKE_INGEST_WAIT_S``,
    until in-flight serving cursors are back and the read-only parent is
    closed, then opens a read-write connection. Once that wait has started,
    new serving reads wait behind it. The two parents are never open together.
    """
    key = _key(path)
    lake = _lake_for(key)
    if write:
        lake.rw.acquire_write(ingest_wait_s())
        try:
            cur = _borrow(lake, key, write=True)
        except BaseException:
            lake.rw.release_write()
            with lake.mu:
                lake.mu.notify_all()
            raise
        return _Lease(lake, cur, mode="write")
    cur = _acquire_serving(lake, key)
    note_serving_lease_granted()
    return _Lease(lake, cur, mode="read")


def reader_pause_s(path: Path | str) -> float:
    """Longest time a serving read on ``path`` has waited behind a writer."""
    key = _key(path)
    with _GUARD:
        lake = _LAKES.get(key)
    if lake is None:
        return 0.0
    return lake.rw.max_read_wait_s


def _lake_if(path: Path | str) -> _Lake | None:
    key = _key(path)
    with _GUARD:
        return _LAKES.get(key)


def serving_waiter_count(path: Path | str) -> int:
    """Waiters in the FIFO queue. They do not hold a cursor."""
    lake = _lake_if(path)
    if lake is None:
        return 0
    with lake.mu:
        return lake.waiters


def serving_max_waiters(path: Path | str) -> int:
    lake = _lake_if(path)
    if lake is None:
        return 0
    with lake.mu:
        return lake.max_waiters


def serving_max_readers(path: Path | str) -> int:
    """Peak shared holds. Queued waiters are not included."""
    lake = _lake_if(path)
    if lake is None:
        return 0
    return lake.rw.max_readers_seen


def cancel_serving_waiters(path: Path | str) -> None:
    """Drop every ask still waiting for a slot. In-flight leases stay out."""
    lake = _lake_if(path)
    if lake is None:
        return
    with lake.mu:
        lake.cancelled.update(lake.queue)
        lake.mu.notify_all()


def lease_refs(path: Path | str) -> int:
    """Leases currently out on ``path``. Zero when the lake has not been opened."""
    key = _key(path)
    with _GUARD:
        lake = _LAKES.get(key)
    if lake is None:
        return 0
    with lake.mu:
        return lake.refs


def serving_read_holds(path: Path | str) -> int:
    """Read locks currently held. A queued waiter does not count."""
    lake = _lake_if(path)
    if lake is None:
        return 0
    with lake.rw._cond:
        return lake.rw._readers


def close_lakes() -> None:
    """Drop every parent connection. In-flight leases release their own holds.

    Process shutdown (``Executor.close``). A replaced lake file is picked up
    on the next ``open_lake`` after the last lease has closed, because the
    parent is not kept once ``refs`` hits 0.
    """
    with _GUARD:
        lakes = list(_LAKES.values())
    for lake in lakes:
        with lake.mu:
            if lake.conn is not None:
                try:
                    lake.conn.close()
                except Exception:  # noqa: BLE001
                    pass
                lake.conn = None
            lake.mode = None
            lake.refs = 0
            lake.mu.notify_all()
