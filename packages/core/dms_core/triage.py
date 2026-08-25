"""Ingest triage domain types — no DuckDB, no FastAPI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SheetClass(StrEnum):
    TABULAR_CLEAN = "TABULAR_CLEAN"
    TABULAR_DIRTY = "TABULAR_DIRTY"
    MULTI_TABLE = "MULTI_TABLE"
    HEADERLESS = "HEADERLESS"
    UNSTRUCTURED = "UNSTRUCTURED"


@dataclass(frozen=True)
class ShapeFingerprint:
    """Key for post-D1 Repair Desk recipes."""

    col_count: int
    header_text_hash: str
    type_signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "col_count": self.col_count,
            "header_text_hash": self.header_text_hash,
            "type_signature": self.type_signature,
        }


@dataclass
class FileTriageResult:
    file: str
    sheet: str | None
    classification: SheetClass
    reason: str
    fix: str
    shape_fingerprint: ShapeFingerprint | None = None
    confidence: float = 0.0
    header_row: int | None = None
    ingested: bool = False
    table: str | None = None
    blob_key: str | None = None
    document_index: str | None = None
    chunk_count: int | None = None
    source_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "sheet": self.sheet,
            "classification": self.classification.value,
            "reason": self.reason,
            "fix": self.fix,
            "shape_fingerprint": (
                self.shape_fingerprint.to_dict() if self.shape_fingerprint else None
            ),
            "confidence": self.confidence,
            "header_row": self.header_row,
            "ingested": self.ingested,
            "table": self.table,
            "blob_key": self.blob_key,
            "document_index": self.document_index,
            "chunk_count": self.chunk_count,
            "source_id": self.source_id,
        }


@dataclass
class TriageReceipt:
    """Honest multi-file ingest receipt — never a silent partial success."""

    files_seen: int
    ingested: int
    need_attention: int
    per_class: dict[str, int] = field(default_factory=dict)
    files: list[FileTriageResult] = field(default_factory=list)
    ingest_id: str = ""
    #: Whether the rows reached the warehouse chat actually reads.
    #:
    #: "ingested" only ever meant "landed in bronze". When ingest and serving are
    #: two DuckDB files, bronze can land and chat still not see it - the receipt
    #: said ingested=N, the next question abstained, and nothing on screen
    #: connected the two. The failure went to a logger, which is not a place a
    #: customer looks (R-0011).
    #:
    #: Three states, deliberately not two. "not_attempted" is not a synonym for
    #: ok: it means no serving warehouse is configured, so nothing was copied and
    #: nothing was verified. Collapsing it into success is how the silence
    #: started.
    serving_sync: str = "not_attempted"
    serving_sync_detail: str = ""

    @property
    def chat_can_see_it(self) -> bool:
        """True only on a positive statement, never on absence of bad news."""
        return self.serving_sync in {"ok", "not_needed"}

    def _summary(self) -> str:
        base = (
            f"{self.files_seen} files · {self.ingested} ingested · "
            f"{self.need_attention} need attention"
        )
        if not self.ingested:
            return base
        if self.serving_sync == "failed":
            return (
                f"{base} — but chat cannot see "
                f"{'them' if self.ingested != 1 else 'it'} yet: "
                f"{self.serving_sync_detail or 'the serving warehouse was not updated'}. "
                f"Run: python scripts/sync_bronze_to_serving.py"
            )
        if self.serving_sync == "not_attempted":
            return f"{base} · serving sync not attempted (no serving warehouse configured)"
        return f"{base} · chat can see it"

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_seen": self.files_seen,
            "ingested": self.ingested,
            "need_attention": self.need_attention,
            "per_class": dict(self.per_class),
            "files": [f.to_dict() for f in self.files],
            "ingest_id": self.ingest_id,
            "serving_sync": self.serving_sync,
            "serving_sync_detail": self.serving_sync_detail,
            "chat_can_see_it": self.chat_can_see_it,
            "summary": self._summary(),
        }
