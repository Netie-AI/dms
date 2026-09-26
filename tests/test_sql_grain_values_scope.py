"""SPACE-GEN-01 round 3 fix: an inline VALUES list is not a lateral read.

The verifier found every SQL with a VALUES derived table refused as
``sql_unanalysable:lateral``. VALUES reads no table, so it must pass; a real
table hidden inside a VALUES row must still be seen and checked.
"""

from __future__ import annotations

from dms_executor.sql_grain import real_table_labels, scope_refusal_reason


def test_values_cte_is_analysable() -> None:
    sql = (
        "WITH targets(county) AS (VALUES ('Alameda'), ('San Diego')) "
        "SELECT s.county, COUNT(*) AS n FROM bronze.public_schools s "
        "JOIN targets t ON s.county = t.county GROUP BY s.county"
    )
    assert scope_refusal_reason(sql) is None
    assert real_table_labels(sql) == ["bronze.public_schools"]


def test_values_derived_table_reads_no_table() -> None:
    sql = "SELECT v.x FROM (VALUES (1), (2)) AS v(x)"
    assert scope_refusal_reason(sql) is None
    assert real_table_labels(sql) == []


def test_table_inside_values_row_is_still_seen() -> None:
    sql = "SELECT v.x FROM (VALUES ((SELECT MAX(id) FROM bronze.secrets))) AS v(x)"
    labels = real_table_labels(sql)
    assert labels is None or "bronze.secrets" in labels


def test_lateral_still_refused() -> None:
    sql = (
        "SELECT * FROM bronze.public_schools s, "
        "LATERAL (SELECT * FROM bronze.secrets x WHERE x.id = s.id) l"
    )
    assert scope_refusal_reason(sql) is not None
