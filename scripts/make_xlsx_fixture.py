"""Build the .xlsx test fixtures from stdlib zipfile only.

Hard rule 5 makes Excel source-only: nothing in this repo may *write* a workbook
via openpyxl/xlsxwriter. Test fixtures still need to be real .xlsx bytes, so this
writes minimal OOXML parts directly into a zip container. openpyxl stays
read-only everywhere, including here.

Regenerate:  python scripts/make_xlsx_fixture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CORE = str(ROOT / "packages" / "core")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from dms_core.xlsx_ooxml import write_xlsx, write_xlsx_sheets  # noqa: E402

__all__ = ["Q3_SALES_ROWS", "main", "write_xlsx", "write_xlsx_sheets"]

FIXTURES = ROOT / "tests" / "fixtures" / "ingest"

#: The fixture whose absence let P0-DEMO-01 ship: every other ingest fixture is
#: CSV, and a CSV ingested first creates the registry that the xlsx path assumed.
Q3_SALES_ROWS: list[list[object]] = [
    ["sku", "units_sold", "revenue_myr"],
    ["SKU-00397", 12, 4380.5],
    ["SKU-00412", 7, 2555.0],
    ["SKU-00518", 23, 8395.75],
    ["SKU-00644", 4, 1460.0],
]


def main() -> None:
    target = FIXTURES / "15_q3_sales_export.xlsx"
    write_xlsx(target, sheet_name="Q3", rows=Q3_SALES_ROWS)
    print(f"wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
