"""Industry fixture benches: warehouse, banking, insurance, storage.

No Docker, no AdventureWorks, no packs/insurance mill (F96).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "executor"))

import industry_ontology_bench as industry  # noqa: E402
from dms_executor.ontology import Ontology  # noqa: E402


@pytest.mark.parametrize("family", industry.FAMILIES)
def test_industry_family_agrees_with_oracle(family: str, tmp_path: Path) -> None:
    report = industry.run_family(family, tmp_path)
    assert "error" not in report, report.get("error")
    assert report["failed"] == 0, report.get("failures")
    assert report["passed"] > 0
    assert industry._ok(report), report.get("self_check")
    by = {int(d): v for d, v in (report.get("by_depth") or {}).items()}
    assert 2 in by, f"{family} generated no two-hop case: {report.get('by_depth')}"
    assert by[2]["group"] >= 1, f"{family} two-hop was not grouped: {by[2]}"


def test_insurance_three_hop_is_sampled(tmp_path: Path) -> None:
    report = industry.run_family("insurance", tmp_path)
    by = {int(d): v for d, v in (report.get("by_depth") or {}).items()}
    assert 3 in by, f"payment -> claim -> policy -> party missing: {report.get('by_depth')}"
    assert by[3]["passed"] >= 1


def test_cli_all_families_exit_0(tmp_path: Path) -> None:
    lake = tmp_path / "lakes"
    out = tmp_path / "industry_bench.json"
    assert industry.main(["--lake", str(lake), "--json", str(out)]) == 0
    payload = out.read_text(encoding="utf-8")
    assert "WarehouseDemo" in payload
    assert "BankingDemo" in payload
    assert "InsuranceDemo" in payload
    assert "StorageDemo" in payload


def test_lying_insurance_claim_key_fails_verify() -> None:
    """A claim keyed on policy_id is the 1:N grain lie NEXT_COMPANY names."""
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE claims (claim_id VARCHAR, policy_id VARCHAR, amount DOUBLE)")
    con.execute(
        "INSERT INTO claims VALUES "
        "('C1','POL1',100),('C2','POL1',20),('C3','POL2',40)"
    )
    o = Ontology()
    o.add_object("claim", "claims", ["policy_id"])
    o.add_measure("incurred", "claim", "SUM(f.amount)")
    violations = o.verify(con)
    con.close()
    assert any(v.check == "key_unique" for v in violations), violations


def test_storage_lot_grouped_by_zone_conserves() -> None:
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE lots (lot_id VARCHAR, bin_id VARCHAR, value DOUBLE)")
    con.execute("CREATE TABLE bins (bin_id VARCHAR, zone VARCHAR)")
    con.execute(
        "INSERT INTO lots VALUES "
        "('LOT1','BIN-1',100),('LOT2','BIN-1',50),('LOT3','BIN-2',40)"
    )
    con.execute("INSERT INTO bins VALUES ('BIN-1','COLD'),('BIN-2','DRY')")
    o = Ontology()
    o.add_object("lot", "lots", ["lot_id"])
    o.add_object("bin", "bins", ["bin_id"])
    o.add_link("lot_in_bin", "lot", ["bin_id"], "bin", ["bin_id"])
    o.add_measure("stock_value", "lot", "SUM(f.value)")
    assert o.verify(con) == []
    compiled = o.compile("stock_value", group_by=[("bin", "zone")])
    assert compiled
    rows = con.execute(compiled.sql).fetchall()
    total = sum(float(r[-1]) for r in rows)
    raw = con.execute("SELECT SUM(value) FROM lots").fetchone()[0]
    con.close()
    assert abs(total - float(raw)) < 1e-9
