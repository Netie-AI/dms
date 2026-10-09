"""AI-LADDER-PLAN-01: plan, then one higher-tier retry, then reconfirm.

The fake model is deterministic. It is not a live provider. DMS source
(apps and packages, not tests or fixtures) must not name a provider or a
model. The two pre-existing OpenVault contract modules are the only
exceptions: the pin defaults and the FreeRoute catalog order.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from dms_executor.ai_ladder import ov_complete
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology

_ROOT = Path(__file__).resolve().parents[1]
_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_PLAN = "step-1 read locations.is_cold_storage from the schema context"
_GOOD = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_BAD = "SELECT nope FROM inventory"
_NAME = "not_granted"
_UNG = f"SELECT sku FROM {_NAME}"
# Response metadata the fake reports. These strings are not in product source.
_P_PLAN = "ov-reported-plan-provider"
_M_PLAN = "ov-reported-plan-model"
_P_FIRST = "ov-reported-first-provider"
_M_FIRST = "ov-reported-first-model"
_P_STRONG = "ov-reported-strong-provider"
_M_STRONG = "ov-reported-strong-model"
_CLOSEST = "Which locations are marked cold storage?"
_KEY_ENVS = (
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "COHERE_API_KEY",
    "TOGETHER_API_KEY",
)
_NAME_RE = (
    "groq",
    "openrouter",
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "gemma",
    "llama",
    "mistral",
    "huggingface",
    "cerebras",
    "cohere",
    "gpt-4",
    "gpt-3",
    "gpt-oss",
    "chatgpt",
    "nvidia",
    "google",
)
# Closed. A new file that names a provider or a model fails the scan.
_OV_CONTRACT = {
    "packages/cortex_client/cortex_client/strict_pin.py",
    "packages/core/dms_core/freeroute.py",
}


def _loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")


def _stamp(provider: str, model: str, **extra: Any) -> dict[str, Any]:
    out = {"served_provider": provider, "served_model": model}
    out.update(extra)
    return out


def _ask(tmp_path: Path, question: str, compute) -> dict[str, Any] | None:
    from cortex_client.compute import begin_answer_model_calls

    begin_answer_model_calls()
    db = tmp_path / "ladder.duckdb"
    ensure_demo_warehouse(db)

    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_ladder", output={"rows": rows})

    onto = load_verified_ontology(db, demo_ontology(db))
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=submit,
        ledger_append=lambda _p: SimpleNamespace(entry_id="led_ladder", hash="h"),
        ontology=onto,
    )


def test_plan_is_in_the_sql_prompt_and_uses_schema_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    seen: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx))
        if ctx.get("ladder_step") == "plan":
            prompt = str(ctx.get("sql_prompt") or "")
            schema = str(ctx.get("schema_context") or "")
            assert schema
            assert schema in prompt
            return _stamp(_P_PLAN, _M_PLAN, plan=_PLAN)
        return _stamp(_P_FIRST, _M_FIRST, query_sql=_GOOD)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    sql_calls = [ctx for ctx in seen if ctx.get("ladder_step") == "sql"]
    assert sql_calls
    assert _PLAN in str(sql_calls[0].get("sql_prompt") or "")
    assert env["badge"] == "L2_VALIDATED"


def test_stronger_tier_retry_converts_a_first_model_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    tiers: list[str | None] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("ladder_step") == "plan":
            return _stamp(_P_PLAN, _M_PLAN, plan=_PLAN)
        if ctx.get("ladder_step") == "reconfirm":
            return _stamp(_P_FIRST, _M_FIRST, closest_question=_CLOSEST)
        tier = ctx.get("ov_tier")
        tiers.append(tier if isinstance(tier, str) else None)
        assert "model" not in ctx
        assert "provider" not in ctx
        if tier == "higher":
            return _stamp(_P_STRONG, _M_STRONG, query_sql=_GOOD)
        return _stamp(_P_FIRST, _M_FIRST, query_sql=_BAD)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"]
    assert tiers[-1] == "higher"
    assert None in tiers
    calls = env["ladder_calls"]
    by_step = {item["step"]: item for item in calls}
    assert by_step["plan"]["provider"] == _P_PLAN
    assert by_step["plan"]["model"] == _M_PLAN
    assert by_step["sql"]["provider"] == _P_FIRST
    assert by_step["sql"]["model"] == _M_FIRST
    assert by_step["escalate"]["provider"] == _P_STRONG
    assert by_step["escalate"]["model"] == _M_STRONG


def test_exhausted_ladder_reconfirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("ladder_step") == "plan":
            return _stamp(_P_PLAN, _M_PLAN, plan=_PLAN)
        if ctx.get("ladder_step") == "reconfirm":
            return _stamp(_P_FIRST, _M_FIRST, closest_question=_CLOSEST)
        return _stamp(_P_FIRST, _M_FIRST, query_sql=_BAD)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstain_reason"] == "reconfirm"
    assert env["route"] == "confirm"
    assert env["suggestions"] == [_CLOSEST]
    assert _CLOSEST in env["text"]
    assert env["rows"] == []
    assert "loop_exhausted" not in env["text"]


def test_ungranted_name_is_not_in_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("ladder_step") == "plan":
            return _stamp(_P_PLAN, _M_PLAN, plan=_PLAN)
        return _stamp(_P_FIRST, _M_FIRST, query_sql=_UNG)

    env = _ask(tmp_path, "Show the sheet", compute)
    assert env is not None
    visible = json.dumps(
        {
            "text": env.get("text"),
            "suggestions": env.get("suggestions"),
            "assumptions": env.get("assumptions"),
        }
    )
    assert _NAME not in visible
    assert env["route"] != "confirm"


def test_openvault_tier_request_has_no_model_provider_or_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(
            200,
            headers={
                "x-openvault-served-provider": "header-provider",
                "x-openvault-served-model": "header-model",
            },
            json={
                "served_provider": "body-provider",
                "served_model": "body-model",
                "choices": [{"message": {"content": "SELECT 1"}}],
            },
        )

    original = httpx.Client

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if "transport" not in kwargs:
                kwargs["transport"] = httpx.MockTransport(handler)
            self._inner = original(*args, **kwargs)

        def __enter__(self) -> httpx.Client:
            return self._inner

        def __exit__(self, *_a: Any) -> None:
            self._inner.close()

    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.setattr("dms_executor.ai_ladder.httpx.Client", _Client)
    got = ov_complete("write sql", tier="higher")
    assert seen["body"]["tier"] == "higher"
    assert "model" not in seen["body"]
    assert "provider" not in seen["body"]
    assert "authorization" not in seen["headers"]
    assert got is not None
    assert got["served_provider"] == "body-provider"
    assert got["served_model"] == "body-model"
    assert got["query_sql"] == "SELECT 1"


def test_no_provider_key_in_process_or_config() -> None:
    for name in _KEY_ENVS:
        assert not os.environ.get(name), name
    for rel in (".env.example", "pyproject.toml"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        for name in _KEY_ENVS:
            assert name not in text
    ladder = (_ROOT / "packages/executor/dms_executor/ai_ladder.py").read_text(encoding="utf-8")
    assert "API_KEY" not in ladder
    assert "Authorization" not in ladder
    for token in _NAME_RE:
        assert token not in ladder


def _source_files() -> list[Path]:
    roots = [_ROOT / "apps", _ROOT / "packages"]
    out: list[Path] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(_ROOT).as_posix()
            if "/tests/" in f"/{rel}/" or "/fixtures/" in f"/{rel}/":
                continue
            if path.name.endswith((".test.ts", ".test.tsx")):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".toml", ".yml", ".yaml", ".env"}:
                continue
            if "node_modules" in rel or "__pycache__" in rel:
                continue
            out.append(path)
    return out


def test_dms_source_names_no_model_or_provider() -> None:
    """Fails when a provider or model name appears outside the OV contract files."""
    hits: list[str] = []
    for path in _source_files():
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _OV_CONTRACT:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        # File name and the ask-mode label are not model ids.
        text = text.replace("claude.md", "").replace("claude-white", "")
        for token in _NAME_RE:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
                hits.append(f"{rel}:{token}")
    assert hits == []
