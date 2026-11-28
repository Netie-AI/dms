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
from dms_core.create_generator import GeneratorError, generate, skills_for
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


def _app() -> dict[str, Any]:
    return {
        "kind": "app",
        "title": "Orders",
        "estimated_cost": 0.02,
        "parts": [
            {
                "id": "by_month",
                "title": "Orders by month",
                "body": "The chart follows the granted table.",
                "sql": "SELECT month, amount FROM orders ORDER BY month",
            }
        ],
    }


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
    assert body["role"] == "create_generate"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["tool_choice"] == {"type": "auto"}
    names = [item["name"] for item in body["tools"]]
    assert "return_artifact" in names
    assert "run_sql" in names
    text = body["messages"][0]["content"]
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
    skills = body["container"]["skills"]
    assert {"type": "anthropic", "skill_id": "pptx", "version": "latest"} in skills
    assert any(item.get("name") == "code_execution" for item in body["tools"])
    assert "create-slides" in body["messages"][0]["content"]
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
    app_stub = _patch(monkeypatch, [_usage(_app(), 11, 0.02)])
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
    deck_context = dict(context)
    deck_context["skills"] = skills_for("slides")

    def ov(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        assert body["role"] == "create_cursor_key"
        assert api_key == OV_KEY
        return {"content": {"cursor_key": CURSOR_KEY}, "usage": {}}

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
