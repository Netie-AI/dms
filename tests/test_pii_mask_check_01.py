"""PII-MASK-CHECK-01 / dms#303: free-text PII + NANP/intl phones vs dms#272.

Synthetic values only. No BIRD rows, no network, no keys, no Cortex.
CI fixtures, not live. Not COMPLETE. Existing tests/test_pii_01.py is untouched.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from dms_core.pii import (
    classify_column,
    column_is_pii,
    is_mask_token,
    mask_payload,
    sanitize_retrieve_parts,
)
from dms_executor.semantic_retrieve import retrieve_value_encodings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pii_mask_check import (  # noqa: E402
    FLAGGED_CSV,
    FLAGGED_TABLES,
    SCAN_SHA256,
    SYNTH_BY_PATTERN,
    check_all,
    check_cell,
    load_flagged,
)

# Obviously fake. Must fail on 0c81026 / 3f0353a6 (fullmatch-only, +60 phones).
EMAIL_IN_TEXT = "contact pii.mask.check@example.invalid please"
NANP_WHOLE = "(555) 555 0100"
NANP_DOTS = "555.555.0100"
NANP_IN_TEXT = "call (555) 555 0100 later"
INTL_IN_TEXT = "reach me on +44 7700 900123 thanks"
CARD_IN_TEXT = "card 4111111111111111 on file"
DOB_IN_TEXT = "born 1990-01-15 in test city"
DOB_WHOLE = "1990-01-15"
MY_MOBILE = "+60135550101"
MY_LOCAL = "012-35550102"

FLAGGED_HEADER = ("db", "table", "column", "pattern", "match_count")
EXPECTED_FLAGGED_ROWS = 285


def _blob(obj: object) -> str:
    return json.dumps(obj, default=str)


def test_scan_sha256_constant() -> None:
    assert SCAN_SHA256 == (
        "878f664d1d920bee99b3859285dd669b86edc16945288a5251c2fa66fae0c330"
    )


def test_flagged_columns_csv_is_counts_only() -> None:
    text = FLAGGED_CSV.read_text(encoding="utf-8")
    assert "@" not in text
    assert "4111111111111111" not in text
    with FLAGGED_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames or ()) == FLAGGED_HEADER
        rows = list(reader)
    assert len(rows) == EXPECTED_FLAGGED_ROWS
    found = {r["table"].split(".")[-1].removeprefix("public_").lower() for r in rows}
    assert found == set(FLAGGED_TABLES)
    for row in rows:
        assert int(row["match_count"]) > 0
        assert "*" not in row["column"]
        assert row["pattern"] != "__rows_scanned__"


def test_free_text_email_phone_card_dob_are_pii() -> None:
    assert classify_column("text", [EMAIL_IN_TEXT], table="comments") == "email"
    assert classify_column("aboutme", [NANP_IN_TEXT], table="users") == "phone"
    assert classify_column("aboutme", [INTL_IN_TEXT], table="users") == "phone"
    assert classify_column("body", [CARD_IN_TEXT], table="posts") == "account"
    assert classify_column("text", [DOB_IN_TEXT], table="comments") == "dob"
    assert column_is_pii("notes", [EMAIL_IN_TEXT]) is True
    assert column_is_pii("notes", [NANP_WHOLE]) is True
    assert classify_column("notes", [NANP_DOTS]) == "phone"


def test_client_dob_and_users_phone_shapes() -> None:
    assert classify_column("birth_date", [DOB_WHOLE], table="client") == "dob"
    assert classify_column("dob", [DOB_WHOLE], table="drivers") == "dob"
    assert classify_column("birthday", [DOB_WHOLE], table="patient") == "dob"
    assert classify_column("aboutme", [NANP_WHOLE], table="users") == "phone"
    assert classify_column("aboutme", ["+44 7700 900123"], table="users") == "phone"
    assert classify_column("phone", [NANP_WHOLE], table="member") == "phone"


def test_non_dob_date_column_not_flagged_by_value_alone() -> None:
    assert classify_column("last_audit_date", ["2024-01-15"]) is None
    assert classify_column("order_date", [DOB_WHOLE]) is None


def test_dms272_seed_shapes_still_caught() -> None:
    assert classify_column("email", ()) == "email"
    assert classify_column("phone", ()) == "phone"
    assert classify_column("notes", [MY_MOBILE]) == "phone"
    assert classify_column("notes", [MY_LOCAL]) == "phone"
    assert classify_column("notes", ["aisha.zulkifli@pii-seed.example"]) == "email"
    assert classify_column("notes", ["900101-14-5678"]) == "nric"
    assert classify_column("notes", ["123-456-789012"]) == "account"
    assert classify_column("customer_name", ()) == "name"


def test_retrieve_and_sanitize_drop_free_text_email(tmp_path: Path) -> None:
    import duckdb

    lake = tmp_path / "pii_mask_check.duckdb"
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE TABLE comments (text VARCHAR)")
        con.execute("INSERT INTO comments VALUES (?)", [EMAIL_IN_TEXT])
    finally:
        con.close()
    enc = retrieve_value_encodings(
        lake,
        [{"table": "comments", "columns": ["text"]}],
        {"comments", "text"},
    )
    assert EMAIL_IN_TEXT not in _blob(enc)
    assert "comments.text" not in enc
    cleaned = sanitize_retrieve_parts(
        {"encodings": {"comments.text": [EMAIL_IN_TEXT]}, "bound_values": {"x": EMAIL_IN_TEXT}}
    )
    assert EMAIL_IN_TEXT not in _blob(cleaned)
    payload = {"ontology": cleaned}
    assert EMAIL_IN_TEXT not in _blob(payload)


def test_export_masks_nanp_intl_card_dob_in_rows() -> None:
    got = mask_payload(
        text=f"{NANP_IN_TEXT} {INTL_IN_TEXT} {CARD_IN_TEXT} {DOB_IN_TEXT}",
        rows=[
            {"aboutme": NANP_IN_TEXT},
            {"aboutme": INTL_IN_TEXT},
            {"body": CARD_IN_TEXT},
            {"text": DOB_IN_TEXT},
        ],
    )
    blob = _blob(got)
    for raw in (NANP_WHOLE, "+44 7700 900123", "4111111111111111", "1990-01-15"):
        assert raw not in blob, raw
    for row in got["rows"]:
        val = next(iter(row.values()))
        assert is_mask_token(val) or str(val).startswith("DMSMASK_")


def test_unknown_pattern_fails_closed(tmp_path: Path) -> None:
    from pii_mask_check import FlaggedCell

    lake = tmp_path / "pii_unknown.duckdb"
    lake.touch()
    cell = FlaggedCell("ci", "users", "aboutme", "no_such_pattern", 1)
    got = check_cell(cell, lake)
    assert got.path_a == "FAIL"
    assert got.path_b == "FAIL"
    assert got.path_c == "FAIL"
    assert got.note == "unknown_pattern"


def test_script_covers_flagged_cells_and_target_patterns_pass() -> None:
    rows = check_all()
    assert len(rows) == EXPECTED_FLAGGED_ROWS
    cells = load_flagged()
    assert {c.pattern for c in cells} <= set(SYNTH_BY_PATTERN) | {r.cell.pattern for r in rows}
    target = {
        "email",
        "pii_column_name:email",
        "phone_intl",
        "phone_nanp_like_low",
        "pii_column_name:phone",
        "pii_column_name:dob",
        "payment_card_luhn",
        "nric_undashed_low",
        "phone_my_mobile",
    }
    misses: list[str] = []
    for row in rows:
        if row.cell.pattern not in target:
            continue
        if row.path_a != "PASS" or row.path_b != "PASS" or row.path_c != "PASS":
            misses.append(
                f"{row.cell.table}.{row.cell.column} {row.cell.pattern} "
                f"a={row.path_a} b={row.path_b} c={row.path_c} {row.note}"
            )
    assert not misses, misses
    assert any(
        r.cell.pattern == "phone_intl" and "users" in r.cell.table.lower() for r in rows
    )
    assert any(
        r.cell.pattern == "phone_nanp_like_low" and "users" in r.cell.table.lower()
        for r in rows
    )
    assert any(
        r.cell.pattern == "pii_column_name:dob" and "client" in r.cell.table.lower()
        for r in rows
    )
    # Fail-closed: a passport cell is classified, not skipped, and may FAIL.
    passports = [r for r in rows if r.cell.pattern == "passport_generic_like_low"]
    assert passports
    assert all(r.path_a in {"PASS", "FAIL"} and r.note != "skipped" for r in passports)
