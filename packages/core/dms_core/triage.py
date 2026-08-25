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


#: The sync never ran. Distinct from success, and distinct from failure - "not
#: attempted" is a third outcome and reporting it as ok would be the same lie in a
#: quieter voice.
SYNC_NOT_ATTEMPTED = "not_attempted"
SYNC_OK = "ok"
SYNC_FAILED = "failed"


@dataclass
class ServingSync:
    """Whether chat can actually see what was just ingested.

    Bronze landing and chat being able to read it are two different events. The copy
    from the ingest warehouse into the serving one used to fail into a ``logger.warning``
    and nowhere else, while the receipt went on reporting ``ingested=N``. So a customer
    uploaded a file, was told it landed, asked a question, and got an abstention with
    nothing on screen connecting the two. R-0011: a degradation visible in a log line
    and nowhere in the output is a lie.

    ``state`` is deliberately three-valued. ``not_attempted`` means no serving warehouse
    is configured, so there is nothing to be out of date - that is not success, and it
    is not failure either.
    """

    state: str = SYNC_NOT_ATTEMPTED
    status: str = ""
    detail: str = ""
    action: str = ""

    @property
    def visible_to_chat(self) -> bool:
        return self.state == SYNC_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "status": self.status,
            "detail": self.detail,
            "action": self.action,
            "visible_to_chat": self.visible_to_chat,
        }

    def sentence(self, ingested: int) -> str:
        """One clause a non-technical reader can act on, for the summary line."""
        if self.state == SYNC_OK:
            return "chat can see them"
        if self.state == SYNC_FAILED:
            it = "it" if ingested == 1 else "them"
            noun = "table" if ingested == 1 else "tables"
            return f"but chat cannot see {it} yet ({noun} landed, serving copy failed)"
        return "no chat warehouse configured, so nothing was published to chat"


@dataclass
class TriageReceipt:
    """Honest multi-file ingest receipt — never a silent partial success."""

    files_seen: int
    ingested: int
    need_attention: int
    per_class: dict[str, int] = field(default_factory=dict)
    files: list[FileTriageResult] = field(default_factory=list)
    ingest_id: str = ""
    serving_sync: ServingSync = field(default_factory=ServingSync)

    def to_dict(self) -> dict[str, Any]:
        summary = (
            f"{self.files_seen} files · {self.ingested} ingested · "
            f"{self.need_attention} need attention"
        )
        # Only speak about publishing when something was actually ingested. "0
        # ingested, chat cannot see them" is noise, not news.
        if self.ingested:
            summary += f" · {self.serving_sync.sentence(self.ingested)}"
            if self.serving_sync.state == SYNC_FAILED and self.serving_sync.action:
                summary += f" · {self.serving_sync.action}"
        return {
            "files_seen": self.files_seen,
            "ingested": self.ingested,
            "need_attention": self.need_attention,
            "per_class": dict(self.per_class),
            "files": [f.to_dict() for f in self.files],
            "ingest_id": self.ingest_id,
            "serving_sync": self.serving_sync.to_dict(),
            "summary": summary,
        }
