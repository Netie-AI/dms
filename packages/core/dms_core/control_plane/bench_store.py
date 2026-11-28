"""Space benchmark store (FEAT-BENCH-SPACE-01).

Benchmarks are a separate table from verified queries. The retriever does
not read this store. ``DATABASE_URL`` selects Postgres (the alembic tables).
Unset uses process memory, which is what tests and a database-less demo use.

Swap: a durable accuracy book per Space. Flag off does not call this module.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any
from uuid import uuid4

_SOURCES = frozenset({"steward", "verified_example"})
_ITEM_STATUS = frozenset({"valid", "invalid"})
_RUN_STATUS = frozenset({"queued", "running", "done", "cancelled", "failed"})
_REVIEWS = frozenset({"good", "bad"})
_DEFAULT_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class BenchRefused(Exception):
    """The write was refused. ``code`` is a short token, never a row value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BenchStoreError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now(seq: int) -> str:
    from datetime import UTC, datetime

    return f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S')}.{seq:06d}Z"


def _copy_run(run: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    body = dict(run)
    body["results"] = [dict(row) for row in results]
    body.pop("seq", None)
    return body


class MemoryBenchStore:
    """Process-local book. One lock. Upgrade: the Postgres store below."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._results: dict[str, list[dict[str, Any]]] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def _tick(self) -> int:
        self._seq += 1
        return self._seq

    def add_item(
        self,
        *,
        space_id: str,
        question: str,
        gold_sql: str,
        status: str,
        invalid_reason: str,
        source: str,
    ) -> dict[str, Any]:
        if status not in _ITEM_STATUS or source not in _SOURCES:
            raise BenchRefused("bad_request")
        with self._lock:
            seq = self._tick()
            row = {
                "id": str(uuid4()),
                "space_id": space_id,
                "question": question,
                "gold_sql": gold_sql,
                "status": status,
                "invalid_reason": invalid_reason,
                "source": source,
                "created_at": _now(seq),
                "seq": seq,
            }
            self._items[row["id"]] = row
            return dict(row)

    def list_items(self, space_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._items.values() if row["space_id"] == space_id]
        rows.sort(key=lambda row: int(row.get("seq") or 0))
        for row in rows:
            row.pop("seq", None)
        return rows

    def get_item(self, space_id: str, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._items.get(item_id)
            if row is None or row["space_id"] != space_id:
                return None
            body = dict(row)
        body.pop("seq", None)
        return body

    def update_item(
        self,
        space_id: str,
        item_id: str,
        *,
        gold_sql: str,
        status: str,
        invalid_reason: str,
    ) -> dict[str, Any] | None:
        if status not in _ITEM_STATUS:
            raise BenchRefused("bad_request")
        with self._lock:
            row = self._items.get(item_id)
            if row is None or row["space_id"] != space_id:
                return None
            row["gold_sql"] = gold_sql
            row["status"] = status
            row["invalid_reason"] = invalid_reason
            body = dict(row)
        body.pop("seq", None)
        return body

    def delete_item(self, space_id: str, item_id: str) -> bool:
        with self._lock:
            row = self._items.get(item_id)
            if row is None or row["space_id"] != space_id:
                return False
            del self._items[item_id]
            return True

    def start_run(self, space_id: str, total: int) -> dict[str, Any]:
        with self._lock:
            seq = self._tick()
            run_id = str(uuid4())
            row = {
                "id": run_id,
                "space_id": space_id,
                "status": "queued",
                "progress_done": 0,
                "progress_total": total,
                "cancel_requested": False,
                "tally": {},
                "created_at": _now(seq),
                "seq": seq,
            }
            self._runs[run_id] = row
            self._results[run_id] = []
            return _copy_run(row, [])

    def set_status(self, run_id: str, status: str) -> None:
        if status not in _RUN_STATUS:
            raise BenchRefused("bad_request")
        with self._lock:
            row = self._runs.get(run_id)
            if row is not None:
                row["status"] = status

    def set_progress(self, run_id: str, done: int, total: int) -> None:
        with self._lock:
            row = self._runs.get(run_id)
            if row is None:
                return
            row["progress_done"] = done
            row["progress_total"] = total

    def request_cancel(self, space_id: str, run_id: str) -> bool:
        with self._lock:
            row = self._runs.get(run_id)
            if row is None or row["space_id"] != space_id:
                return False
            if row["status"] in {"done", "cancelled", "failed"}:
                return True
            row["cancel_requested"] = True
            return True

    def cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            row = self._runs.get(run_id)
            return bool(row and row["cancel_requested"])

    def add_result(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if run_id not in self._runs:
                raise BenchStoreError("run_not_found")
            row = dict(result)
            row["id"] = str(uuid4())
            row["run_id"] = run_id
            self._results.setdefault(run_id, []).append(row)
            return dict(row)

    def finish_run(self, run_id: str, *, status: str, tally: dict[str, Any]) -> None:
        if status not in _RUN_STATUS:
            raise BenchRefused("bad_request")
        with self._lock:
            row = self._runs.get(run_id)
            if row is None:
                return
            row["status"] = status
            row["tally"] = dict(tally)

    def get_run(self, space_id: str, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._runs.get(run_id)
            if row is None or row["space_id"] != space_id:
                return None
            return _copy_run(row, self._results.get(run_id, []))

    def latest_run(self, space_id: str) -> dict[str, Any] | None:
        with self._lock:
            rows = [row for row in self._runs.values() if row["space_id"] == space_id]
            if not rows:
                return None
            row = max(rows, key=lambda item: int(item.get("seq") or 0))
            return _copy_run(row, self._results.get(str(row["id"]), []))

    def set_review(
        self,
        space_id: str,
        run_id: str,
        result_id: str,
        *,
        review: str,
        note: str,
    ) -> dict[str, Any] | None:
        if review not in _REVIEWS:
            raise BenchRefused("bad_request")
        if review == "bad" and not note.strip():
            raise BenchRefused("review_note_required")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run["space_id"] != space_id:
                return None
            for row in self._results.get(run_id, []):
                if row["id"] == result_id:
                    row["review"] = review
                    row["review_note"] = note.strip()
                    return dict(row)
        return None


class PostgresBenchStore:
    """Alembic tables ``dms.space_benchmarks``, runs, and results."""

    def __init__(self, database_url: str, tenant_id: str) -> None:
        if not database_url:
            raise BenchStoreError("bench_store_unavailable")
        self._url = database_url
        self._tenant = tenant_id or _DEFAULT_TENANT

    def _conn(self) -> Any:
        import psycopg

        from dms_core.control_plane.session import set_tenant_context

        try:
            conn = psycopg.connect(self._url)
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        try:
            set_tenant_context(conn, self._tenant, role="steward")
        except Exception:
            conn.close()
            raise BenchStoreError("bench_store_failed") from None
        return conn

    def add_item(
        self,
        *,
        space_id: str,
        question: str,
        gold_sql: str,
        status: str,
        invalid_reason: str,
        source: str,
    ) -> dict[str, Any]:
        if status not in _ITEM_STATUS or source not in _SOURCES:
            raise BenchRefused("bad_request")
        item_id = str(uuid4())
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO dms.space_benchmarks (
                  id, tenant_id, space_id, question, gold_sql, status,
                  invalid_reason, source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item_id,
                    self._tenant,
                    space_id,
                    question,
                    gold_sql,
                    status,
                    invalid_reason,
                    source,
                ),
            )
            conn.commit()
        except BenchStoreError:
            raise
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        got = self.get_item(space_id, item_id)
        if got is None:
            raise BenchStoreError("bench_store_failed")
        return got

    def list_items(self, space_id: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                SELECT id, space_id, question, gold_sql, status, invalid_reason,
                       source, created_at
                FROM dms.space_benchmarks
                WHERE space_id = %s
                ORDER BY created_at, id
                """,
                (space_id,),
            )
            return [_item_row(row) for row in cur.fetchall()]
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()

    def get_item(self, space_id: str, item_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                SELECT id, space_id, question, gold_sql, status, invalid_reason,
                       source, created_at
                FROM dms.space_benchmarks
                WHERE space_id = %s AND id = %s
                """,
                (space_id, item_id),
            )
            row = cur.fetchone()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return None if row is None else _item_row(row)

    def update_item(
        self,
        space_id: str,
        item_id: str,
        *,
        gold_sql: str,
        status: str,
        invalid_reason: str,
    ) -> dict[str, Any] | None:
        if status not in _ITEM_STATUS:
            raise BenchRefused("bad_request")
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                UPDATE dms.space_benchmarks
                SET gold_sql = %s, status = %s, invalid_reason = %s
                WHERE space_id = %s AND id = %s
                """,
                (gold_sql, status, invalid_reason, space_id, item_id),
            )
            changed = cur.rowcount
            conn.commit()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        if not changed:
            return None
        return self.get_item(space_id, item_id)

    def delete_item(self, space_id: str, item_id: str) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM dms.space_benchmarks WHERE space_id = %s AND id = %s",
                (space_id, item_id),
            )
            changed = cur.rowcount
            conn.commit()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return bool(changed)

    def start_run(self, space_id: str, total: int) -> dict[str, Any]:
        run_id = str(uuid4())
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO dms.space_bench_runs (
                  id, tenant_id, space_id, status, progress_done, progress_total,
                  cancel_requested, tally_json
                ) VALUES (%s, %s, %s, 'queued', 0, %s, false, '{}')
                """,
                (run_id, self._tenant, space_id, total),
            )
            conn.commit()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        got = self.get_run(space_id, run_id)
        if got is None:
            raise BenchStoreError("bench_store_failed")
        return got

    def set_status(self, run_id: str, status: str) -> None:
        if status not in _RUN_STATUS:
            raise BenchRefused("bad_request")
        self._exec(
            "UPDATE dms.space_bench_runs SET status = %s WHERE id = %s",
            (status, run_id),
        )

    def set_progress(self, run_id: str, done: int, total: int) -> None:
        self._exec(
            """
            UPDATE dms.space_bench_runs
            SET progress_done = %s, progress_total = %s
            WHERE id = %s
            """,
            (done, total, run_id),
        )

    def request_cancel(self, space_id: str, run_id: str) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                UPDATE dms.space_bench_runs
                SET cancel_requested = true
                WHERE id = %s AND space_id = %s
                """,
                (run_id, space_id),
            )
            changed = cur.rowcount
            conn.commit()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return bool(changed)

    def cancel_requested(self, run_id: str) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT cancel_requested FROM dms.space_bench_runs WHERE id = %s",
                (run_id,),
            )
            row = cur.fetchone()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return bool(row and row[0])

    def add_result(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        result_id = str(uuid4())
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO dms.space_bench_results (
                  id, tenant_id, run_id, benchmark_id, question, served_sql,
                  outcome, bucket, reason, gold_n, served_n, ordered_rows,
                  review, review_note
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, ''
                )
                """,
                (
                    result_id,
                    self._tenant,
                    run_id,
                    str(result.get("benchmark_id") or ""),
                    str(result.get("question") or ""),
                    str(result.get("served_sql") or ""),
                    str(result.get("outcome") or ""),
                    str(result.get("bucket") or ""),
                    str(result.get("reason") or ""),
                    int(result.get("gold_n") or 0),
                    int(result.get("served_n") or 0),
                    bool(result.get("ordered")),
                ),
            )
            conn.commit()
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        body = dict(result)
        body["id"] = result_id
        body["run_id"] = run_id
        body.setdefault("review", None)
        body.setdefault("review_note", "")
        return body

    def finish_run(self, run_id: str, *, status: str, tally: dict[str, Any]) -> None:
        if status not in _RUN_STATUS:
            raise BenchRefused("bad_request")
        self._exec(
            """
            UPDATE dms.space_bench_runs
            SET status = %s, tally_json = %s
            WHERE id = %s
            """,
            (status, json.dumps(tally, sort_keys=True), run_id),
        )

    def get_run(self, space_id: str, run_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                SELECT id, space_id, status, progress_done, progress_total,
                       cancel_requested, tally_json, created_at
                FROM dms.space_bench_runs
                WHERE space_id = %s AND id = %s
                """,
                (space_id, run_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            results = self._result_rows(conn, run_id)
        except BenchStoreError:
            raise
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return _run_row(row, results)

    def latest_run(self, space_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                SELECT id, space_id, status, progress_done, progress_total,
                       cancel_requested, tally_json, created_at
                FROM dms.space_bench_runs
                WHERE space_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (space_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            results = self._result_rows(conn, str(row[0]))
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return _run_row(row, results)

    def set_review(
        self,
        space_id: str,
        run_id: str,
        result_id: str,
        *,
        review: str,
        note: str,
    ) -> dict[str, Any] | None:
        if review not in _REVIEWS:
            raise BenchRefused("bad_request")
        if review == "bad" and not note.strip():
            raise BenchRefused("review_note_required")
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                UPDATE dms.space_bench_results AS result
                SET review = %s, review_note = %s
                FROM dms.space_bench_runs AS run
                WHERE result.id = %s
                  AND result.run_id = %s
                  AND run.id = result.run_id
                  AND run.space_id = %s
                """,
                (review, note.strip(), result_id, run_id, space_id),
            )
            changed = cur.rowcount
            conn.commit()
            if not changed:
                return None
            got = conn.execute(
                """
                SELECT id, run_id, benchmark_id, question, served_sql, outcome,
                       bucket, reason, gold_n, served_n, ordered_rows, review,
                       review_note
                FROM dms.space_bench_results
                WHERE id = %s
                """,
                (result_id,),
            ).fetchone()
        except BenchRefused:
            raise
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()
        return None if got is None else _result_row(got)

    def _result_rows(self, conn: Any, run_id: str) -> list[dict[str, Any]]:
        cur = conn.execute(
            """
            SELECT id, run_id, benchmark_id, question, served_sql, outcome,
                   bucket, reason, gold_n, served_n, ordered_rows, review,
                   review_note
            FROM dms.space_bench_results
            WHERE run_id = %s
            ORDER BY id
            """,
            (run_id,),
        )
        return [_result_row(row) for row in cur.fetchall()]

    def _exec(self, sql: str, params: tuple[Any, ...]) -> None:
        conn = self._conn()
        try:
            conn.execute(sql, params)
            conn.commit()
        except BenchRefused:
            raise
        except Exception:
            raise BenchStoreError("bench_store_failed") from None
        finally:
            conn.close()


def _item_row(row: tuple[Any, ...]) -> dict[str, Any]:
    created = row[7]
    return {
        "id": str(row[0]),
        "space_id": str(row[1]),
        "question": str(row[2]),
        "gold_sql": str(row[3]),
        "status": str(row[4]),
        "invalid_reason": str(row[5] or ""),
        "source": str(row[6]),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


def _result_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "run_id": str(row[1]),
        "benchmark_id": str(row[2]),
        "question": str(row[3]),
        "served_sql": str(row[4] or ""),
        "outcome": str(row[5]),
        "bucket": str(row[6] or ""),
        "reason": str(row[7] or ""),
        "gold_n": int(row[8] or 0),
        "served_n": int(row[9] or 0),
        "ordered": bool(row[10]),
        "review": None if row[11] is None else str(row[11]),
        "review_note": str(row[12] or ""),
    }


def _run_row(row: tuple[Any, ...], results: list[dict[str, Any]]) -> dict[str, Any]:
    raw = row[6] or "{}"
    try:
        tally = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except json.JSONDecodeError:
        tally = {}
    created = row[7]
    return {
        "id": str(row[0]),
        "space_id": str(row[1]),
        "status": str(row[2]),
        "progress_done": int(row[3] or 0),
        "progress_total": int(row[4] or 0),
        "cancel_requested": bool(row[5]),
        "tally": tally if isinstance(tally, dict) else {},
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
        "results": results,
    }


_MEMORY = MemoryBenchStore()
_MEMORY_LOCK = threading.Lock()


def reset_memory_bench() -> MemoryBenchStore:
    global _MEMORY
    with _MEMORY_LOCK:
        _MEMORY = MemoryBenchStore()
        return _MEMORY


def memory_bench() -> MemoryBenchStore:
    with _MEMORY_LOCK:
        return _MEMORY


def get_bench_store() -> MemoryBenchStore | PostgresBenchStore:
    """Postgres when ``DATABASE_URL`` is set. Otherwise the process store."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return memory_bench()
    tenant = os.environ.get("DMS_TENANT_ID", "").strip() or _DEFAULT_TENANT
    return PostgresBenchStore(url, tenant)
