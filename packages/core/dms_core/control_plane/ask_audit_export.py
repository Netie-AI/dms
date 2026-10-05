"""BANK-02 (dms#269): serialise ask-audit rows as CSV (RFC 4180) or JSONL.

Every row carries the ledger-verification result, not only the response headers,
so a file saved on its own still says whether it was verified. The columns are
identical in both formats.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from dms_core.control_plane.ask_audit import AskAuditRecord

ExportFormat = Literal["csv", "jsonl"]
VerifyStatus = Literal["ok", "break", "unavailable"]

#: Characters a spreadsheet reads as the start of a formula (OWASP CSV injection).
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")

COLUMNS: tuple[str, ...] = (
    "ask_id",
    "asked_at",
    "actor",
    "actor_kind",
    "space_id",
    "question",
    "executed_sql",
    "tables_read",
    "badge",
    "badge_level",
    "abstain_reason",
    "row_count",
    "cortex_entry_id",
    "ask_mode",
    "export_verified",
    "ledger_verify_status",
    "ledger_first_break",
    "ledger_entries_checked",
    "verified_at",
)


@dataclass(frozen=True)
class LedgerVerification:
    """What ``ledger/verify`` said when the export was produced.

    ``verified`` is true only for ``status == "ok"``. A break and an unreachable
    ledger are both unverified: an export must not read as checked because the
    check could not be run.

    Contract 1.2.0 verifies the whole chain from ``start_seq``; it takes no end
    and no entry id. So this is the chain's state, which covers the exported
    range and everything around it. It proves the chain is intact, not that a
    row here matches a ledger payload; the contract has no entry read-back.
    """

    status: VerifyStatus
    first_break: str | None
    checked: int | None
    verified_at: datetime

    @property
    def verified(self) -> bool:
        return self.status == "ok"


def neutralise_formula(value: str) -> str:
    """Prefix ``'`` when a cell would be read as a formula. Text cells only."""
    if value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def _cells(rec: AskAuditRecord, ver: LedgerVerification) -> dict[str, Any]:
    r = rec.scrubbed()
    return {
        "ask_id": r.ask_id,
        "asked_at": r.asked_at.astimezone(UTC).isoformat(),
        "actor": r.actor,
        "actor_kind": r.actor_kind,
        "space_id": r.space_id,
        "question": r.question,
        "executed_sql": r.executed_sql,
        "tables_read": list(r.tables_read),
        "badge": r.badge,
        "badge_level": r.badge_level,
        "abstain_reason": r.abstain_reason,
        "row_count": r.row_count,
        "cortex_entry_id": r.cortex_entry_id,
        "ask_mode": r.ask_mode,
        "export_verified": ver.verified,
        "ledger_verify_status": ver.status,
        "ledger_first_break": ver.first_break or "",
        "ledger_entries_checked": ver.checked,
        "verified_at": ver.verified_at.astimezone(UTC).isoformat(),
    }


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return neutralise_formula(";".join(str(v) for v in value))
    return neutralise_formula(str(value))


def to_csv(rows: Sequence[AskAuditRecord], ver: LedgerVerification) -> str:
    """RFC 4180: CRLF, a header row, fields quoted when they hold , " CR or LF."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, dialect="excel", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    for rec in rows:
        cells = _cells(rec, ver)
        writer.writerow([_csv_cell(cells[c]) for c in COLUMNS])
    return buf.getvalue()


def to_jsonl(rows: Sequence[AskAuditRecord], ver: LedgerVerification) -> str:
    """One JSON object per line. ASCII-escaped so U+2028 cannot split a line."""
    lines = [json.dumps(_cells(rec, ver), ensure_ascii=True, separators=(",", ":")) for rec in rows]
    return "".join(f"{line}\n" for line in lines)


__all__ = [
    "COLUMNS",
    "ExportFormat",
    "LedgerVerification",
    "VerifyStatus",
    "neutralise_formula",
    "to_csv",
    "to_jsonl",
]
