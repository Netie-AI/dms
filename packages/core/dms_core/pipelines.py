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
    """What one promotion run did, including what it could not account for.

    ``source_rows`` and ``unmatched`` exist so row conservation is *sayable*.
    Without an input count, ``passed + quarantined == source_rows`` was not
    merely unasserted — it could not be written, by a test or by a customer. A
    two-source run of 1000 rows against 997 returned ``passed=997,
    quarantined=0`` and looked complete, because the three lost rows disappeared
    upstream of both counters and nothing recorded how many had set out.

    A warehouse has to answer one question above all others: did every row that
    entered end up either in the target or in quarantine? These fields are what
    make the answer checkable.
    """

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
    #: Rows read from the source relation(s) before any join or contract check.
    source_rows: int | None = None
    #: Rows lost to join cardinality — dropped by an INNER JOIN, or added by
    #: fan-out on a duplicated key. Negative means the join produced more rows
    #: than it consumed.
    unmatched: int = 0

    @property
    def reconciled(self) -> bool:
        """True when every input row is accounted for in the output.

        ``None`` source_rows means the run predates the count and cannot be
        reconciled — reported as unreconciled rather than assumed fine.
        """
        if self.source_rows is None:
            return False
        return self.unmatched == 0 and self.passed + self.quarantined == self.source_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target": self.target,
            "sources": list(self.sources),
            "source_rows": self.source_rows,
            "passed": self.passed,
            "quarantined": self.quarantined,
            "unmatched": self.unmatched,
            "reconciled": self.reconciled,
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
        """Whether this metric carries a completed ledger attestation.

        ``ledger_entry_id`` is part of the condition, not decoration. Without it the
        three remaining fields are just strings, and a caller that could set them could
        assert its own certification - which is exactly what ``POST /v1/pipelines/run``
        allowed before dms#76: a gold promote passed this gate with nothing ever
        appended to the chain.

        Requiring the entry id does not make this a *verification* - it is still a
        claim about state. ``run_promote`` re-reads the Cortex chain via
        ``verify_ledger`` before materialising gold; this property is the second
        lock, not the first. ``wiring.pipeline_run`` also refuses caller-supplied
        attestation fields so a request cannot mint these strings.
        """
        return bool(self.signature and self.steward_id and self.signed_at and self.ledger_entry_id)

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
