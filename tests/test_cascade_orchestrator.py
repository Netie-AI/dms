"""CCA-05 — cascade orchestrator before unverified L0."""

from __future__ import annotations

from dms_executor.cascade_orchestrator import (
    cascade_allows_l0,
    run_constraint_cascade,
)


def test_ordinary_sku_ask_skips_cascade() -> None:
    applies, trace = run_constraint_cascade("Top 5 selling SKUs by revenue")
    assert applies is False
    assert trace == []


def test_sea_commercial_rental_abstains_without_inventing_pack() -> None:
    q = "rental across SEA, commercial only, ignore residential"
    applies, trace = run_constraint_cascade(q)
    assert applies is True
    assert cascade_allows_l0(trace) is False
    assert any(c["status"] == "ABSTAIN" for c in trace)


def test_full_cascade_happy_with_landed_packs() -> None:
    q = "rental across SEA, commercial only, ignore residential"
    applies, trace = run_constraint_cascade(
        q,
        class_encodings={"commercial": ("COMMERCIAL",), "residential": ("RESIDENTIAL",)},
        landed_class_dim=("COMMERCIAL", "RESIDENTIAL"),
        region_members={"SEA": ("MY",)},
        landed_geo_dim=("MY", "SG"),
    )
    assert applies is True
    assert cascade_allows_l0(trace) is True
    types = [c["type"] for c in trace]
    assert types == ["sense", "asset_class", "geo"]
    assert all(c["status"] == "CERTIFIED" for c in trace)
