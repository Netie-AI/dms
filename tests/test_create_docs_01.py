"""Doc and slides call dms_core.create_generator.generate. No local file renderer."""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from create_docs.adapter import _ADAPTER_MODULES, governance
from create_docs.flag import enabled
from create_docs.harness import edit, new_run, produce
from dms_core.create_generator import GeneratorError

_FIXTURE = Path(__file__).parent / "fixtures" / "create_docs" / "answers.json"
_KEY = "ov_test_docs_key"
_GENERIC = re.compile(r"^(chart|series|graph)( \d+)?$", re.I)
_REVENUE = "SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region"
_ORDERS = "SELECT channel, COUNT(*) AS orders FROM orders GROUP BY channel"


def _answers() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _style() -> dict[str, Any]:
    return {
        "kind": "slides",
        "prompt_version": "1",
        "prompt_text": "Write the narrative in sentences.",
        "template": "plain",
        "brand": {"colours": "ink", "fonts": "plain", "logo": "", "tone": "calm"},
        "few_shots": [{"title": "Region note", "text": "Lead with the larger region."}],
    }


def _chart(label: str, sql: str, mark: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"label": label, "title": label, "sql": sql, "mark": mark, "rows": rows}


def _deck(bad: bool) -> dict[str, Any]:
    revenue = "North was 1200. South was 800. North led the granted rows."
    if bad:
        revenue = revenue + " Extra 99999."
    return {
        "kind": "slides",
        "title": "Answers from this session",
        "parts": [
            {
                "id": "s0",
                "title": "Revenue by region",
                "body": revenue,
                "sql": _REVENUE,
                "charts": [
                    _chart(
                        "Revenue by region, North against South",
                        _REVENUE,
                        "bar",
                        [{"region": "North", "revenue": 1200}, {"region": "South", "revenue": 800}],
                    ),
                    _chart(
                        "Revenue by region, North against South as a line",
                        _REVENUE,
                        "line",
                        [{"region": "North", "revenue": 1200}, {"region": "South", "revenue": 800}],
                    ),
                ],
            },
            {
                "id": "s1",
                "title": "Orders by channel",
                "body": "Web was 40. Store was 10. Web led the granted rows.",
                "sql": _ORDERS,
                "charts": [
                    _chart(
                        "Orders by channel, Web against Store",
                        _ORDERS,
                        "column",
                        [{"channel": "Web", "orders": 40}, {"channel": "Store", "orders": 10}],
                    ),
                ],
            },
        ],
        "style": {"colours": "ink", "fonts": "plain", "logo": "", "tone": "calm"},
    }


def _usage(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": artifact,
        "usage": {"prompt_tokens": 420, "completion_tokens": 180},
    }


def _transport(script: list[Any]) -> Any:
    seen: list[dict[str, Any]] = []

    def transport(body: dict[str, Any], api_key: str) -> Any:
        assert api_key == _KEY
        assert "model" not in body
        seen.append(body)
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def _part_charts(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for part in artifact.get("parts") or []:
        if isinstance(part, dict):
            found.extend(chart for chart in part.get("charts") or [] if isinstance(chart, dict))
    return found


def _assert_submittable(artifact: dict[str, Any]) -> None:
    charts = _part_charts(artifact)
    assert 2 <= len(charts) <= 5
    titles = [chart["label"] for chart in charts]
    assert len(titles) == len(set(titles))
    sqls = {_REVENUE, _ORDERS}
    for chart in charts:
        assert not _GENERIC.match(chart["label"])
        assert "against" in chart["label"]
        assert chart["sql"] in sqls
        assert chart["mark"] in {"bar", "line", "column"}
    bodies = []
    for part in artifact["parts"]:
        body = part["body"]
        assert ". " in body
        assert body != part["title"]
        bodies.append(body)
        assert part["sql"] in sqls
    text = " ".join(bodies)
    for number in ("1200", "800", "40", "10"):
        assert number in text
    assert "99999" not in text


def test_deck_comes_from_their_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    deck = _deck(False)
    transport = _transport([_usage(deck)])
    run = new_run(_answers(), _style())
    view = produce(run, api_key=_KEY, transport=transport)
    assert view["stopped"] is None
    assert view["steps"] == 1
    assert view["versions"][0]["issues"] == []
    assert view["charts"] == _part_charts(deck)
    assert view["versions"][0]["artifact"]["parts"] == deck["parts"]
    _assert_submittable(view["versions"][0]["artifact"])
    sent = transport.seen[0]
    user = next(item["content"] for item in sent["messages"] if item["role"] == "user")
    assert "Plain layout. Keep the brand colours" in user
    assert "One idea in each section" in user
    assert "SELECT *" not in user
    assert "pptx" in user
    assert "python-pptx" not in json.dumps(sent)
    assert sent["role"] == "reason"
    assert "tools" not in sent
    digest = view["versions"][0]["sign"]["digest"]
    assert view["versions"][0]["sign"]["tool"] == "sign_request"
    assert digest == governance().prompt_digest(user)
    assert view["spend"]["prompt_tokens"] == 420
    assert view["spend"]["completion_tokens"] == 180
    assert view["spend"]["usd"] is None
    root = "/opt/cursor/artifacts"
    if os.path.isdir("/opt/cursor"):
        os.makedirs(root, exist_ok=True)
        Path(root, "create-docs-stub-deck.json").write_text(
            json.dumps(view["versions"][0]["artifact"], indent=2),
            encoding="utf-8",
        )


def test_judge_repair_uses_their_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    transport = _transport([_usage(_deck(True)), _usage(_deck(False))])
    run = new_run(_answers(), _style())
    view = produce(run, api_key=_KEY, transport=transport)
    assert view["steps"] == 2
    assert "unsupported_claim" in view["versions"][0]["issues"]
    assert view["versions"][1]["issues"] == []
    assert len(transport.seen) == 2
    assert "unsupported_claim" in json.dumps(transport.seen[1])


def test_section_edit_splices_with_their_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    edited = _deck(False)
    edited["parts"][0]["body"] = "North was 1200. South was 800. Shorter."
    edited["parts"][0].pop("charts")
    edited["charts"] = [{"label": "Chart 1", "sql": _REVENUE}]
    transport = _transport([_usage(_deck(False)), _usage(edited)])
    run = new_run(_answers(), _style())
    produce(run, api_key=_KEY, transport=transport)
    view = edit(run, "s0", "Make the insight shorter.", api_key=_KEY, transport=transport)
    latest = view["versions"][1]["artifact"]
    assert latest["parts"][0]["body"].endswith("Shorter.")
    assert latest["parts"][1]["body"].startswith("Web was 40.")
    assert latest["parts"][0]["charts"][0]["label"] == "Revenue by region, North against South"
    assert view["versions"][1]["issues"] == []


def test_budget_exceeded_keeps_the_prior_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    transport = _transport([_usage(_deck(False)), GeneratorError("budget_exceeded")])
    run = new_run(_answers(), _style())
    produce(run, api_key=_KEY, transport=transport)
    view = edit(run, "s1", "Shorten this paragraph.", api_key=_KEY, transport=transport)
    assert view["stopped"] == "budget_exceeded"
    assert len(view["versions"]) == 1
    assert view["versions"][0]["issues"] == []


def test_prompt_carries_style_as_context_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    transport = _transport([_usage(_deck(False))])
    run = new_run(_answers(), _style())
    produce(run, api_key=_KEY, transport=transport)
    text = json.dumps(transport.seen[0])
    assert "Region note" in text
    assert "Write the narrative in sentences." in text
    assert "python-pptx" not in text
    assert run["data_context"]["skills"]["anthropic"] == ["pptx"]


def test_adapter_module_is_the_shared_generator() -> None:
    assert _ADAPTER_MODULES == ("dms_core.create_generator",)
    gov = governance()
    assert gov.generate.__module__ == "dms_core.create_generator"
    assert gov.judge_artifact.__module__ == "dms_core.create_generator"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("create_docs.judge")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("create_docs.context")


@pytest.mark.parametrize(
    ("raw", "on"),
    [
        (None, False),
        ("0", False),
        ("true", False),
        ("yes", False),
        ("2", False),
        ("01", False),
        ("1", True),
        (" 1 ", True),
    ],
)
def test_flag_digit_one_only(monkeypatch: pytest.MonkeyPatch, raw: str | None, on: bool) -> None:
    if raw is None:
        monkeypatch.delenv("DMS_CREATE_DOCS", raising=False)
    else:
        monkeypatch.setenv("DMS_CREATE_DOCS", raw)
    assert enabled() is on


def test_routes_flag_off_404_and_flag_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cortex_contract")
    pytest.importorskip("fastapi")
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    body = {
        "answers": [
            {
                "question": "Revenue by region",
                "text": "North was 1200.",
                "rows": [{"region": "North", "revenue": 1200}],
            }
        ]
    }
    monkeypatch.delenv("DMS_CREATE_DOCS", raising=False)
    off = TestClient(create_app())
    assert off.post("/v1/create-docs/runs", json=body).status_code == 404
    status = off.get("/v1/create-docs/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": False}
    monkeypatch.setenv("DMS_CREATE_DOCS", "1")
    on = TestClient(create_app())
    assert on.get("/v1/create-docs/status").json() == {"enabled": True}
    denied = on.post("/v1/create-docs/runs", json=body)
    assert denied.status_code == 403
