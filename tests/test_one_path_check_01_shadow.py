"""ONE-PATH-CHECK-01: flag-off envelopes stay the f9ffc3e1 bytes.

The 52-question replay is the existing flag-off fixture
(``tests/fixtures/ask_guide``). ``as_of`` is the wall clock, so both sides
replace it. The only other allowed difference is ``served_check_shadow``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures.ask_guide.capture_flag_off_52 import HERE, dump_rows, replay_pack

_AS_OF = "<as_of>"
_SHADOW = "served_check_shadow"
GOLDEN = HERE / "flag_off_52_f9ffc3e1.json"


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    out.pop(_SHADOW, None)
    if "as_of" in out:
        out["as_of"] = _AS_OF
    return out


def _stable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": row["id"], "env": _stable(row["env"])} for row in rows]


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


def test_flag_off_envelopes_match_f9ffc3e1_except_shadow() -> None:
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
