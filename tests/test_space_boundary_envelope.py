"""The Space boundary as the customer sees it (DR-0002 Confirmation, dms#2).

``test_space_acl_boundary.py`` asserts the minted ACL. That is an intermediate
artifact, and CLAUDE.md §10/§10a require the assertion to land on the DMS
envelope from ``POST /v1/chat/ask`` instead: badge, ``abstained``, and the
refusal reason are what a viewer actually reads.

DR-0002 chose the grant split so the demo shows **both halves** of the control.
A boundary that only ever refuses looks broken; the pair below is the thing
worth showing:

  - "What was our revenue last month?"  answers in Finance, abstains in Warehouse Ops
  - "Where is SKU-00397 stored?"        answers in both (locations/inventory are shared)

**Scope of this gate.** The fake below enforces the manifest the way Cortex
does - it refuses a question whose table is not in the row_predicates DMS
minted. That makes this a real assertion about *DMS's* half: the manifest DMS
mints and the envelope DMS renders from a refusal. It is necessary and *not*
sufficient (R-0001): that the real engine refuses the same SQL is asserted by
the live-stack demo verification, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.manifest import ManifestMinter, SessionAcl

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"

REVENUE = "What was our revenue last month?"
WHERE_IS_SKU = "Where is SKU-00397 stored?"

#: Which table each demo question cannot be answered without.
_NEEDS = {REVENUE: "transactions", WHERE_IS_SKU: "locations"}


@dataclass
class ManifestEnforcingCortex:
    """A Cortex that actually honours the manifest it was bound with.

    A fake that answers everything would let this suite certify the boundary as
    working while the manifest was still wide open - the exact failure mode that
    let dms#2 ship.
    """

    submits: list[Any] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)
    _bound: dict[str, set[str]] = field(default_factory=dict)

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        self._bound[req.manifest.session_id] = set(req.manifest.row_predicates)
        return QueryResult(ok=True, status="bound", run_id="run-1")

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        readable = self._bound.get(req.session_id, set())
        needed = _NEEDS[req.question]
        if needed not in readable:
            return AskResponse(
                answer=(
                    f"I cannot answer that here - this Space has no access to "
                    f"{needed!r}. Ask in a Space that does, or request the grant."
                ),
                abstained=True,
                badge="abstain",
                rows=[],
                route="refused",
                audit_id="aud-refused",
            )
        return AskResponse(
            answer="RM 128,400.00 across 412 outbound movements.",
            abstained=False,
            badge="certified",
            sql_used=f"SELECT * FROM {needed}",
            rows=[{"revenue_myr": 128400.0}],
            audit_id="aud-ok",
            route="sql",
        )


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    """Mint without OpenVault; the signature is not what is under test here."""
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-08-02T00:00:00+00:00",
            expires_at="2026-08-02T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _ask(minter: ManifestMinter, question: str, *, space_id: str) -> dict[str, Any]:
    exe = Executor(cortex=ManifestEnforcingCortex(), minter=minter)  # type: ignore[arg-type]
    return exe.live_ask(question, space_id=space_id, session_id=f"ses_{space_id[:8]}")


def test_the_sensitive_question_answers_in_finance(minter: ManifestMinter) -> None:
    env = _ask(minter, REVENUE, space_id=FINANCE)

    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"], "an answering Space must return rows"


def test_the_same_question_abstains_in_warehouse_ops(minter: ManifestMinter) -> None:
    """WHEN Space A asks a question whose answer requires a table granted only to
    Space B THE SYSTEM SHALL return abstained: true with a refusal reason."""
    env = _ask(minter, REVENUE, space_id=WAREHOUSE_OPS)

    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN", "a green badge over a refusal is a P0"
    assert not env["rows"]
    # The refusal has to say what to do next, not just decline (R-0005).
    assert "transactions" in env["text"]


def test_the_operational_question_answers_in_both_spaces(minter: ManifestMinter) -> None:
    """The other half of the control. A boundary that only refuses looks broken."""
    for space in (FINANCE, WAREHOUSE_OPS):
        env = _ask(minter, WHERE_IS_SKU, space_id=space)
        assert env["abstained"] is False, f"{space} wrongly refused a shared table"
        assert env["badge"] == "L0_CERTIFIED"


def test_the_two_spaces_are_not_handed_the_same_session(minter: ManifestMinter) -> None:
    """Distinct manifests must not share a bound session id, or the second Space
    is served under whichever manifest was bound first."""
    fin = Executor(cortex=ManifestEnforcingCortex(), minter=minter).demo_acl(  # type: ignore[arg-type]
        session_id="ses_shared", space_id=FINANCE
    )
    ops = Executor(cortex=ManifestEnforcingCortex(), minter=minter).demo_acl(  # type: ignore[arg-type]
        session_id="ses_shared", space_id=WAREHOUSE_OPS
    )

    assert fin.row_predicates != ops.row_predicates
    assert fin.session_id != ops.session_id


def test_switching_space_mid_chat_does_not_reuse_the_first_binding(
    minter: ManifestMinter,
) -> None:
    """The serving-path version of the leak above, and the one a viewer can drive.

    One chat, one session id, Finance first. Asking the same question again after
    switching to Warehouse Ops must abstain. While the bound-session cache keyed
    on the caller's session id alone, the second ask reused the Finance binding
    and answered - the boundary held at mint time and leaked at bind time.
    """
    exe = Executor(cortex=ManifestEnforcingCortex(), minter=minter)  # type: ignore[arg-type]

    in_finance = exe.live_ask(REVENUE, space_id=FINANCE, session_id="ses_one_chat")
    then_in_ops = exe.live_ask(REVENUE, space_id=WAREHOUSE_OPS, session_id="ses_one_chat")

    assert in_finance["abstained"] is False
    assert then_in_ops["abstained"] is True, (
        "Warehouse Ops was served under the manifest Finance bound first"
    )
    assert then_in_ops["badge"] == "ABSTAIN"
