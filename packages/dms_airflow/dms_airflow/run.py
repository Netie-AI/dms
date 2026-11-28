"""Run ask then benchmark the way a two-task DAG would, without a scheduler.

Used by the CI timer. The bench operator's WRONG check is separate: this
helper returns the report so a not-configured bench still has a duration.
"""

from __future__ import annotations

import time
from typing import Any

from dms_airflow.bench import fail_if_wrong


def run_ask_then_benchmark(client: Any, *, space_id: str, question: str) -> dict[str, Any]:
    started = time.perf_counter()
    answer = client.ask(space_id=space_id, question=question)
    report = client.benchmark_run(space_id=space_id)
    seconds = time.perf_counter() - started
    wrong = report.get("wrong")
    failed = False
    try:
        fail_if_wrong(report)
    except Exception:
        failed = True
    return {
        "ask_outcome": answer.get("outcome"),
        "ask_route": answer.get("route"),
        "bench_outcome": report.get("outcome"),
        "bench_code": report.get("code"),
        "wrong": wrong,
        "benchmark_failed": failed,
        "seconds": seconds,
    }
