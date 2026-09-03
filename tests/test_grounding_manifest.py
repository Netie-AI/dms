"""Grounding mints exactly what was chosen, or refuses (P0-DEMO-03, dms#5).

Ticking one uploaded table in Studio called ``demo_acl``, which kept only names
in ``DEMO_TABLES``. An uploaded bronze table is never in that set, so the
selection emptied - and an empty selection fell back to the full demo warehouse.
The UI read "Grounded in 1 file" over a manifest holding all six demo tables,
and the answer then came from data the scope never included.

Two lies compounding, so both halves are asserted here: the manifest contains
exactly the selection, and the count the viewer reads comes from the manifest
rather than from the request that asked for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.ask import GroundingRefused
from dms_executor import Executor
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.manifest import ManifestMinter, SessionAcl

UPLOAD = "bronze.q3_sales_export_Q3"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@dataclass
class RecordingCortex:
    """Keeps every manifest it was bound with, so the test can inspect the real one."""

    submits: list[Any] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)
    manifests: list[Manifest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        self.manifests.append(req.manifest)
        return QueryResult(ok=True, status="bound", run_id="run-1")

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="4 rows across 3 SKUs.",
            abstained=False,
            badge="certified",
            sql_used=f"SELECT * FROM {UPLOAD}",
            rows=[{"sku": "SKU-00397", "units_sold": 12}],
            audit_id="aud-1",
            route="sql",
        )


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
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


def _executor(minter: ManifestMinter, cortex: Any, *, uploads: tuple[str, ...] = (UPLOAD,)):
    """An Executor whose warehouse holds exactly ``uploads`` as bronze tables."""
    return Executor(
        cortex=cortex,
        minter=minter,
        session_store=DemoSessionStore(uploads=lambda: uploads),
    )


def test_grounding_on_an_upload_mints_only_that_table(minter: ManifestMinter) -> None:
    """The reported defect: one ticked upload granted the whole demo warehouse."""
    acl = _executor(minter, None).demo_acl(session_id="ses", tables=[UPLOAD])

    assert set(acl.row_predicates) == {UPLOAD}, (
        "the manifest must hold exactly the selection, not the demo warehouse"
    )


def test_the_ui_count_equals_the_minted_manifest_length(minter: ManifestMinter) -> None:
    """WHEN a user grounds a question on a specific set of sources THE SYSTEM
    SHALL mint a manifest containing exactly those sources, and the count shown
    in the UI SHALL equal the number of tables in the minted manifest."""
    cortex = RecordingCortex()
    env = _executor(minter, cortex).live_ask(
        "How many units did we sell?", session_id="ses", tables=[UPLOAD]
    )

    minted = cortex.manifests[-1]
    # The count a viewer reads, asserted against the manifest and not the request.
    assert len(env["grounded_tables"]) == len(minted.row_predicates)
    assert env["grounded_tables"] == [UPLOAD]
    assert set(minted.row_predicates) == {UPLOAD}
    sent = cortex.asks[-1].question
    assert UPLOAD in sent, "Cortex ask must name the ticked table (AskRequest has no tables field)"
    assert sent.startswith("Using only ")


def test_an_ungrantable_selection_refuses_instead_of_widening(
    minter: ManifestMinter,
) -> None:
    """Refuse rather than widen, and name the source that could not be granted."""
    exe = _executor(minter, RecordingCortex(), uploads=())

    with pytest.raises(GroundingRefused) as caught:
        exe.demo_acl(session_id="ses", tables=["bronze.never_ingested"])

    assert caught.value.ungrantable == ["bronze.never_ingested"]
    # The refusal has to say what to do next, not merely decline (R-0005).
    assert "bronze.never_ingested" in caught.value.message
    assert "Space" in caught.value.message


def test_a_partly_grantable_selection_refuses_the_whole_ask(
    minter: ManifestMinter,
) -> None:
    """Answering from the grantable subset would misreport the scope just as badly."""
    exe = _executor(minter, None)

    with pytest.raises(GroundingRefused) as caught:
        exe.demo_acl(session_id="ses", tables=[UPLOAD, "bronze.not_mine"])

    assert caught.value.ungrantable == ["bronze.not_mine"]


def test_no_selection_still_means_no_narrowing(minter: ManifestMinter) -> None:
    """R-0005: "ground in nothing" is not "ground in something I cannot have"."""
    acl = _executor(minter, None).demo_acl(session_id="ses", tables=[])

    assert len(acl.row_predicates) > 1, "an empty selection must not narrow to nothing"
    assert "transactions" in acl.row_predicates


def test_grounding_inside_a_space_cannot_reach_past_the_space(
    minter: ManifestMinter,
) -> None:
    """Grounding narrows within the Space's grants; it never widens past them.

    Warehouse Ops has no ``transactions`` grant, so ticking it is refused rather
    than silently honoured because the user asked for it explicitly.
    """
    exe = _executor(minter, None)
    ops = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    with pytest.raises(GroundingRefused):
        exe.demo_acl(session_id="ses", space_id=ops, tables=["transactions"])

    # ...and the same tick inside Finance, which does hold it, is honoured.
    acl = exe.demo_acl(session_id="ses", space_id=FINANCE, tables=["transactions"])
    assert set(acl.row_predicates) == {"transactions"}


def test_the_refusal_reaches_the_customer_over_http(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0001: assert the artifact the customer receives, at the layer they get it.

    A refusal that only exists as a Python exception would surface as a 500 and
    read as a crash rather than as the scope working.
    """
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = RecordingCortex()
    app = create_app()
    app.state.ask_service = _executor(minter, cortex, uploads=())
    app.state.cortex = cortex
    client = TestClient(app)

    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "How many units?",
            "session_id": "ses_http",
            "grounded_tables": ["bronze.never_ingested"],
        },
    )

    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "grounding_not_grantable"
    assert detail["ungrantable_tables"] == ["bronze.never_ingested"]
    assert "bronze.never_ingested" in detail["message"]
    # Refusing must not have quietly asked anyway.
    assert not cortex.asks, "the ask was sent despite the scope being refused"

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()


def test_an_upload_is_grantable_as_soon_as_it_lands(tmp_path: Path) -> None:
    """Ingest registers the bronze table, so it is groundable without a restart.

    The store reads the registry at call time; a store that snapshotted at
    construction would refuse a file uploaded during the session.
    """
    from dms_executor.bronze import ingest_csv_bytes

    wh = tmp_path / "wh.duckdb"
    exe = Executor(cortex=None, warehouse_path=wh)

    assert "bronze.late_upload" not in exe.grantable_tables(space_id=None)

    ingest_csv_bytes(
        filename="late_upload.csv", data=b"sku,qty\nSKU-A,1\n", path=wh
    )

    assert "bronze.late_upload" in exe.grantable_tables(space_id=None)
