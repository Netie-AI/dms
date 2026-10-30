"""ACT-03: the UI's refusal-code fixture mirrors the ACT-01/02 server error map.

``apps/ui/src/lib/actions.test.ts`` pins the UI's ``REFUSAL_CODES`` to
``tests/fixtures/actions/act02_refusal_codes.json``. This test pins that fixture's
``route_codes`` to the codes the server source can actually emit, so a new
server refusal without an honest UI sentence fails here, not in front of a buyer.

Source scan only: no import of the routes, no Postgres.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "actions" / "act02_refusal_codes.json"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _server_codes() -> set[str]:
    codes: set[str] = set()
    routes = _read("apps/api/dms_api/routes/actions.py")
    codes |= set(re.findall(r'_refuse\(\s*\d{3},\s*"([a-z_]+)"', routes))
    for rel in (
        "packages/core/dms_core/control_plane/action_types.py",
        "packages/core/dms_core/control_plane/actions.py",
    ):
        text = _read(rel)
        codes |= set(re.findall(r'ActionSpecError\(\s*"([a-z_]+)"', text))
        codes |= set(re.findall(r'PreconditionFailed\(\s*"([a-z_]+)"', text))
        codes |= set(re.findall(r'^\s*code = "([a-z_]+)"', text, re.M))
        # approval_ref_problem returns its refusal code (ApprovalRefInvalid carries it).
        codes |= set(re.findall(r'return "(approval_ref_[a-z_]+)"', text))
    queries = _read("packages/core/dms_core/control_plane/action_queries.py")
    codes |= set(re.findall(r'EvidenceRefused\(\s*"([a-z_]+)"', queries))
    amend = _read("apps/api/dms_api/routes/amend.py")
    codes |= set(re.findall(r'detail="(action_[a-z_]+)"', amend))
    return codes


def _gate_reasons() -> set[str]:
    reasons = {"gate_denied"}  # gatekeeping.enforce's default
    gate = _read("packages/cortex_client/cortex_client/gate.py")
    reasons |= set(re.findall(r'reason="([a-z_]+)"', gate))
    keeping = _read("apps/api/dms_api/gatekeeping.py")
    block = re.search(r"NO_DECISION_REASONS = frozenset\(\{([^}]*)\}\)", keeping)
    assert block is not None
    reasons |= set(re.findall(r'"([a-z_]+)"', block.group(1)))
    return reasons


def test_fixture_route_codes_equal_the_server_error_map() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    scanned = _server_codes()
    assert len(scanned) >= 30, scanned  # the scan itself still finds the map
    assert set(fixture["route_codes"]) == scanned
    assert fixture["route_codes"] == sorted(fixture["route_codes"])


def test_fixture_covers_every_gate_reason() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _gate_reasons() <= set(fixture["gate_reasons"])
    # ACT-02's own gate-refusal test drives a Cortex reason of 'denied'.
    assert "denied" in fixture["gate_reasons"]
