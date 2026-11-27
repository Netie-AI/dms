"""R1-UI-COPY-01: DMS_RUNTIME_SELFCHECK defaults off and does not touch ask.

The flag only swaps the offline banner. Prompt bytes stay equal with the
flag unset, set to 0, or set to 1. The ask route source does not read it,
so the envelope and the served route do not branch on it.
Synthetic question text only. No held-out pack.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sql_vote():
    path = ROOT / "packages" / "executor" / "dms_executor" / "sql_vote.py"
    spec = importlib.util.spec_from_file_location("sql_vote_runtime_selfcheck", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("sql_vote module missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_QUESTION = "how many rows are in the synthetic table"
_ONTOLOGY = '{"tables":["t"],"columns":["id"]}'
_PLAN = {"tables": ["t"], "joins": [], "measure": "", "filters": []}
_ASK_FILES = (
    "apps/api/dms_api/routes/chat.py",
    "packages/executor/dms_executor/generative_ask.py",
    "packages/executor/dms_executor/sql_vote.py",
    "packages/cortex_client/cortex_client/compute.py",
)


def _prompt_bytes() -> bytes:
    vote = _sql_vote()
    plan = vote.plan_prompt(_QUESTION, _ONTOLOGY)
    reading = vote.reading_prompt(_QUESTION, reading=2, plan=_PLAN, ontology=_ONTOLOGY)
    direct = vote.reading_prompt(_QUESTION, reading=1)
    return (plan + "\n" + reading + "\n" + direct).encode("utf-8")


def test_flag_off_prompt_bytes_match_flag_on(monkeypatch) -> None:
    monkeypatch.delenv("DMS_RUNTIME_SELFCHECK", raising=False)
    unset = _prompt_bytes()
    monkeypatch.setenv("DMS_RUNTIME_SELFCHECK", "0")
    off = _prompt_bytes()
    monkeypatch.setenv("DMS_RUNTIME_SELFCHECK", "1")
    on = _prompt_bytes()
    assert unset == off == on
    assert len(on) > 0


def _settings():
    path = ROOT / "apps" / "api" / "dms_api" / "settings.py"
    spec = importlib.util.spec_from_file_location("dms_settings_runtime_selfcheck", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("settings module missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("DMS_RUNTIME_SELFCHECK", raising=False)
    settings = _settings()
    settings.get_settings.cache_clear()
    assert settings.get_settings().dms_runtime_selfcheck is False
    monkeypatch.setenv("DMS_RUNTIME_SELFCHECK", "0")
    settings.get_settings.cache_clear()
    assert settings.get_settings().dms_runtime_selfcheck is False
    monkeypatch.setenv("DMS_RUNTIME_SELFCHECK", "1")
    settings.get_settings.cache_clear()
    assert settings.get_settings().dms_runtime_selfcheck is True
    settings.get_settings.cache_clear()


def test_ask_route_does_not_read_the_flag() -> None:
    for rel in _ASK_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "DMS_RUNTIME_SELFCHECK" not in text
        assert "dms_runtime_selfcheck" not in text
    chat = (ROOT / "apps/api/dms_api/routes/chat.py").read_text(encoding="utf-8")
    assert '"/ask"' in chat or "'/ask'" in chat
