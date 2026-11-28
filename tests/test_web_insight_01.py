"""DMS_WEB_INSIGHT. Digit 1 only. Fixture replay, no browser, no user rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "web_insight"
QUESTION = "what is a typical supplier lead time"


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WEB_INSIGHT", "1")


def test_flag_accepts_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.skills.web_insight import enabled

    monkeypatch.delenv("DMS_WEB_INSIGHT", raising=False)
    assert enabled() is False
    for value in ("0", "true", "yes", "on", "01", ""):
        monkeypatch.setenv("DMS_WEB_INSIGHT", value)
        assert enabled() is False
    monkeypatch.setenv("DMS_WEB_INSIGHT", "1")
    assert enabled() is True


def test_flag_off_tool_is_absent_and_fetch_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.create_generator import client_tools
    from dms_core.skills.errors import SkillError
    from dms_core.skills.web_insight import TOOL_NAME, run

    monkeypatch.delenv("DMS_WEB_INSIGHT", raising=False)
    names = [item["name"] for item in client_tools(None)]
    assert TOOL_NAME not in names

    def _boom(_query: str) -> dict[str, str]:
        raise AssertionError("fetcher called with the flag off")

    with pytest.raises(SkillError) as caught:
        run(QUESTION, transport=_boom)
    assert caught.value.code == "web_insight_disabled"


def test_fixture_replay_cites_overview_query_and_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.skills.web_insight import fixture_transport, run

    _on(monkeypatch)
    fixture = json.loads((FIXTURES / "overview.json").read_text(encoding="utf-8"))
    seen: list[str] = []

    def transport(query: str) -> dict[str, str]:
        seen.append(query)
        return fixture_transport(fixture)(query)

    cited = run(
        QUESTION,
        transport=transport,
        fetched_at="2026-11-28T17:16:00+00:00",
        now=1_700_000_000.0,
        hits={},
    )
    assert seen == [QUESTION]
    assert cited["label"] == "Google AI Overview"
    assert cited["query"] == QUESTION
    assert cited["fetched_at"] == "2026-11-28T17:16:00+00:00"
    assert cited["cited"] is True
    assert cited["served"] is False
    assert cited["overview"] == fixture["overview"]
    assert "Infer using this citation" in cited["infer"]


def test_empty_fixture_is_an_honest_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.skills.web_insight import fixture_transport, run

    _on(monkeypatch)
    fixture = json.loads((FIXTURES / "empty.json").read_text(encoding="utf-8"))
    cited = run(
        QUESTION,
        transport=fixture_transport(fixture),
        fetched_at="2026-11-28T17:16:00+00:00",
        now=1_700_000_000.0,
        hits={},
    )
    assert cited["cited"] is False
    assert cited["overview"] == ""
    assert cited["fallback"] == "No Google AI Overview appeared for this query."
    assert fixture["overview"] is None


def test_search_payload_is_the_question_only() -> None:
    from dms_core.skills.web_insight import fetch_payload, search_query

    payload = fetch_payload(search_query(QUESTION))
    assert payload == {"query": QUESTION}
    assert set(payload) == {"query"}


def test_rows_in_the_argument_dict_never_reach_the_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_core.skills.web_insight import run

    _on(monkeypatch)
    seen: list[str] = []

    def transport(query: str) -> dict[str, str]:
        seen.append(query)
        return {"overview": "Public sentence."}

    rows = [{"supplier": "Ada", "spend": 10}]
    cited = run(
        QUESTION,
        transport=transport,
        fetched_at="2026-11-28T17:16:00+00:00",
        now=1_700_000_000.0,
        hits={},
    )
    assert seen == [QUESTION]
    assert "Ada" not in json.dumps(cited)
    assert rows[0]["supplier"] not in seen[0]


def test_rate_limit_skips_the_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.skills.web_insight import run

    _on(monkeypatch)
    calls = {"n": 0}
    hits: dict[str, list[float]] = {}

    def transport(query: str) -> dict[str, str]:
        calls["n"] += 1
        return {"overview": "Public sentence."}

    first = run(QUESTION, transport=transport, now=100.0, hits=hits, limit=1, tenant="t")
    second = run(QUESTION, transport=transport, now=100.0, hits=hits, limit=1, tenant="t")
    assert first["cited"] is True
    assert calls["n"] == 1
    assert second["cited"] is False
    assert second["overview"] == ""
    assert "rate limited" in second["fallback"]


def test_unconfigured_fetcher_does_not_invent_an_overview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_core.skills.web_insight import run

    _on(monkeypatch)
    monkeypatch.delenv("DMS_WEB_INSIGHT_FETCH_URL", raising=False)
    cited = run(QUESTION, fetched_at="2026-11-28T17:16:00+00:00", now=50.0, hits={})
    assert cited["cited"] is False
    assert cited["overview"] == ""
    assert "not configured" in cited["fallback"]
    assert "did not invent" in cited["fallback"]


def test_flag_on_registers_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.create_generator import client_tools, validate_call
    from dms_core.skills.context import provider_context
    from dms_core.skills.web_insight import TOOL_NAME

    monkeypatch.delenv("DMS_SKILLS", raising=False)
    _on(monkeypatch)
    names = [item["name"] for item in client_tools(None)]
    assert names[-1] == TOOL_NAME
    assert validate_call(TOOL_NAME, {"question": QUESTION}) == []
    assert validate_call(TOOL_NAME, {}) == ["missing:question"]
    ctx = provider_context(surface="ask", tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    assert TOOL_NAME in [item["name"] for item in ctx["tools"]]
    assert ctx["skills"] == []


def test_flag_off_route_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_WEB_INSIGHT", raising=False)
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/v1/studio/web-insight/status").status_code == 404
    assert client.post(
        "/v1/studio/web-insight/overview", json={"question": QUESTION, "rows": [{"n": 1}]}
    ).status_code == 404


def test_route_replays_a_fixture_and_rejects_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DMS_WEB_INSIGHT", "1")
    monkeypatch.setenv("DMS_SKILLS_DIR", str(tmp_path))
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    get_settings.cache_clear()
    fixture = json.loads((FIXTURES / "overview.json").read_text(encoding="utf-8"))
    seen: list[str] = []

    def transport(query: str) -> dict[str, str]:
        seen.append(query)
        return {"overview": fixture["overview"]}

    def allow(*, action: str, actor: str | None = None, **_kwargs: object) -> ComplianceDecision:
        del actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    app = create_app()
    app.state.web_insight_transport = transport
    monkeypatch.setattr("dms_api.routes.web_insight.compliance_gate", allow)
    client = TestClient(app)
    rejected = client.post(
        "/v1/studio/web-insight/overview",
        json={"question": QUESTION, "rows": [{"supplier": "Ada"}]},
    )
    assert rejected.status_code == 422
    ok = client.post("/v1/studio/web-insight/overview", json={"question": QUESTION})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["label"] == "Google AI Overview"
    assert body["query"] == QUESTION
    assert body["cited"] is True
    assert body["served"] is False
    assert seen == [QUESTION]
    assert "Ada" not in json.dumps(body)
