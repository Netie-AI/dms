"""PII-MASK-02 / dms#318: widen dms#303 masker. Synthetic values only.

No BIRD rows, no network, no keys, no Cortex. No skip/xfail.
Does not lift the BIRD block or dms#284's live block.
tests/test_pii_01.py and tests/test_pii_mask_check_01.py are not edited.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dms_core.pii import (
    classify_column,
    is_mask_token,
    mask_payload,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pii_mask_check import (  # noqa: E402
    SCAN_SHA256,
    bare_table,
    check_all,
)

# Honest remainder after the widen. Still FAIL on all three paths.
# dms#304 owns the deny/preflight file for these columns. This test does not
# relabel them. Value detection for these kinds is still dms#318 work.
REMAINING_FAIL_COLUMNS = frozenset(
    {
        ("drivers", "nationality"),
        ("member", "position"),
        ("patient", "diagnosis"),
        ("users", "location"),
        ("schools", "city"),
        ("schools", "county"),
        ("schools", "district"),
        ("schools", "doctype"),
        ("schools", "edopsname"),
        ("schools", "eilname"),
        ("schools", "mailcity"),
        ("schools", "school"),
        ("schools", "soctype"),
    }
)

# Obviously fake. Must fail on cdd3ae2a.
NAME_IN_TEXT = "met Ada Lovelace yesterday"
NAME_WHOLE = "Ada Lovelace"
LANDLINE = "03-5555-0101"
LANDLINE_IN_TEXT = "call 03-5555-0101 later"
LANDLINE_07 = "07-55550102"
PASSPORT = "A12345678"
PASSPORT_IN_TEXT = "passport A12345678 on file"
STREET = "123 Fake Street"
PLACE = "Testville CI"
ACCOUNT_IN_TEXT = "ref 123-456-789012 thanks"
ACCOUNT_URL = "https://example.invalid/path?account=123456789012"
DOB_OLD_YEAR = "born 1888-03-03 in test city"
DOB_NEW_YEAR = "born 2024-03-03 in test city"
DOB_WHOLE_OLD = "1888-12-31"
DOB_WHOLE_NEW = "2020-05-05"
UUID_PLAIN = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

# dms#303 shapes that already masked on cdd3ae2a. Each changed function keeps them.
OLD_MATCHES = (
    ("email", "notes", "contact pii.mask.check@example.invalid please"),
    ("phone", "notes", "+60135550101"),
    ("phone", "notes", "012-35550102"),
    ("phone", "aboutme", "(555) 555 0100"),
    ("phone", "aboutme", "call (555) 555 0100 later"),
    ("phone", "aboutme", "+44 7700 900123"),
    ("phone", "notes", "555.555.0100"),
    ("account", "body", "card 4111111111111111 on file"),
    ("account", "notes", "123-456-789012"),
    ("nric", "notes", "900101-14-5678"),
    ("nric", "notes", "note 900101145678 end"),
    ("dob", "text", "born 1990-01-15 in test city"),
    ("dob", "birth_date", "1990-01-15"),
    ("dob", "dob", "1990-01-15"),
    ("dob", "birthday", "1990-01-15"),
    ("dob", "date_of_birth", "1990-01-15"),
    ("name", "customer_name", "Ada Lovelace"),
    ("name", "displayname", "Ada Lovelace"),
    ("name", "forename", "Ada"),
    ("name", "first_name", "Ada"),
    ("name", "player_name", "Ada Lovelace"),
)


def _blob(obj: object) -> str:
    return json.dumps(obj, default=str)


def _masked(column: str, sample: str, *, table: str | None = None) -> str:
    got = mask_payload(
        text=f"field {sample}",
        rows=[{column: sample, "n": 1}],
        values=[{column: sample}],
    )
    assert classify_column(column, [sample], table=table) is not None
    return _blob(got)


def test_scan_sha_unchanged() -> None:
    assert SCAN_SHA256 == (
        "878f664d1d920bee99b3859285dd669b86edc16945288a5251c2fa66fae0c330"
    )


def test_free_text_person_name() -> None:
    assert classify_column("text", [NAME_WHOLE], table="comments") == "name"
    assert classify_column("body", [NAME_IN_TEXT], table="posts") == "name"
    assert classify_column("title", [NAME_WHOLE], table="posts") == "name"
    assert classify_column("aboutme", [NAME_WHOLE], table="users") == "name"
    assert classify_column("comment", [NAME_IN_TEXT], table="posthistory") == "name"
    for column, sample in (
        ("text", NAME_WHOLE),
        ("body", NAME_IN_TEXT),
        ("title", NAME_WHOLE),
    ):
        assert NAME_WHOLE not in _masked(column, sample)
        assert "Ada Lovelace" not in _masked(column, sample)


def test_admin_name_columns() -> None:
    for column in ("admfname1", "admfname2", "admlname1", "admlname3"):
        assert classify_column(column, [NAME_WHOLE], table="schools") == "name"
        assert NAME_WHOLE not in _masked(column, NAME_WHOLE)


def test_my_landline_in_free_text() -> None:
    assert classify_column("aboutme", [LANDLINE_IN_TEXT], table="users") == "phone"
    assert classify_column("text", [LANDLINE], table="comments") == "phone"
    assert classify_column("notes", [LANDLINE_07]) == "phone"
    assert LANDLINE not in _masked("aboutme", LANDLINE_IN_TEXT)
    assert LANDLINE_07 not in _masked("body", f"desk {LANDLINE_07}")


def test_passport_value() -> None:
    assert classify_column("aboutme", [PASSPORT_IN_TEXT], table="users") == "passport"
    assert classify_column("body", [PASSPORT], table="posts") == "passport"
    assert PASSPORT not in _masked("text", PASSPORT_IN_TEXT)


def test_street_address_column_and_value() -> None:
    assert classify_column("street", [STREET], table="schools") == "address"
    assert classify_column("mailstreet", [NAME_WHOLE], table="schools") == "address"
    assert classify_column("streetabr", ["AB"], table="schools") == "address"
    assert classify_column("mailstrabr", [NAME_WHOLE], table="schools") == "address"
    assert classify_column("notes", [f"ship to {STREET} please"]) == "address"
    assert STREET not in _masked("street", STREET)
    assert STREET not in _masked("notes", f"ship to {STREET} please")


def test_place_code_location_value() -> None:
    assert classify_column("location", [PLACE], table="users") == "address"
    assert classify_column("city", [PLACE], table="schools") == "address"
    assert PLACE not in _masked("location", PLACE)


def test_account_in_text_and_url() -> None:
    assert classify_column("websiteurl", [ACCOUNT_IN_TEXT], table="users") == "account"
    assert classify_column("profileimageurl", [ACCOUNT_URL], table="users") == "account"
    assert classify_column("aboutme", [ACCOUNT_URL], table="users") == "account"
    assert "123-456-789012" not in _masked("websiteurl", ACCOUNT_IN_TEXT)
    assert "123456789012" not in _masked("aboutme", ACCOUNT_URL)


def test_dob_every_year_in_free_text() -> None:
    assert classify_column("notes", [DOB_OLD_YEAR]) == "dob"
    assert classify_column("text", [DOB_NEW_YEAR], table="comments") == "dob"
    assert "1888-03-03" not in _masked("notes", DOB_OLD_YEAR)
    assert "2024-03-03" not in _masked("text", DOB_NEW_YEAR)


def test_whole_value_dob_without_dob_name() -> None:
    # Birth-named columns still mask any year. A date-shaped value on a
    # column that does not point at birth stays visible (OVERMASK-01).
    assert classify_column("born_on", [DOB_WHOLE_OLD]) == "dob"
    assert classify_column("dateofbirth", [DOB_WHOLE_NEW]) == "dob"
    assert classify_column("year_of_birth", [DOB_WHOLE_OLD], table="patient") == "dob"
    assert classify_column("enrolled", [DOB_WHOLE_NEW], table="patient") is None
    assert classify_column("notes", [DOB_WHOLE_OLD]) is None
    assert DOB_WHOLE_OLD not in _masked("born_on", DOB_WHOLE_OLD)
    visible = mask_payload(
        text=DOB_WHOLE_NEW,
        rows=[{"enrolled": DOB_WHOLE_NEW}],
    )
    assert visible["rows"][0]["enrolled"] == DOB_WHOLE_NEW
    assert visible["text"] == DOB_WHOLE_NEW
    assert classify_column("order_date", ["1990-01-15"]) is None
    assert classify_column("last_audit_date", ["2024-01-15"]) is None
    assert classify_column("order_date", ["1990-01-15"], table="patient") is None
    assert classify_column("month", ["2024-01-01"]) is None


def test_uuid_shaped_column_not_skipped() -> None:
    assert classify_column("revisionguid", [PASSPORT]) == "passport"
    assert PASSPORT not in _masked("revisionguid", PASSPORT)
    assert classify_column("revisionguid", [UUID_PLAIN]) is None
    got = mask_payload(rows=[{"revisionguid": UUID_PLAIN}], text=UUID_PLAIN)
    assert got["rows"][0]["revisionguid"] == UUID_PLAIN


def test_old_matches_still_mask() -> None:
    """Every changed function still masks the dms#303 / dms#272 shapes."""
    for kind, column, sample in OLD_MATCHES:
        got = classify_column(column, [sample])
        assert got == kind, (column, sample, got)
        blob = _blob(
            mask_payload(
                text=f"field {sample}",
                rows=[{column: sample}],
                values=[{column: sample}],
            )
        )
        for piece in (
            "pii.mask.check@example.invalid",
            "+60135550101",
            "012-35550102",
            "(555) 555 0100",
            "+44 7700 900123",
            "555.555.0100",
            "4111111111111111",
            "123-456-789012",
            "900101-14-5678",
            "900101145678",
            "1990-01-15",
            "Ada Lovelace",
        ):
            if piece in sample:
                assert piece not in blob, (column, piece)


def test_non_pii_stays_visible() -> None:
    assert classify_column("supplier_name", ["Northshore Materials"]) is None
    assert classify_column("name", ["Warehouse A"], table="locations") is None
    assert classify_column("city", ["Kuala Lumpur"]) is None
    assert classify_column("region", ["North", "South"]) is None
    got = mask_payload(
        text="Warehouse Ops peaks at Warehouse A",
        rows=[{"location": "Warehouse A", "city": "Kuala Lumpur", "n": 1}],
    )
    assert got["rows"][0]["location"] == "Warehouse A"
    assert got["rows"][0]["city"] == "Kuala Lumpur"
    assert "Warehouse Ops" in got["text"]
    assert "Warehouse A" in got["text"]


def test_three_path_table_258_pass_27_fail() -> None:
    """258 PASS / 27 FAIL / 285. The 27 stay FAIL. No EXCLUDE relabel."""
    rows = check_all()
    assert len(rows) == 285
    fails = [r for r in rows if "FAIL" in (r.path_a, r.path_b, r.path_c)]
    passed = [r for r in rows if r.path_a == "PASS"]
    assert len(fails) == 27, [
        f"{bare_table(r.cell.table)}.{r.cell.column} {r.cell.pattern} "
        f"a={r.path_a} b={r.path_b} c={r.path_c}"
        for r in fails
    ]
    assert len(passed) == 258
    assert all(r.path_a == r.path_b == r.path_c == "FAIL" for r in fails)
    assert all(r.path_b == "PASS" and r.path_c == "PASS" for r in passed)
    assert all(r.cell.pattern == "person_name_shape_low" for r in fails)
    keys = {(bare_table(r.cell.table), r.cell.column.lower()) for r in fails}
    assert keys == set(REMAINING_FAIL_COLUMNS)
    assert all(r.synth and not is_mask_token(r.synth) for r in rows)
    other = [r for r in rows if r.path_a not in {"PASS", "FAIL"}]
    assert not other, other
