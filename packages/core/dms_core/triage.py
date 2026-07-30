"""Ingest triage domain types — no DuckDB, no FastAPI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SheetClass(str, Enum):
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_seen": self.files_seen,
            "ingested": self.ingested,
            "need_attention": self.need_attention,
            "per_class": dict(self.per_class),
            "files": [f.to_dict() for f in self.files],
            "ingest_id": self.ingest_id,
            "summary": (
                f"{self.files_seen} files · {self.ingested} ingested · "
                f"{self.need_attention} need attention"
            ),
        }
