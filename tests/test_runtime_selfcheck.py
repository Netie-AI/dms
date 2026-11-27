"""R1-RUNTIME-01: timeout pin, caller class, startup self-check.

``DMS_RUNTIME_SELFCHECK`` defaults off. Off, ``/health`` and envelopes gain no
runtime key, and the served text, SQL, and route stay put. On, ``/health``
gains ``runtime`` from env, and a loopback caller or a timeout under 30s
stamps ``runtime_degraded``. The Python insights default stays 8s.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest
from cortex_client.compute import (
    attach_runtime_banner,
    classify_cortex_caller,
    insights_timeout_s,
    log_effective_insights_timeout,
    log_runtime_selfcheck,
)
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.envelope import build_answer_envelope
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
_FLAG = "DMS_RUNTIME_SELFCHECK"
_TIMEOUT = "DMS_INSIGHTS_TIMEOUT_S"
_SERVICE_KEY = "ov_svc_runtime_01"
_LOOPBACK_KEY = "dms-demo-viewer-key"
# Key-shaped ov_ tokens. The contract field ov_reachable is not a key id.
_SECRET = re.compile(
    r"(?:ov_(?!reachable\b)[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9_-]{8,})"
)
_NEW_FUNCS = frozenset(
    {
        "runtime_selfcheck_enabled",
        "log_effective_insights_timeout",
        "classify_cortex_caller",
        "runtime_is_degraded",
        "runtime_health_fields",
        "attach_runtime_banner",
        "log_runtime_selfcheck",
    }
)
_SERVED = ("text", "sql_used", "route", "badge", "values", "abstained")


@pytest.fixture
def runtime_env(monkeypatch: pytest.MonkeyPatch):
    """Drop the settings cache after the test env is still in force.

    The next ``get_settings()`` then reads the restored environment.
    """
    get_settings.cache_clear()
    monkeypatch.delenv(_FLAG, raising=False)
    monkeypatch.delenv(_TIMEOUT, raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    yield monkeypatch
    get_settings.cache_clear()


def _envelope() -> dict:
    return build_answer_envelope(
        answer_id="a-runtime",
        text="Cannot answer.",
        badge="ABSTAIN",
        abstained=True,
        ask_mode="demo",
        route="abstain",
        as_of="2026-01-01T00:00:00Z",
        question="total revenue last quarter please",
        sql_used="SELECT 1",
    )


def _health() -> dict:
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert _SECRET.search(response.text) is None, "secret-shaped prefix in /health"
    return response.json()


def _served(body: dict) -> dict:
    return {key: body.get(key) for key in _SERVED}


def test_unset_timeout_default_stays_eight(runtime_env: pytest.MonkeyPatch) -> None:
    runtime_env.delenv(_TIMEOUT, raising=False)
    assert insights_timeout_s() == 8.0


def test_flag_off_health_and_envelope_are_byte_identical(
    runtime_env: pytest.MonkeyPatch,
) -> None:
    off = _envelope()
    assert attach_runtime_banner(off) is off
    runtime_env.setenv(_FLAG, "0")
    zero = _envelope()
    runtime_env.setenv(_FLAG, "false")
    false = _envelope()
    runtime_env.delenv(_FLAG, raising=False)
    unset = _envelope()
    assert off == zero == false == unset
    assert "runtime_degraded" not in unset
    assert "banner_reason" not in unset
    assert unset["route"] == "abstain"
    assert unset["text"] == "Cannot answer."

    health_unset = _health()
    runtime_env.setenv(_FLAG, "0")
    health_zero = _health()
    assert "runtime" not in health_unset
    assert "runtime_degraded" not in health_unset
    assert health_unset == health_zero


def test_health_runtime_block_comes_from_env_and_hides_the_key(
    runtime_env: pytest.MonkeyPatch,
) -> None:
    runtime_env.setenv(_FLAG, "1")
    runtime_env.setenv(_TIMEOUT, "45")
    runtime_env.setenv("CORTEX_API_KEY", _SERVICE_KEY)
    body = _health()
    runtime = body["runtime"]
    assert runtime["insights_timeout_s"] == 45.0
    assert runtime["cortex_caller_kind"] == "service"
    assert runtime["cortex_reachable"] is False
    assert runtime["ov_reachable"] is False
    assert set(runtime) == {
        "insights_timeout_s",
        "cortex_reachable",
        "cortex_caller_kind",
        "ov_reachable",
    }
    assert _SERVICE_KEY not in str(body)
    assert "runtime_degraded" not in body


def test_loopback_sets_degraded_and_service_does_not(
    runtime_env: pytest.MonkeyPatch,
) -> None:
    runtime_env.setenv(_FLAG, "1")
    runtime_env.setenv(_TIMEOUT, "60")
    runtime_env.setenv("CORTEX_API_KEY", _LOOPBACK_KEY)
    loop = _health()
    loop_env = _envelope()
    assert classify_cortex_caller(_LOOPBACK_KEY) == "loopback"
    assert loop["runtime"]["cortex_caller_kind"] == "loopback"
    assert loop["runtime_degraded"] is True
    assert loop["banner_reason"] == "runtime_degraded"
    assert loop_env["runtime_degraded"] is True
    assert loop_env["banner_reason"] == "runtime_degraded"
    assert _served(loop_env) == _served(_bare_envelope(runtime_env))

    runtime_env.setenv("CORTEX_API_KEY", _SERVICE_KEY)
    service = _health()
    service_env = _envelope()
    assert service["runtime"]["cortex_caller_kind"] == "service"
    assert service["runtime"]["insights_timeout_s"] == 60.0
    assert "runtime_degraded" not in service
    assert "banner_reason" not in service
    assert "runtime_degraded" not in service_env
    assert attach_runtime_banner(service_env) is service_env


def _bare_envelope(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.delenv(_FLAG, raising=False)
    body = _envelope()
    monkeypatch.setenv(_FLAG, "1")
    return body


def test_timeout_under_30_degrades_a_service_caller(
    runtime_env: pytest.MonkeyPatch,
) -> None:
    runtime_env.setenv(_FLAG, "1")
    runtime_env.setenv("CORTEX_API_KEY", _SERVICE_KEY)
    runtime_env.setenv(_TIMEOUT, "30")
    assert "runtime_degraded" not in _health()
    runtime_env.setenv(_TIMEOUT, "29.9")
    low = _health()
    assert low["runtime"]["insights_timeout_s"] == 29.9
    assert low["runtime_degraded"] is True
    assert _envelope()["banner_reason"] == "runtime_degraded"


def test_unknown_caller_is_not_loopback(runtime_env: pytest.MonkeyPatch) -> None:
    runtime_env.setenv(_FLAG, "1")
    runtime_env.setenv(_TIMEOUT, "60")
    body = _health()
    assert body["runtime"]["cortex_caller_kind"] == "unknown"
    assert "runtime_degraded" not in body
    assert classify_cortex_caller(None) == "unknown"
    assert classify_cortex_caller("seeded-test-token") == "unknown"


def test_startup_logs_the_effective_timeout_and_not_the_key(
    runtime_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_env.setenv(_FLAG, "1")
    runtime_env.setenv(_TIMEOUT, "8")
    runtime_env.setenv("CORTEX_API_KEY", _SERVICE_KEY)
    get_settings.cache_clear()
    with caplog.at_level(logging.INFO, logger="cortex_client.compute"):
        with TestClient(create_app()):
            pass
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "cortex_client.compute"
    ]
    text = "\n".join(messages)
    assert "DMS_INSIGHTS_TIMEOUT_S effective 8.0" in text
    assert "runtime_degraded caller_kind=service insights_timeout_s=8.0" in text
    assert _SERVICE_KEY not in text
    assert log_effective_insights_timeout() == 8.0


def test_selfcheck_off_does_not_warn(
    runtime_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_env.setenv(_TIMEOUT, "8")
    runtime_env.setenv("CORTEX_API_KEY", _LOOPBACK_KEY)
    with caplog.at_level(logging.WARNING, logger="cortex_client.compute"):
        log_runtime_selfcheck(_LOOPBACK_KEY)
    assert "runtime_degraded" not in caplog.text
    assert _LOOPBACK_KEY not in caplog.text


def test_deploy_pins_timeout_60_and_leaves_the_flag_off() -> None:
    compose = (ROOT / "deploy/compose/docker-compose.yml").read_text(encoding="utf-8")
    example = (ROOT / "deploy/compose/.env.example").read_text(encoding="utf-8")
    start = (ROOT / "scripts/windows/Start-DMSStack.ps1").read_text(encoding="utf-8")
    assert compose.count("DMS_INSIGHTS_TIMEOUT_S: ${DMS_INSIGHTS_TIMEOUT_S:-60}") == 2
    assert "DMS_INSIGHTS_TIMEOUT_S=60" in example
    assert "DMS_RUNTIME_SELFCHECK=1" not in example
    assert "DMS_RUNTIME_SELFCHECK=1" not in compose
    assert "DMS_INSIGHTS_TIMEOUT_S" in start
    assert "else { 60 }" in start


def test_runtime_functions_do_not_read_question_text() -> None:
    source = (ROOT / "packages/cortex_client/cortex_client/compute.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _NEW_FUNCS:
            seen.add(node.name)
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in {"question", "prompt"}:
                    raise AssertionError(node.name)
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    assert " in question" not in child.value
                    assert " in prompt" not in child.value
    assert seen == _NEW_FUNCS
