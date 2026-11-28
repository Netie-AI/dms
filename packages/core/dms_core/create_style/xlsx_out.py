"""XLSX with a formatted Summary sheet and every table row on Data.

Outbound cells are written as OOXML. No openpyxl workbook and no xlsxwriter.
A cell longer than Excel's inline limit is split on a space, never inside a word.
"""

from __future__ import annotations

from typing import Any

from dms_core.create_style.errors import CreateError
from dms_core.create_style.pack import rgb, write_zip, xml_text
from dms_core.create_style.theme import Theme

_LIMIT = 32767
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def xlsx_bytes(document: dict[str, Any], theme: Theme) -> bytes:
    sheets = workbook_sheets(document, theme)
    return _workbook(sheets, theme)


def workbook_sheets(
    document: dict[str, Any], theme: Theme
) -> list[tuple[str, list[list[tuple[object, int]]]]]:
    """Return (name, rows of (value, style index))."""
    del theme
    summary: list[list[tuple[object, int]]] = [[(document.get("title") or "", 1)]]
    data_sheets: list[tuple[str, list[list[tuple[object, int]]]]] = []
    table_index = 0
    for block in document["blocks"]:
        kind = block["type"]
        summary.append([])
        summary.append([(_label(kind), 2)])
        if kind in ("image_side", "image_below"):
            summary.append([("caption", 2), (block["caption"], 3)])
        elif kind == "two_column":
            summary.append([("left", 2), ("right", 2)])
            summary.append([(block["left"], 3), (block["right"], 3)])
        elif kind == "kpi_strip":
            summary.append([("headline", 2), ("value", 2), ("source", 2)])
            for item in block["items"]:
                summary.append([(item["headline"], 3), (item["value"], 4), (item["source"], 5)])
        elif kind == "chart_commentary":
            summary.append([("commentary", 2), (block["commentary"], 3)])
            _chart_rows(summary, block["chart"])
        elif kind == "chart_page":
            for chart in block["charts"]:
                _chart_rows(summary, chart)
        elif kind == "table":
            table_index += 1
            sheet_name = "Data" if table_index == 1 else f"Data{table_index}"
            summary.append([("sheet", 5), (sheet_name, 3), ("rows", 5), (len(block["rows"]), 4)])
            summary.append([(name, 2) for name in block["columns"]])
            grid: list[list[tuple[object, int]]] = [[(name, 2) for name in block["columns"]]]
            for row in block["rows"]:
                grid.append([(_value(cell), 0) for cell in row])
                summary.append([(_value(cell), 3) for cell in row])
            data_sheets.append((sheet_name, _expand(grid)))
        else:
            raise CreateError("bad_block")
    sheets: list[tuple[str, list[list[tuple[object, int]]]]] = [("Summary", _expand(summary))]
    sheets.extend(data_sheets)
    if not data_sheets:
        sheets.append(("Data", [[("rows", 2)], [(0, 4)]]))
    return sheets


def sheet_grid(document: dict[str, Any], theme: Theme) -> dict[str, list[list[object]]]:
    """Values only, for the golden snapshot."""
    out: dict[str, list[list[object]]] = {}
    for name, rows in workbook_sheets(document, theme):
        out[name] = [[cell for cell, _style in row] for row in rows]
    return out


def _label(kind: str) -> str:
    return str(kind)


def _chart_rows(summary: list[list[tuple[object, int]]], chart: dict[str, Any]) -> None:
    if chart.get("title"):
        summary.append([("title", 2), (chart["title"], 3)])
    summary.append([("categories", 2), *[(label, 3) for label in chart["categories"]]])
    for series in chart["series"]:
        summary.append([(series["name"], 2), *[(value, 4) for value in series["values"]]])


def _value(cell: object) -> object:
    if isinstance(cell, str):
        return cell
    return cell


def _expand(rows: list[list[tuple[object, int]]]) -> list[list[tuple[object, int]]]:
    """Split a too-long string on a space into following rows. Never cut a word."""
    expanded: list[list[tuple[object, int]]] = []
    for row in rows:
        pieces: list[list[tuple[object, int]]] = [[]]
        overflow: list[tuple[object, int]] = []
        for value, style in row:
            if isinstance(value, str) and len(value) > _LIMIT:
                head, rest = _split_words(value)
                pieces[-1].append((head, style))
                while rest:
                    head, rest = _split_words(rest)
                    overflow.append((head, style))
            else:
                pieces[-1].append((value, style))
        expanded.extend(pieces)
        for extra in overflow:
            expanded.append([extra])
    return expanded


def _split_words(text: str) -> tuple[str, str]:
    if len(text) <= _LIMIT:
        return text, ""
    cut = text.rfind(" ", 0, _LIMIT + 1)
    if cut <= 0:
        raise CreateError("cell_too_long")
    return text[:cut], text[cut + 1 :]


def _workbook(sheets: list[tuple[str, list[list[tuple[object, int]]]]], theme: Theme) -> bytes:
    names = [name for name, _rows in sheets]
    sheet_tags = [
        f'<sheet name="{xml_text(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(names, start=1)
    ]
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_NS}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheet_tags)}</sheets></workbook>"
    )
    rels = []
    for index in range(1, len(sheets) + 1):
        rels.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
    rels.append(
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    parts: dict[str, bytes | str] = {
        "[Content_Types].xml": _content_types(len(sheets)),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>"
        ),
        "xl/styles.xml": _styles(theme),
    }
    for index, (_name, rows) in enumerate(sheets, start=1):
        parts[f"xl/worksheets/sheet{index}.xml"] = _sheet(rows)
    return write_zip(parts)


def _styles(theme: Theme) -> str:
    accent = rgb(theme.colours.accent)
    paper = rgb(theme.colours.paper)
    ink = rgb(theme.colours.ink)
    muted = rgb(theme.colours.muted)
    body = xml_text(theme.fonts.body)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{_NS}">'
        '<fonts count="6">'
        f'<font><sz val="11"/><name val="{body}"/><color rgb="FF{ink}"/></font>'
        f'<font><b/><sz val="16"/><name val="{body}"/><color rgb="FF{ink}"/></font>'
        f'<font><b/><sz val="11"/><name val="{body}"/><color rgb="FFFFFFFF"/></font>'
        f'<font><sz val="11"/><name val="{body}"/><color rgb="FF{ink}"/></font>'
        f'<font><b/><sz val="20"/><name val="{body}"/><color rgb="FF{accent}"/></font>'
        f'<font><sz val="10"/><name val="{body}"/><color rgb="FF{muted}"/></font>'
        "</fonts>"
        '<fills count="5">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{paper}"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{accent}"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{paper}"/></patternFill></fill>'
        "</fills>"
        '<borders count="2"><border/><border>'
        '<left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/>'
        "</border></borders>"
        '<cellStyleXfs count="1">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" '
        'applyFont="1" applyFill="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="3" fillId="2" borderId="0" xfId="0" '
        'applyFont="1" applyFill="1" applyAlignment="1">'
        '<alignment wrapText="1" vertical="top"/></xf>'
        '<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="5" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def _sheet(rows: list[list[tuple[object, int]]]) -> str:
    body = []
    width = 1
    for row_index, row in enumerate(rows, start=1):
        width = max(width, len(row))
        cells = []
        for col_index, (value, style) in enumerate(row):
            ref = f"{_col(col_index)}{row_index}"
            cells.append(_cell(ref, value, style))
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    last = max(len(rows), 1)
    dim = f"A1:{_col(width - 1)}{last}"
    cols = '<cols><col min="1" max="8" width="36" customWidth="1"/></cols>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_NS}">'
        f'<dimension ref="{dim}"/>{cols}<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def _cell(ref: str, value: object, style: int) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        if style:
            return f'<c r="{ref}"{style_attr}/>'
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        word = "true" if value else "false"
        return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{word}</t></is></c>'
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    if isinstance(value, float):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = xml_text(str(value))
    return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _content_types(count: int) -> str:
    overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for index in range(1, count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>' + "".join(overrides) + "</Types>"
    )


def plain_rows(raw: bytes, sheet_name: str) -> list[list[str]]:
    """Read cell text back. Used by tests. Numbers come back as their digits."""
    import zipfile
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(io_bytes(raw)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    ns = {"m": _NS, "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    rel_ns = {"p": "http://schemas.openxmlformats.org/package/2006/relationships"}
    target = ""
    for sheet in workbook.findall("m:sheets/m:sheet", ns):
        if sheet.attrib.get("name") == sheet_name:
            rid = sheet.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            for rel in rels.findall("p:Relationship", rel_ns):
                if rel.attrib.get("Id") == rid:
                    target = "xl/" + rel.attrib["Target"]
            break
    if not target:
        raise CreateError("sheet_missing")
    with zipfile.ZipFile(io_bytes(raw)) as archive:
        root = ET.fromstring(archive.read(target))
    rows: list[list[str]] = []
    for row in root.findall("m:sheetData/m:row", ns):
        values: list[str] = []
        for cell in row.findall("m:c", ns):
            text = cell.find("m:is/m:t", ns)
            if text is not None and text.text:
                values.append(text.text)
                continue
            node = cell.find("m:v", ns)
            values.append("" if node is None or node.text is None else node.text)
        rows.append(values)
    return rows


def io_bytes(raw: bytes) -> Any:
    import io

    return io.BytesIO(raw)
