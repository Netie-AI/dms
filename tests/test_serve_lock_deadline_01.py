"""Serving lock waits stop at the ask deadline.

A writer that holds the file for 12s must not hang an ask. Twenty concurrent
HTTP asks, including the Space grants path, each return ``serving_lock_wait``
inside the serving deadline. After the writer releases, the same twenty match
a serial run.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from cortex_client.compute import insights_timeout_s
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter, SessionAcl

_PACK = Path(__file__).resolve().parent / "fixtures" / "curated_ceo" / "questions.yaml"
_IDS = (
    "cq_capacity_above_90",
    "cq_capacity_utilisation",
    "cq_cctv_wh_a",
    "cq_chemicals_list",
    "cq_cold_storage",
    "cq_expired_items",
    "cq_low_stock_wh_a",
    "cq_sales_top3_volume",
    "cq_sales_top5_syn_sales",
    "cq_sku_count",
    "cq_sku_count_by_category",
    "cq_spend_by_country",
    "cq_stock_value_by_category",
    "cq_supplier_ranking",
    "cq_top3_category_syn_plain",
    "ops_freight_spend_destination",
    "cq_sales_top5_syn_skus",
    "cq_sales_top5_value",
    "cq_sku_count_by_category_per",
    "cq_sku_count_syn_label",
)
_CLOCK = {
    "as_of",
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
}
_HOLD_S = 12.0


class _Cortex:
    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        del question
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_lock",
                output={"rows": [{"n": 1}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_lock_bind")

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_lock", hash="hash_lock")

    def ask(self, req: AskRequest) -> AskResponse:
        del req
        return AskResponse(
            answer="There are 5 locations.",
            badge="certified",
            sql_used="SELECT COUNT(*) AS n FROM locations",
            rows=[{"n": 5}],
            audit_id="aud_lock",
            route="sql",
        )


def _minter() -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-09T00:00:00+00:00",
            expires_at="2026-10-09T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


def _cases() -> list[dict[str, str]]:
    pack = yaml.safe_load(_PACK.read_text(encoding="utf-8"))
    spaces = dict(pack["spaces"])
    by_id = {str(row["id"]): row for row in pack["questions"]}
    out: list[dict[str, str]] = []
    for case_id in _IDS:
        row = by_id[case_id]
        out.append(
            {
                "id": case_id,
                "question": str(row["question"]),
                "space_id": str(spaces[str(row["space"])]),
            }
        )
    return out


def _body(case: dict[str, str], *, session: str) -> dict[str, str]:
    return {
        "question": case["question"],
        "space_id": case["space_id"],
        "session_id": session,
    }


def _port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _post(port: int, body: dict[str, str]) -> tuple[float, int, dict[str, Any]]:
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"http://127.0.0.1:{port}/v1/chat/ask",
            json=body,
            timeout=20.0,
        )
    except httpx.TimeoutException:
        return time.perf_counter() - started, 0, {}
    payload = response.json()
    if not isinstance(payload, dict):
        payload = {}
    return time.perf_counter() - started, response.status_code, payload


def _session(case: dict[str, str]) -> str:
    return f"ses_{case['id']}"


def _burst(
    port: int, cases: list[dict[str, str]]
) -> list[tuple[float, int, dict[str, Any]]]:
    box: list[tuple[float, int, dict[str, Any]] | None] = [None] * len(cases)
    errors: list[BaseException] = []

    def _one(index: int) -> None:
        try:
            box[index] = _post(port, _body(cases[index], session=_session(cases[index])))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_one, args=(i,)) for i in range(len(cases))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(25)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert all(item is not None for item in box)
    return [item for item in box if item is not None]


def _mask(env: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in env.items() if key not in _CLOCK}


def _rows(env: dict[str, Any]) -> list[str]:
    rows = env.get("rows") if isinstance(env.get("rows"), list) else []
    return sorted(repr(row) for row in rows)


def _same(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a = _mask(left)
    b = _mask(right)
    a_rows = _rows(a)
    b_rows = _rows(b)
    a.pop("rows", None)
    b.pop("rows", None)
    a.pop("values", None)
    b.pop("values", None)
    return a == b and a_rows == b_rows


@pytest.fixture()
def live_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    db = ensure_demo_warehouse(tmp_path / "serve.duckdb")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in (
        "DMS_CLOOP_B",
        "DMS_SCHEMA_CONTEXT",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_HARNESS_ASK_PATHS",
        "DMS_CCA_CASCADE",
        "DMS_LANE_BRONZE_SHEET",
        "DMS_ASK_RECONFIRM",
        "DMS_INSIGHTS_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    app = create_app()
    cortex = _Cortex()
    app.state.cortex = cortex
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=_minter(),
        warehouse_path=db,
    )
    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    health = 0
    while time.monotonic() < deadline:
        if not thread.is_alive():
            break
        try:
            health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).status_code
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if health == 200:
            break
        time.sleep(0.05)
    assert health == 200
    yield port, db
    server.should_exit = True
    thread.join(10)


def test_lock_wait_uses_only_the_time_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wait that starts late must stop at the time left, not a fresh deadline."""
    from dms_executor.demo_warehouse import ServingLockWait, serving_deadline

    monkeypatch.setattr(
        "cortex_client.compute.insights_timeout_s", lambda env=None: 0.6
    )
    db = ensure_demo_warehouse(tmp_path / "left.duckdb")
    ready = threading.Event()

    def _hold() -> None:
        con = connect_file(db)
        ready.set()
        time.sleep(2.0)
        con.close()

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    assert ready.wait(5)
    with serving_deadline():
        time.sleep(0.3)
        started = time.perf_counter()
        with pytest.raises(ServingLockWait):
            connect_file(db)
        elapsed = time.perf_counter() - started
    holder.join(3)
    assert elapsed < 0.45, elapsed


def test_twenty_http_asks_abstain_while_the_writer_holds(
    live_port: tuple[int, Path],
) -> None:
    port, db = live_port
    cases = _cases()
    assert len(cases) == 20
    assert any(case["id"].startswith("ops_") for case in cases)
    warm_elapsed, warm_status, warm_body = _post(port, _body(cases[0], session="ses_warm"))
    del warm_elapsed
    assert warm_status == 200, warm_body

    ready = threading.Event()

    def _hold() -> None:
        con = connect_file(db)
        ready.set()
        time.sleep(_HOLD_S)
        con.close()

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    assert ready.wait(5)

    locked = _burst(port, cases)
    limit = insights_timeout_s() + 1.0
    for elapsed, status, payload in locked:
        assert status == 200, payload
        assert elapsed <= limit, elapsed
        assert payload.get("badge") == "ABSTAIN"
        assert payload.get("abstained") is True
        assert payload.get("rows") == []
        assert "serving_lock_wait" in list(payload.get("assumptions") or [])
    holder.join(_HOLD_S + 2)

    serial = [_post(port, _body(case, session=_session(case))) for case in cases]
    again = _burst(port, cases)
    for left, right in zip(serial, again, strict=True):
        assert left[1] == 200, left[2]
        assert right[1] == 200, right[2]
        assert _same(left[2], right[2])
