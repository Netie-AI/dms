"""PII-MASK-CHECK-01 (dms#303): per-flagged-column mask check with synthetic values.

Counts-only scan source sha256:
  878f664d1d920bee99b3859285dd669b86edc16945288a5251c2fa66fae0c330
CI fixtures, not live. No BIRD values. No network. Fake transport only.

Prints one row per flagged_columns.csv cell with PASS/FAIL for:
  (a) retrieve_value_encodings then sanitize_retrieve_parts drops or masks it
  (b) dms#272 Masker / export path masks it
  (c) Cortex-bound payload (encodings in an insights-shaped dict) has no raw value

Unknown pattern names are FAIL, not skipped. Unclassifiable = FAIL.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    ROOT / "packages" / "core",
    ROOT / "packages" / "executor",
    ROOT / "packages" / "cortex_client",
    ROOT / "packages" / "ledger",
):
    sys.path.insert(0, str(_p))

from dms_core.pii import (  # noqa: E402
    fail_closed_mask_envelope,
    mask_payload,
    sanitize_retrieve_parts,
)
from dms_executor.semantic_retrieve import retrieve_value_encodings  # noqa: E402

SCAN_SHA256 = "878f664d1d920bee99b3859285dd669b86edc16945288a5251c2fa66fae0c330"
FLAGGED_CSV = ROOT / "tests" / "fixtures" / "pii_hold" / "flagged_columns.csv"
FLAGGED_TABLES = frozenset(
    {
        "schools",
        "member",
        "client",
        "patient",
        "player",
        "drivers",
        "users",
        "posts",
        "posthistory",
        "comments",
    }
)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RETRIEVE_SKIP = re.compile(
    r"(amount|qty|quantity|cost|kg|myr|score|load|capacity|date|id)$", re.I
)
_FREE_TEXT_COLS = frozenset(
    {
        "text",
        "body",
        "comment",
        "aboutme",
        "title",
        "notes",
        "location",
        "websiteurl",
        "profileimageurl",
        "mailstreet",
        "street",
    }
)

# Obviously fake shapes. Never real BIRD rows.
SYNTH_BY_PATTERN: dict[str, str] = {
    "email": "pii.mask.check@example.invalid",
    "pii_column_name:email": "pii.mask.check@example.invalid",
    "phone_my_mobile": "+60135550199",
    "phone_my_fixed": "03-5555-0101",
    "phone_intl": "+44 7700 900123",
    "phone_nanp_like_low": "(555) 555 0100",
    "pii_column_name:phone": "(555) 555 0100",
    "pii_column_name:dob": "1990-01-15",
    "nric_undashed_low": "900101145678",
    "payment_card_luhn": "4111111111111111",
    "bank_account_like_low": "123-456-789012",
    "person_name_column_low": "Ada Lovelace",
    "person_name_shape_low": "Ada Lovelace",
    "passport_generic_like_low": "A12345678",
    "pii_column_name:person_location_low": "Testville CI",
    "pii_column_name:address": "123 Fake Street",
}


@dataclass(frozen=True)
class FlaggedCell:
    db: str
    table: str
    column: str
    pattern: str
    match_count: int


@dataclass(frozen=True)
class CheckResult:
    cell: FlaggedCell
    synth: str
    path_a: str
    path_b: str
    path_c: str
    note: str

    def verdict_row(self) -> str:
        return (
            f"| {self.cell.db} | {self.cell.table} | {self.cell.column} | "
            f"{self.cell.pattern} | {self.path_a} | {self.path_b} | {self.path_c} | "
            f"{self.note} |"
        )


def bare_table(table: str) -> str:
    parts = str(table or "").replace('"', "").split(".")
    last = parts[-1] if parts else ""
    if last.lower().startswith("public_"):
        last = last[7:]
    return last.lower()


def load_flagged(path: Path = FLAGGED_CSV) -> list[FlaggedCell]:
    rows: list[FlaggedCell] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            try:
                count = int(raw.get("match_count") or 0)
            except ValueError:
                count = 0
            rows.append(
                FlaggedCell(
                    db=str(raw.get("db") or ""),
                    table=str(raw.get("table") or ""),
                    column=str(raw.get("column") or ""),
                    pattern=str(raw.get("pattern") or ""),
                    match_count=count,
                )
            )
    return rows


def _wrap_synth(column: str, pattern: str, synth: str) -> str:
    col = column.lower()
    value_pattern = pattern.startswith("phone_") or pattern in {
        "email",
        "payment_card_luhn",
        "bank_account_like_low",
        "nric_undashed_low",
        "passport_generic_like_low",
    }
    if col in _FREE_TEXT_COLS and value_pattern:
        return f"note {synth} end"
    return synth


def _contains_raw(blob: object, synth: str) -> bool:
    if not synth:
        return False
    return synth in json.dumps(blob, default=str)


def _toks(table: str, column: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", f"{table} {column}".lower().replace("_", " "))
    return {p for p in parts if len(p) >= 2}


def _path_a(table: str, column: str, sample: str, synth: str, warehouse: Path) -> str:
    """PASS if retrieve+sanitize does not carry the synthetic raw value."""
    import duckdb

    if not _IDENT.match(table) or not _IDENT.match(column):
        return "FAIL"
    con = duckdb.connect(str(warehouse))
    try:
        con.execute(f'CREATE OR REPLACE TABLE "{table}" ("{column}" VARCHAR)')
        con.execute(f'INSERT INTO "{table}" VALUES (?)', [sample])
    finally:
        con.close()
    enc = retrieve_value_encodings(
        warehouse,
        [{"table": table, "columns": [column]}],
        _toks(table, column),
    )
    cleaned = sanitize_retrieve_parts({"encodings": enc, "bound_values": {}})
    if _contains_raw(cleaned, synth):
        return "FAIL"
    return "PASS"


def _path_b(column: str, sample: str, synth: str) -> str:
    masked = mask_payload(
        text=f"field {sample}",
        rows=[{column: sample, "n": 1}],
        values=[{column: sample}],
    )
    env = fail_closed_mask_envelope(
        {
            "answer_id": "ans_pii_mask_check",
            "badge": "L2_VALIDATED",
            "text": f"field {sample}",
            "rows": [{column: sample, "n": 1}],
            "values": [{column: sample}],
            "as_of": "2026-09-25T00:00:00Z",
            "sql_used": "SELECT 1",
            "audit_id": "aud_pii_mask_check",
            "assumptions": [],
            "contributing_sources": [],
        }
    )
    if _contains_raw(masked, synth) or _contains_raw(env, synth):
        return "FAIL"
    return "PASS"


def _path_c(table: str, column: str, sample: str, synth: str, warehouse: Path) -> str:
    """PASS if the insights-shaped Cortex-bound dict has no synthetic raw value."""
    enc = retrieve_value_encodings(
        warehouse,
        [{"table": table, "columns": [column]}],
        _toks(table, column),
    )
    cleaned = sanitize_retrieve_parts({"encodings": enc, "bound_values": {column: sample}})
    payload = {
        "ontology": {
            "encodings": cleaned.get("encodings") or {},
            "bound_values": cleaned.get("bound_values") or {},
            "schema": [{"table": table, "columns": [column]}],
        }
    }
    if _contains_raw(payload, synth):
        return "FAIL"
    return "PASS"


def check_cell(cell: FlaggedCell, warehouse: Path) -> CheckResult:
    note = ""
    synth = SYNTH_BY_PATTERN.get(cell.pattern)
    if not synth:
        return CheckResult(cell, "", "FAIL", "FAIL", "FAIL", "unknown_pattern")
    table = bare_table(cell.table)
    column = cell.column
    if bare_table(cell.table) not in FLAGGED_TABLES:
        return CheckResult(cell, synth, "FAIL", "FAIL", "FAIL", "unflagged_table")
    sample = _wrap_synth(column, cell.pattern, synth)
    if _RETRIEVE_SKIP.search(column):
        note = "retrieve_skip"
    try:
        path_a = _path_a(table, column, sample, synth, warehouse)
        path_b = _path_b(column, sample, synth)
        path_c = _path_c(table, column, sample, synth, warehouse)
    except Exception as exc:  # fail closed
        return CheckResult(
            cell, synth, "FAIL", "FAIL", "FAIL", f"error:{type(exc).__name__}"
        )
    return CheckResult(cell, synth, path_a, path_b, path_c, note)


def check_all(path: Path = FLAGGED_CSV) -> list[CheckResult]:
    cells = load_flagged(path)
    out: list[CheckResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        for idx, cell in enumerate(cells):
            # One file per cell. DuckDB 1.5 unique-file-handle 500s a second
            # attach of the same path (retrieve opens its own connect_file).
            warehouse = Path(tmp) / f"pii_mask_check_{idx}.duckdb"
            out.append(check_cell(cell, warehouse))
    return out


def summary(rows: list[CheckResult]) -> dict[str, dict[str, int]]:
    tally = {
        "a": {"PASS": 0, "FAIL": 0},
        "b": {"PASS": 0, "FAIL": 0},
        "c": {"PASS": 0, "FAIL": 0},
    }
    for row in rows:
        tally["a"][row.path_a] = tally["a"].get(row.path_a, 0) + 1
        tally["b"][row.path_b] = tally["b"].get(row.path_b, 0) + 1
        tally["c"][row.path_c] = tally["c"].get(row.path_c, 0) + 1
    return tally


def render_markdown(rows: list[CheckResult]) -> str:
    lines = [
        f"scan_sha256: `{SCAN_SHA256}`",
        "",
        "| db | table | column | pattern | a | b | c | note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(row.verdict_row() for row in rows)
    tallied = summary(rows)
    lines.extend(
        [
            "",
            (
                f"summary: a PASS {tallied['a']['PASS']} FAIL {tallied['a']['FAIL']}; "
                f"b PASS {tallied['b']['PASS']} FAIL {tallied['b']['FAIL']}; "
                f"c PASS {tallied['c']['PASS']} FAIL {tallied['c']['FAIL']}; "
                f"n={len(rows)}"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    del argv
    rows = check_all()
    sys.stdout.write(render_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
