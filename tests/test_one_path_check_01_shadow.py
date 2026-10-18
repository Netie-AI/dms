"""ONE-PATH-CHECK-01: flag-off envelopes stay the f9ffc3e1 bytes.

The 52-question replay is the existing flag-off fixture
(``tests/fixtures/ask_guide``). ``as_of`` is the wall clock, so both sides
replace it. The only other allowed difference is ``served_check_shadow``,
except two constant ``SELECT 1`` answers. The grant check refuses
those directly. The reply does not name a table.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from tests.fixtures.ask_guide.capture_flag_off_52 import HERE, replay_pack

_AS_OF = "<as_of>"
_SHADOW = "served_check_shadow"
# Capture day of flag_off_52_f9ffc3e1.json. semantic_retrieve uses
# date.today() - 90 days, which was 2026-07-10 on this day.
_CAPTURE_DAY = (2026, 10, 8)
GOLDEN = HERE / "flag_off_52_f9ffc3e1.json"
# Constant selects. They are not value-correct answers.
_UNGROUNDED = frozenset({"trap_alerts_ungranted", "trap_high_risk_pending"})


def _pin_capture_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin date.today() to the capture day.

    typed_filters imports date inside the function, and datetime.date is
    immutable, so the class on the datetime module is swapped. A later
    system clock still yields the golden literal 2026-07-10.
    """
    base = dt.date

    class _CaptureDate(base):
        @classmethod
        def today(cls) -> dt.date:
            return base(*_CAPTURE_DAY)

    monkeypatch.setattr(dt, "date", _CaptureDate)


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    out.pop(_SHADOW, None)
    if "as_of" in out:
        out["as_of"] = _AS_OF
    return out


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
    main_rows = {row["id"]: row["env"] for row in golden}
    problems: list[str] = []
    for row in live:
        if row["id"] in _UNGROUNDED:
            env = row["env"]
            assert env.get("abstained") is True, row["id"]
            assert env.get("badge") != "L2_VALIDATED"
            assert env.get("badge") != "L0_CERTIFIED"
            notes = " ".join(str(a) for a in (env.get("assumptions") or []))
            blob = f"{env.get('text') or ''} {notes}"
            assert "ungranted" in notes, row["id"]
            assert "reconfirm" not in blob, row["id"]
            continue
        paths = _diff_paths(_stable(row["env"]), _stable(main_rows[row["id"]]))
        if paths:
            problems.append(f"{row['id']}: {paths}")
    if problems:
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
