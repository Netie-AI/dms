"""CREATE-SURFACE-01. Create app only. Flag off is inert.

A create run is one cheap plan, then one component at a time, then confirm.
Doc and slides are not this surface. Numbers cite their SQL and source row.
The log stores model, tokens, and cost only when the response included them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.create_surface import (
    CreateDisabled,
    CreateProblem,
    create_enabled,
    draft_events,
    start_session,
)
from dms_executor.lake_registry import close_lakes
from dms_executor.verified_queries import (
    _rows_for_space,
    lookup_verified_query,
    scope_key,
)
from fastapi.testclient import TestClient

FLAG = "DMS_CREATE"
SPACE = "space-create-01"
DMS_SECRET = "dms-test-secret"
OV_KEY = "ov_customer_test"
INSTALL_KEY = "ov_install_should_not_be_used"
HEADERS = {
    "Authorization": f"Bearer {DMS_SECRET}",
    "X-OpenVault-Key": OV_KEY,
}

INTENT = {
    "goal": "See order trend",
    "audience": "Founder",
    "decision": "Whether to add stock",
    "clarify": None,
}
PLAN = {
    "tables": ["orders"],
    "joins": [],
    "metrics": [{"name": "total", "sql": "SELECT SUM(amount) AS amount FROM orders"}],
}
PLAN_CARD = {
    **INTENT,
    **PLAN,
    "outline": [{"id": "main", "title": "Main"}],
    "estimated_tokens": 400,
    "estimated_cost": 0.02,
}
FORECAST = {
    "id": "rev",
    "title": "Orders",
    "sql": "SELECT month, amount FROM orders ORDER BY month",
    "time": "month",
    "value": "amount",
    "method": "drift",
    "horizon": 2,
}
APP = {
    "title": "Orders",
    "widgets": [
        {
            "id": "by_month",
            "title": "Orders by month",
            "sql": "SELECT month, amount FROM orders ORDER BY month",
            "chart": {"kind": "line", "x": "month", "y": ["amount"]},
        }
    ],
    "forecasts": [FORECAST],
}


def _clear_settings() -> None:
    get_settings.cache_clear()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "orders.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders (month VARCHAR, amount INTEGER)")
    con.execute(
        "INSERT INTO orders VALUES "
        "('2024-01', 15), ('2024-02', 18), ('2024-03', 21), "
        "('2024-04', 24), ('2024-05', 27), ('2024-06', 30)"
    )
    con.close()
    return path


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str | None) -> Path:
    if flag is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, flag)
    monkeypatch.setenv("DMS_API_KEYS", f"admin:{DMS_SECRET}")
    monkeypatch.setenv("DMS_CREATE_WAREHOUSE", str(_lake(tmp_path)))
    monkeypatch.setenv("DMS_CREATE_GRANTS", "orders")
    monkeypatch.setenv("DMS_CREATE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    _clear_settings()
    return tmp_path / "orders.duckdb"


def _allow(monkeypatch: pytest.MonkeyPatch) -> None:
    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        del action, actor
        return ComplianceDecision(allowed=True, reason="test_allow", action="studio.create")

    monkeypatch.setattr("dms_api.routes.studio_create.compliance_gate", allow)


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str | None) -> TestClient:
    _env(monkeypatch, tmp_path, flag)
    _allow(monkeypatch)
    return TestClient(create_app())


class _Stub:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.bodies: list[dict[str, Any]] = []
        self.keys: list[str] = []

    def __call__(self, body: dict[str, Any], api_key: str) -> Any:
        self.bodies.append(body)
        self.keys.append(api_key)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_transport(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _Stub:
    stub = _Stub(script)
    monkeypatch.setattr("dms_executor.create_surface.default_transport", stub)
    return stub


def _usage(spec: dict[str, Any], prompt: int, cost: float) -> dict[str, Any]:
    return {
        "content": spec,
        "usage": {
            "model": "routed-by-customer",
            "prompt_tokens": prompt,
            "completion_tokens": 4,
            "total_tokens": prompt + 4,
            "cost": cost,
        },
    }


def test_flag_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw in (None, "0", "true", "yes", "01", ""):
        if raw is None:
            monkeypatch.delenv(FLAG, raising=False)
        else:
            monkeypatch.setenv(FLAG, raw)
        assert create_enabled() is False
    monkeypatch.setenv(FLAG, "1")
    assert create_enabled() is True


def test_flag_off_status_is_the_same_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bodies = []
    for raw in (None, "0", "true", "yes", "01"):
        client = _client(monkeypatch, tmp_path / raw if raw else tmp_path / "unset", raw)
        response = client.get("/v1/studio/create/status")
        assert response.status_code == 200
        bodies.append(response.json())
        missing = client.post("/v1/studio/create/sessions", json={}, headers=HEADERS)
        assert missing.status_code == 404
    assert bodies == [{"enabled": False}] * len(bodies)


def test_flag_off_does_not_call_or_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, None)
    stub = _patch_transport(monkeypatch, [INTENT])
    with pytest.raises(CreateDisabled):
        start_session(
            space_id=SPACE,
            kind="app",
            goal="See order trend",
            audience="Founder",
            api_key=OV_KEY,
            actor="api_admin",
        )
    assert stub.bodies == []
    assert list((tmp_path / "store").glob("*.json")) == []


def test_both_keys_required_and_no_install_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("OPENVAULT_API_KEY", INSTALL_KEY)
    stub = _patch_transport(monkeypatch, [INTENT, INTENT])
    body = {
        "space_id": SPACE,
        "kind": "app",
        "goal": "See order trend",
        "audience": "Founder",
    }
    missing = client.post("/v1/studio/create/sessions", json=body)
    assert missing.status_code == 401
    assert missing.json()["detail"] == "dms_api_key_required"
    bad = client.post(
        "/v1/studio/create/sessions",
        json=body,
        headers={"Authorization": "Bearer nope", "X-OpenVault-Key": OV_KEY},
    )
    assert bad.status_code == 401
    no_ov = client.post(
        "/v1/studio/create/sessions",
        json=body,
        headers={"Authorization": f"Bearer {DMS_SECRET}"},
    )
    assert no_ov.status_code == 401
    assert no_ov.json()["detail"] == "ov_api_key_required"
    assert stub.bodies == []
    ok = client.post("/v1/studio/create/sessions", json=body, headers=HEADERS)
    assert ok.status_code == 200
    assert stub.keys == [OV_KEY]
    assert INSTALL_KEY not in json.dumps(stub.bodies)


def test_request_has_no_model_or_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    stub = _patch_transport(monkeypatch, [INTENT])
    start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    assert stub.bodies
    assert set(stub.bodies[0]) == {"role", "messages", "schema"}
    assert stub.bodies[0]["role"] == "create_plan"
    payload = json.loads(stub.bodies[0]["messages"][1]["content"])
    overview = payload["overview"][0]
    assert overview["name"] == "orders"
    assert len(overview["sample"]) <= 20
    assert overview["stats"]
    assert "rows" not in payload
    assert "model" not in stub.bodies[0]
    assert "provider" not in stub.bodies[0]
    assert "strict" not in stub.bodies[0]


def _run_kind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    spec: dict[str, Any],
    *,
    usage: bool,
    repair: bool = False,
) -> tuple[TestClient, dict[str, Any], _Stub]:
    client = _client(monkeypatch, tmp_path, "1")
    script: list[Any]
    if usage:
        script = [
            _usage(PLAN_CARD, 18, 0.02),
            _usage(spec, 12, 0.04),
        ]
    else:
        script = [PLAN_CARD, spec]
    if repair:
        script.append(spec)
    stub = _patch_transport(monkeypatch, script)
    opened = client.post(
        "/v1/studio/create/sessions",
        json={"space_id": SPACE, "kind": kind, "goal": "See order trend", "audience": "Founder"},
        headers=HEADERS,
    )
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]
    confirmed = client.post(
        f"/v1/studio/create/sessions/{session_id}/intent",
        json={"answer": None},
        headers=HEADERS,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["step"] == "plan"
    planned = client.post(
        f"/v1/studio/create/sessions/{session_id}/plan",
        json={},
        headers=HEADERS,
    )
    assert planned.status_code == 200, planned.text
    assert planned.json()["plan"]["tables"][0]["name"] == "orders"
    drafted = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate",
        headers=HEADERS,
    )
    assert drafted.status_code == 200, drafted.text
    return client, drafted.json(), stub


def _assert_citations(artifact: dict[str, Any]) -> None:
    found = False
    for row in _walk_citations(artifact):
        found = True
        assert row.get("sql")
        assert "orders" in str(row["sql"])
        assert isinstance(row.get("source_row"), dict)
        assert row.get("value") not in (None, "")
    assert found


def _walk_citations(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        cites = node.get("citations")
        if isinstance(cites, list):
            out.extend(row for row in cites if isinstance(row, dict))
        summary = node.get("summary_citations")
        if isinstance(summary, list):
            out.extend(row for row in summary if isinstance(row, dict))
        for value in node.values():
            if isinstance(value, (dict, list)) and value is not cites and value is not summary:
                out.extend(_walk_citations(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_citations(item))
    return out


def test_e2e_app_on_synthetic_orders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logs = []
    for kind, spec in (("app", APP),):
        _client, view, stub = _run_kind(
            monkeypatch, tmp_path / kind, kind, spec, usage=False
        )
        assert view["shown"] is True
        assert view["step"] == "draft"
        assert view["final"] is False
        artifact = view["artifact"]
        _assert_citations(artifact)
        forecast = artifact["forecasts"][0]
        assert forecast["forecast"][0]["value"] == 33
        assert forecast["forecast"][1]["value"] == 36
        assert forecast["backtest_error"] == 0
        assert forecast["forecast"][0]["time"] == "2024-07"
        log = view["log"]
        assert log["prompt_tokens"] is None
        assert log["completion_tokens"] is None
        assert log["total_tokens"] is None
        assert log["cost"] is None
        assert log["model"] is None
        assert view["estimate"]["tokens"] == 400
        assert view["estimate"]["cost"] == pytest.approx(0.02)
        assert view["log"]["cost"] is None
        assert isinstance(log["seconds"], float)
        assert log["seconds"] > 0
        assert all(set(body) == {"role", "messages", "schema"} for body in stub.bodies)
        assert all(key == OV_KEY for key in stub.keys)
        stored = json.loads((tmp_path / kind / "store" / f"{view['session_id']}.json").read_text())
        blob = json.dumps(stored)
        assert OV_KEY not in blob
        assert DMS_SECRET not in blob
        logs.append({"kind": kind, "seconds": log["seconds"], "tokens": None, "cost": None})
        print("CREATE_MEASURE", json.dumps(logs[-1]))
    (tmp_path / "measured.json").write_text(json.dumps(logs))


def test_log_copies_usage_when_the_response_has_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, view, _stub = _run_kind(monkeypatch, tmp_path, "app", APP, usage=True)
    log = view["log"]
    assert log["model"] == "routed-by-customer"
    assert log["prompt_tokens"] == 30
    assert log["completion_tokens"] == 8
    assert log["total_tokens"] == 38
    assert log["cost"] == pytest.approx(0.06)
    assert log["seconds"] > 0


def test_stream_steps_then_confirm_writes_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    stub = _patch_transport(monkeypatch, [PLAN_CARD, APP])
    view = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    from dms_executor.create_surface import confirm_final, confirm_intent, draft_plan, export_bytes

    confirm_intent(session_id=view["session_id"], api_key=OV_KEY)
    draft_plan(session_id=view["session_id"], api_key=OV_KEY)
    labels: list[str] = []
    for event in draft_events(session_id=view["session_id"], api_key=OV_KEY):
        labels.append(str(event["label"]))
        seen = [body["role"] for body in stub.bodies]
        if event["status"] == "started" and event["step"] == "sql":
            assert "create_generate" not in seen
        if event["step"] == "sql" and event["status"] == "done":
            assert "create_generate" in seen
        assert "steps" in event["spend"]
    assert labels[:1] == ["Fetching orders"]
    assert labels.index("Fetching orders") < labels.index("Writing SQL")
    assert labels.index("Writing SQL") < labels.index("Checking")
    assert labels.index("Checking") < labels.index("Drafting the app")
    assert labels[-1] == "Waiting for confirmation"
    from dms_executor.create_surface import CreateProblem

    with pytest.raises(CreateProblem, match="confirm_required"):
        export_bytes(session_id=view["session_id"], kind="pptx")
    close_lakes()
    ready = confirm_final(session_id=view["session_id"], actor="api_admin")
    assert ready["step"] == "ready"
    assert ready["final"] is True
    app_file = tmp_path / "store" / view["session_id"] / "app.json"
    assert app_file.is_file()
    assert "Orders by month" in app_file.read_text(encoding="utf-8")
    rows = _rows_for_space(Path(tmp_path / "orders.duckdb"), scope_key(SPACE))
    assert any(row.get("provenance") == "create_accept" for row in rows)
    question = next(row["question"] for row in rows if row.get("provenance") == "create_accept")
    assert (
        lookup_verified_query(
            question,
            space_id=SPACE,
            warehouse=Path(tmp_path / "orders.duckdb"),
            grantable={"orders"},
            dialect="duckdb",
        )
        is None
    )


def test_http_stream_and_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    _patch_transport(monkeypatch, [PLAN_CARD, APP])
    opened = client.post(
        "/v1/studio/create/sessions",
        json={"space_id": SPACE, "kind": "app", "goal": "See order trend", "audience": "Founder"},
        headers=HEADERS,
    )
    session_id = opened.json()["session_id"]
    client.post(
        f"/v1/studio/create/sessions/{session_id}/intent",
        json={"answer": None},
        headers=HEADERS,
    )
    client.post(f"/v1/studio/create/sessions/{session_id}/plan", json={}, headers=HEADERS)
    with client.stream(
        "POST",
        f"/v1/studio/create/sessions/{session_id}/run",
        headers=HEADERS,
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "Fetching orders" in body
    assert "Writing SQL" in body
    assert "Checking" in body
    assert "Drafting the app" in body
    assert "Waiting for confirmation" in body
    early = client.get(f"/v1/studio/create/artifacts/{session_id}/pptx", headers=HEADERS)
    assert early.status_code == 400
    close_lakes()
    confirmed = client.post(
        f"/v1/studio/create/sessions/{session_id}/confirm",
        headers=HEADERS,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["step"] == "ready"
    app_file = tmp_path / "store" / session_id / "app.json"
    assert app_file.is_file()
    assert "Orders by month" in app_file.read_text()


def test_ungranted_and_destructive_do_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lake = _env(monkeypatch, tmp_path, "1")
    import dms_executor.sql_loop as sql_loop

    ran: list[str] = []
    real = sql_loop.run_readonly

    def spy(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return real(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", spy)
    _patch_transport(
        monkeypatch,
        [
            {
                **INTENT,
                "tables": ["payroll"],
                "joins": [],
                "metrics": [],
                "outline": [{"id": "main", "title": "Main"}],
            }
        ],
    )
    from dms_executor.create_surface import confirm_intent, draft_plan

    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    assert opened["step"] == "abstain"
    assert opened["abstain"]["abstain_reason"] == "ungranted"
    assert not any("payroll" in sql.lower() for sql in ran)
    _patch_transport(
        monkeypatch,
        [
            PLAN_CARD,
            {
                "title": "Orders",
                "widgets": [
                    {
                        "id": "drop_widget",
                        "title": "Do not run",
                        "sql": "DROP TABLE orders",
                        "chart": {"kind": "bar", "x": "month", "y": ["amount"]},
                    }
                ],
            },
        ],
    )
    second = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    confirm_intent(session_id=second["session_id"], api_key=OV_KEY)
    draft_plan(session_id=second["session_id"], api_key=OV_KEY)
    blocked = None
    for event in draft_events(session_id=second["session_id"], api_key=OV_KEY):
        blocked = event.get("session") or blocked
    assert blocked is not None
    assert blocked["step"] == "abstain"
    assert not any(sql.strip().upper().startswith("DROP") for sql in ran)
    close_lakes()
    con = duckdb.connect(str(lake))
    count = con.execute("SELECT count(*) FROM orders").fetchone()
    con.close()
    assert count is not None
    assert count[0] == 6


def test_unsupported_number_is_withheld(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, view, _stub = _run_kind(
        monkeypatch,
        tmp_path,
        "app",
        {
            "title": "Orders",
            "widgets": [
                {
                    "id": "bad_widget",
                    "title": "Orders total 99999.",
                    "sql": "SELECT month, amount FROM orders ORDER BY month",
                    "chart": {"kind": "line", "x": "month", "y": ["amount"]},
                }
            ],
        },
        usage=False,
        repair=True,
    )
    assert view["shown"] is False
    assert "unsupported_claim" in view["issues"]
    assert view["artifact"] is None


def test_refresh_shows_the_new_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, view, _stub = _run_kind(monkeypatch, tmp_path, "app", APP, usage=False)
    lake = tmp_path / "orders.duckdb"
    close_lakes()
    con = duckdb.connect(str(lake))
    con.execute("INSERT INTO orders VALUES ('2024-07', 33)")
    con.close()
    from dms_executor.create_surface import read_artifact

    refreshed = read_artifact(session_id=view["session_id"])
    assert refreshed["changes"]
    assert any(row["after"] == "33" or "33" in row["after"] for row in refreshed["changes"])


def test_css_keeps_the_breathing_light() -> None:
    css = Path("apps/ui/src/components/studio/createSurface.css").read_text(encoding="utf-8")
    assert "create-breathe" in css
    assert "prefers-reduced-motion" in css
    assert "create-spend" in css
    ui = Path("apps/ui/src/components/studio/CreateSurface.tsx").read_text(encoding="utf-8")
    assert "create-spend" in ui
    assert "create-estimate" in ui
    assert "create-warn" in ui
    ask = Path("packages/executor/dms_executor/generative_ask.py").read_text(encoding="utf-8")
    assert "DMS_CREATE" not in ask


def _drive(session_id: str) -> dict[str, Any]:
    from dms_executor.create_surface import confirm_intent, draft_plan

    confirm_intent(session_id=session_id, api_key=OV_KEY)
    draft_plan(session_id=session_id, api_key=OV_KEY)
    last: dict[str, Any] | None = None
    for event in draft_events(session_id=session_id, api_key=OV_KEY):
        found = event.get("session")
        if isinstance(found, dict):
            last = found
    assert last is not None
    return last


def _two_piece_card(**extra: Any) -> dict[str, Any]:
    card = {
        **PLAN_CARD,
        "outline": [
            {"id": "one", "title": "One"},
            {"id": "two", "title": "Two"},
        ],
    }
    card.update(extra)
    return card


def _open_app(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> tuple[_Stub, dict[str, Any]]:
    stub = _patch_transport(monkeypatch, script)
    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    return stub, _drive(opened["session_id"])


def test_full_build_ignores_the_old_caps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_MAX_TOKENS", "1")
    monkeypatch.setenv("DMS_CREATE_BUDGETS", json.dumps({"tenant-a": {"max_steps": 1}}))
    card = _two_piece_card()
    stub, built = _open_app(monkeypatch, [card, APP, APP])
    assert built["stopped"] is None
    assert built["message"] is None
    assert len(built["outline"]) == 2
    assert len(built["artifact"]["widgets"]) == 2
    assert stub.script == []
    assert len(stub.bodies) == 3


def test_runaway_guard_stops_with_a_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_MAX_STEPS", "2")
    card = _two_piece_card()
    stub, stopped = _open_app(monkeypatch, [card, APP, APP])
    assert stopped["stopped"] == "runaway_steps"
    assert "runaway" in stopped["message"]
    assert len(stopped["outline"]) == 2
    assert len(stopped["artifact"]["widgets"]) == 1
    assert len(stub.bodies) == 2
    assert stub.script

    monkeypatch.setenv("DMS_CREATE_MAX_STEPS", "60")
    monkeypatch.setenv("DMS_CREATE_MAX_USD", "0.03")
    stub, priced = _open_app(
        monkeypatch,
        [_usage(card, 4, 0.02), _usage(APP, 4, 0.02), _usage(APP, 4, 0.01)],
    )
    assert priced["stopped"] == "runaway_usd"
    assert "runaway" in priced["message"]
    assert len(priced["outline"]) == 2
    assert len(priced["artifact"]["widgets"]) == 1
    assert len(stub.bodies) == 2

    monkeypatch.delenv("DMS_CREATE_MAX_USD", raising=False)
    from dms_executor.create_surface import CreateCallError

    stub, blocked = _open_app(
        monkeypatch,
        [card, APP, CreateCallError("budget_exceeded"), APP],
    )
    assert blocked["stopped"] == "budget_exceeded"
    assert "402" in blocked["message"]
    assert len(blocked["artifact"]["widgets"]) == 1
    assert len(stub.bodies) == 3
    assert stub.script


def test_twice_the_estimate_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    card = _two_piece_card(estimated_cost=0.01)
    stub, built = _open_app(
        monkeypatch,
        [_usage(card, 4, 0.02), _usage(APP, 4, 0.02), _usage(APP, 4, 0.01)],
    )
    assert built["stopped"] is None
    assert "twice the estimate" in built["warning"]
    assert len(built["artifact"]["widgets"]) == 2
    assert stub.script == []


def test_overview_is_aggregates_and_a_sample(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lake = _env(monkeypatch, tmp_path, "1")
    con = duckdb.connect(str(lake))
    con.execute(
        "INSERT INTO orders VALUES "
        + ", ".join(f"('m{i:02d}', 1)" for i in range(40))
    )
    counted = con.execute("SELECT count(*), sum(amount) FROM orders").fetchone()
    con.close()
    assert counted is not None
    stub = _patch_transport(monkeypatch, [PLAN_CARD])
    start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    payload = json.loads(stub.bodies[0]["messages"][1]["content"])
    overview = payload["overview"][0]
    amount = next(row for row in overview["stats"] if row["column"] == "amount")
    month = next(row for row in overview["groups"] if row["column"] == "month")
    assert overview["row_count"] == counted[0]
    assert amount["n"] == counted[0]
    assert amount["sum_v"] == counted[1]
    assert len(overview["sample"]) <= 20
    assert len(overview["sample"]) < overview["row_count"]
    assert len(month["values"]) <= 8
    assert "rows" not in payload


def test_piece_retry_stops_at_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    card = {
        **PLAN_CARD,
        "outline": [
            {"id": "badpiece", "title": "Bad"},
            {"id": "goodpiece", "title": "Good"},
        ],
    }
    bad = {
        "title": "Orders",
        "widgets": [
            {
                "id": "bad_widget",
                "title": "Orders total 99999.",
                "sql": "SELECT month, amount FROM orders ORDER BY month",
                "chart": {"kind": "bar", "x": "month", "y": ["amount"]},
            }
        ],
    }
    stub = _patch_transport(monkeypatch, [card, bad, bad, APP, bad])
    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    built = _drive(opened["session_id"])
    assert built["shown"] is True
    assert "99999" not in json.dumps(built["artifact"])
    assert built["artifact"]["forecasts"][0]["forecast"][0]["value"] == 33
    generates = [body for body in stub.bodies if body["role"] == "create_generate"]
    bad_calls = [body for body in generates if "badpiece" in body["messages"][1]["content"]]
    good_calls = [body for body in generates if "goodpiece" in body["messages"][1]["content"]]
    assert len(bad_calls) == 2
    assert len(good_calls) == 1
    assert stub.script == [bad]
    for body in generates:
        payload = json.loads(body["messages"][1]["content"])
        assert "spec" not in payload
        assert "artifact" not in payload
        assert payload["piece"]["id"] in {"badpiece", "goodpiece"}
        assert payload["task"] in {"piece", "piece_retry"}


def test_doc_and_slides_are_not_this_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    stub = _patch_transport(monkeypatch, [PLAN_CARD])
    for kind in ("doc", "slides"):
        with pytest.raises(CreateProblem, match="kind_required"):
            start_session(
                space_id=SPACE,
                kind=kind,
                goal="See order trend",
                audience="Founder",
                api_key=OV_KEY,
                actor="api_admin",
            )
    assert stub.bodies == []
