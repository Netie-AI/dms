"""CONNECT-ASK-01: an empty Insights generation on a Space ask is a named gap.

When Cortex Insights answers a Space ask with nothing to plan from (empty output,
an empty ``query_sql``), the ask still falls through to the contract ask, which
also serves doc RAG. If that abstains too, the customer used to read the
engine's generic "no governed answer" prose. The envelope now names the gap:
``cortex_empty_generation``.

Every case posts ``POST /v1/chat/ask`` and asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows, values, audit receipt).
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from test_space_gen_01 import _reasons, _rig, minter  # noqa: F401 -- fixture

Q = "How many accounts are there?"
GENERIC = "no governed answer"

EMPTY_PAYLOADS: dict[str, dict[str, Any]] = {
    "empty_output": {},
    "empty_query_sql": {"query_sql": ""},
    "blank_query_sql": {"query_sql": "   ", "plan_source": "ontology_plan"},
    "generate_phase_no_sql": {
        "ok": True,
        "phase": "generate",
        "generative": {"ok": True, "sql": ""},
    },
}


def _post(rig: Any) -> dict[str, Any]:
    r = rig.client.post(
        "/v1/chat/ask",
        json={"question": Q, "session_id": "ses_empty_gen", "space_id": rig.space_id},
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env


@pytest.mark.parametrize("case", sorted(EMPTY_PAYLOADS))
def test_empty_generation_abstain_names_the_gap(
    tmp_path: Any, minter: ManifestMinter, case: str  # noqa: F811
) -> None:
    rig = _rig(tmp_path, minter, EMPTY_PAYLOADS[case])
    env = _post(rig)

    assert rig.cortex.insights, "generation was never reached"
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert env["contributing_sources"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    assert "gap: cortex_empty_generation" in text, text
    assert GENERIC not in text, text
    assert env["audit_receipt"]["unsure"]["why"] == "ABSTAIN reason: cortex_empty_generation"
    # The engine's own words stay in the audit trail, not the rendered answer.
    assert f"Cortex contract ask answer: Cortex refused: {GENERIC}" in _reasons(env)
    assert rig.cortex.executed == []


def test_named_cortex_refusal_is_not_relabelled_empty(
    tmp_path: Any, minter: ManifestMinter  # noqa: F811
) -> None:
    raw = "table secret_salary is not in the caller catalog"
    rig = _rig(tmp_path, minter, {"phase": "generate", "refuse_reason": raw})
    env = _post(rig)

    text = str(env.get("text") or "")
    assert "gap: cortex_refused:table_not_in_catalog" in text, text
    assert "cortex_empty_generation" not in text
    assert rig.cortex.asks == []


def test_doc_rag_answer_after_empty_generation_is_still_served(
    tmp_path: Any, minter: ManifestMinter  # noqa: F811
) -> None:
    rig = _rig(tmp_path, minter, {})
    mark = "Loan officer notes for this Space"

    def _doc_ask(req: AskRequest) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer=f"From the notes: {mark}",
            audit_id="aud_doc",
            route="doc_rag",
            provenance={"badge": "query_skill"},
            rows=[{"excerpt": mark}],
            drillthrough_token="dt_space_doc_rag",
            contributing_sources=[
                {"ref_id": "91cc8921-b320-4502-9218-4ad5f2cd8f21", "filename": "notes.pdf"}
            ],
        )

    rig.cortex.ask = _doc_ask  # type: ignore[method-assign]
    env = _post(rig)

    assert env["abstained"] is False
    assert mark in env["text"]
    assert "cortex_empty_generation" not in env["text"]
    assert env["rows"] == [{"excerpt": mark}]
    assert [s["ref_id"] for s in env["contributing_sources"]] == [
        "91cc8921-b320-4502-9218-4ad5f2cd8f21"
    ]
