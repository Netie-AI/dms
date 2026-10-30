"""Generated ontology benches for four industry families.

Warehouse / banking / insurance / storage are the same compiler. This does
not mint packs/insurance or packs/banking (F96/F93). Fixtures live under
the given lake root so pytest can run with no AdventureWorks and no Docker.

  python scripts/industry_ontology_bench.py
  python scripts/industry_ontology_bench.py --family insurance

Exit 0 only when every family has failed==0, passed>0, and the
corrupted-link self-check caught.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _rel in (
    "scripts",
    "apps/api",
    "packages/core",
    "packages/cortex_client",
    "packages/executor",
    "packages/ledger",
):
    sys.path.insert(0, str(ROOT / _rel))

import ontology_bench as bench  # noqa: E402

FAMILIES = ("warehouse", "banking", "insurance", "storage")


def _parquet(con: Any, path: Path, columns: str, values_sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        "COPY (SELECT * FROM (VALUES "
        + values_sql
        + f") AS t({columns})) TO '{path.as_posix()}' (FORMAT PARQUET)"
    )


def _manifest(
    database: str,
    tables: list[dict[str, Any]],
    pks: dict[str, list[str]],
    fks: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "database": database,
        "tables": tables,
        "skipped": [],
        "primary_keys": pks,
        "foreign_keys": fks,
    }


def _tbl(schema: str, table: str, rows: int, cols: int, rel: str) -> dict[str, Any]:
    return {
        "schema": schema,
        "table": table,
        "declared_rows": rows,
        "extracted_rows": rows,
        "columns": cols,
        "path": rel,
    }


def _fk(name: str, child: str, from_col: str, parent: str, to_col: str) -> dict[str, str]:
    return {
        "name": name,
        "from_table": child,
        "from_column": from_col,
        "to_table": parent,
        "to_column": to_col,
    }


def build_warehouse(con: Any, lake: Path) -> dict[str, Any]:
    db = lake / "warehouse"
    _parquet(
        con,
        db / "W.Region.parquet",
        "region_id, label",
        "('R1', 'North'), ('R2', 'South')",
    )
    _parquet(
        con,
        db / "W.Location.parquet",
        "loc_id, loc_code, city, region_id",
        "('L1', 'WH-A', 'KL', 'R1'), ('L2', 'WH-B', 'JB', 'R2')",
    )
    _parquet(
        con,
        db / "W.Product.parquet",
        "sku, category",
        "('SKU-1', 'FOOD'), ('SKU-2', 'ELEC')",
    )
    _parquet(
        con,
        db / "W.Supplier.parquet",
        "sup_id, country",
        "('S1', 'MY'), ('S2', 'SG')",
    )
    _parquet(
        con,
        db / "W.Orders.parquet",
        "order_id, sku, loc_id, amount",
        "('O1', 'SKU-1', 'L1', 100.0), ('O2', 'SKU-1', 'L1', 50.0), "
        "('O3', 'SKU-2', 'L2', 25.0), ('O4', 'SKU-2', 'L2', 7.0)",
    )
    _parquet(
        con,
        db / "W.Shipment.parquet",
        "ship_id, sku, sup_id, dest_id, cost",
        "('H1', 'SKU-1', 'S1', 'L1', 10.0), ('H2', 'SKU-2', 'S2', 'L2', 8.0)",
    )
    return _manifest(
        "WarehouseDemo",
        [
            _tbl("W", "Orders", 4, 4, "warehouse/W.Orders.parquet"),
            _tbl("W", "Product", 2, 2, "warehouse/W.Product.parquet"),
            _tbl("W", "Location", 2, 4, "warehouse/W.Location.parquet"),
            _tbl("W", "Region", 2, 2, "warehouse/W.Region.parquet"),
            _tbl("W", "Supplier", 2, 2, "warehouse/W.Supplier.parquet"),
            _tbl("W", "Shipment", 2, 5, "warehouse/W.Shipment.parquet"),
        ],
        {
            "W.Orders": ["order_id"],
            "W.Product": ["sku"],
            "W.Location": ["loc_id"],
            "W.Region": ["region_id"],
            "W.Supplier": ["sup_id"],
            "W.Shipment": ["ship_id"],
        },
        [
            _fk("FK_O_P", "W.Orders", "sku", "W.Product", "sku"),
            _fk("FK_O_L", "W.Orders", "loc_id", "W.Location", "loc_id"),
            _fk("FK_L_R", "W.Location", "region_id", "W.Region", "region_id"),
            _fk("FK_H_P", "W.Shipment", "sku", "W.Product", "sku"),
            _fk("FK_H_S", "W.Shipment", "sup_id", "W.Supplier", "sup_id"),
            _fk("FK_H_L", "W.Shipment", "dest_id", "W.Location", "loc_id"),
        ],
    )


def build_banking(con: Any, lake: Path) -> dict[str, Any]:
    db = lake / "banking"
    _parquet(
        con,
        db / "B.Party.parquet",
        "party_id, segment",
        "('P1', 'RETAIL'), ('P2', 'SME')",
    )
    _parquet(
        con,
        db / "B.Branch.parquet",
        "branch_id, city",
        "('B1', 'KL'), ('B2', 'JB')",
    )
    _parquet(
        con,
        db / "B.Account.parquet",
        "account_id, party_id, branch_id, product",
        "('A1', 'P1', 'B1', 'SAVINGS'), ('A2', 'P2', 'B2', 'CURRENT')",
    )
    _parquet(
        con,
        db / "B.Txn.parquet",
        "txn_id, account_id, amount",
        "('T1', 'A1', 100.0), ('T2', 'A1', 50.0), "
        "('T3', 'A2', 25.0), ('T4', 'A2', 10.0)",
    )
    _parquet(
        con,
        db / "B.Loan.parquet",
        "loan_id, party_id, principal",
        "('LN1', 'P1', 1000.0), ('LN2', 'P2', 500.0)",
    )
    return _manifest(
        "BankingDemo",
        [
            _tbl("B", "Txn", 4, 3, "banking/B.Txn.parquet"),
            _tbl("B", "Account", 2, 4, "banking/B.Account.parquet"),
            _tbl("B", "Party", 2, 2, "banking/B.Party.parquet"),
            _tbl("B", "Branch", 2, 2, "banking/B.Branch.parquet"),
            _tbl("B", "Loan", 2, 3, "banking/B.Loan.parquet"),
        ],
        {
            "B.Txn": ["txn_id"],
            "B.Account": ["account_id"],
            "B.Party": ["party_id"],
            "B.Branch": ["branch_id"],
            "B.Loan": ["loan_id"],
        },
        [
            _fk("FK_T_A", "B.Txn", "account_id", "B.Account", "account_id"),
            _fk("FK_A_P", "B.Account", "party_id", "B.Party", "party_id"),
            _fk("FK_A_B", "B.Account", "branch_id", "B.Branch", "branch_id"),
            _fk("FK_L_P", "B.Loan", "party_id", "B.Party", "party_id"),
        ],
    )


def build_insurance(con: Any, lake: Path) -> dict[str, Any]:
    db = lake / "insurance"
    _parquet(
        con,
        db / "I.Party.parquet",
        "party_id, segment",
        "('P1', 'RETAIL'), ('P2', 'SME')",
    )
    _parquet(
        con,
        db / "I.Policy.parquet",
        "policy_id, party_id, line",
        "('POL1', 'P1', 'MOTOR'), ('POL2', 'P2', 'FIRE')",
    )
    _parquet(
        con,
        db / "I.Claim.parquet",
        "claim_id, policy_id, status",
        "('C1', 'POL1', 'OPEN'), ('C2', 'POL1', 'CLOSED'), ('C3', 'POL2', 'OPEN')",
    )
    _parquet(
        con,
        db / "I.Payment.parquet",
        "payment_id, claim_id, amount",
        "('PAY1', 'C1', 100.0), ('PAY2', 'C1', 20.0), "
        "('PAY3', 'C2', 30.0), ('PAY4', 'C3', 40.0)",
    )
    return _manifest(
        "InsuranceDemo",
        [
            _tbl("I", "Payment", 4, 3, "insurance/I.Payment.parquet"),
            _tbl("I", "Claim", 3, 3, "insurance/I.Claim.parquet"),
            _tbl("I", "Policy", 2, 3, "insurance/I.Policy.parquet"),
            _tbl("I", "Party", 2, 2, "insurance/I.Party.parquet"),
        ],
        {
            "I.Payment": ["payment_id"],
            "I.Claim": ["claim_id"],
            "I.Policy": ["policy_id"],
            "I.Party": ["party_id"],
        },
        [
            _fk("FK_PAY_C", "I.Payment", "claim_id", "I.Claim", "claim_id"),
            _fk("FK_C_POL", "I.Claim", "policy_id", "I.Policy", "policy_id"),
            _fk("FK_POL_P", "I.Policy", "party_id", "I.Party", "party_id"),
        ],
    )


def build_storage(con: Any, lake: Path) -> dict[str, Any]:
    db = lake / "storage"
    _parquet(
        con,
        db / "S.Location.parquet",
        "loc_id, loc_code, city",
        "('L1', 'WH-A', 'KL'), ('L2', 'WH-B', 'JB')",
    )
    _parquet(
        con,
        db / "S.Bin.parquet",
        "bin_id, loc_id, zone",
        "('BIN-1', 'L1', 'COLD'), ('BIN-2', 'L1', 'DRY'), ('BIN-3', 'L2', 'DRY')",
    )
    _parquet(
        con,
        db / "S.Lot.parquet",
        "lot_id, bin_id, qty, value",
        "('LOT1', 'BIN-1', 10.0, 100.0), ('LOT2', 'BIN-1', 5.0, 50.0), "
        "('LOT3', 'BIN-2', 8.0, 40.0), ('LOT4', 'BIN-3', 12.0, 60.0)",
    )
    return _manifest(
        "StorageDemo",
        [
            _tbl("S", "Lot", 4, 4, "storage/S.Lot.parquet"),
            _tbl("S", "Bin", 3, 3, "storage/S.Bin.parquet"),
            _tbl("S", "Location", 2, 3, "storage/S.Location.parquet"),
        ],
        {
            "S.Lot": ["lot_id"],
            "S.Bin": ["bin_id"],
            "S.Location": ["loc_id"],
        },
        [
            _fk("FK_LOT_B", "S.Lot", "bin_id", "S.Bin", "bin_id"),
            _fk("FK_B_L", "S.Bin", "loc_id", "S.Location", "loc_id"),
        ],
    )


BUILDERS = {
    "warehouse": build_warehouse,
    "banking": build_banking,
    "insurance": build_insurance,
    "storage": build_storage,
}


def run_family(name: str, lake: Path, *, max_cases: int = 200) -> dict[str, Any]:
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        entry = BUILDERS[name](con, lake)
        report = bench.generate_and_run(
            con, entry, max_cases=max_cases, lake_root=lake
        )
        report["family"] = name
        return report
    finally:
        con.close()


def _ok(report: dict[str, Any]) -> bool:
    if report.get("error"):
        return False
    sc = report.get("self_check") or {}
    return (
        int(report.get("failed") or 0) == 0
        and int(report.get("refusal_failures") or 0) == 0
        and int(report.get("passed") or 0) > 0
        and bool(sc.get("caught"))
    )


def summarize(report: dict[str, Any]) -> str:
    by = report.get("by_depth") or {}
    hops = ",".join(f"{d}={by[d].get('passed', 0)}" for d in sorted(by, key=int))
    err = report.get("error") or ""
    return (
        f"{report.get('family')}\tpass={report.get('passed')}\t"
        f"fail={report.get('failed')}\tgaps={report.get('known_gaps')}\t"
        f"shapes={report.get('n_shapes')}\thops={hops}\t"
        f"{'ERROR ' + err if err else ('OK' if _ok(report) else 'FAIL')}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--family", choices=FAMILIES, action="append")
    p.add_argument("--lake", type=Path, default=ROOT / ".tmp" / "industry_lakes")
    p.add_argument("--json", type=Path, default=ROOT / ".tmp" / "industry_bench.json")
    args = p.parse_args(argv)
    names = args.family or list(FAMILIES)
    args.lake.mkdir(parents=True, exist_ok=True)
    reports = [run_family(n, args.lake) for n in names]
    for r in reports:
        print(summarize(r))
    payload = {
        "kind": "dms.industry_ontology_bench",
        "families": names,
        "passed_families": [r["family"] for r in reports if _ok(r)],
        "failed_families": [r["family"] for r in reports if not _ok(r)],
        "reports": [
            {
                "family": r.get("family"),
                "database": r.get("database"),
                "passed": r.get("passed"),
                "failed": r.get("failed"),
                "known_gaps": r.get("known_gaps"),
                "n_shapes": r.get("n_shapes"),
                "by_depth": r.get("by_depth"),
                "self_check": r.get("self_check"),
                "error": r.get("error"),
            }
            for r in reports
        ],
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    bad = [r["family"] for r in reports if not _ok(r)]
    if bad:
        print(f"FAIL families: {', '.join(bad)}")
        return 1
    print(f"PASS {len(reports)}/{len(reports)} families, 0 compiler disagreements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
