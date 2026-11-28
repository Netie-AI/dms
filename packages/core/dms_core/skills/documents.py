"""Upload PDF, DOCX, or Markdown. Chunk it. Embed only through a caller-supplied
OpenVault embedder. Citations are short excerpts, not the file.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Sequence
from xml.etree import ElementTree

from dms_core.skills.errors import SkillError

_MAX_UPLOAD = 2_000_000
_CHUNK = 800
_OVERLAP = 80
_MAX_CHUNKS = 40
_EXCERPT = 240
_W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
_TOKEN = re.compile(r"[a-z0-9]+")

Embedder = Callable[[list[str]], object]


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    if len(data) > _MAX_UPLOAD:
        raise SkillError("document_too_large")
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix in {"md", "markdown", "txt"}:
        return _utf8(data), suffix
    if suffix == "pdf":
        return _pdf(data), "pdf"
    if suffix == "docx":
        return _docx(data), "docx"
    raise SkillError("document_type")


def _utf8(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError("document_unreadable") from exc


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SkillError("pdf_reader_missing") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise SkillError("document_unreadable") from exc
    return "\n".join(parts)


def _docx(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("docx_unreadable") from exc
    total = sum(info.file_size for info in archive.infolist())
    if total > 8_000_000:
        raise SkillError("docx_too_large")
    try:
        xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise SkillError("docx_unreadable") from exc
    if len(xml) > 4_000_000:
        raise SkillError("docx_too_large")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise SkillError("docx_unreadable") from exc
    return " ".join(node.text or "" for node in root.iter(_W_T))


def chunk_text(text: str) -> list[str]:
    body = " ".join((text or "").split())
    if not body:
        raise SkillError("document_empty")
    if len(body) > 200_000:
        body = body[:200_000]
    chunks: list[str] = []
    index = 0
    step = _CHUNK - _OVERLAP
    while index < len(body) and len(chunks) < _MAX_CHUNKS:
        chunks.append(body[index : index + _CHUNK])
        index += step
    return chunks


def embed_chunks(
    chunks: Sequence[str], embedder: Embedder | None
) -> tuple[list[list[float] | None], str]:
    """OpenVault vectors when the embedder returns them. Never a stand-in vector."""
    empty: list[list[float] | None] = [None] * len(chunks)
    if embedder is None:
        return empty, "unavailable"
    try:
        raw = embedder(list(chunks))
    except Exception:
        return empty, "unavailable"
    clean = _vectors(raw, len(chunks))
    if clean is None:
        return empty, "unavailable"
    vectors: list[list[float] | None] = [row for row in clean]
    return vectors, "openvault"


def excerpt(text: str) -> str:
    body = " ".join((text or "").split())
    if len(body) <= _EXCERPT:
        return body
    return body[:_EXCERPT]


def rank_chunks(
    question: str,
    chunks: Sequence[dict[str, object]],
    embedder: Embedder | None,
) -> list[int]:
    """Indexes, best first. Cosine when every chunk has an OpenVault vector."""
    vectors = [_as_vector(row.get("vector")) for row in chunks]
    if chunks and all(item is not None for item in vectors):
        query = _query_vector(question, embedder)
        if query is not None:
            scored = [
                (_cosine(query, item), index)
                for index, item in enumerate(vectors)
                if item is not None
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            return [index for score, index in scored if score > 0]
    scores = _lexical(question, [str(row.get("text") or "") for row in chunks])
    order = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    return [index for index in order if scores[index] > 0]


def _query_vector(question: str, embedder: Embedder | None) -> list[float] | None:
    if embedder is None:
        return None
    try:
        raw = embedder([question])
    except Exception:
        return None
    vectors = _vectors(raw, 1)
    if not vectors or vectors[0] is None:
        return None
    return vectors[0]


def _vectors(raw: object, count: int) -> list[list[float]] | None:
    if not isinstance(raw, list) or len(raw) != count:
        return None
    width: int | None = None
    out: list[list[float]] = []
    for item in raw:
        if not isinstance(item, list) or not item:
            return None
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in item):
            return None
        if width is None:
            width = len(item)
        if len(item) != width:
            return None
        out.append([float(value) for value in item])
    return out


def _as_vector(value: object) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    return [float(item) for item in value]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _lexical(question: str, chunks: Sequence[str]) -> list[float]:
    wanted = _TOKEN.findall(question.casefold())
    scores: list[float] = []
    for chunk in chunks:
        tokens = _TOKEN.findall(chunk.casefold())
        if not wanted or not tokens:
            scores.append(0.0)
            continue
        scores.append(sum(tokens.count(token) for token in wanted) / len(tokens))
    return scores
