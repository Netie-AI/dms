"""SERVED-ATTR-01 (#305, proposed F120). ``model_requested`` is what Cortex asked for.

DMS strips ``model`` and ``strict`` from every Insights body, so the DMS pin
constant never leaves DMS. The grade used to stamp that constant on every
case, L1 and zero-call cases included, and derived ``pin_mismatch`` from it.
The requested id is the one on Cortex's FreeRoute stamp for that call
(``generative.stamp.requested``); with no stamp it is unknown, never the pin.

Synthetic payloads and captures only. The armed test drives the product ask
path against loopback fakes from ``test_grade52_armed``; nothing live is called.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))
sys.path.insert(0, str(ROOT / "tests"))

import grade52  # noqa: E402
import test_grade52_armed as armed  # noqa: E402


@pytest.fixture(autouse=True)
def _no_local_openvault_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """``create_app`` probes OpenVault on :5000. These tests reach no live service."""
    import dms_executor

    monkeypatch.setattr(dms_executor, "probe_openvault", lambda **_kw: (None, ""))


# The id the live Cortex asked OpenVault for. Not the DMS pin constant.
_CORTEX_PICK = "thinkingmachines/inkling:free"
_OTHER = "qwen/qwen3.8-27b"


def _pin() -> str:
    grade52._bootstrap()
    from cortex_client.strict_pin import pin_config

    return pin_config().model


def _stamp(requested: str, served: str | None) -> dict[str, Any]:
    """A RouteStamp.public() as Cortex attaches it to ``generative.stamp``."""
    return {
        "call_id": "call-1",
        "task": "insights",
        "requested": requested,
        "served": served or "",
        "served_model": served,
        "pick_reason": "measured best validity 0.9 over n=12 among 3 scored",
        "source": "scoreboard",
    }


def _payload(requested: str, served: str | None, sql: str | None) -> dict[str, Any]:
    gen: dict[str, Any] = {"ok": bool(sql), "sql": sql, "stamp": _stamp(requested, served)}
    out: dict[str, Any] = {"ok": True, "generative": gen, "served_model": served}
    if sql:
        out["sql_used"] = sql
        out["query_sql"] = sql
    return out


_SQL = "SELECT 1 AS n"


def _ai_env(served: str) -> dict[str, Any]:
    return {
        "rows": [{"n": 1}],
        "text": "Found 1 row(s).",
        "sql_used": _SQL,
        "served_model": served,
        "served_attribution": "reported",
        "plan_origin": "generate_sql",
        "plan_source": "ontology_plan",
    }


def test_requested_is_the_cortex_stamp_not_the_dms_pin() -> None:
    assert _CORTEX_PICK != _pin()
    env = _ai_env(_CORTEX_PICK)
    block = grade52._served_block(env, [_payload(_CORTEX_PICK, _CORTEX_PICK, _SQL)])
    assert block["insights_calls"] == 1
    assert block["model_requested"] == _CORTEX_PICK
    assert block["served_model"] == env["served_model"] == _CORTEX_PICK


def test_strict_stamp_model_requested_wins() -> None:
    stamp_payload = _payload(_CORTEX_PICK, _CORTEX_PICK, _SQL)
    stamp_payload["generative"]["stamp"]["model_requested"] = _CORTEX_PICK
    stamp_payload["generative"]["stamp"]["requested"] = ""
    block = grade52._served_block(_ai_env(_CORTEX_PICK), [stamp_payload])
    assert block["model_requested"] == _CORTEX_PICK


def test_requested_pairs_with_the_call_whose_sql_was_kept() -> None:
    first = _payload(_OTHER, _OTHER, _SQL)
    second = _payload(_CORTEX_PICK, None, None)
    block = grade52._served_block(_ai_env(_OTHER), [first, second])
    assert block["insights_calls"] == 2
    assert block["model_requested"] == _OTHER


def test_zero_call_case_has_no_requested_model() -> None:
    env = {"rows": [{"n": 1}], "text": "Found 1 row(s).", "sql_used": _SQL}
    block = grade52._served_block(env, [])
    assert block["insights_calls"] == 0
    assert block["model_requested"] is None


def test_unstamped_call_is_unknown_never_the_pin() -> None:
    payload = {"ok": True, "generative": {"ok": False}, "served_model": None}
    block = grade52._served_block({"rows": [], "text": "Abstained."}, [payload])
    assert block["insights_calls"] == 1
    assert block["model_requested"] is None


def test_l1_case_with_a_model_call_has_no_requested_model() -> None:
    env = {
        "rows": [{"n": 1}],
        "text": "Found 1 row(s).",
        "sql_used": _SQL,
        "ladder_rung": "l1_governed_metric",
    }
    assert grade52.serve_path(env, None) == "L1"
    block = grade52._served_block(env, [_payload(_CORTEX_PICK, None, None)])
    assert block["serve_path"] == "L1"
    assert block["model_requested"] is None


# --- grade side: captures --------------------------------------------------


def _capture_rows(served: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every pack question with an abstain envelope; ``served`` overrides blocks by id."""
    pack = grade52._load_yaml(grade52.PACK_PATH)
    out: list[dict[str, Any]] = []
    for question in pack["questions"]:
        qid = str(question["id"])
        env = {"rows": [], "text": "Abstained.", "abstained": True, "badge": "abstain"}
        block = served.get(qid, {"insights_calls": 0, "serve_path": "abstain"})
        env["served_model"] = block.get("served_model")
        out.append({"id": qid, "env": env, "served": block})
    return out


def _ids(n: int) -> list[str]:
    pack = grade52._load_yaml(grade52.PACK_PATH)
    return [str(q["id"]) for q in pack["questions"]][:n]


def _cases(rows: list[dict[str, Any]], tmp_path: Path) -> dict[str, dict[str, Any]]:
    report = grade52.grade_envelopes(rows, mode=grade52.MODE_SERVED, warehouse_dir=tmp_path)
    return {str(case["id"]): case for case in report["cases"]}


def test_old_capture_pin_constant_is_not_a_requested_model(tmp_path: Path) -> None:
    """A pre-fix block carries the DMS pin on every case. None of it is attributed."""
    pin = _pin()
    zero, l1, ai = _ids(3)
    rows = _capture_rows(
        {
            zero: {"insights_calls": 0, "serve_path": "L0", "model_requested": pin},
            l1: {"insights_calls": 1, "serve_path": "L1", "model_requested": pin},
            ai: {
                "insights_calls": 1,
                "serve_path": "ai",
                "model_requested": pin,
                "served_model": _CORTEX_PICK,
            },
        }
    )
    cases = _cases(rows, tmp_path)
    for qid in (zero, l1, ai):
        assert cases[qid]["model_requested"] is None
        assert cases[qid]["pin_mismatch"] is False
    assert grade52.not_scored_entries(list(cases.values())) == []


def test_cortex_requested_x_served_x_is_scored(tmp_path: Path) -> None:
    """Requested X, served X: scored, even though X is not the DMS pin."""
    assert _CORTEX_PICK != _pin()
    same, diff, l1 = _ids(3)
    block = grade52._served_block(
        _ai_env(_CORTEX_PICK), [_payload(_CORTEX_PICK, _CORTEX_PICK, _SQL)]
    )
    moved = grade52._served_block(_ai_env(_OTHER), [_payload(_CORTEX_PICK, _OTHER, _SQL)])
    rule_env = {"rows": [], "text": "x", "ladder_rung": "l1_governed_metric"}
    rule = grade52._served_block(rule_env, [_payload(_CORTEX_PICK, None, None)])
    cases = _cases(_capture_rows({same: block, diff: moved, l1: rule}), tmp_path)
    assert cases[same]["model_requested"] == _CORTEX_PICK
    assert cases[same]["served_model"] == _CORTEX_PICK
    assert cases[same]["pin_mismatch"] is False
    assert cases[diff]["model_requested"] == _CORTEX_PICK
    assert cases[diff]["served_model"] == _OTHER
    assert cases[diff]["pin_mismatch"] is True
    assert cases[l1]["model_requested"] is None and cases[l1]["pin_mismatch"] is False
    assert grade52.not_scored_entries(list(cases.values())) == [
        {"id": diff, "reason": f"pin_mismatch:{_CORTEX_PICK}/{_OTHER}"}
    ]


# --- armed path: the product ask over loopback fakes ------------------------

_armed_env = armed._armed_env
stack = armed.stack


def _usage_ledger(calls: list[dict[str, Any]]) -> Callable[..., tuple[int, Any]]:
    def usage(_body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        return 200, {"events": list(calls)}

    return usage


def test_armed_capture_carries_what_cortex_requested(
    stack: Callable[..., tuple[Any, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Each AI case: requested is Cortex's pick and served equals the OV ledger row."""
    assert _CORTEX_PICK != _pin()
    cortex, vault = stack(model=armed._gold_model)
    ledger: list[dict[str, Any]] = []
    generate = cortex.routes[("POST", "/v1/insights")]

    def stamped(body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        assert "model" not in body and "strict" not in body
        status, out = generate(body, headers)
        gen = out.get("generative") or {}
        served = _CORTEX_PICK if out.get("served_model") else None
        if served:
            out["served_model"] = served
        gen["stamp"] = _stamp(_CORTEX_PICK, served)
        out["generative"] = gen
        ledger.append(
            {"caller_role": "cortex", "model_requested": _CORTEX_PICK, "model_served": served}
        )
        return status, out

    cortex.routes[("POST", "/v1/insights")] = stamped
    vault.routes[("GET", "/api/usage")] = _usage_ledger(ledger)
    armed._arm(monkeypatch, cortex, vault)
    grade52.main(["--armed", "--layout", "full-tree"])

    runner = tmp_path / "runner" / "grade52"
    capture = (runner / "grade52.armed.jsonl").read_text(encoding="utf-8").splitlines()
    meta = json.loads(capture[0])
    ov_cortex = meta["ov_calls_by_caller_role"]["cortex"]
    assert ov_cortex["calls"] == len(ledger) > 0
    assert ov_cortex["model_mismatch"] == 0
    records = [json.loads(line) for line in capture[1:]]
    ledger_served = {row["model_served"] for row in ledger if row["model_served"]}
    ai = [rec for rec in records if rec["served"]["route"] == "ai"]
    assert ai
    for rec in ai:
        assert rec["served"]["model_requested"] == _CORTEX_PICK
        assert rec["env"]["served_model"] == _CORTEX_PICK
        assert rec["env"]["served_model"] in ledger_served
    quiet = [rec for rec in records if not rec["served"]["insights_calls"]]
    assert quiet, "the pack has cases that never call the model"
    for rec in quiet + [r for r in records if r["served"]["serve_path"] == "L1"]:
        assert rec["served"].get("model_requested") is None

    artifact = json.loads((runner / "grade52.json").read_text(encoding="utf-8"))
    assert artifact["not_scored"] == []
    assert all(case["not_scored"] is None for case in artifact["cases"])

    # The written capture re-grades the same: requested X, served X is scored.
    regraded = grade52.grade_envelopes_path(runner / "grade52.armed.jsonl")
    by_id = {str(case["id"]): case for case in regraded["cases"]}
    for rec in records:
        case = by_id[str(rec["id"])]
        assert case["pin_mismatch"] is False
        if rec["served"]["route"] == "ai":
            assert case["model_requested"] == _CORTEX_PICK
            assert case["served_model"] == _CORTEX_PICK
        if not rec["served"]["insights_calls"]:
            assert case["model_requested"] is None
