"""Pipeline domain types — no DuckDB, no FastAPI.

Promote pipelines are declarative YAML under ``pipelines/``.
Swap scenario for YAML parsing lives in executor (PyYAML → ruamel.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


LineageMode = Literal["propagate", "aggregate"]


@dataclass(frozen=True)
class ColumnContract:
    type: str
    required: bool = False
    min: float | None = None
    max: float | None = None
    allowed_from: str | None = None  # e.g. dim.region


@dataclass(frozen=True)
class PipelineContract:
    columns: dict[str, ColumnContract]
    dedup_key: list[str] = field(default_factory=list)
    expectations: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineDef:
    """Declarative bronze→silver (or silver→gold) pipeline."""

    target: str
    sources: list[str]
    lineage: LineageMode
    contract: PipelineContract | None = None
    lineage_reason: str | None = None
    join_on: list[str] | None = None  # optional equi-join keys for multi-source
    name: str | None = None
    path: str | None = None

    @property
    def is_silver(self) -> bool:
        return self.target.startswith("silver.")

    @property
    def is_gold(self) -> bool:
        return self.target.startswith("gold.")

    @property
    def quarantine_table(self) -> str:
        # quarantine.silver_sales for target silver.sales
        flat = self.target.replace(".", "_")
        return f"quarantine.{flat}"


@dataclass
class PromoteReceipt:
    run_id: str
    target: str
    sources: list[str]
    passed: int
    quarantined: int
    counts_by_reason: dict[str, int] = field(default_factory=dict)
    dedup_key: list[str] = field(default_factory=list)
    lineage: str = "propagate"
    table: str | None = None
    quarantine_table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target": self.target,
            "sources": list(self.sources),
            "passed": self.passed,
            "quarantined": self.quarantined,
            "counts_by_reason": dict(self.counts_by_reason),
            "dedup_key": list(self.dedup_key),
            "lineage": self.lineage,
            "table": self.table,
            "quarantine_table": self.quarantine_table,
        }


@dataclass
class ContractProposal:
    """Proposed contract from observed bronze — never auto-applied."""

    source: str
    columns: dict[str, dict[str, Any]]
    candidate_keys: list[list[str]]
    null_rates: dict[str, float]
    row_count: int
    note: str = "proposal only — steward must review before applying"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "columns": self.columns,
            "candidate_keys": self.candidate_keys,
            "null_rates": self.null_rates,
            "row_count": self.row_count,
            "note": self.note,
        }


@dataclass
class GoldMetricDef:
    """Steward-signed metric definition required before gold promote."""

    metric_id: str
    name: str
    sql: str
    steward_id: str
    signed_at: str | None = None
    ledger_entry_id: str | None = None
    signature: str | None = None  # steward attestation token / hash

    @property
    def is_signed(self) -> bool:
        return bool(self.signature and self.steward_id and self.signed_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "name": self.name,
            "sql": self.sql,
            "steward_id": self.steward_id,
            "signed_at": self.signed_at,
            "ledger_entry_id": self.ledger_entry_id,
            "signature": self.signature,
            "is_signed": self.is_signed,
        }
