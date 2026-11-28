"""Render a document. Layout and theme only. The strings are already written."""

from __future__ import annotations

from typing import Any

from dms_core.create_style.docx_out import docx_bytes
from dms_core.create_style.errors import CreateError
from dms_core.create_style.html_out import html_bytes
from dms_core.create_style.layout import layout_pages, snapshot_pages
from dms_core.create_style.pptx_out import pptx_bytes
from dms_core.create_style.spec import normalize
from dms_core.create_style.theme import Theme
from dms_core.create_style.xlsx_out import sheet_grid, xlsx_bytes

_FORMATS = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "create.docx",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "create.pptx",
    ),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "create.xlsx"),
    "html": ("text/html; charset=utf-8", "create.html"),
}


def render_document(document: dict[str, Any], theme: Theme, fmt: str) -> tuple[bytes, str, str]:
    if fmt not in _FORMATS:
        raise CreateError("bad_format")
    spec = normalize(document)
    media, filename = _FORMATS[fmt]
    if fmt == "html":
        pages = layout_pages(spec, theme, "slide")
        return html_bytes(pages, theme, spec["title"]), media, filename
    if fmt == "docx":
        return docx_bytes(layout_pages(spec, theme, "doc")), media, filename
    if fmt == "pptx":
        return pptx_bytes(layout_pages(spec, theme, "slide")), media, filename
    return xlsx_bytes(spec, theme), media, filename


def snapshot(document: dict[str, Any], theme: Theme) -> dict[str, Any]:
    """Geometry and cell text. Image bytes stay out of the golden file."""
    spec = normalize(document)
    return {
        "slide": snapshot_pages(layout_pages(spec, theme, "slide")),
        "doc": snapshot_pages(layout_pages(spec, theme, "doc")),
        "xlsx": sheet_grid(spec, theme),
    }
