"""OVERMASK-01 / dms#318: ordinary dates and codes stay visible.

Synthetic values only. No BIRD rows, no network, no keys, no Cortex.
No skip, xfail, or importorskip.

Edits that belong in tests/test_pii_mask_02.py are named there. This file
is the new acceptance: what must stay visible, and what must stay masked.
Lineage cases call build_answer_envelope, which is the served path that
calls fail_closed_mask_payload.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from dms_core.pii import (
    classify_column,
    is_mask_token,
    mask_payload,
    reset_untraced_date_column_count,
    untraced_date_column_count,
)
from dms_executor.envelope import build_answer_envelope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pii_mask_check import check_all  # noqa: E402

# These eight strings are masked on ca34419d. They must stay visible.
_DATE_COLUMNS = (
    ("ts", "2026-09-30 10:00:00"),
    ("created_at", "2026-10-01"),
    ("shipped_at", "2026-09-30"),
    ("event_time", "2026-09-30"),
    ("audit_due", "2026-09-30"),
)
_PROSE_VISIBLE = (
    "As of 2026-10-01",
    "Peak on 2026-09-30",
    "SKU AB1234567",
)


def _served(
    *,
    text: str,
    rows: list[dict],
    sql_used: str | None,
) -> dict:
    """The served constructor. Masking runs at envelope.py fail_closed_mask_payload."""
    env = build_answer_envelope(
        answer_id="ans_overmask",
        text=text,
        badge="L2_VALIDATED",
        sql_used=sql_used,
        rows=rows,
        audit_id="aud_overmask",
        as_of="2026-10-01T00:00:00Z",
        ask_mode="live",
    )
    assert env["abstained"] is False, env.get("text")
    return env


def test_mask_payload_keeps_ordinary_dates_codes_and_native_dates() -> None:
    """Fails on ca34419d: each of these eight strings, plus date and datetime."""
    row = {column: value for column, value in _DATE_COLUMNS}
    row["qty"] = 12
    row["amount"] = 12847.5
    got = mask_payload(
        text=" ".join(_PROSE_VISIBLE),
        rows=[row],
        values=[{"qty": 12, "amount": 12847.5}],
    )
    out = got["rows"][0]
    for column, value in _DATE_COLUMNS:
        assert out[column] == value, column
        assert not is_mask_token(out[column])
    assert out["qty"] == 12
    assert out["amount"] == 12847.5
    for prose in _PROSE_VISIBLE:
        assert prose in got["text"], prose
    assert "DMSMASK_" not in got["text"]
    native_dt = mask_payload(rows=[{"ts": datetime(2026, 9, 30, 10)}])
    assert native_dt["rows"][0]["ts"] == datetime(2026, 9, 30, 10)
    native_d = mask_payload(rows=[{"ts": date(2026, 9, 30)}])
    assert native_d["rows"][0]["ts"] == date(2026, 9, 30)


def test_served_transactions_ts_stays_visible() -> None:
    """Fails on ca34419d. Goes through the envelope mask call, not a helper mask."""
    env = build_answer_envelope(
        answer_id="ans_overmask_ts",
        text="Peak on 2026-09-30.",
        badge="L2_VALIDATED",
        sql_used="SELECT ts, qty FROM transactions",
        rows=[{"ts": "2026-09-30 10:00:00", "qty": 12}],
        audit_id="aud_overmask_ts",
        as_of="2026-10-01T00:00:00Z",
        ask_mode="live",
    )
    assert env["abstained"] is False, env.get("text")
    assert env["rows"][0]["ts"] == "2026-09-30 10:00:00"
    assert env["rows"][0]["qty"] == 12
    assert "Peak on 2026-09-30." in env["text"]
    assert "DMSMASK_" not in env["text"]


def test_alias_without_birth_cue_stays_visible() -> None:
    """MAX(ts) AS last_seen and expiry. Fails on ca34419d. Envelope path."""
    last_seen = _served(
        text="Latest reading.",
        rows=[{"last_seen": "2026-09-30"}],
        sql_used="SELECT MAX(ts) AS last_seen FROM transactions",
    )
    assert last_seen["rows"][0]["last_seen"] == "2026-09-30"
    expiry = _served(
        text="Expiry listed.",
        rows=[{"expiry": date(2026, 12, 31)}],
        sql_used="SELECT expiry FROM products",
    )
    assert expiry["rows"][0]["expiry"] == date(2026, 12, 31)


def test_birth_source_stays_masked_through_alias_function_and_cte() -> None:
    """birth_date AS d is a LOCK: already masked on ca34419d.

    birth_date AS order_date fails on ca34419d, because that alias matched
    the event-date exemption and the date was left visible.
    """
    locked = _served(
        text="Listed.",
        rows=[{"d": "1990-01-15"}],
        sql_used="SELECT birth_date AS d FROM patients",
    )
    assert is_mask_token(locked["rows"][0]["d"])
    assert "1990-01-15" not in locked["text"]

    hidden = _served(
        text="Listed.",
        rows=[{"order_date": "1990-01-15"}],
        sql_used="SELECT birth_date AS order_date FROM patients",
    )
    assert is_mask_token(hidden["rows"][0]["order_date"])

    carried = _served(
        text="Listed.",
        rows=[{"d": "1888-12-31"}],
        sql_used=(
            "WITH c AS (SELECT birth_date AS x FROM patients) "
            "SELECT MAX(x) AS d FROM c"
        ),
    )
    assert is_mask_token(carried["rows"][0]["d"])
    assert "1888-12-31" not in str(carried["rows"])


def test_untraced_date_column_stays_masked() -> None:
    """Star and a star CTE cannot name a source column, so the date stays masked.

    Reason: sqlglot lineage cannot see through SELECT *. The served date is
    masked by default. An explicit SELECT ts FROM transactions is the same
    column name and stays visible, because the source is proven not to be birth.
    """
    reset_untraced_date_column_count()
    star = _served(
        text="Listed.",
        rows=[{"ts": "2026-09-30 10:00:00"}],
        sql_used="SELECT * FROM transactions",
    )
    assert is_mask_token(star["rows"][0]["ts"])
    assert untraced_date_column_count() == 1

    reset_untraced_date_column_count()
    cte = _served(
        text="Listed.",
        rows=[{"event_time": datetime(2026, 9, 30, 10)}],
        sql_used="WITH c AS (SELECT * FROM transactions) SELECT event_time FROM c",
    )
    assert is_mask_token(cte["rows"][0]["event_time"])
    assert untraced_date_column_count() == 1

    proven = _served(
        text="Listed.",
        rows=[{"ts": "2026-09-30 10:00:00"}],
        sql_used="SELECT ts FROM transactions",
    )
    assert proven["rows"][0]["ts"] == "2026-09-30 10:00:00"


def test_no_sql_keeps_birth_cue_rule() -> None:
    """An answer with no SQL does not fail closed. Prose uses the birth cue."""
    visible = _served(
        text="As of 2026-10-01. Peak on 2026-09-30. SKU AB1234567.",
        rows=[{"ts": "2026-09-30"}],
        sql_used=None,
    )
    assert visible["rows"][0]["ts"] == "2026-09-30"
    assert "As of 2026-10-01" in visible["text"]
    assert "Peak on 2026-09-30" in visible["text"]
    assert "SKU AB1234567" in visible["text"]

    masked = mask_payload(
        text="born 1888-03-03. DOB 2035-12-31. passport AB1234567 on file.",
    )
    assert "1888-03-03" not in masked["text"]
    assert "2035-12-31" not in masked["text"]
    assert "AB1234567" not in masked["text"]
    assert "born" in masked["text"]
    assert "passport" in masked["text"]


def test_birth_and_passport_cues_stay_masked_at_any_year() -> None:
    """Must stay unchanged: birth columns, cued prose, cued passports."""
    assert classify_column("dateofbirth", ["2099-04-04"]) == "dob"
    assert classify_column("notes", ["born 1888-03-03 in test city"]) == "dob"
    assert classify_column("text", ["DOB 2035-12-31"]) == "dob"
    got = mask_payload(
        text="born 1888-03-03. DOB 2035-12-31. passport number AB1234567.",
        rows=[
            {"dateofbirth": "2099-04-04", "birthday": "1900-01-01"},
        ],
    )
    blob = str(got)
    for raw in ("2099-04-04", "1900-01-01", "1888-03-03", "2035-12-31", "AB1234567"):
        assert raw not in blob, raw
    assert is_mask_token(got["rows"][0]["dateofbirth"])
    assert is_mask_token(got["rows"][0]["birthday"])


def test_numbers_timestamps_and_codes_stay_unchanged() -> None:
    """Must stay unchanged, through mask_payload. Not only the over-mask list.

    A bare code in a row with no SQL is not a proven code column, so that
    cell is covered by the served sku test, not here. Uncued SKU prose stays.
    """
    got = mask_payload(
        text="12 units at 12847.50. SKU AB1234567. As of 2026-10-01.",
        rows=[
            {
                "qty": 12,
                "amount": 12847.5,
                "ts": datetime(2026, 9, 30, 10),
                "created_at": date(2026, 10, 1),
            }
        ],
    )
    row = got["rows"][0]
    assert row["qty"] == 12
    assert row["amount"] == 12847.5
    assert row["ts"] == datetime(2026, 9, 30, 10)
    assert row["created_at"] == date(2026, 10, 1)
    assert "12847.50" in got["text"]
    assert "SKU AB1234567" in got["text"]
    assert "As of 2026-10-01" in got["text"]
    assert "DMSMASK_" not in got["text"]


def test_notes_passport_shape_stays_masked() -> None:
    """LOCK. Already masked on ca34419d. Free-text cell, no cue required."""
    env = _served(
        text="Listed.",
        rows=[{"notes": "A12345678"}],
        sql_used="SELECT notes FROM patients",
    )
    assert is_mask_token(env["rows"][0]["notes"])
    assert "A12345678" not in str(env["rows"])


def test_notes_aliased_as_sku_stays_masked() -> None:
    """LOCK. Already masked on ca34419d. Source is notes, not the alias sku."""
    env = _served(
        text="Listed.",
        rows=[{"sku": "A12345678"}],
        sql_used="SELECT notes AS sku FROM patients",
    )
    assert is_mask_token(env["rows"][0]["sku"])
    assert "A12345678" not in str(env["rows"])


def test_sku_source_column_stays_visible() -> None:
    """FAILS on ca34419d. Lineage source is sku, so a bare code stays visible."""
    env = _served(
        text="Listed.",
        rows=[{"sku": "AB1234567"}],
        sql_used="SELECT sku FROM products",
    )
    assert env["rows"][0]["sku"] == "AB1234567"
    assert "DMSMASK_" not in str(env["rows"])


def test_sku_prose_without_passport_cue_stays_visible() -> None:
    """FAILS on ca34419d. Answer prose needs a passport cue."""
    env = _served(
        text="SKU AB1234567",
        rows=[{"label": "listed"}],
        sql_used=None,
    )
    assert env["text"] == "SKU AB1234567"
    assert "DMSMASK_" not in env["text"]


def test_cued_passport_prose_stays_masked() -> None:
    """LOCK. Already masked on ca34419d. Prose with a passport cue."""
    env = _served(
        text="passport A12345678",
        rows=[{"label": "listed"}],
        sql_used=None,
    )
    assert "A12345678" not in env["text"]
    assert "passport" in env["text"]
    assert is_mask_token(env["text"].split()[-1])


def test_untraced_code_column_gets_no_passport_exemption() -> None:
    """LOCK. SELECT * cannot name a source, so a passport shape stays masked."""
    env = _served(
        text="Listed.",
        rows=[{"sku": "AB1234567"}],
        sql_used="SELECT * FROM products",
    )
    assert is_mask_token(env["rows"][0]["sku"])
    assert "AB1234567" not in str(env["rows"])


def test_285_untraced_default_is_separate_from_pass_count() -> None:
    """Checker passes no SQL, so nothing is masked for an untraced column.

    That zero is not added to the PASS count.
    """
    reset_untraced_date_column_count()
    rows = check_all()
    untraced = untraced_date_column_count()
    passed = [r for r in rows if r.path_a == "PASS" == r.path_b == r.path_c]
    assert len(rows) == 285
    assert untraced == 0
    assert len(passed) == 258
