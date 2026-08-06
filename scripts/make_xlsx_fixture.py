"""Build the .xlsx test fixtures from stdlib zipfile only.

Hard rule 5 makes Excel source-only: nothing in this repo may *write* a workbook
via openpyxl/xlsxwriter. Test fixtures still need to be real .xlsx bytes, so this
writes minimal OOXML parts directly into a zip container. openpyxl stays
read-only everywhere, including here.

Regenerate:  python scripts/make_xlsx_fixture.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ingest"

#: Namespace roots, split out so the parts below stay inside the line limit.
#: XML allows whitespace between attributes, so the wrapping is not significant.
_NS_PKG = "http://schemas.openxmlformats.org/package/2006"
_NS_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_SML = "application/vnd.openxmlformats-officedocument.spreadsheetml"

_CONTENT_TYPES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{_NS_PKG}/content-types">
<Default Extension="rels"
 ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="{_NS_SML}.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_NS_SML}.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="{_NS_SML}.styles+xml"/>
</Types>"""

_ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_NS_PKG}/relationships">
<Relationship Id="rId1" Type="{_NS_DOC}/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WB_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_NS_PKG}/relationships">
<Relationship Id="rId1" Type="{_NS_DOC}/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="{_NS_DOC}/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _col(idx: int) -> str:
    name = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _cell(ref: str, value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{_esc(str(value))}</t></is></c>'


def _sheet_xml(rows: list[list[object]]) -> str:
    body = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(_cell(f"{_col(c)}{r}", v) for c, v in enumerate(row))
        body.append(f'<row r="{r}">{cells}</row>')
    dim = f"A1:{_col(len(rows[0]) - 1)}{len(rows)}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dim}"/><sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def write_xlsx(path: Path, *, sheet_name: str, rows: list[list[object]]) -> None:
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{_esc(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))


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
