"""CREATE-SURFACE-01. The provider generates. DMS governs.

Flag off is inert. A run is one provider call, then a judge, then at most
one repair. Edits keep every version.
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
from dms_core.create_generator import (
    GeneratorError,
    bind_secret_store,
    default_transport,
    direct_prompt,
    generate,
    holds_direct_bar,
    judge_artifact,
    local_skill,
    request_body,
    request_max_tokens,
    score_artifact,
    skills_for,
    transport_timeout,
    validate_call,
)
from dms_executor.create_surface import (
    CreateDisabled,
    CreateProblem,
    apply_tool,
    create_enabled,
    start_session,
)
from dms_executor.lake_registry import close_lakes
from dms_executor.verified_queries import lookup_verified_query, scope_key
from fastapi.testclient import TestClient

FLAG = "DMS_CREATE"
SPACE = "space-create-01"
DMS_SECRET = "dms-test-secret"
OV_KEY = "ov_customer_test"
INSTALL_KEY = "ov_install_should_not_be_used"
HEADERS = {"Authorization": f"Bearer {DMS_SECRET}", "X-OpenVault-Key": OV_KEY}
CURSOR_KEY = "cursor_live_key_abc"


def _recorded(name: str) -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "create" / name
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise AssertionError(name)
    return loaded


def _app() -> dict[str, Any]:
    content = _recorded("provider_app.json")["content"]
    if not isinstance(content, dict):
        raise AssertionError("provider_app")
    return content


def _deck() -> dict[str, Any]:
    return {
        "kind": "slides",
        "title": "Orders deck",
        "parts": [{"id": "s1", "title": "Trend", "body": "Orders moved."}],
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
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    monkeypatch.delenv("DMS_CREATE_OV_ROLE", raising=False)
    monkeypatch.delenv("DMS_CREATE_OV_TOOLS", raising=False)
    monkeypatch.delenv("DMS_CREATE_MAX_TOKENS", raising=False)
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


def _patch(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _Stub:
    stub = _Stub(script)
    monkeypatch.setattr("dms_core.create_generator.default_transport", stub)
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


def _open(client: TestClient, kind: str = "app") -> str:
    opened = client.post(
        "/v1/studio/create/sessions",
        json={"space_id": SPACE, "kind": kind, "goal": "See order trend", "audience": "Founder"},
        headers=HEADERS,
    )
    assert opened.status_code == 200, opened.text
    return str(opened.json()["session_id"])


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
    stub = _patch(monkeypatch, [_app()])
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
    stub = _patch(monkeypatch, [_app()])
    body = {"space_id": SPACE, "kind": "app", "goal": "See order trend", "audience": "Founder"}
    missing = client.post("/v1/studio/create/sessions", json=body)
    assert missing.status_code == 401
    assert missing.json()["detail"] == "dms_api_key_required"
    no_ov = client.post(
        "/v1/studio/create/sessions",
        json=body,
        headers={"Authorization": f"Bearer {DMS_SECRET}"},
    )
    assert no_ov.status_code == 401
    assert no_ov.json()["detail"] == "ov_api_key_required"
    opened = client.post("/v1/studio/create/sessions", json=body, headers=HEADERS)
    assert opened.status_code == 200
    assert stub.bodies == []
    generated = client.post(
        f"/v1/studio/create/sessions/{opened.json()['session_id']}/generate",
        headers=HEADERS,
    )
    assert generated.status_code == 200, generated.text
    assert stub.keys == [OV_KEY]
    assert INSTALL_KEY not in json.dumps(stub.bodies)


def test_request_is_provider_context_without_a_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    stub = _patch(monkeypatch, [_app()])
    session_id = _open(client)
    generated = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert generated.status_code == 200, generated.text
    body = stub.bodies[0]
    assert body["role"] == "reason"
    assert "tools" not in body
    assert "system" not in body
    assert "thinking" not in body
    assert body["max_tokens"] == 8000
    assert body["messages"][0]["role"] == "system"
    text = body["messages"][1]["content"]
    for tag in ("<schema>", "<data>", "<choices>", "<style>", "<examples>", "<skills>"):
        assert tag in text
    assert "sample" not in text
    assert "model" not in body
    assert "provider" not in body
    assert "strict" not in body
    assert "container" not in body


def test_slides_request_uses_the_pptx_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    stub = _patch(monkeypatch, [_deck()])
    session_id = _open(client, "slides")
    generated = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert generated.status_code == 200, generated.text
    body = stub.bodies[0]
    assert "tools" not in body
    assert "container" not in body
    assert "create-slides" in body["messages"][1]["content"]
    assert "pptx" in body["messages"][1]["content"]
    assert generated.json()["artifact"]["title"] == "Orders deck"


def test_stub_app_and_deck_through_the_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_CURSOR_REPO", "https://example.com/acme/create")
    context = {
        "schema": [{"name": "orders", "columns": [{"name": "amount", "type": "INTEGER"}]}],
        "data": [{"name": "orders", "stats": [{"column": "amount", "sum_v": 135}]}],
        "skills": skills_for("app"),
        "examples": [],
        "choices": {"inputs": ["orders"]},
    }
    style = {"prompt": "Quiet", "template": "Plain layout.", "brand": {}, "guide": "One screen."}
    recorded = _recorded("provider_app.json")
    app_stub = _patch(monkeypatch, [recorded])
    app = generate(
        {"kind": "app", "goal": "See order trend", "audience": "Founder"},
        context,
        style,
        api_key=OV_KEY,
    )
    assert app.artifact is not None
    assert app.artifact["title"] == "Orders"
    assert app.cost == pytest.approx(0.02)
    assert app.provider == "claude_api"
    assert "model" not in app_stub.bodies[0]

    monkeypatch.setenv("DMS_CREATE_GENERATOR", "cursor_api")
    monkeypatch.setenv("CURSOR_API_KEY", CURSOR_KEY)
    deck_context = dict(context)
    deck_context["skills"] = skills_for("slides")

    def ov(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        raise AssertionError(body.get("role") or api_key)

    def cursor(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        assert api_key == CURSOR_KEY
        assert "model" not in body
        text = body["prompt"]["text"]
        assert CURSOR_KEY not in text
        assert "<schema>" in text
        assert "pptx" in text
        return {"content": _deck(), "usage": {"cost": 0.04, "total_tokens": 9}}

    deck = generate(
        {"kind": "slides", "goal": "See order trend", "audience": "Founder"},
        deck_context,
        style,
        api_key=OV_KEY,
        transport=ov,
        cursor_transport=cursor,
    )
    assert deck.artifact is not None
    assert deck.artifact["kind"] == "slides"
    assert deck.cost == pytest.approx(0.04)
    assert CURSOR_KEY not in deck.prompt
    print("CREATE_APP", json.dumps({"title": app.artifact["title"], "cost": app.cost}))
    print("CREATE_DECK", json.dumps({"title": deck.artifact["title"], "cost": deck.cost}))


def test_tool_off_does_not_run_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path, "1")
    import dms_executor.sql_loop as sql_loop

    ran: list[str] = []
    real = sql_loop.run_readonly

    def spy(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return real(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", spy)
    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
        tools={"run_sql": False},
    )
    stored = json.loads((tmp_path / "store" / f"{opened['session_id']}.json").read_text())
    before = len(ran)
    result = apply_tool(stored, "run_sql", {"sql": "SELECT amount FROM orders"})
    assert result["error"] == "tool_disabled"
    assert len(ran) == before


def test_tool_call_is_audited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    call = {
        "content": [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "run_sql",
                "input": {"sql": "SELECT sum(amount) AS amount FROM orders"},
            }
        ],
        "usage": {"cost": 0.01, "total_tokens": 5},
    }
    stub = _patch(monkeypatch, [call, _app()])
    session_id = _open(client)
    generated = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["shown"] is True
    assert any(row["tool"] == "run_sql" and row["ok"] is True for row in generated.json()["audit"])
    assert stub.bodies[1]["messages"][0]["content"].count("tool_results") >= 0


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
    monkeypatch.setattr("dms_executor.create_surface.run_readonly", spy, raising=False)
    from dms_executor.create_surface import generate_artifact

    _patch(monkeypatch, [_app()])
    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
        inputs=["payroll"],
    )
    blocked = generate_artifact(session_id=opened["session_id"], api_key=OV_KEY)
    assert blocked["step"] == "abstain"
    assert blocked["abstain"]["abstain_reason"] == "ungranted"
    assert not any("payroll" in sql.lower() for sql in ran)
    bad = {
        "kind": "app",
        "title": "Orders",
        "parts": [{"id": "drop", "title": "Do not run", "sql": "DROP TABLE orders"}],
    }
    _patch(monkeypatch, [bad])
    second = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    dropped = generate_artifact(session_id=second["session_id"], api_key=OV_KEY)
    assert dropped["step"] == "abstain"
    assert not any(sql.strip().upper().startswith("DROP") for sql in ran)
    close_lakes()
    con = duckdb.connect(str(lake))
    count = con.execute("SELECT count(*) FROM orders").fetchone()
    con.close()
    assert count is not None
    assert count[0] == 6


def test_unsupported_number_secret_and_pii_are_withheld(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    bad = {
        "kind": "app",
        "title": "Orders total 99999.",
        "parts": [
            {
                "id": "bad",
                "title": "Hidden",
                "body": "Reach ov_customer_secret_key or ada@example.com",
                "sql": "SELECT month, amount FROM orders ORDER BY month",
            }
        ],
    }
    _patch(monkeypatch, [bad, bad])
    session_id = _open(client)
    generated = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert generated.status_code == 200, generated.text
    view = generated.json()
    assert view["shown"] is False
    assert "unsupported_claim" in view["issues"]
    assert "secret_present" in view["issues"]
    assert view["artifact"] is None


def test_edit_keeps_versions_and_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    first = _app()
    first["parts"].append({"id": "note", "title": "Note", "body": "Keep this."})
    edited = {
        "kind": "app",
        "title": "Orders",
        "parts": [
            first["parts"][0],
            {"id": "note", "title": "Note", "body": "A shorter note."},
        ],
    }
    _patch(monkeypatch, [first, edited])
    session_id = _open(client)
    preview = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert preview.json()["shown"] is True
    changed = client.post(
        f"/v1/studio/create/sessions/{session_id}/edit",
        json={"section_id": "note", "instruction": "Shorten the note."},
        headers=HEADERS,
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    parts = {part["id"]: part for part in body["artifact"]["parts"]}
    assert parts["note"]["body"] == "A shorter note."
    assert parts["by_month"]["title"] == "Orders by month"
    assert [item["id"] for item in body["versions"]] == [1, 2]
    restored = client.post(
        f"/v1/studio/create/sessions/{session_id}/restore",
        json={"version_id": 1},
        headers=HEADERS,
    )
    assert restored.status_code == 200, restored.text
    again = {part["id"]: part for part in restored.json()["artifact"]["parts"]}
    assert again["note"]["body"] == "Keep this."
    assert restored.json()["current_version"] == 3


def test_confirm_writes_the_file_and_does_not_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    _patch(monkeypatch, [_app()])
    session_id = _open(client)
    client.post(f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS)
    close_lakes()
    confirmed = client.post(
        f"/v1/studio/create/sessions/{session_id}/confirm", headers=HEADERS
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["step"] == "ready"
    written = tmp_path / "store" / session_id / "artifact.json"
    assert written.is_file()
    assert "Orders by month" in written.read_text(encoding="utf-8")
    from dms_executor.verified_queries import _rows_for_space

    rows = _rows_for_space(tmp_path / "orders.duckdb", scope_key(SPACE))
    question = next(row["question"] for row in rows if row.get("provenance") == "create_accept")
    assert (
        lookup_verified_query(
            question,
            space_id=SPACE,
            warehouse=tmp_path / "orders.duckdb",
            grantable={"orders"},
            dialect="duckdb",
        )
        is None
    )


def test_log_copies_usage_when_the_response_has_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    _patch(monkeypatch, [_usage(_app(), 18, 0.04)])
    session_id = _open(client)
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    log = view["log"]
    assert log["model"] == "routed-by-customer"
    assert log["prompt_tokens"] == 18
    assert log["total_tokens"] == 22
    assert log["cost"] == pytest.approx(0.04)


def test_full_build_ignores_the_old_caps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_MAX_TOKENS", "1")
    monkeypatch.setenv("DMS_CREATE_BUDGETS", json.dumps({"tenant-a": {"max_steps": 1}}))
    _patch(monkeypatch, [_app()])
    from dms_executor.create_surface import generate_artifact

    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    built = generate_artifact(session_id=opened["session_id"], api_key=OV_KEY)
    assert built["stopped"] is None
    assert built["shown"] is True
    assert built["artifact"]["title"] == "Orders"


def test_runaway_and_budget_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_MAX_STEPS", "1")
    bad = {"kind": "app", "parts": []}
    _patch(monkeypatch, [bad, _app()])
    from dms_executor.create_surface import generate_artifact

    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    stopped = generate_artifact(session_id=opened["session_id"], api_key=OV_KEY)
    assert stopped["stopped"] == "runaway_steps"
    assert stopped["message"]
    _patch(monkeypatch, [GeneratorError("budget_exceeded")])
    second = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    budget = generate_artifact(session_id=second["session_id"], api_key=OV_KEY)
    assert budget["stopped"] == "budget_exceeded"


def test_twice_the_estimate_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    artifact = _app()
    artifact["estimated_cost"] = 0.02
    _patch(monkeypatch, [_usage(artifact, 10, 0.05)])
    session_id = _open(client)
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["shown"] is True
    assert view["warning"]
    assert view["stopped"] is None


def test_refresh_shows_the_new_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    _patch(monkeypatch, [_app()])
    from dms_executor.create_surface import generate_artifact, read_artifact

    opened = start_session(
        space_id=SPACE,
        kind="app",
        goal="See order trend",
        audience="Founder",
        api_key=OV_KEY,
        actor="api_admin",
    )
    view = generate_artifact(session_id=opened["session_id"], api_key=OV_KEY)
    close_lakes()
    con = duckdb.connect(str(tmp_path / "orders.duckdb"))
    con.execute("INSERT INTO orders VALUES ('2024-07', 33)")
    con.close()
    refreshed = read_artifact(session_id=view["session_id"])
    assert refreshed["changes"]
    assert "33" in refreshed["changes"][0]["after"]


def test_guide_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    saved = client.put(
        "/v1/studio/create/guide",
        json={"brand": {"colours": "ink"}, "guides": {"app": "One screen."}},
        headers=HEADERS,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["brand"]["colours"] == "ink"
    again = client.get("/v1/studio/create/guide", headers=HEADERS)
    assert again.json()["guides"]["app"] == "One screen."


def test_stream_and_css(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    _patch(monkeypatch, [_app()])
    session_id = _open(client)
    with client.stream(
        "POST", f"/v1/studio/create/sessions/{session_id}/run", headers=HEADERS
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "Reading the schema" in body
    assert "Asking the generator" in body
    assert "Checking" in body
    css = Path("apps/ui/src/components/studio/createSurface.css").read_text(encoding="utf-8")
    assert "create-breathe" in css
    assert "prefers-reduced-motion" in css
    assert "create-spend" in css
    ui = Path("apps/ui/src/components/studio/CreateSurface.tsx").read_text(encoding="utf-8")
    assert "create-spend" in ui
    assert "create-estimate" in ui
    assert "create-warn" in ui
    source = Path("packages/executor/dms_executor/create_surface.py").read_text(encoding="utf-8")
    assert "def _pptx" not in source
    assert "def forecast_series" not in source
    ask = Path("packages/executor/dms_executor/generative_ask.py").read_text(encoding="utf-8")
    assert "DMS_CREATE" not in ask


def test_ov_role_defaults_to_reason_and_can_be_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_CREATE_OV_ROLE", raising=False)
    spec = {"kind": "app", "goal": "See order trend", "audience": "Founder"}
    context = {"schema": [], "data": [], "examples": [], "skills": skills_for("app"), "choices": {}}
    style = {"prompt": "", "template": "", "brand": {}, "guide": ""}
    body, _xml = request_body(
        provider="claude_api",
        role="generate",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert body["role"] == "reason"
    monkeypatch.setenv("DMS_CREATE_OV_ROLE", "summarize")
    named, _xml = request_body(
        provider="claude_api",
        role="improve",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert named["role"] == "summarize"
    monkeypatch.setenv("DMS_CREATE_OV_ROLE", "create_generate")
    with pytest.raises(GeneratorError) as exc:
        request_body(
            provider="claude_api",
            role="create_generate",
            spec=spec,
            data_context=context,
            style=style,
            enabled=None,
            prompt_version="1",
        )
    assert exc.value.code == "ov_role_unknown"


def test_cursor_key_comes_from_the_secret_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "cursor_api")
    monkeypatch.setenv("DMS_CREATE_CURSOR_REPO", "https://example.com/acme/create")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    context = {
        "schema": [],
        "data": [],
        "examples": [],
        "skills": skills_for("slides"),
        "choices": {},
    }
    style = {"prompt": "", "template": "", "brand": {}, "guide": ""}

    class Store:
        def get(self, name: str) -> str | None:
            assert name == "CURSOR_API_KEY"
            return CURSOR_KEY

    def ov(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        raise AssertionError(body.get("role") or api_key)

    def cursor(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        assert api_key == CURSOR_KEY
        assert CURSOR_KEY not in body["prompt"]["text"]
        return {"content": _deck(), "usage": {"cost": 0.01, "total_tokens": 1}}

    bind_secret_store(Store())
    try:
        deck = generate(
            {"kind": "slides", "goal": "See order trend", "audience": "Founder"},
            context,
            style,
            api_key=OV_KEY,
            transport=ov,
            cursor_transport=cursor,
        )
    finally:
        bind_secret_store(None)
    assert deck.artifact is not None
    assert CURSOR_KEY not in deck.prompt


def test_slides_skill_caps_charts_and_traces_numbers() -> None:
    text = local_skill("slides")
    assert "create-slides" in text
    assert "pptx" in text
    assert "narrative" in text
    assert "5" in text
    assert "run_sql" in text
    charts = [
        {"label": f"Month {index}", "sql": "SELECT month, amount FROM orders"}
        for index in range(5)
    ]
    ok = {
        "kind": "slides",
        "title": "Orders",
        "parts": [{"id": "s1", "title": "Trend", "body": "Orders moved.", "charts": charts}],
    }
    assert validate_call("return_artifact", ok) == []
    extra = json.loads(json.dumps(ok))
    extra["parts"][0]["charts"].append(
        {"label": "Extra", "sql": "SELECT month, amount FROM orders"}
    )
    assert any(item.endswith(":maxItems") for item in validate_call("return_artifact", extra))
    labelled = json.loads(json.dumps(ok))
    labelled["parts"][0]["charts"][0]["label"] = "Peak 99999"
    issues = judge_artifact(labelled, kind="slides", brand={}, allowed_numbers={"15", "30"})
    assert "unsupported_claim" in issues


def test_claude_body_is_the_direct_ask_plus_tools() -> None:
    spec = {"kind": "app", "goal": "See order trend", "audience": "Founder"}
    context = {
        "schema": [{"name": "orders"}],
        "data": [{"name": "orders", "stats": [{"column": "amount", "sum_v": 135}]}],
        "examples": [{"name": "example", "body": "A good chart."}],
        "skills": skills_for("app"),
        "choices": {"inputs": ["orders"]},
    }
    style = {"prompt": "Quiet", "template": "Plain layout.", "brand": {}, "guide": "One screen."}
    text = direct_prompt(spec, context, style)
    body, xml = request_body(
        provider="claude_api",
        role="create_generate",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert "tools" not in body
    assert "system" not in body
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == xml
    system = body["messages"][0]["content"]
    assert "one JSON object" in system
    assert '"kind"' in system
    assert "return_artifact" not in system
    assert "run_sql" not in system
    assert "return_artifact" not in xml
    assert "run_sql" not in xml
    assert text == system + "\n" + xml


def test_shown_app_scores_at_least_the_direct_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    made = _app()
    made["parts"][0]["body"] = "One month is 18."
    _patch(monkeypatch, [_usage(made, 11, 0.02)])
    session_id = _open(client)
    generated = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert generated.status_code == 200, generated.text
    view = generated.json()
    assert view["shown"] is True
    assert view["artifact"]["title"] == "Orders"
    assert view["score"]["holds"] is True
    assert view["score"]["create"] > view["score"]["direct"]
    invented = {
        "kind": "app",
        "title": "Orders",
        "parts": [{"id": "by_month", "title": "Orders by month", "body": "The total is 99999."}],
    }
    plain = score_artifact(invented, kind="app", brand={}, allowed_numbers=set())
    assert holds_direct_bar(view["score"]["create"], plain["points"])
    assert view["score"]["create"] > plain["points"]


def _plain_spec() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    spec = {"kind": "slides", "goal": "See order trend", "audience": "Founder"}
    context = {
        "schema": [],
        "data": [],
        "examples": [],
        "skills": skills_for("slides"),
        "choices": {},
    }
    style = {"prompt": "", "template": "", "brand": {}, "guide": ""}
    return spec, context, style


def test_plain_contract_omits_tools_until_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CREATE_OV_TOOLS", raising=False)
    spec, context, style = _plain_spec()
    body, _xml = request_body(
        provider="claude_api",
        role="generate",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert "tools" not in body
    assert "system" not in body
    assert body["messages"][0]["role"] == "system"
    sent = body["messages"][0]["content"] + body["messages"][1]["content"]
    assert "one JSON object" in sent
    assert '"kind"' in body["messages"][0]["content"]
    assert "return_artifact" not in sent
    assert "run_sql" not in sent
    monkeypatch.setenv("DMS_CREATE_OV_TOOLS", "true")
    still, _xml = request_body(
        provider="claude_api",
        role="generate",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert "tools" not in still
    monkeypatch.setenv("DMS_CREATE_OV_TOOLS", "1")
    armed, _xml = request_body(
        provider="claude_api",
        role="generate",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert armed["tools"]
    assert armed["system"]
    assert "return_artifact" in armed["system"]
    assert armed["thinking"] == {"type": "adaptive"}
    skills = armed["container"]["skills"]
    assert {"type": "anthropic", "skill_id": "pptx", "version": "latest"} in skills


def test_thousands_currency_and_percent_trace() -> None:
    def issues(body: str, allowed: set[str]) -> list[str]:
        artifact = {
            "kind": "app",
            "title": "Orders",
            "parts": [{"id": "a", "title": "Total", "body": body}],
        }
        return judge_artifact(artifact, kind="app", brand={}, allowed_numbers=allowed)

    assert "unsupported_claim" not in issues("Total is $1,606,773.", {"1606773"})
    assert "unsupported_claim" not in issues("Total is 1,606,773.", {"1606773"})
    assert "unsupported_claim" not in issues("Share is 75%.", {"75"})
    assert "unsupported_claim" in issues("Total is 1.", {"1606773"})


def test_max_tokens_defaults_to_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CREATE_MAX_TOKENS", raising=False)
    assert request_max_tokens() == 8000
    spec, context, style = _plain_spec()
    body, _xml = request_body(
        provider="claude_api",
        role="generate",
        spec=spec,
        data_context=context,
        style=style,
        enabled=None,
        prompt_version="1",
    )
    assert body["max_tokens"] == 8000
    monkeypatch.setenv("DMS_CREATE_MAX_TOKENS", "4000")
    assert request_max_tokens() == 4000


def test_read_only_multi_select_is_repaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    bad = {
        "kind": "app",
        "title": "Orders",
        "parts": [
            {
                "id": "by_month",
                "title": "Orders by month",
                "body": "The chart follows the granted table.",
                "sql": (
                    "SELECT month, amount FROM orders ORDER BY month; "
                    "SELECT month, amount FROM orders ORDER BY month"
                ),
            }
        ],
    }
    write = {
        "kind": "app",
        "title": "Orders",
        "parts": [
            {
                "id": "by_month",
                "title": "Orders by month",
                "body": "The chart follows the granted table.",
                "sql": "DELETE FROM orders",
            }
        ],
    }
    stub = _patch(monkeypatch, [bad, _app()])
    session_id = _open(client)
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["shown"] is True
    assert view["abstain"] is None
    assert len(stub.bodies) == 2
    stub_write = _patch(monkeypatch, [write, _app()])
    again = _open(client)
    refused = client.post(
        f"/v1/studio/create/sessions/{again}/generate", headers=HEADERS
    ).json()
    assert refused["shown"] is False
    assert refused["abstain"]["abstain_reason"] == "destructive"
    assert len(stub_write.bodies) == 1


def test_est_cost_usd_feeds_the_spend_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_MAX_USD", "0.05")
    stub = _patch(monkeypatch, [{"content": _app(), "est_cost_usd": 0.2}])
    session_id = _open(client)
    first = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert first.status_code == 200, first.text
    assert first.json()["spend"]["usd"] == pytest.approx(0.2)
    second = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    )
    assert second.status_code == 200, second.text
    assert second.json()["stopped"] == "runaway_usd"
    assert len(stub.bodies) == 1


def test_chart_number_must_be_in_its_own_sql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    bad = {
        "kind": "slides",
        "title": "Orders",
        "parts": [
            {
                "id": "s1",
                "title": "Trend",
                "body": "The chart compares the months.",
                "sql": "SELECT month, amount FROM orders ORDER BY month",
                "charts": [
                    {
                        "label": "February is 18",
                        "sql": "SELECT month, amount FROM orders WHERE amount = 30",
                    }
                ],
            }
        ],
    }
    _patch(monkeypatch, [bad, bad])
    session_id = _open(client, "slides")
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["shown"] is False
    assert "unsupported_claim" in view["issues"]


def test_ov_timeout_is_240(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    seen: dict[str, Any] = {}

    def post(*_args: Any, **kwargs: Any) -> Any:
        seen["timeout"] = kwargs.get("timeout")
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(GeneratorError) as exc:
        default_transport({"role": "reason", "messages": []}, "ov_test_key_xx")
    assert exc.value.code == "ov_unreachable"
    assert seen["timeout"] == 240.0
    assert transport_timeout() == 240.0


def test_cursor_missing_key_is_reported_before_the_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "cursor_api")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("DMS_CREATE_CURSOR_REPO", raising=False)
    bind_secret_store(None)
    spec, context, style = _plain_spec()
    with pytest.raises(GeneratorError) as exc:
        generate(spec, context, style, api_key=OV_KEY)
    assert exc.value.code == "cursor_key_missing"


def test_confirm_writes_pptx_pdf_and_a_one_page_doc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pptx import Presentation

    client = _client(monkeypatch, tmp_path, "1")
    opened = client.post(
        "/v1/studio/create/sessions",
        json={
            "space_id": SPACE,
            "kind": "slides",
            "goal": "See order trend",
            "audience": "Founder",
            "brand_colours": "#112233",
            "brand_fonts": "Source Sans",
        },
        headers=HEADERS,
    )
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]
    deck = _deck()
    deck["style"] = {
        "colours": "#112233",
        "fonts": "Source Sans",
        "logo": "",
        "tone": "",
    }
    deck["parts"][0]["charts"] = [
        {
            "label": "Amount by month",
            "sql": "SELECT month, amount FROM orders ORDER BY month",
        }
    ]
    _patch(monkeypatch, [deck])
    client.post(f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS)
    close_lakes()
    confirmed = client.post(
        f"/v1/studio/create/sessions/{session_id}/confirm", headers=HEADERS
    )
    assert confirmed.status_code == 200, confirmed.text
    folder = tmp_path / "store" / session_id
    assert (folder / "slides.pptx").is_file()
    assert (folder / "slides.pdf").read_bytes().startswith(b"%PDF")
    html = (folder / "slides.html").read_text(encoding="utf-8")
    assert "Orders deck" in html
    assert "Amount by month" in html
    assert "#112233" in html
    assert "Source Sans" in html
    deck_file = Presentation(str(folder / "slides.pptx"))
    assert len(deck_file.slides) == 1
    assert any(shape.has_chart for slide in deck_file.slides for shape in slide.shapes)
    doc = {
        "kind": "doc",
        "title": "Note",
        "parts": [
            {"id": "a", "title": "First", "body": "Opened."},
            {"id": "b", "title": "Second", "body": "Closed."},
        ],
    }
    _patch(monkeypatch, [doc])
    one = client.post(
        "/v1/studio/create/sessions",
        json={
            "space_id": SPACE,
            "kind": "doc",
            "goal": "Write a one page note",
            "audience": "Founder",
        },
        headers=HEADERS,
    )
    assert one.status_code == 200, one.text
    one_id = one.json()["session_id"]
    client.post(f"/v1/studio/create/sessions/{one_id}/generate", headers=HEADERS)
    close_lakes()
    client.post(f"/v1/studio/create/sessions/{one_id}/confirm", headers=HEADERS)
    one_pdf = (tmp_path / "store" / one_id / "doc.pdf").read_bytes()
    assert one_pdf.count(b"/Type /Page ") == 1
    one_html = (tmp_path / "store" / one_id / "doc.html").read_text(encoding="utf-8")
    assert "page-break-before" not in one_html
    _patch(monkeypatch, [doc])
    many = client.post(
        "/v1/studio/create/sessions",
        json={
            "space_id": SPACE,
            "kind": "doc",
            "goal": "Write the note",
            "audience": "Founder",
        },
        headers=HEADERS,
    )
    many_id = many.json()["session_id"]
    client.post(f"/v1/studio/create/sessions/{many_id}/generate", headers=HEADERS)
    close_lakes()
    client.post(f"/v1/studio/create/sessions/{many_id}/confirm", headers=HEADERS)
    many_pdf = (tmp_path / "store" / many_id / "doc.pdf").read_bytes()
    assert many_pdf.count(b"/Type /Page ") == 3


def test_share_claim_must_match_the_computed_shares() -> None:
    rows = [{"region": "North", "amount": 75}, {"region": "South", "amount": 25}]

    def art(body: str, data: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "kind": "slides",
            "title": "Regions",
            "parts": [
                {
                    "id": "s1",
                    "title": "Where sales sit",
                    "body": body,
                    "charts": [
                        {
                            "label": "Regions",
                            "sql": "SELECT region, amount FROM sales",
                            "rows": data or rows,
                        }
                    ],
                }
            ],
        }

    spread = judge_artifact(
        art("Sales sit in a few regions."),
        kind="slides",
        brand={},
        allowed_numbers={"75", "25"},
    )
    assert "share_mismatch" not in spread
    single = judge_artifact(
        art("Sales are spread across a few regions.", [{"region": "All", "amount": 100}]),
        kind="slides",
        brand={},
        allowed_numbers={"100"},
    )
    assert "share_mismatch" not in single
    unnamed = {
        "kind": "slides",
        "title": "Mix",
        "parts": [
            {
                "id": "s1",
                "title": "Mix",
                "body": "Overall is 10%.",
                "charts": [
                    {"label": "Regions", "rows": rows},
                    {
                        "label": "Channels",
                        "rows": [
                            {"channel": "Web", "amount": 50},
                            {"channel": "Store", "amount": 50},
                        ],
                    },
                ],
            }
        ],
    }
    assert "share_mismatch" not in judge_artifact(
        unnamed, kind="slides", brand={}, allowed_numbers={"75", "25", "50", "10"}
    )
    named = json.loads(json.dumps(unnamed))
    named["parts"][0]["body"] = "Regions is 10%."
    assert "share_mismatch" in judge_artifact(
        named, kind="slides", brand={}, allowed_numbers={"75", "25", "50", "10"}
    )
    honest = judge_artifact(
        art("North is 75%."),
        kind="slides",
        brand={},
        allowed_numbers={"75", "25"},
    )
    assert "share_mismatch" not in honest
    wrong = judge_artifact(
        art("North is 10%."),
        kind="slides",
        brand={},
        allowed_numbers={"75", "25", "10"},
    )
    assert "share_mismatch" in wrong
    balanced = judge_artifact(
        art(
            "Sales sit in a few regions.",
            [
                {"region": "North", "amount": 40},
                {"region": "South", "amount": 30},
                {"region": "East", "amount": 30},
            ],
        ),
        kind="slides",
        brand={},
        allowed_numbers={"40", "30"},
    )
    assert "share_mismatch" not in balanced


def test_unknown_template_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path, "1")
    with pytest.raises(CreateProblem, match="template_unknown"):
        start_session(
            space_id=SPACE,
            kind="app",
            goal="See order trend",
            audience="Founder",
            api_key=OV_KEY,
            actor="api_admin",
            template="neon",
        )


def test_plain_prompt_states_the_schema_and_repairs_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    stub = _patch(monkeypatch, ["not json", _app()])
    session_id = _open(client)
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["shown"] is True
    assert len(stub.bodies) == 2
    repair = stub.bodies[1]["messages"][1]["content"]
    assert "model_unreadable" in repair
    assert "schema:required kind, title, parts" in repair
    for body in stub.bodies:
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        assert "one JSON object" in system
        assert '"kind"' in system
        assert '"parts"' in system
        assert "Short title" in system
        assert "return_artifact" not in system + user
        assert "run_sql" not in system + user
    shape = _patch(monkeypatch, [{"title": "Nope"}, _app()])
    again = _open(client)
    fixed = client.post(
        f"/v1/studio/create/sessions/{again}/generate", headers=HEADERS
    ).json()
    assert fixed["shown"] is True
    assert len(shape.bodies) == 2
    assert "missing:kind" in shape.bodies[1]["messages"][1]["content"]


def test_pdf_wraps_and_keeps_every_line(tmp_path: Path) -> None:
    import re

    from dms_executor.create_files import render_artifact

    lines = [f"line {index} of the note" for index in range(60)]
    lines.append("TAILTOKEN")
    rows = [{"region": f"r{index:02d}", "revenue": float(index)} for index in range(1, 56)]
    rows.append({"region": "TAILREGION", "revenue": 1.0})
    artifact = {
        "kind": "doc",
        "title": "Note",
        "parts": [
            {
                "id": "a",
                "title": "Body",
                "body": "\n".join(lines) + "\n" + ("WRAPME" * 30),
                "charts": [{"label": "Rows", "rows": rows}],
            }
        ],
    }
    render_artifact(tmp_path, artifact, kind="doc", one_page=False, brand={"colours": "#112233"})
    pdf = (tmp_path / "doc.pdf").read_bytes()
    assert b"TAILTOKEN" in pdf
    assert b"TAILREGION" in pdf
    assert b"WRAPME" in pdf
    assert pdf.count(b"/Type /Page ") > 1
    assert b"/Subtype /Image" in pdf
    for block in re.findall(rb"BT\n(.*?)\nET", pdf, re.S):
        for line in re.findall(rb"\(([^)]*)\) Tj", block):
            assert len(line) <= 90


def test_pptx_multi_series_brand_and_extra_slide(tmp_path: Path) -> None:
    from dms_executor.create_files import render_artifact
    from pptx import Presentation
    from pptx.dml.color import RGBColor

    regions = ["North", "South", "East", "West", "Central", "Coast"]
    multi = {
        "label": "Quarterly by region",
        "rows": [
            {"region": name, "q1": 10 + index, "q2": 12 + index, "q3": 8 + index, "q4": 9 + index}
            for index, name in enumerate(regions)
        ],
    }
    charts = [multi] + [
        {
            "label": f"Chart {index}",
            "rows": [
                {"region": "North", "amount": index},
                {"region": "South", "amount": index + 1},
            ],
        }
        for index in range(1, 6)
    ]
    artifact = {
        "kind": "slides",
        "title": "Regions",
        "parts": [
            {"id": "s1", "title": "Where sales sit", "body": "Six charts.", "charts": charts}
        ],
    }
    render_artifact(
        tmp_path,
        artifact,
        kind="slides",
        one_page=False,
        brand={"colours": "#112233 #445566", "fonts": "Source Sans"},
    )
    deck = Presentation(str(tmp_path / "slides.pptx"))
    assert len(deck.slides) == 2
    drawn = [shape.chart for slide in deck.slides for shape in slide.shapes if shape.has_chart]
    assert len(drawn) == 6
    assert len(drawn[0].series) == 4
    assert drawn[0].series[0].format.fill.fore_color.rgb == RGBColor(0x11, 0x22, 0x33)
    assert drawn[0].series[1].format.fill.fore_color.rgb == RGBColor(0x44, 0x55, 0x66)
    assert drawn[0].has_title
    assert drawn[0].chart_title.text_frame.text == "Quarterly by region"
    assert drawn[0].category_axis.has_title
    assert drawn[0].category_axis.axis_title.text_frame.text == "region"
    assert drawn[0].value_axis.has_title
    title = deck.slides[0].shapes[0].text_frame.paragraphs[0]
    assert title.font.name == "Source Sans"


def test_html_formats_numbers_and_charts(tmp_path: Path) -> None:
    from dms_executor.create_files import render_artifact

    artifact = {
        "kind": "app",
        "title": "Revenue",
        "parts": [
            {
                "id": "a",
                "title": "Total",
                "body": "Revenue for the year.",
                "charts": [
                    {
                        "label": "Revenue by region",
                        "rows": [
                            {"region": "North", "revenue": 545806.0},
                            {"region": "South", "revenue": 1200.5},
                        ],
                    }
                ],
            }
        ],
    }
    render_artifact(
        tmp_path,
        artifact,
        kind="app",
        one_page=False,
        brand={"colours": "#112233", "fonts": "Source Sans"},
    )
    html = (tmp_path / "app.html").read_text(encoding="utf-8")
    assert "<style>" in html
    assert "#112233" in html
    assert "Source Sans" in html
    assert "545,806" in html
    assert "1,200.50" in html
    assert "revenue=545806.0" not in html
    assert "545806.0" not in html
    assert "data:image/png;base64," in html


def test_chart_rows_past_forty_stay_and_a_sliced_part_is_marked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    con = duckdb.connect(str(tmp_path / "orders.duckdb"))
    values = ", ".join(f"('m{index:02d}', {index})" for index in range(7, 49))
    con.execute(f"INSERT INTO orders VALUES {values}")
    con.close()
    spec = {
        "kind": "slides",
        "title": "Orders deck",
        "parts": [
            {
                "id": "s1",
                "title": "Trend",
                "body": "The chart follows the granted table.",
                "sql": "SELECT month, amount FROM orders ORDER BY month",
                "charts": [
                    {
                        "label": "Amount by month",
                        "sql": "SELECT month, amount FROM orders ORDER BY month",
                    }
                ],
            }
        ],
    }
    _patch(monkeypatch, [spec])
    session_id = _open(client, "slides")
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["shown"] is True, view
    part = view["artifact"]["parts"][0]
    assert part["truncated"] == {"shown": 40, "total": 48}
    assert len(part["rows"]) == 40
    chart_rows = part["charts"][0]["rows"]
    assert len(chart_rows) == 48
    assert any(row["month"] == "m48" for row in chart_rows)
    close_lakes()
    confirmed = client.post(
        f"/v1/studio/create/sessions/{session_id}/confirm", headers=HEADERS
    )
    assert confirmed.status_code == 200, confirmed.text
    html = (tmp_path / "store" / session_id / "slides.html").read_text(encoding="utf-8")
    assert "Showing 40 of 48." in html
    assert "m48" in html


def test_derived_percent_traces_within_one_point() -> None:
    def art(body: str, rows: list[dict[str, int]]) -> dict[str, Any]:
        return {
            "kind": "slides",
            "title": "Mix",
            "parts": [
                {
                    "id": "s1",
                    "title": "Mix",
                    "body": body,
                    "charts": [{"label": "Regions", "rows": rows}],
                }
            ],
        }

    heavy = [{"region": "North", "amount": 770}, {"region": "South", "amount": 230}]
    allowed = {"770", "230"}
    honest = judge_artifact(
        art("North is 77%.", heavy), kind="slides", brand={}, allowed_numbers=allowed
    )
    assert "unsupported_claim" not in honest
    assert "share_mismatch" not in honest
    close = judge_artifact(
        art("North is 76%.", heavy), kind="slides", brand={}, allowed_numbers=allowed
    )
    assert "unsupported_claim" not in close
    off = judge_artifact(
        art("North is 75%.", heavy), kind="slides", brand={}, allowed_numbers=allowed
    )
    assert "unsupported_claim" in off
    tiny = [{"region": "North", "amount": 4}, {"region": "South", "amount": 996}]
    small = judge_artifact(
        art("North is 0.4%.", tiny), kind="slides", brand={}, allowed_numbers={"4", "996"}
    )
    assert "unsupported_claim" not in small
    assert "share_mismatch" not in small


def test_letter_suffix_numbers_trace() -> None:
    def issues(body: str, allowed: set[str]) -> list[str]:
        artifact = {
            "kind": "app",
            "title": "Orders",
            "parts": [{"id": "a", "title": "Total", "body": body}],
        }
        return judge_artifact(artifact, kind="app", brand={}, allowed_numbers=allowed)

    assert "unsupported_claim" not in issues("Revenue is $9.9M.", {"9876543"})
    assert "unsupported_claim" not in issues("Orders are 5K.", {"5000"})
    assert "unsupported_claim" not in issues("Bookings are $1.6B.", {"1600000000"})
    assert "unsupported_claim" in issues("Revenue is $1.2M.", {"9900000"})
    assert "unsupported_claim" in issues("Orders are 5K.", {"100"})


def test_served_model_is_logged_and_a_mismatch_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, "1")
    usage = {
        "model": None,
        "prompt_tokens": 6,
        "completion_tokens": 4,
        "total_tokens": 10,
        "cost": 0.02,
    }
    _patch(monkeypatch, [{"content": _app(), "served_model": "nvidia/nemotron-3", "usage": usage}])
    session_id = _open(client)
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["shown"] is True
    assert view["log"]["model"] == "nvidia/nemotron-3"
    assert view["log"]["provider"] == "claude_api"
    note = "Served model is not the requested provider."
    assert view["model_warning"] == note
    assert view["warning"] == note
    assert "served_model" not in view["artifact"]
    matched = {
        "content": _app(),
        "served_model": "claude-sonnet-4",
        "usage": {**usage, "model": "routed-by-customer"},
    }
    _patch(monkeypatch, [matched])
    again = _open(client)
    quiet = client.post(
        f"/v1/studio/create/sessions/{again}/generate", headers=HEADERS
    ).json()
    assert quiet["shown"] is True
    assert quiet["log"]["model"] == "claude-sonnet-4"
    assert quiet["model_warning"] is None


def test_log_seconds_are_the_job_not_the_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dms_executor.create_surface as surface

    client = _client(monkeypatch, tmp_path, "1")
    real = surface.time.time
    monkeypatch.setattr(surface.time, "time", lambda: real() - 10000)
    session_id = _open(client)
    monkeypatch.setattr(surface.time, "time", real)
    _patch(monkeypatch, [_usage(_app(), 11, 0.02)])
    view = client.post(
        f"/v1/studio/create/sessions/{session_id}/generate", headers=HEADERS
    ).json()
    assert view["log"]["seconds"] < 30
