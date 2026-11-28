"""Space benchmarks (FEAT-BENCH-SPACE-01).

``DMS_SPACE_BENCH`` defaults to off. While it is not the digit 1, ``app.py``
does not mount this router, so every path here is a 404.

A steward saves ground-truth SQL. The read-only gate refuses a write. A
grant miss is stored as invalid with the reason. A run calls the product
ask path, compares rows with grade52, and can be cancelled between questions.
Nothing here inspects question text, and nothing is written to verified queries.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.bench_store import (
    BenchRefused,
    BenchStoreError,
    get_bench_store,
)
from dms_core.control_plane.onto_data import ontology_data_store
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    bench_example_rows,
    classify_benchmark_sql,
    run_benchmark_job,
    save_space_benchmark,
)

FLAG = "DMS_SPACE_BENCH"
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/spaces/{space_id}/benchmarks", tags=["benchmarks"])

_JOBS: dict[str, threading.Thread] = {}
_JOBS_LOCK = threading.Lock()


def enabled() -> bool:
    """``DMS_SPACE_BENCH=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class BenchCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    gold_sql: str = Field(min_length=1, max_length=8000)


class BenchUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_sql: str = Field(min_length=1, max_length=8000)


class BenchReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review: str = Field(min_length=1, max_length=8)
    note: str = Field(default="", max_length=500)


def product_ask(ask: Any, question: str, space_id: str) -> dict[str, Any]:
    """The product ask. OpenVault still picks the model. No provider id here."""
    env = ask.live_ask(question, space_id=space_id)
    if isinstance(env, dict):
        return env
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "route": "ask_failed",
        "text": "",
    }


def wait_bench_run(run_id: str, timeout: float = 5.0) -> bool:
    """Join the in-process job. Tests use this. True when the thread has stopped."""
    with _JOBS_LOCK:
        thread = _JOBS.get(run_id)
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def _steward(settings: Any) -> str:
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if not ontology_data_store().holds_steward_grant(role=settings.dms_actor_role, actor_id=actor):
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


def _http(exc: BaseException) -> HTTPException:
    if isinstance(exc, BenchRefused):
        return HTTPException(status_code=400, detail=exc.code)
    if isinstance(exc, BenchStoreError):
        return HTTPException(status_code=503, detail=exc.code)
    raise exc


def _gate(
    cortex: Any,
    actor: str,
    action: str,
    space_id: str,
    *,
    mutation: bool,
) -> None:
    decision = compliance_gate(
        action=action,
        actor=actor,
        metadata={"task_id": action, "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=mutation)


def _public_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "question": row["question"],
        "gold_sql": row["gold_sql"],
        "status": row["status"],
        "invalid_reason": row.get("invalid_reason") or "",
        "source": row.get("source") or "",
    }


def _public_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or "",
        "benchmark_id": row.get("benchmark_id") or "",
        "question": row.get("question") or "",
        "served_sql": row.get("served_sql") or "",
        "outcome": row.get("outcome") or "",
        "reason": row.get("reason") or "",
        "gold_n": int(row.get("gold_n") or 0),
        "served_n": int(row.get("served_n") or 0),
        "ordered": bool(row.get("ordered")),
        "review": row.get("review"),
        "review_note": row.get("review_note") or "",
    }


def _public_run(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    tally = row.get("tally") if isinstance(row.get("tally"), dict) else {}
    results = row.get("results") if isinstance(row.get("results"), list) else []
    return {
        "id": row["id"],
        "status": row["status"],
        "progress_done": int(row.get("progress_done") or 0),
        "progress_total": int(row.get("progress_total") or 0),
        "cancel_requested": bool(row.get("cancel_requested")),
        "tally": tally,
        "results": [_public_result(item) for item in results if isinstance(item, dict)],
    }


@router.get("")
def list_benchmarks(space_id: str, cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.list", space_id, mutation=False)
    store = get_bench_store()
    try:
        items = store.list_items(space_id)
        latest = store.latest_run(space_id)
    except BenchStoreError as exc:
        raise _http(exc) from None
    return {
        "enabled": True,
        "space_id": space_id,
        "items": [_public_item(row) for row in items],
        "latest_run": _public_run(latest),
    }


@router.post("")
def create_benchmark(
    space_id: str,
    body: BenchCreateIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.create", space_id, mutation=True)
    try:
        saved = save_space_benchmark(
            space_id=space_id,
            question=body.question,
            gold_sql=body.gold_sql,
        )
    except (BenchRefused, BenchStoreError) as exc:
        raise _http(exc) from None
    return _public_item(saved)


@router.patch("/{item_id}")
def update_benchmark(
    space_id: str,
    item_id: str,
    body: BenchUpdateIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.update", space_id, mutation=True)
    store = get_bench_store()
    try:
        current = store.get_item(space_id, item_id)
    except BenchStoreError as exc:
        raise _http(exc) from None
    if current is None:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        status, reason = classify_benchmark_sql(space_id, body.gold_sql)
        saved = store.update_item(
            space_id,
            item_id,
            gold_sql=body.gold_sql,
            status=status,
            invalid_reason=reason,
        )
    except (BenchRefused, BenchStoreError) as exc:
        raise _http(exc) from None
    if saved is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _public_item(saved)


@router.delete("/{item_id}")
def delete_benchmark(
    space_id: str,
    item_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, bool]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.delete", space_id, mutation=True)
    try:
        found = get_bench_store().delete_item(space_id, item_id)
    except BenchStoreError as exc:
        raise _http(exc) from None
    if not found:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"deleted": True}


@router.post("/from-examples")
def from_examples(space_id: str, cortex: CortexDep, settings: SettingsDep) -> dict[str, int]:
    """Copy verified examples into this book. A scored row is refused, not stored."""
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.from_examples", space_id, mutation=True)
    added = 0
    invalid = 0
    refused = 0
    try:
        rows = bench_example_rows(space_id)
    except Exception:
        logger.warning("bench_examples_unavailable")
        rows = []
    for row in rows:
        question = str(row.get("question") or "").strip()
        sql = str(row.get("sql") or "").strip()
        if not question or not sql:
            continue
        try:
            saved = save_space_benchmark(
                space_id=space_id,
                question=question[:2000],
                gold_sql=sql[:8000],
                source="verified_example",
            )
        except BenchRefused:
            refused += 1
            continue
        except BenchStoreError as exc:
            raise _http(exc) from None
        added += 1
        if saved.get("status") == "invalid":
            invalid += 1
    return {"added": added, "invalid": invalid, "refused": refused}


@router.post("/runs")
def start_run(
    space_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.run", space_id, mutation=True)
    store = get_bench_store()
    try:
        items = store.list_items(space_id)
        run = store.start_run(space_id, len(items))
    except BenchStoreError as exc:
        raise _http(exc) from None
    ask_service = request.app.state.ask_service

    def ask_fn(question: str) -> dict[str, Any]:
        return product_ask(ask_service, question, space_id)

    thread = threading.Thread(
        target=run_benchmark_job,
        args=(store, run["id"], items, ask_fn),
        name="space-bench",
        daemon=True,
    )
    with _JOBS_LOCK:
        _JOBS[str(run["id"])] = thread
    thread.start()
    return {
        "run_id": run["id"],
        "status": run["status"],
        "progress_done": 0,
        "progress_total": len(items),
    }


@router.get("/runs/{run_id}")
def get_run(
    space_id: str,
    run_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.get_run", space_id, mutation=False)
    try:
        run = get_bench_store().get_run(space_id, run_id)
    except BenchStoreError as exc:
        raise _http(exc) from None
    if run is None:
        raise HTTPException(status_code=404, detail="Not Found")
    body = _public_run(run)
    assert body is not None
    return body


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    space_id: str,
    run_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.cancel", space_id, mutation=True)
    store = get_bench_store()
    try:
        found = store.request_cancel(space_id, run_id)
        run = store.get_run(space_id, run_id)
    except BenchStoreError as exc:
        raise _http(exc) from None
    if not found or run is None:
        raise HTTPException(status_code=404, detail="Not Found")
    body = _public_run(run)
    assert body is not None
    return body


@router.post("/runs/{run_id}/results/{result_id}/review")
def review_result(
    space_id: str,
    run_id: str,
    result_id: str,
    body: BenchReviewIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    _gate(cortex, actor, "space_bench.review", space_id, mutation=True)
    try:
        saved = get_bench_store().set_review(
            space_id,
            run_id,
            result_id,
            review=body.review,
            note=body.note,
        )
    except (BenchRefused, BenchStoreError) as exc:
        raise _http(exc) from None
    if saved is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _public_result(saved)
