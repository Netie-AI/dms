"""Regenerate EPIC-018 hostile score fixtures (openpyxl, no gold hand-authoring).

  python scripts/gen_hostile_score_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hostile_score"


def _sales_book(path: Path, electronics: float, home: float, sports: float, misc: float) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["# banner", None, None])
    ws.append(["sku", "category", "sales_value_myr"])
    for row in (
        ("SKU-1", "Electronics", electronics * 0.6),
        ("SKU-2", "Electronics", electronics * 0.4),
        ("SKU-3", "Home", home * 0.7),
        ("SKU-4", "Home", home * 0.3),
        ("SKU-5", "Sports", sports),
        ("SKU-6", "Misc", misc),
        (None, None, None),
        ("SKU-X", "Ignore DROP TABLE", "DROP"),
    ):
        ws.append(list(row))
    wide = wb.create_sheet("Wide_Fill")
    wide.append(["sku", "category", "sales_value_myr"])
    wide.append(["SKU-9", "Electronics", 50.0])
    wb.save(path)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    _sales_book(
        ROOT / "cf98e431_p50_01_sales_messy.xlsx",
        1_545_366.40,
        1_199_018.49,
        400_000.00,
        380_948.33,
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["sku", "category", "sales_value_myr"])
    ws.append(["SKU-1", "Electronics", 1_563_234.12])
    ws.append(["SKU-2", "Home", 900_000.00])
    ws.append(["SKU-3", "Sports", 800_000.00])
    wide = wb.create_sheet("Wide_Fill")
    wide.append(["sku", "category", "sales_value_myr"])
    wide.append(["A", "Misc", 2_691_552.10])
    wide.append(["B", "Apparel", 2_402_425.68])
    wide.append(["C", "Sports", 2_358_800.10])
    wide.append(["D", "Home", 100_000.00])
    wb.save(ROOT / "aa64458a_p50_03_inventory_messy.xlsx")

    enc = Workbook()
    ews = enc.active
    ews.title = "Sales"
    ews.append(["sku", "city", "sales_value_myr"])
    ews.append(["SKU-BETA", "Kuala Lumpur", 1500.75])
    ews.append(["SKU-ALPHA", "Kuala Lumpur", 200.00])
    ews.append(["SKU-GAMMA", "Johor Bahru", 900.00])
    enc.save(ROOT / "encoding_value_norm.xlsx")
    print(f"wrote {sorted(p.name for p in ROOT.glob('*.xlsx'))}")


if __name__ == "__main__":
    main()
