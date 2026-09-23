"""SCALE-FREE-AI-01 — OV-only FreeRoute consumption, dup labels skipped, WRONG=0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from cortex_client.compute import FREEROUTE_PREFERENCE as COMPUTE_PREF
from dms_api.app import create_app
from dms_core.freeroute import (
    FREEROUTE_PREFERENCE,
    WRONG_DISCIPLINE,
    candidate_models,
    catalog_paths,
    plan_free_providers,
    public_row,
    records_from_payloads,
    redact_text,
    render_harness_md,
)
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "apps" / "api" / "dms_api" / "freeroute_client.py"
CORE = ROOT / "packages" / "core" / "dms_core" / "freeroute.py"
BAKEOFF = ROOT / "scripts" / "bakeoff_freeroute.py"


def _records() -> list[dict[str, Any]]:
    return records_from_payloads(
        status={
            "hops": [
                {
                    "label": "Groq",
                    "provider": "groq",
                    "role": "free",
                    "chat_models": ["llama-3.3-70b-versatile"],
                },
                {
                    "label": "Groq",
                    "provider": "groq",
                    "role": "free",
                    "secret": "gsk_notareallivekeyxx",
                },
            ],
            "spendable": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "tier": "paid",
                    "default_role": "primary",
                    "openai_compatible": True,
                    "chat_models": ["gpt-4o-mini"],
                },
                {
                    "id": "groq",
                    "name": "Groq",
                    "tier": "freemium",
                    "default_role": "free",
                    "openai_compatible": True,
                    "chat_models": ["llama-3.3-70b-versatile"],
                },
            ],
        },
        onboard={
            "providers": [
                {
                    "id": "google",
                    "label": "Google AI Studio",
                    "tier": "freemium",
                    "role": "free",
                    "chat_models": ["gemini-flash-latest"],
                }
            ]
        },
        register={"providers": [{"id": "github_models", "label": "GitHub Models"}]},
        keys=[{"label": "Groq", "provider": "groq", "role": "free", "token": "sk-leak"}],
    )


def test_preference_matches_compute_free_normal() -> None:
    assert FREEROUTE_PREFERENCE == "free+normal"
    assert COMPUTE_PREF == FREEROUTE_PREFERENCE
    assert WRONG_DISCIPLINE == "WRONG=0"


def test_dup_labels_skipped_groq_first_paid_skipped() -> None:
    plan = plan_free_providers(_records())
    labels = [row["label"] for row in plan["attempted"]]
    assert labels[0] == "Groq"
    assert "Google AI Studio" in labels
    assert "OpenAI" not in labels
    reasons = {row["reason"] for row in plan["skipped"]}
    assert "dup_label" in reasons
    assert "paid_not_free_normal" in reasons
    assert "retired" in reasons
    assert plan["dup_labels_skipped"] >= 1
    assert plan["chat_tokens"] is False
    assert plan["scrape_local_vault"] is False
    assert plan["second_vault"] is False
    assert plan["live_key_rotate"] is False
    blob = json_blob(plan)
    assert "gsk_" not in blob
    assert "sk-leak" not in blob
    assert "LIVE_KEY" not in blob


def json_blob(plan: dict[str, Any]) -> str:
    return json.dumps(plan)


def test_public_row_drops_secrets() -> None:
    row = public_row(
        {
            "label": "Groq",
            "provider": "groq",
            "secret": "gsk_notareallivekeyxx",
            "token": "ov_notarealseat",
            "LIVE_KEY": "nope",
        }
    )
    assert "secret" not in row
    assert "token" not in row
    assert "LIVE_KEY" not in row
    assert row["label"] == "Groq"


def test_redact_and_harness_md() -> None:
    raw = "Bearer ov_notarealseat LIVE_KEY gsk_notareallivekeyxx"
    out = redact_text(raw)
    assert "ov_" not in out
    assert "LIVE_KEY" not in out
    assert "gsk_" not in out
    plan = plan_free_providers(_records())
    md = render_harness_md(plan)
    assert "dup_label" in md
    assert "Attempted" in md
    assert "LIVE_KEY" not in md
    assert candidate_models(plan)[0] == "llama-3.3-70b-versatile"


def test_client_and_bakeoff_never_chat_or_scrape() -> None:
    for path in (CLIENT, CORE, BAKEOFF):
        text = path.read_text(encoding="utf-8")
        assert "/v1/chat/completions" not in text
        assert "X-OpenVault-Reveal" not in text
        assert "OPENVAULT_ROOT" not in text


def test_catalog_paths_are_get_api_only() -> None:
    paths = catalog_paths()
    assert "/api/freeroute/status" in paths
    assert "/api/keys" in paths
    assert "/v1/chat/completions" not in paths


def test_route_returns_ov_plan(monkeypatch: Any) -> None:
    plan = plan_free_providers(_records())
    plan["openvault_ok"] = True
    monkeypatch.setattr(
        "dms_api.routes.freeroute.consume_freeroute_plan", lambda *_a, **_k: dict(plan)
    )
    body = TestClient(create_app()).get("/v1/freeroute/providers").json()
    assert body["preference"] == "free+normal"
    assert body["attempted"][0]["label"] == "Groq"
    assert any(row.get("reason") == "dup_label" for row in body["skipped"])
    assert body["chat_tokens"] is False
    assert "harness_md" in body
    assert "LIVE_KEY" not in str(body)


def test_route_ov_down_is_honest_empty() -> None:
    class _Boom:
        def __init__(self, *a: Any, **k: Any) -> None:
            req = httpx.Request("GET", "http://127.0.0.1:9")
            raise httpx.ConnectError("down", request=req)

    app = create_app()
    with patch("dms_api.freeroute_client.httpx.Client", _Boom):
        res = TestClient(app).get("/v1/freeroute/providers")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["attempted"] == []
    assert body["openvault_ok"] is False
    assert body["preference"] == "free+normal"
    assert "LIVE_KEY" not in res.text


def test_bakeoff_self_check() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("bakeoff_freeroute", BAKEOFF)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.self_check() == 0
