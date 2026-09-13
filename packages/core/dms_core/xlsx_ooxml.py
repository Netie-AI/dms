"""Minimal OOXML .xlsx via stdlib zipfile.

Excel inbound stays read-only. Outbound is generated cells we already have.
Swap: LibreOffice headless if a client needs richer styles; this writer is
values-only.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

_NS_PKG = "http://schemas.openxmlformats.org/package/2006"
_NS_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_SML = "application/vnd.openxmlformats-officedocument.spreadsheetml"

_ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_NS_PKG}/relationships">
<Relationship Id="rId1" Type="{_NS_DOC}/officeDocument" Target="xl/workbook.xml"/>
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

_ILLEGAL_XML = {i: None for i in range(32) if i not in (9, 10, 13)}


def _content_types_xml(sheet_count: int) -> str:
    overrides = [
        f'<Override PartName="/xl/workbook.xml" ContentType="{_NS_SML}.sheet.main+xml"/>'
    ]
    for i in range(1, sheet_count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            f'ContentType="{_NS_SML}.worksheet+xml"/>'
        )
    overrides.append(
        f'<Override PartName="/xl/styles.xml" ContentType="{_NS_SML}.styles+xml"/>'
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_NS_PKG}/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )


def _wb_rels_xml(sheet_count: int) -> str:
    rels = []
    for i in range(1, sheet_count + 1):
        rels.append(
            f'<Relationship Id="rId{i}" Type="{_NS_DOC}/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )
    rels.append(
        f'<Relationship Id="rId{sheet_count + 1}" Type="{_NS_DOC}/styles" '
        'Target="styles.xml"/>'
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_NS_PKG}/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )


def col_letter(idx: int) -> str:
    name = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def xml_text(text: str) -> str:
    cleaned = str(text).translate(_ILLEGAL_XML)
    return (
        cleaned.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def sheet_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in r":\/?*[]")
    cleaned = cleaned.strip()[:31] or "Sheet"
    return cleaned


def _cell(ref: str, value: object) -> str:
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        word = "true" if value else "false"
        return f'<c r="{ref}" t="inlineStr"><is><t>{xml_text(word)}</t></is></c>'
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return f'<c r="{ref}" t="inlineStr"><is><t>{xml_text(str(value))}</t></is></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = xml_text(str(value))
    # ponytail: Excel inlineStr cap 32767; upgrade = split across rows
    if len(text) > 32767:
        text = text[:32767]
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _sheet_xml(rows: list[list[object]]) -> str:
    body = []
    max_col = 1
    for r, row in enumerate(rows, start=1):
        if row:
            max_col = max(max_col, len(row))
        cells = "".join(_cell(f"{col_letter(c)}{r}", v) for c, v in enumerate(row))
        body.append(f'<row r="{r}">{cells}</row>')
    last_row = max(len(rows), 1)
    dim = f"A1:{col_letter(max_col - 1)}{last_row}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dim}"/><sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def xlsx_workbook_bytes(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    """Build .xlsx bytes. ``sheets`` is (name, row-grid) with at least one sheet."""
    if not sheets:
        raise ValueError("at least one sheet is required")
    used: set[str] = set()
    named: list[tuple[str, list[list[object]]]] = []
    for raw_name, rows in sheets:
        name = sheet_name(str(raw_name))
        base = name
        n = 1
        while name.lower() in used:
            n += 1
            suffix = f"_{n}"
            if len(base) + len(suffix) > 31:
                name = base[: 31 - len(suffix)] + suffix
            else:
                name = base + suffix
        used.add(name.lower())
        named.append((name, rows))
    sheet_tags = [
        f'<sheet name="{xml_text(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _rows) in enumerate(named, start=1)
    ]
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_tags)}</sheets></workbook>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types_xml(len(named)))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", _wb_rels_xml(len(named)))
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_name, rows) in enumerate(named, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return buf.getvalue()


def write_xlsx_sheets(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    """Write a multi-sheet xlsx via stdlib zip/OOXML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(xlsx_workbook_bytes(sheets))


def write_xlsx(path: Path, *, sheet_name: str, rows: list[list[object]]) -> None:
    write_xlsx_sheets(path, [(sheet_name, rows)])
