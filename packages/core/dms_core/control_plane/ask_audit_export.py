"""BANK-02 (dms#269): serialise ask-audit rows as CSV (RFC 4180) or JSONL.

Every row carries the ledger-verification result, the verification scope, and the
store and unrecorded-ask count, not only the response headers, so a file saved on
its own still says whether it was verified and how complete it is. The columns
are identical in both formats.
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from dms_core.control_plane.ask_audit import AskAuditRecord

ExportFormat = Literal["csv", "jsonl"]
VerifyStatus = Literal["ok", "break", "incomplete", "unavailable"]

#: What "verified" covers, said on every row and in a response header.
VERIFY_SCOPE = "chain_integrity_only; rows are not matched to ledger entries"

#: Characters a spreadsheet reads as the start of a formula (OWASP CSV injection).
_FORMULA_LEAD = ("=", "+", "-", "@")
_CONTROL_LEAD = ("\t", "\r", "\n")

#: Blanks a spreadsheet skips before it decides a cell is a formula.
_LEAD_BLANKS = (
    " "
    "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u200b\u200c\u200d\u2060\u202f\u205f\u3000\ufeff\t\r\n\x0b\x0c"
)

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
    "redactions",
    "truncated",
    "export_verified",
    "ledger_verify_status",
    "ledger_verify_scope",
    "ledger_first_break",
    "ledger_entries_checked",
    "verified_at",
    "store_backend",
    "unrecorded_asks_since_start",
)


@dataclass(frozen=True)
class LedgerVerification:
    """What ``ledger/verify`` said when the export was produced.

    ``verified`` is true only for ``status == "ok"``. A break, an unreachable
    ledger, and a verify that covered fewer entries than the export points at
    (``incomplete``, including a verify that checked none) are all unverified: an
    export must not read as checked because the check could not be run or did not
    reach the entries it names.

    Contract 1.2.0 verifies the whole chain from ``start_seq``; it takes no end
    and no entry id. So this is the chain's state, which covers the exported
    range and everything around it. It proves the chain is intact, not that a
    row here matches a ledger payload; the contract has no entry read-back. That
    is ``VERIFY_SCOPE``, on every row.
    """

    status: VerifyStatus
    first_break: str | None
    checked: int | None
    verified_at: datetime

    @property
    def verified(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ExportMeta:
    """What the file says about itself, repeated on every row."""

    verification: LedgerVerification
    #: ``postgres`` or ``memory``: where these rows were read from.
    store_backend: str
    #: Asks whose row could not be written or was evicted since the API process
    #: started. A per-process counter: it resets when the process restarts.
    unrecorded_asks: int


def neutralise_formula(value: str) -> str:
    """Prefix ``'`` when a cell would be read as a formula. Text cells only.

    Looks past leading whitespace (including NBSP and zero-width characters) and
    compares the NFKC form, so ``" =1+1"``, a no-break space or a fullwidth ``=``
    are caught as well as a plain ``=``.
    """
    if not value:
        return value
    probe = unicodedata.normalize("NFKC", value).lstrip(_LEAD_BLANKS)
    if value.startswith(_CONTROL_LEAD) or probe.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def _cells(rec: AskAuditRecord, meta: ExportMeta) -> dict[str, Any]:
    r = rec.scrubbed()
    ver = meta.verification
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
        "redactions": r.redactions,
        "truncated": r.truncated,
        "export_verified": ver.verified,
        "ledger_verify_status": ver.status,
        "ledger_verify_scope": VERIFY_SCOPE,
        "ledger_first_break": ver.first_break or "",
        "ledger_entries_checked": ver.checked,
        "verified_at": ver.verified_at.astimezone(UTC).isoformat(),
        "store_backend": meta.store_backend,
        "unrecorded_asks_since_start": meta.unrecorded_asks,
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


def to_csv(rows: Sequence[AskAuditRecord], meta: ExportMeta) -> str:
    """RFC 4180: CRLF, a header row, fields quoted when they hold , " CR or LF.

    ``tables_read`` is joined with ``;``, which a semicolon-locale spreadsheet may
    split into columns on import; JSONL carries it as an array.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, dialect="excel", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    for rec in rows:
        cells = _cells(rec, meta)
        writer.writerow([_csv_cell(cells[c]) for c in COLUMNS])
    return buf.getvalue()


def to_jsonl(rows: Sequence[AskAuditRecord], meta: ExportMeta) -> str:
    """One JSON object per line. ASCII-escaped so U+2028 cannot split a line."""
    lines = [
        json.dumps(_cells(rec, meta), ensure_ascii=True, separators=(",", ":")) for rec in rows
    ]
    return "".join(f"{line}\n" for line in lines)


__all__ = [
    "COLUMNS",
    "VERIFY_SCOPE",
    "ExportFormat",
    "ExportMeta",
    "LedgerVerification",
    "VerifyStatus",
    "neutralise_formula",
    "to_csv",
    "to_jsonl",
]
