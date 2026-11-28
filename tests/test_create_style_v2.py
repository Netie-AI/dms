"""CREATE-STYLE-V2. Flag off. Layout blocks render to docx, pptx, xlsx, and HTML.

The sentences in this file are the fixture. The product does not contain them.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from dms_core.create_style.adopt import document_from_artifact
from dms_core.create_style.errors import CreateError
from dms_core.create_style.flag import enabled
from dms_core.create_style.layout import layout_pages
from dms_core.create_style.paint import page_png, png_size
from dms_core.create_style.render import render_document, snapshot
from dms_core.create_style.spec import layout_blocks, normalize
from dms_core.create_style.theme import default_theme_path, load_theme
from dms_core.create_style.xlsx_out import plain_rows
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "create_style" / "golden.json"
EMU = 9525

CAPTION = "North warehouse, photographed this morning."
CAPTION_BELOW = "The same aisle, with the full caption under the picture."
LEFT = "Collections landed in full. No word in this column is cut short."
RIGHT = "The source is the shipments ledger for the same week."
HEADLINE = "On-time deliveries rose."
VALUE = "96.2%"
SOURCE = "shipments.ontime"
COMMENTARY = (
    "The bars are the weekly totals the model was given, "
    "and this sentence stays whole through the last word untruncated."
)
TITLE = "Week in review"
LONG_CELL = "full-row-value-not-cut"

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _wide_png() -> str:
    image = Image.new("RGB", (60, 30), (180, 40, 40))
    for x in range(15, 45):
        for y in range(0, 30):
            image.putpixel((x, y), (20, 40, 160))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _chart(title: str, values: list[int], kind: str = "bar") -> dict[str, object]:
    return {
        "title": title,
        "kind": kind,
        "categories": ["Mon", "Tue", "Wed"],
        "series": [{"name": "Orders", "values": values}],
    }


def pitch_document() -> dict[str, object]:
    image = _wide_png()
    return {
        "title": TITLE,
        "blocks": [
            {"type": "image_side", "image_b64": image, "caption": CAPTION},
            {"type": "image_below", "image_b64": image, "caption": CAPTION_BELOW},
            {"type": "two_column", "left": LEFT, "right": RIGHT},
            {
                "type": "kpi_strip",
                "items": [
                    {"headline": HEADLINE, "value": VALUE, "source": SOURCE},
                    {"headline": "Open orders fell.", "value": "14", "source": "orders.open"},
                ],
            },
            {
                "type": "chart_commentary",
                "chart": _chart("Weekly orders", [4, 9, 6], "line"),
                "commentary": COMMENTARY,
            },
            {
                "type": "table",
                "columns": ["Region", "Orders"],
                "rows": [["North", 12], ["South", 9], ["East", LONG_CELL], ["West", 7]],
            },
            {
                "type": "chart_page",
                "charts": [
                    _chart("Monday orders", [4, 5, 6]),
                    _chart("Tuesday orders", [3, 8, 2]),
                    _chart("Wednesday orders", [7, 1, 4]),
                    _chart("Thursday orders", [2, 6, 5]),
                ],
            },
        ],
    }


def _render(
    fmt: str, document: dict[str, object] | None = None, theme: object | None = None
) -> bytes:
    spec = document if document is not None else pitch_document()
    chosen = theme if theme is not None else load_theme()
    data, _media, _name = render_document(spec, chosen, fmt)  # type: ignore[arg-type]
    return data


def test_flag_accepts_only_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CREATE_STYLE", raising=False)
    assert enabled() is False
    for raw in ("", "0", "true", "yes", "01", "2", " 1 "):
        monkeypatch.setenv("DMS_CREATE_STYLE", raw)
        assert enabled() is (raw.strip() == "1")
    monkeypatch.setenv("DMS_CREATE_STYLE", "1")
    assert enabled() is True


def test_theme_file_is_the_source() -> None:
    raw = json.loads(default_theme_path().read_text(encoding="utf-8"))
    theme = load_theme()
    assert theme.colours.accent == raw["colours"]["accent"].upper()
    assert theme.spacing.unit == raw["spacing"]["unit"]
    html = _render("html").decode("utf-8")
    assert raw["colours"]["accent"].upper() in html
    assert "text-overflow" not in html


def test_custom_theme_recolours_every_export(tmp_path: Path) -> None:
    brand = tmp_path / "brand.json"
    brand.write_text(
        json.dumps({"colours": {"accent": "#112233"}, "card_alpha": 0.4}), encoding="utf-8"
    )
    theme = load_theme(path=brand)
    html = _render("html", theme=theme).decode("utf-8")
    pptx = _zip_text(_render("pptx", theme=theme), ".xml")
    with zipfile.ZipFile(io.BytesIO(_render("xlsx", theme=theme))) as archive:
        styles = archive.read("xl/styles.xml").decode("utf-8")
    assert "#112233" in html
    assert "0.4" in html
    assert "112233" in pptx
    assert "112233" in styles


def test_each_block_keeps_full_text_and_square_images() -> None:
    texts = [
        CAPTION,
        CAPTION_BELOW,
        LEFT,
        RIGHT,
        HEADLINE,
        VALUE,
        SOURCE,
        COMMENTARY,
        LONG_CELL,
        TITLE,
    ]
    for fmt in ("docx", "pptx", "html"):
        text = _export_text(_render(fmt), fmt)
        for sentence in texts:
            assert sentence in text, fmt
        assert "..." not in text
    rows = plain_rows(_render("xlsx"), "Data")
    flat = [cell for row in rows for cell in row]
    assert rows[0] == ["Region", "Orders"]
    assert ["North", "12"] in rows
    assert ["East", LONG_CELL] in rows
    assert len(rows) == 5
    summary = [cell for row in plain_rows(_render("xlsx"), "Summary") for cell in row]
    for sentence in (CAPTION, LEFT, HEADLINE, VALUE, SOURCE, COMMENTARY, TITLE):
        assert sentence in summary
    assert LONG_CELL in flat


def test_images_are_square_and_shapes_stay_on_the_page() -> None:
    docx = _docx_shapes(_render("docx"))
    pptx = _pptx_shapes(_render("pptx"))
    for shapes in (docx, pptx):
        images = [item for item in shapes if item["name"] == "image"]
        assert len(images) == 2
        for item in images:
            assert item["w"] == item["h"]
            assert item["png"][0] == item["png"][1]
            assert item["x"] >= 0 and item["y"] >= 0
            assert item["x"] + item["w"] <= item["page_w"]
            assert item["y"] + item["h"] <= item["page_h"]
        captions = [item["text"] for item in shapes if item["name"] == "caption"]
        assert CAPTION in captions
        assert CAPTION_BELOW in captions
        for item in shapes:
            assert item["x"] + item["w"] <= item["page_w"]
            assert item["y"] + item["h"] <= item["page_h"]
    html = _render("html").decode("utf-8")
    assert 'data-name="image"' in html
    assert 'data-name="caption"' in html


def test_chart_page_is_four_equal_cells() -> None:
    theme = load_theme()
    pages = layout_pages(normalize(pitch_document()), theme, "slide")
    chart_pages = [page for page in pages if any(box.name == "chart_title" for box in page.boxes)]
    assert chart_pages
    charts = [box for box in chart_pages[-1].boxes if box.name == "chart"]
    assert len(charts) == 4
    assert len({(box.w, box.h) for box in charts}) == 1
    titles = [box.text for box in chart_pages[-1].boxes if box.name == "chart_title"]
    assert titles == ["Monday orders", "Tuesday orders", "Wednesday orders", "Thursday orders"]


def test_layout_stays_on_the_grid_and_bytes_are_stable() -> None:
    theme = load_theme()
    spec = normalize(pitch_document())
    for canvas in ("slide", "doc"):
        pages = layout_pages(spec, theme, canvas)
        unit = theme.spacing.unit
        margin = theme.spacing.page_margin
        for page in pages:
            for box in page.boxes:
                assert box.x % unit == 0
                assert box.y % unit == 0
                assert box.w % unit == 0
                assert box.h % unit == 0
                if box.name == "paper":
                    continue
                assert box.x >= margin
                assert box.y >= margin
                assert box.x + box.w <= page.w - margin
                assert box.y + box.h <= page.h - margin
    first = {fmt: _render(fmt) for fmt in ("docx", "pptx", "xlsx", "html")}
    second = {fmt: _render(fmt) for fmt in ("docx", "pptx", "xlsx", "html")}
    assert first == second


def test_golden_snapshot() -> None:
    got = snapshot(pitch_document(), load_theme())
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == expected


def test_refuses_a_short_chart_page_and_a_kpi_without_a_source() -> None:
    image = _wide_png()
    with pytest.raises(CreateError) as short:
        normalize(
            {
                "title": "",
                "blocks": [{"type": "chart_page", "charts": [_chart("A", [1, 2, 3])]}],
            }
        )
    assert short.value.code == "chart_page_needs_four"
    with pytest.raises(CreateError) as missing:
        normalize(
            {
                "title": "",
                "blocks": [
                    {
                        "type": "kpi_strip",
                        "items": [{"headline": HEADLINE, "value": VALUE}],
                    }
                ],
            }
        )
    assert missing.value.code == "kpi_required"
    side = normalize(
        {"title": "", "blocks": [{"type": "image_side", "image_b64": image, "caption": CAPTION}]}
    )
    assert side["blocks"][0]["caption"] == CAPTION


def test_layout_blocks_hook_is_empty_until_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CREATE_STYLE", raising=False)
    assert layout_blocks("doc") == []
    assert layout_blocks("slides") == []
    monkeypatch.setenv("DMS_CREATE_STYLE", "1")
    listed = layout_blocks("doc")
    assert [item["type"] for item in listed] == [
        "image_side",
        "image_below",
        "two_column",
        "kpi_strip",
        "chart_commentary",
        "table",
        "chart_page",
    ]
    side = next(item for item in listed if item["type"] == "image_side")
    assert side["fields"] == ["image_b64", "caption"]
    assert side["optional"] == ["label"]
    assert layout_blocks("other") == []


def test_parts_keep_the_model_words_and_every_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = f"{LEFT} {RIGHT}"
    rows = [{"Region": "North", "Orders": index} for index in range(12)]
    rows.append({"Region": "East", "Orders": LONG_CELL})
    artifact = {
        "title": TITLE,
        "parts": [
            {
                "id": "a",
                "title": HEADLINE,
                "body": body,
                "rows": rows,
                "charts": [
                    {
                        "label": "Weekly orders",
                        "type": "line",
                        "rows": [
                            {"week": "Mon", "orders": 4},
                            {"week": "Tue", "orders": 9},
                            {"week": "Wed", "orders": 6},
                        ],
                    }
                ],
            }
        ],
    }
    document = document_from_artifact(artifact)
    commentary = next(item for item in document["blocks"] if item["type"] == "chart_commentary")
    assert commentary["commentary"] == body
    table = next(item for item in document["blocks"] if item["type"] == "table")
    assert len(table["rows"]) == 13
    assert table["rows"][-1] == ["East", LONG_CELL]
    package = ROOT / "packages" / "core" / "dms_core" / "create_style"
    blob = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
    assert HEADLINE not in blob
    assert "\u2014" not in blob
    assert "\u2013" not in blob
    monkeypatch.setenv("DMS_CREATE_STYLE", "1")
    from dms_executor.create_files import render_artifact

    names = render_artifact(
        tmp_path, artifact, kind="doc", one_page=False, brand={"colours": "#112233"}
    )
    assert "doc.docx" in names
    assert "doc.xlsx" in names
    assert "doc.pdf" not in names
    html = (tmp_path / "doc.html").read_text(encoding="utf-8")
    assert body in html
    assert "#112233" in html
    data = plain_rows((tmp_path / "doc.xlsx").read_bytes(), "Data")
    assert len(data) == 14
    assert ["East", LONG_CELL] in data


def test_flag_off_keeps_the_create_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CREATE_STYLE", raising=False)
    from dms_executor.create_files import render_artifact

    artifact = {
        "kind": "doc",
        "title": "Note",
        "parts": [{"id": "a", "title": "Body", "body": "Opened."}],
    }
    names = render_artifact(tmp_path, artifact, kind="doc", one_page=False)
    assert "doc.pdf" in names
    assert "doc.docx" not in names
    assert (tmp_path / "doc.pdf").read_bytes().startswith(b"%PDF")
    html = (tmp_path / "doc.html").read_text(encoding="utf-8")
    assert "data-name" not in html
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_CREATE_STYLE", "1")
    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/v1/create/layouts").status_code == 404
    assert client.post("/v1/create/render", json={"format": "html"}).status_code == 404


def test_preview_pages_match_the_boxes() -> None:
    theme = load_theme()
    pages = layout_pages(normalize(pitch_document()), theme, "slide")
    for page in pages:
        raw = page_png(page, theme)
        assert png_size(raw) == (page.w, page.h)


def _zip_text(data: bytes, suffix: str) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.endswith(suffix):
                parts.append(archive.read(name).decode("utf-8"))
    return "\n".join(parts)


def _export_text(data: bytes, fmt: str) -> str:
    if fmt == "html":
        return data.decode("utf-8")
    if fmt == "docx":
        return _zip_text(data, "word/document.xml")
    return _zip_text(data, ".xml")


def _local(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if key == name or key.endswith("}" + name):
            return value
    raise KeyError(name)


def _docx_shapes(data: bytes) -> list[dict[str, object]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        pngs = {}
        for name in archive.namelist():
            if name.startswith("word/media/") and name.endswith(".png"):
                pngs[name] = png_size(archive.read(name))
        rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
    rel_map = {}
    rel_ns = {"p": "http://schemas.openxmlformats.org/package/2006/relationships"}
    for rel in rels.findall("p:Relationship", rel_ns):
        rel_map[rel.attrib["Id"]] = rel.attrib["Target"]
    page = root.find(f".//{{{_W}}}sectPr/{{{_W}}}pgSz")
    assert page is not None
    page_w = int(_local(page, "w")) // 15
    page_h = int(_local(page, "h")) // 15
    shapes = []
    for anchor in root.iter(f"{{{_WP}}}anchor"):
        name = anchor.find(f"{{{_WP}}}docPr").attrib["name"]  # type: ignore[union-attr]
        extent = anchor.find(f"{{{_WP}}}extent")
        assert extent is not None
        pos_h = anchor.find(f"{{{_WP}}}positionH/{{{_WP}}}posOffset")
        pos_v = anchor.find(f"{{{_WP}}}positionV/{{{_WP}}}posOffset")
        assert pos_h is not None and pos_v is not None and pos_h.text and pos_v.text
        texts = [node.text or "" for node in anchor.iter(f"{{{_W}}}t")]
        blip = anchor.find(f".//{{{_A}}}blip")
        png = (0, 0)
        if blip is not None:
            rid = blip.attrib[f"{{{_R}}}embed"]
            target = "word/" + rel_map[rid].lstrip("/")
            png = pngs[target]
        shapes.append(
            {
                "name": name,
                "x": int(pos_h.text) // EMU,
                "y": int(pos_v.text) // EMU,
                "w": int(extent.attrib["cx"]) // EMU,
                "h": int(extent.attrib["cy"]) // EMU,
                "text": "".join(texts),
                "png": png,
                "page_w": page_w,
                "page_h": page_h,
            }
        )
    return shapes


def _pptx_shapes(data: bytes) -> list[dict[str, object]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        pres = ET.fromstring(archive.read("ppt/presentation.xml"))
        size = pres.find(f"{{{_P}}}sldSz")
        assert size is not None
        page_w = int(size.attrib["cx"]) // EMU
        page_h = int(size.attrib["cy"]) // EMU
        pngs = {}
        for name in archive.namelist():
            if name.startswith("ppt/media/") and name.endswith(".png"):
                pngs[name] = png_size(archive.read(name))
        shapes: list[dict[str, object]] = []
        for name in sorted(
            item for item in archive.namelist() if item.startswith("ppt/slides/slide")
        ):
            if not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            rel_name = name.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"
            rel_map = {}
            rels = ET.fromstring(archive.read(rel_name))
            for rel in rels:
                rel_map[rel.attrib["Id"]] = rel.attrib["Target"]
            for el in list(root.iter(f"{{{_P}}}sp")) + list(root.iter(f"{{{_P}}}pic")):
                c_nv = el.find(f".//{{{_P}}}cNvPr")
                assert c_nv is not None
                off = el.find(f".//{{{_A}}}off")
                ext = el.find(f".//{{{_A}}}ext")
                assert off is not None and ext is not None
                texts = [node.text or "" for node in el.iter(f"{{{_A}}}t")]
                blip = el.find(f".//{{{_A}}}blip")
                png = (0, 0)
                if blip is not None:
                    rid = blip.attrib[f"{{{_R}}}embed"]
                    target = "ppt/" + rel_map[rid].replace("../", "")
                    png = pngs[target]
                shapes.append(
                    {
                        "name": c_nv.attrib["name"],
                        "x": int(off.attrib["x"]) // EMU,
                        "y": int(off.attrib["y"]) // EMU,
                        "w": int(ext.attrib["cx"]) // EMU,
                        "h": int(ext.attrib["cy"]) // EMU,
                        "text": "".join(texts),
                        "png": png,
                        "page_w": page_w,
                        "page_h": page_h,
                    }
                )
    return shapes
