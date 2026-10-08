"""Exclude scored-pack rows from the verified-question store.

The exclusion list is pack content hashes, never question text. Operators
(DMS Check) pass hashes of packs this process does not have in
``DMS_SCORED_PACK_HASHES``. When ``tests/fixtures/curated_ceo/questions.yaml``
is on disk, its sha256 is added at call time. A missing file adds nothing,
so an image without ``tests/`` still boots.

A row that carries a pack hash is excluded only when that hash is listed.
A row with no pack hash is excluded when the sha256 of its normalised
question (or of a stored synonym) equals a fingerprint computed from a
listed pack file this process can open. The only file opened is the
in-repo questions file above.

Limits:
- An env hash with no file here cannot fingerprint old rows. Those rows
  are excluded only when they already carry that hash.
- A rephrase is a different fingerprint. This is not a phrasing rule.
- The questions-file hash is sha256 of the raw file bytes. A comment-only
  edit changes the pack hash and does not change item fingerprints.
- File hash and fingerprints are cached for the process. A replaced file
  is picked up on restart.
- An unreadable questions file adds no fingerprints (``scored_pack_fingerprint_unreadable``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from dms_executor.demo_ask import normalize_ask_question

logger = logging.getLogger(__name__)

SCORED_PACK_HASHES_ENV = "DMS_SCORED_PACK_HASHES"
WRITE_BLOCKED = "scored_pack_write_blocked"
_HASH = re.compile(r"^[0-9a-f]{64}$")


def curated_questions_path() -> Path:
    """In-repo scored pack. Not read at import."""
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "curated_ceo"
        / "questions.yaml"
    )


def pack_files() -> tuple[Path, ...]:
    path = curated_questions_path()
    if path.is_file():
        return (path,)
    return ()


def canonical_pack_hash(value: object) -> str | None:
    """64-hex sha256, or None when ``value`` is not a pack hash."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[7:].strip()
    if _HASH.fullmatch(text):
        return text
    return None


def _normalised_item(text: str) -> str:
    # Same collapse the store uses for question_norm, so a stored row and
    # the pack item that produced it share one fingerprint.
    return " ".join(normalize_ask_question(text or "").casefold().split())


def item_fingerprint(text: str) -> str | None:
    """sha256 of the normalised item. Empty text has no fingerprint."""
    norm = _normalised_item(text)
    if not norm:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def env_pack_hashes() -> frozenset[str]:
    raw = os.environ.get(SCORED_PACK_HASHES_ENV, "")
    found: set[str] = set()
    for part in raw.replace(",", " ").split():
        digest = canonical_pack_hash(part)
        if digest:
            found.add(digest)
    return frozenset(found)


@lru_cache(maxsize=4)
def _file_hash(key: str) -> str:
    return hashlib.sha256(Path(key).read_bytes()).hexdigest()


def questions_file_hash(path: Path) -> str:
    return _file_hash(str(path.resolve()))


def _load_questions(path: Path) -> list[str]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows: list[object]
    if isinstance(data, dict):
        raw = data.get("questions") or []
        rows = raw if isinstance(raw, list) else []
    elif isinstance(data, list):
        rows = data
    else:
        return []
    out: list[str] = []
    for row in rows:
        if isinstance(row, str):
            out.append(row)
        elif isinstance(row, dict):
            question = row.get("question")
            if isinstance(question, str):
                out.append(question)
    return out


@lru_cache(maxsize=4)
def _file_fingerprints(key: str) -> frozenset[str]:
    found: set[str] = set()
    for question in _load_questions(Path(key)):
        digest = item_fingerprint(question)
        if digest:
            found.add(digest)
    return frozenset(found)


def scored_pack_hashes() -> frozenset[str]:
    found = set(env_pack_hashes())
    for path in pack_files():
        found.add(questions_file_hash(path))
    return frozenset(found)


def listed_item_fingerprints() -> frozenset[str]:
    """Fingerprints of pack files whose own hash is on the exclusion list."""
    listed = scored_pack_hashes()
    found: set[str] = set()
    for path in pack_files():
        try:
            digest = questions_file_hash(path)
            if digest in listed:
                found.update(_file_fingerprints(str(path.resolve())))
        except Exception:
            logger.warning("scored_pack_fingerprint_unreadable")
    return frozenset(found)


def provenance_pack_hash(row: Mapping[str, Any]) -> str | None:
    """Pack hash carried on the row, if it has one."""
    for key in ("pack_hash", "source_pack_hash"):
        digest = canonical_pack_hash(row.get(key))
        if digest:
            return digest
    for key in ("provenance", "source"):
        raw = row.get(key)
        if isinstance(raw, str):
            digest = canonical_pack_hash(raw)
            if digest:
                return digest
            continue
        if not isinstance(raw, dict):
            continue
        for sub in ("pack_hash", "hash", "pack"):
            digest = canonical_pack_hash(raw.get(sub))
            if digest:
                return digest
    return None


def _row_texts(row: Mapping[str, Any]) -> list[str]:
    texts = [str(row.get("question") or ""), str(row.get("question_norm") or "")]
    synonyms = row.get("synonyms")
    if isinstance(synonyms, list):
        texts.extend(str(item) for item in synonyms)
    return texts


def row_excluded(row: Mapping[str, Any]) -> bool:
    """True when this row must not be retrieved or written."""
    listed = scored_pack_hashes()
    if not listed:
        return False
    carried = provenance_pack_hash(row)
    if carried is not None:
        return carried in listed
    fingerprints = listed_item_fingerprints()
    if not fingerprints:
        return False
    return any(item_fingerprint(text) in fingerprints for text in _row_texts(row))


def filter_retrieved_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The only retrieval filter. Every store reader calls this."""
    return [dict(row) for row in rows if not row_excluded(row)]


def reject_scored_write(row: Mapping[str, Any]) -> None:
    """Block a write derived from a listed scored pack."""
    if not row_excluded(row):
        return
    logger.warning(WRITE_BLOCKED)
    raise ValueError(WRITE_BLOCKED)
