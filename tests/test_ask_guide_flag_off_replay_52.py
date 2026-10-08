"""52 pack questions, flag off, byte-compared to origin/main.

``MASKED_FIELDS`` is the only rewrite. A second replay of this checkout may
differ from the first only on those exact top-level keys. Any other path
fails. There is no recursive normalizer.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures.ask_guide.capture_flag_off_52 import (
    HERE,
    dump_rows,
    replay_pack,
)

# Wall-clock stamp on the envelope. Two replays of this stub differed only here.
# run_id, session_id, and trace/latency fields did not move, so they are not listed.
MASKED_FIELDS = ("as_of",)

GOLDEN = HERE / "flag_off_52_main.json"


def _mask(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    for key in MASKED_FIELDS:
        if key in out:
            out[key] = None
    return out


def _masked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": row["id"], "env": _mask(row["env"])} for row in rows]


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


def test_flag_off_52_replay_matches_main_after_mask() -> None:
    """DMS_ASK_CLARIFY unset. 52/52 byte-equal to the post-#394 main capture."""
    live = replay_pack()
    again = replay_pack()
    assert [row["id"] for row in live] == [row["id"] for row in again]
    stray: list[str] = []
    for left, right in zip(live, again, strict=True):
        for path in _diff_paths(left["env"], right["env"]):
            if path not in MASKED_FIELDS:
                stray.append(f"{left['id']}.{path}")
    assert stray == [], stray
    for row in live:
        assert "clarify" not in row["env"]
        assert row["env"].get("clarify", "absent") == "absent"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(golden) == 52
    left = dump_rows(_masked(live))
    right = dump_rows(_masked(golden))
    if left != right:
        main_rows = {row["id"]: row["env"] for row in golden}
        problems: list[str] = []
        for row in live:
            paths = _diff_paths(_mask(row["env"]), _mask(main_rows[row["id"]]))
            if paths:
                problems.append(f"{row['id']}: {paths}")
        pytest.fail("flag-off envelopes differ from main:\n" + "\n".join(problems))
