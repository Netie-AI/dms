"""Open the provider file and check it against the spec.

Reading only. A mismatch is a failed judge, not a local redraw.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_A_T = re.compile(r"<a:t[^>]*>([^<]*)</a:t>")
_W_T = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")
_PDF_TEXT = re.compile(r"\((?:\\.|[^)\\])*\)")
_TAG = re.compile(r"<[^>]+>")


def judge(
    content: bytes,
    *,
    media_type: str,
    spec: dict[str, Any],
    allowed: set[str],
) -> dict[str, Any]:
    opened = _open(content, media_type)
    problems: list[str] = []
    text = ""
    if opened is None:
        problems.append("file_closed")
    else:
        text, count = opened
        if spec.get("format") == "pptx" and spec.get("slide_count") is not None:
            if count != int(spec["slide_count"]):
                problems.append("slide_count")
        for section in spec.get("sections") or []:
            title = str(section.get("title") or "")
            if title and title not in text:
                problems.append(f"section_missing:{section.get('id')}")
        extra = _invented(text, allowed)
        if extra:
            problems.append("number_mismatch")
    return {"ok": not problems, "problems": problems, "text_chars": len(text)}


def _open(content: bytes, media_type: str) -> tuple[str, int] | None:
    if "presentationml" in media_type or media_type.endswith("/pptx"):
        return _pptx(content)
    if "wordprocessingml" in media_type or media_type.endswith("/docx"):
        return _docx(content)
    if media_type == "application/pdf" or media_type.endswith("/pdf"):
        return _pdf(content)
    if media_type.startswith("text/html"):
        return _html(content)
    if "spreadsheetml" in media_type or media_type.endswith("/xlsx"):
        return _xlsx(content)
    return None


def _pptx(content: bytes) -> tuple[str, int] | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None
    names = zf.namelist()
    if "[Content_Types].xml" not in names:
        return None
    slides = []
    for name in names:
        match = _SLIDE.match(name)
        if match:
            slides.append((int(match.group(1)), name))
    if not slides:
        return None
    slides.sort()
    texts = []
    for _num, name in slides:
        xml = zf.read(name).decode("utf-8", "replace")
        texts.append(" ".join(_A_T.findall(xml)))
    return "\n".join(texts), len(slides)


def _docx(content: bytes) -> tuple[str, int] | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" not in zf.namelist():
        return None
    xml = zf.read("word/document.xml").decode("utf-8", "replace")
    return " ".join(_W_T.findall(xml)), 1


def _pdf(content: bytes) -> tuple[str, int] | None:
    if not content.startswith(b"%PDF") or b"%%EOF" not in content:
        return None
    raw = content.decode("latin-1", "replace")
    texts = []
    for bit in _PDF_TEXT.findall(raw):
        inner = bit[1:-1].replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
        texts.append(inner)
    return " ".join(texts), 1


def _html(content: bytes) -> tuple[str, int] | None:
    raw = content.decode("utf-8", "replace")
    if "<html" not in raw.lower():
        return None
    return _TAG.sub(" ", raw), 1


def _xlsx(content: bytes) -> tuple[str, int] | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None
    names = zf.namelist()
    if "xl/workbook.xml" not in names:
        return None
    chunks = []
    for name in names:
        if name.startswith("xl/") and name.endswith(".xml"):
            xml = zf.read(name).decode("utf-8", "replace")
            chunks.append(_TAG.sub(" ", xml))
    return "\n".join(chunks), 1


def _invented(text: str, allowed: set[str]) -> list[str]:
    bad: list[str] = []
    for raw in _NUM.findall(text):
        plain = raw.replace(",", "")
        alts = {plain}
        if "." in plain:
            trimmed = plain.rstrip("0").rstrip(".")
            if trimmed:
                alts.add(trimmed)
        if alts.isdisjoint(allowed):
            bad.append(raw)
    return bad
