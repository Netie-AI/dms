"""ACT-02 rule 12 candidates never carry a key value DMS would refuse as input.

``resolve_key`` answers a miss with near keys from the source so the steward can
resend the source encoding. Those values leave DMS in the 422 body and are shown
on screen, so a value the PII classifier or ``mask_text`` flags is withheld, the
same line the input side draws with ``key_value_invalid``. No skip, no xfail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_core.control_plane.action_types import ActionSpecError, validate_spec
from dms_executor import action_keys
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse

EMAIL = "ali.baba@example.com"
NRIC = "900101-14-5678"


def _warehouse(tmp_path: Path, *locations: str) -> Path:
    db = ensure_demo_warehouse(tmp_path / "act02_keys.duckdb")
    con = connect_file(db, write=True)
    try:
        for loc in locations:
            con.execute(
                "INSERT INTO inventory SELECT sku, $loc, quantity_kg, reorder_level_kg, "
                "unit_cost_myr, supplier_id, category, expiry_date FROM inventory "
                "WHERE sku = 'SKU-BETA' AND location_id = 'WH-B'",
                {"loc": loc},
            )
    finally:
        con.close()
    return db


def _resolve(db: Path, key: dict[str, str]) -> dict:
    return action_keys.resolve_key(
        "demo_warehouse",
        "inventory",
        ("sku", "location_id"),
        "reorder_level_kg",
        key,
        path=db,
    )


def test_pii_key_values_are_not_offered_as_candidates(tmp_path: Path) -> None:
    db = _warehouse(tmp_path, EMAIL, NRIC)
    out = _resolve(db, {"sku": "SKU-BETA", "location_id": "WH-ZZ"})
    assert out == {
        "status": "not_found",
        "candidates": [{"sku": "SKU-BETA", "location_id": "WH-B"}],
    }
    shown = repr(out)
    assert EMAIL not in shown and NRIC not in shown


def test_withheld_value_is_one_the_input_side_refuses(tmp_path: Path) -> None:
    # The line is the same on both sides: what is withheld here is refused there.
    for loc in (EMAIL, NRIC):
        with pytest.raises(ActionSpecError) as info:
            validate_spec(
                {
                    "action_type": "inventory.set_reorder_level",
                    "object_key": {"sku": "SKU-BETA", "location_id": loc},
                    "value": "450",
                    "op": "set",
                    "reason": "lead time doubled",
                }
            )
        assert info.value.code == "key_value_invalid"


def test_pii_rows_do_not_crowd_out_clean_candidates(tmp_path: Path) -> None:
    # More flagged keys than CANDIDATE_LIMIT still leave the clean ones visible.
    flagged = [f"ali{i}.baba@example.com" for i in range(action_keys.CANDIDATE_LIMIT + 2)]
    db = _warehouse(tmp_path, *flagged)
    out = _resolve(db, {"sku": "SKU-BETA", "location_id": "WH-ZZ"})
    assert out["status"] == "not_found"
    assert out["candidates"] == [{"sku": "SKU-BETA", "location_id": "WH-B"}]
    assert "@" not in repr(out)


def test_clean_candidates_unchanged(tmp_path: Path) -> None:
    db = _warehouse(tmp_path)
    out = _resolve(db, {"sku": "BETA", "location_id": "WH-B"})
    assert out["status"] == "not_found"
    assert out["candidates"][0] == {"sku": "SKU-BETA", "location_id": "WH-B"}
    assert len(out["candidates"]) <= action_keys.CANDIDATE_LIMIT


def test_pii_named_key_column_offers_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A key column the classifier names as PII returns nothing, whatever its values.
    db = _warehouse(tmp_path)
    monkeypatch.setattr(
        action_keys, "column_is_pii", lambda col, values=(), *, table=None: col == "location_id"
    )
    out = _resolve(db, {"sku": "SKU-BETA", "location_id": "WH-ZZ"})
    assert out == {"status": "not_found", "candidates": []}
