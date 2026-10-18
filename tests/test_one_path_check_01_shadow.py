"""ONE-PATH-CHECK-01: flag-off envelopes stay the f9ffc3e1 bytes.

The 52-question replay is the existing flag-off fixture
(``tests/fixtures/ask_guide``). ``as_of`` is the wall clock, so both sides
replace it. The only other allowed difference is ``served_check_shadow``.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from tests.fixtures.ask_guide.capture_flag_off_52 import HERE, dump_rows, replay_pack

_AS_OF = "<as_of>"
_SHADOW = "served_check_shadow"
# Capture day of flag_off_52_f9ffc3e1.json. semantic_retrieve._today()
# minus 90 days was 2026-07-10 on this day.
_CAPTURE_DAY = (2026, 10, 8)
GOLDEN = HERE / "flag_off_52_f9ffc3e1.json"
# Full envelopes from 57d85c52 with the wall clock on the capture day.
# Shadow included. as_of is the only field that moves. No new key.
MAIN_57 = HERE / "flag_off_52_57d85c52.json"


def _pin_capture_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin semantic_retrieve's clock to the capture day.

    The pin is the module helper only. datetime.date stays the real class,
    including for modules first imported while the pin is active.
    """
    import dms_executor.semantic_retrieve as retrieve

    monkeypatch.setattr(retrieve, "_today", lambda: dt.date(*_CAPTURE_DAY))


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    out.pop(_SHADOW, None)
    if "as_of" in out:
        out["as_of"] = _AS_OF
    return out


def _stable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": row["id"], "env": _stable(row["env"])} for row in rows]


def _mask_clock(obj: Any) -> Any:
    """Mask clock fields only. ``served_check_shadow`` stays in the compare."""
    if isinstance(obj, dict):
        return {
            key: ("<as_of>" if key == "as_of" else _mask_clock(val))
            for key, val in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_clock(val) for val in obj]
    return obj


def _diff_paths(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list):
        if len(left) != len(right):
            return [path or "$"]
        paths = []
        for index, (item, other) in enumerate(zip(left, right, strict=True)):
            paths.extend(_diff_paths(item, other, f"{path}[{index}]"))
        return paths
    if left != right:
        return [path or "$"]
    return []


def test_flag_off_envelopes_match_f9ffc3e1_except_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_capture_day(monkeypatch)
    live = replay_pack()
    assert len(live) == 52
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in golden]
    left = dump_rows(_stable_rows(live))
    right = dump_rows(_stable_rows(golden))
    if left != right:
        main_rows = {row["id"]: row["env"] for row in golden}
        problems: list[str] = []
        for row in live:
            paths = _diff_paths(_stable(row["env"]), _stable(main_rows[row["id"]]))
            if paths:
                problems.append(f"{row['id']}: {paths}")
        pytest.fail("flag-off envelopes differ from f9ffc3e1:\n" + "\n".join(problems))
    served = 0
    for row in live:
        env = row["env"]
        shadow = env.get(_SHADOW)
        if env.get("badge") == "L2_VALIDATED" and env.get("route") == "generated":
            served += 1
            assert isinstance(shadow, dict), row["id"]
            assert shadow.get("checker_version")
            assert "error" not in shadow
            assert "unclear" in shadow and "conjuncts" in shadow
        else:
            assert shadow is None, row["id"]
    assert served > 0


def test_flag_off_envelopes_match_57d85c52_including_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off full envelopes, shadow included, equal the 57d85c52 capture.

    Only ``as_of`` is masked. A new key or a different ``checker_version``
    fails this. The test above still pops the shadow.
    """
    monkeypatch.delenv("DMS_INTENT_SPEC", raising=False)
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    _pin_capture_day(monkeypatch)
    live = replay_pack()
    assert len(live) == 52
    golden = json.loads(MAIN_57.read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in golden]
    main_rows = {row["id"]: row["env"] for row in golden}
    problems: list[str] = []
    for row in live:
        paths = _diff_paths(_mask_clock(row["env"]), _mask_clock(main_rows[row["id"]]))
        if paths:
            problems.append(f"{row['id']}: {paths}")
    if problems:
        pytest.fail("flag-off envelopes differ from 57d85c52:\n" + "\n".join(problems))


def test_capture_day_pin_does_not_replace_datetime_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A module imported during the pin still binds the real date class."""
    import types

    real = dt.date
    _pin_capture_day(monkeypatch)
    assert dt.date is real
    namespace: dict[str, Any] = {}
    exec("from datetime import date", namespace, namespace)
    assert namespace["date"] is real
    # A module body that runs while the pin is active. Private name so the
    # process-wide pii module is left as it was.
    probe = types.ModuleType("_clock_probe")
    exec("from datetime import date", probe.__dict__)
    assert probe.__dict__["date"] is real
    import dms_core.pii as pii

    assert pii.date is real


def test_flag_on_52_keeps_served_answers_and_drops_no_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub spec agrees with each served SQL. Flag-on envelopes stay the golden bytes."""
    import yaml
    from dms_executor.intent_spec import (
        _limit_offset,
        _norm,
        _sql_direction,
        check_sql_against_spec,
        parse_spec_payload,
    )
    from dms_executor.sql_grounds import sql_grounds

    from tests.fixtures.ask_guide.capture_flag_off_52 import PACK

    monkeypatch.setenv("DMS_INTENT_SPEC", "1")
    _pin_capture_day(monkeypatch)
    live = replay_pack()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    left = dump_rows(_stable_rows(live))
    right = dump_rows(_stable_rows(golden))
    if left != right:
        main_rows = {row["id"]: row["env"] for row in golden}
        problems = [
            f"{row['id']}: {_diff_paths(_stable(row['env']), _stable(main_rows[row['id']]))}"
            for row in live
            if _diff_paths(_stable(row["env"]), _stable(main_rows[row["id"]]))
        ]
        pytest.fail("flag-on envelopes differ from f9ffc3e1:\n" + "\n".join(problems))
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    questions = {str(item["id"]): str(item["question"]) for item in pack["questions"]}
    checked = 0
    for row in golden:
        sql = str(row["env"].get("sql_used") or "").strip()
        if not sql:
            continue
        question = questions[row["id"]]
        grounds = sql_grounds(sql)
        span = [question]
        body: dict[str, Any] = {
            "measure": {"value": "measure", "spans": span},
            "grain": {"value": "grain", "spans": span},
        }
        direction, direction_unclear = _sql_direction(grounds)
        if direction and not direction_unclear:
            body["direction"] = {"value": direction, "spans": span}
        elif not grounds.order_by and not grounds.rank_windows and not grounds.rank_bounds:
            body["direction"] = {"value": "none", "spans": span}
        limit, offset, limit_unclear = _limit_offset(grounds)
        if not limit_unclear and limit is not None:
            body["n"] = {"value": limit, "spans": span}
        if not limit_unclear and offset:
            body["offset"] = {"value": offset, "spans": span}
        filters = []
        folded = _norm(question)
        for conjunct in grounds.conjuncts:
            for literal in conjunct.literals:
                if _norm(literal) and _norm(literal) in folded:
                    filters.append(
                        {"description": literal, "literal": literal, "spans": [literal]}
                    )
        body["filters"] = filters
        parsed = parse_spec_payload(
            {
                "intent_spec": body,
                "served_model": "spec-route",
                "served_provider": "spec-provider",
            },
            question,
            writer_payload={"served_model": "writer-route", "query_sql": sql},
        )
        assert parsed.unverified is None, row["id"]
        assert parsed.dropped == (), (row["id"], parsed.dropped)
        assert check_sql_against_spec(sql, parsed.spec) is None, row["id"]
        checked += 1
    assert checked > 0
