"""A row reported as promoted must arrive intact, or be quarantined.

The contract check and the write used to be two separate type mappings that
disagreed, so a value could satisfy the contract and still be destroyed on the
way into silver — stored as NULL while the receipt counted it under ``passed``
and reported ``quarantined: 0``. Nothing in the output said so, and every
aggregate computed above it was wrong with no trace.

Measured against DuckDB before the fix:

    contract          input                     check     landed in silver
    integer           3000000000                BIGINT ok NULL
    integer           2147483648                BIGINT ok NULL
    decimal(18,2)     99999999999999999.99      DOUBLE ok NULL
    timestamptz       2026-03-15T08:00:00+08:00 DATE   ok offset dropped

Both sides now derive from ``_sql_type``, so they cannot drift apart again.
"""

from __future__ import annotations

import duckdb
import pytest
from dms_executor.pipeline_loader import PipelineLoadError
from dms_executor.promote import _cast_sql, _sql_type, _type_check_sql


def _promote_one(contract_type: str, value: str):
    """Return (passes_contract, value_as_stored) for a single raw string."""
    con = duckdb.connect()
    try:
        check = _type_check_sql("v", contract_type)
        cast = _cast_sql("v", contract_type)
        row = con.execute(
            f"SELECT ({check}) AS ok, {cast} AS stored FROM (SELECT ? AS v)", [value]
        ).fetchone()
        return bool(row[0]), row[1]
    finally:
        con.close()


@pytest.mark.parametrize(
    ("contract_type", "value"),
    [
        ("integer", "3000000000"),
        ("integer", "2147483648"),
        ("decimal(18,2)", "99999999999999999.99"),
        ("bigint", "99999999999999999999"),
    ],
)
def test_a_value_the_write_cannot_hold_is_refused_not_nulled(
    contract_type: str, value: str
) -> None:
    """The defect, pinned: passing the check and storing NULL cannot both happen."""
    passes, stored = _promote_one(contract_type, value)
    assert not (passes and stored is None), (
        f"{contract_type} {value!r} passed the contract and landed as NULL — "
        "it would be counted under `passed` with quarantined: 0"
    )
    assert not passes, "an unrepresentable value belongs in quarantine, visibly"


@pytest.mark.parametrize(
    ("contract_type", "value"),
    [
        ("integer", "42"),
        ("bigint", "3000000000"),
        ("decimal(18,2)", "10.00"),
        ("decimal(30,6)", "1.234567"),
        ("date", "2026-03-15"),
        ("text", "nasi lemak"),
    ],
)
def test_representable_values_still_promote(contract_type: str, value: str) -> None:
    """R-0005: the fix must not start refusing rows that were always fine."""
    passes, stored = _promote_one(contract_type, value)
    assert passes, f"{contract_type} {value!r} should promote"
    assert stored is not None


def test_declared_decimal_precision_is_honoured() -> None:
    """``decimal(30,6)`` was written as DECIMAL(18,2), losing four decimal places."""
    assert _sql_type("decimal(30,6)") == "DECIMAL(30,6)"
    _, stored = _promote_one("decimal(30,6)", "1.234567")
    assert str(stored) == "1.234567"


def test_timestamptz_keeps_its_offset() -> None:
    """A KL timestamp and a UTC one must not become the same value."""
    assert _sql_type("timestamptz") == "TIMESTAMPTZ"
    _, stored = _promote_one("timestamptz", "2026-03-15T08:00:00+08:00")
    assert stored is not None
    assert stored.utcoffset() is not None, "offset was dropped"


def test_the_check_and_the_write_agree_for_every_supported_type() -> None:
    """The root cause was two mappings. Assert there is now one."""
    for declared in (
        "integer", "bigint", "decimal(18,2)", "decimal(30,6)", "numeric",
        "float", "double", "date", "timestamp", "timestamptz", "text",
    ):
        target = _sql_type(declared)
        check = _type_check_sql("v", declared)
        cast = _cast_sql("v", declared)
        if target == "VARCHAR":
            continue
        assert target in check, f"{declared}: check does not test {target}"
        assert target in cast, f"{declared}: write does not produce {target}"


def test_an_unenforceable_contract_type_is_refused_loudly() -> None:
    """``{type: decimal(18,2)}`` unquoted parses as 'decimal(18' — a real bug in
    pipelines/silver_sales.yaml that the old startswith() check hid by accident,
    and which would have silently written decimal(30,6) as DECIMAL(18,2)."""
    with pytest.raises(PipelineLoadError, match="cannot enforce"):
        _sql_type("decimal(18")
    with pytest.raises(PipelineLoadError):
        _sql_type("mystery_type")
