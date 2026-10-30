"""Seeded VQ SQL must match the lake Chat executes, not Cortex pack YAML."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from seed_curated_vqs import ASSETS, schema_ok  # noqa: E402


def test_schema_ok_skips_missing_country(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE suppliers (supplier_id VARCHAR)")
    con.close()
    assert schema_ok(db, {"suppliers": ("country",)}) == "suppliers missing country"


def test_adapted_assets_declare_need() -> None:
    assert len(ASSETS) >= 7
    ids = {a["id"] for a in ASSETS}
    assert "ops_delayed_count" in ids
    assert "cq_stock_by_bin" in ids
    assert "cq_expired_items" in ids
    assert "cq_cold_storage" in ids
    low = next(a for a in ASSETS if a["id"] == "cq_low_stock_wh_a")
    assert "what is running low in warehouse A" in low["synonyms"]
    delayed = next(a for a in ASSETS if a["id"] == "ops_delayed_count")
    assert "DELAYED" not in delayed["sql"]
    assert "lower(status)" in delayed["sql"]
    for asset in ASSETS:
        assert asset["need"]
        assert "country" not in str(asset["sql"])
        assert "location_code" not in asset["sql"]
        assert "txn_type = 'OUT'" not in asset["sql"]
