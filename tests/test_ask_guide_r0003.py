"""R-0003 checks for ASK-GUIDE-01. DMS_ASK_CLARIFY stays unset.

Guards exercised on a recompiled reading (the door ``confirm_reading`` uses
after the flag check). ``rank_window_unhandled_terms`` and
``intent_shape_mismatch`` are not in that door on this main.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.ask import ask_clarify_enabled
from dms_executor import Executor
from dms_executor.ask_clarify import (
    _submit_validated,
    _tier2,
    build_insights,
    run_validated_reading,
    validate_compiled_sql,
)
from dms_executor.envelope import build_answer_envelope
from dms_executor.generative_ask import _submit_validated as submit_door
from dms_executor.generative_ask import validate_compiled_sql as validate_door
from dms_executor.manifest import ManifestMinter
from dms_executor.ontology import NO_SILENT_PAD, CompiledQuery, Coverage
from fastapi.testclient import TestClient

from tests.fixtures.ask_guide.capture_flag_off_52 import dump_rows, replay_pack
from tests.test_ask_guide_01 import GRANTS, _ledger, _patch_ontology, _rows_result, _seed
from tests.test_ask_guide_flag_off_replay_52 import GOLDEN, MASKED_FIELDS, _masked

EXERCISED_GUARDS = (
    "unhonored_qualifier_reason",
    "_should_demote_shape_mismatch",
)


def _reason(env: dict[str, Any]) -> str:
    return " ".join(str(note) for note in (env.get("assumptions") or []))


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    assert ask_clarify_enabled() is False


def test_r0003_confirm_and_run_refused_409_before_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm/Run is 409 clarify_disabled. Compile and submit are not called."""
    _off(monkeypatch)
    compiled: list[str] = []
    submitted: list[str] = []

    def _compile(*_a: Any, **_k: Any) -> None:
        compiled.append("compile")
        raise AssertionError("compile")

    def _submit(*_a: Any, **_k: Any) -> None:
        submitted.append("submit")
        raise AssertionError("submit")

    monkeypatch.setattr("dms_executor.ask_clarify.compile_plan", _compile)
    monkeypatch.setattr("dms_executor.ask_clarify.validate_compiled_sql", _compile)
    monkeypatch.setattr("dms_executor.generative_ask.validate_compiled_sql", _compile)
    monkeypatch.setattr("dms_executor.ask_clarify._submit_validated", _submit)
    monkeypatch.setattr("dms_executor.Executor.confirm_clarify", _submit)

    app = create_app()
    app.state.ask_service = Executor(
        cortex=object(),  # type: ignore[arg-type]
        minter=ManifestMinter(openvault_url="http://127.0.0.1:9"),
    )
    app.state.cortex = object()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    res = TestClient(app).post(
        "/v1/chat/clarify/run",
        json={
            "option_id": "opt_x",
            "plan": {
                "measure": "spend_kg",
                "entity": None,
                "filter": None,
                "time_grain": None,
            },
        },
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "clarify_disabled"
    assert compiled == []
    assert submitted == []


def test_r0003_ask_envelope_has_no_clarify_key() -> None:
    """No clarify key at all, not null. Bytes match the post-#394 main capture."""
    live = replay_pack()
    assert len(live) == 52
    for row in live:
        assert "clarify" not in row["env"]
        assert row["env"].get("clarify", "absent") == "absent"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert dump_rows(_masked(live)) == dump_rows(_masked(golden))
    assert MASKED_FIELDS == ("as_of",)


def test_r0003_recompiled_pick_cannot_reach_l2_past_main_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A reading through run_validated_reading stays off L2 when a main guard trips.

    Exercised: unhonored_qualifier_reason, _should_demote_shape_mismatch.
    rank_window_unhandled_terms and intent_shape_mismatch are absent from
    _submit_validated and build_answer_envelope on this main.
    """
    _off(monkeypatch)
    path = tmp_path / "lake.duckdb"
    _seed(path)
    sql = "SELECT SUM(quantity_kg) AS spend_kg FROM transactions"
    compiled = CompiledQuery(
        sql=sql,
        measure="spend_kg",
        grain="transaction",
        group_by=(),
        notes=("recompiled",),
        coverage=Coverage(
            include=("transactions.quantity_kg",),
            exclude=(NO_SILENT_PAD,),
            unsure=(),
        ),
    )

    def _run(question: str) -> tuple[dict[str, Any], list[str]]:
        submits: list[str] = []

        def _submit(statement: str) -> Any:
            submits.append(statement)
            return _rows_result()

        env = run_validated_reading(
            compiled,
            question=question,
            grantable=set(GRANTS),
            warehouse=path,
            space_id=None,
            session_id="ses_r0003",
            submit=_submit,
            ledger_append=lambda _payload: _ledger(),
        )
        return env, submits

    blocked, submits = _run("Total quantity moved by supplier")
    assert blocked["badge"] != "L2_VALIDATED"
    assert "unhonored_qualifier" in _reason(blocked)
    assert submits == []

    demoted, submits = _run("Show the top 3 totals")
    assert demoted["badge"] != "L2_VALIDATED"
    assert "shape mismatch" in _reason(demoted)
    assert submits

    door = inspect.getsource(submit_door) + inspect.getsource(build_answer_envelope)
    assert "unhonored_qualifier_reason" in inspect.getsource(submit_door)
    assert "_should_demote_shape_mismatch" in inspect.getsource(build_answer_envelope)
    assert "rank_window_unhandled_terms" not in door
    assert "intent_shape_mismatch" not in door
    assert EXERCISED_GUARDS == (
        "unhonored_qualifier_reason",
        "_should_demote_shape_mismatch",
    )


def test_r0003_tier2_sql_uses_validate_compiled_sql_and_submit_validated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Tier-2 SQL calls the same validate_compiled_sql and _submit_validated as a normal ask.

    The flag stays unset, so apply_ask_guide does not reach tier 2.
    build_insights is the function that door calls once the flag is on.
    """
    _off(monkeypatch)
    assert validate_compiled_sql is validate_door
    assert _submit_validated is submit_door
    assert "run_validated_reading" in inspect.getsource(_tier2)
    assert "validate_compiled_sql" in inspect.getsource(run_validated_reading)
    assert "_submit_validated" in inspect.getsource(run_validated_reading)

    path = tmp_path / "lake.duckdb"
    onto = _seed(path)
    _patch_ontology(monkeypatch, onto)
    validated: list[str] = []
    submitted: list[str] = []

    def _validate(sql: str, **kwargs: Any) -> str | None:
        validated.append(sql)
        return validate_door(sql, **kwargs)

    def _submit(sql: str, **kwargs: Any) -> dict[str, Any]:
        submitted.append(sql)
        return submit_door(sql, **kwargs)

    monkeypatch.setattr("dms_executor.ask_clarify.validate_compiled_sql", _validate)
    monkeypatch.setattr("dms_executor.ask_clarify._submit_validated", _submit)
    from dms_executor.ask_clarify import apply_ask_guide

    env = {
        "badge": "L2_VALIDATED",
        "abstained": False,
        "text": "Found 1 row(s).",
        "values": [{"id": "v1", "label": "spend_kg", "value": 30}],
        "sql_used": "SELECT 1",
        "rows": [{"spend_kg": 30}],
        "assumptions": ["main"],
    }
    untouched = apply_ask_guide(
        dict(env),
        warehouse=path,
        grantable=set(GRANTS),
        space_id=None,
        session_id="ses_tier",
        submit=lambda _sql: (_ for _ in ()).throw(AssertionError("flag-off submit")),
        ledger_append=lambda _payload: _ledger(),
    )
    assert untouched is not None
    assert "insights" not in untouched
    assert validated == []
    assert submitted == []

    out = build_insights(
        dict(env),
        onto=onto,
        grantable=set(GRANTS),
        warehouse=path,
        space_id=None,
        session_id="ses_tier",
        submit=lambda _sql: _rows_result(),
        ledger_append=lambda _payload: _ledger(),
    )
    assert "tier2" in out
    assert submitted
    assert any(sql in validated for sql in submitted)
