"""SAFE-01: validate_compiled_sql refuses everything that is not one read-only SELECT.

Statement-shape layer only. Which tables a Space may read is the grant layer
(open PR #421 restructures it from parsed SQL objects); this file does not
move it. Two parts:

1. The hostile and legitimate corpora, driven straight at the one grant
   function. Every hostile statement is refused before anything can run.
2. A few of the same statements through POST /v1/chat/ask with a stub Cortex
   that hands the ask path a planted ``query_sql``. The envelope is asserted
   (hard rule 10a) and the stub records that nothing was submitted.

Synthetic demo-warehouse tables only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.generative_ask import validate_compiled_sql

GRANTED = {"inventory", "suppliers", "locations", "transactions"}


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return ensure_demo_warehouse(tmp_path_factory.mktemp("safe01") / "safe01.duckdb")


def _why(sql: str, lake: Path) -> str | None:
    return validate_compiled_sql(sql, grantable=set(GRANTED), warehouse=lake)


#: (sql, reason prefix). Every one of these reached PASS or an engine error
#: after the fact on main, or was refused only by a keyword that a comment or
#: string trick can dodge.
STACKED = [
    "SELECT sku FROM inventory; SELECT supplier_id FROM suppliers",
    "SELECT sku FROM inventory; DROP TABLE inventory",
    "SELECT sku FROM inventory;DELETE FROM inventory",
    "SELECT sku FROM inventory; UPDATE inventory SET quantity_kg = 0",
    "SELECT sku FROM inventory; INSERT INTO inventory SELECT * FROM inventory",
    "SELECT sku FROM inventory; CREATE TABLE t AS SELECT 1",
    "SELECT sku FROM inventory; PRAGMA version",
    "SELECT sku FROM inventory; ATTACH 'a.db' AS a",
    "SELECT sku FROM inventory; COPY inventory TO 'o.csv'",
    "SELECT sku FROM inventory; INSTALL httpfs",
    "SELECT sku FROM inventory; LOAD httpfs",
    "SELECT sku FROM inventory; SET enable_external_access = true",
    "SELECT sku FROM inventory; CALL pragma_version()",
    "SELECT sku FROM inventory; EXPORT DATABASE 'out'",
    "SELECT sku FROM inventory; VACUUM",
    "SELECT sku FROM inventory; CHECKPOINT",
    # comment and string tricks around the second statement
    "SELECT sku FROM inventory /* ; */ ; CALL pragma_version()",
    "SELECT sku FROM inventory -- harmless\n; CHECKPOINT",
    "SELECT sku FROM inventory; -- tail\nVACUUM",
    "SELECT 'a;b' AS s, sku FROM inventory; CHECKPOINT",
    "SELECT $$x;y$$ AS s, sku FROM inventory; VACUUM",
    "SELECT sku FROM inventory /* /* nested ; */ */; CALL pragma_version()",
]

NOT_SELECT = [
    "DROP TABLE inventory",
    "DELETE FROM inventory",
    "UPDATE inventory SET quantity_kg = 0",
    "INSERT INTO inventory SELECT * FROM inventory",
    "CREATE TABLE t AS SELECT * FROM inventory",
    "PRAGMA version",
    "ATTACH 'a.db' AS a",
    "COPY inventory TO 'o.csv'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SET enable_external_access = true",
    "CALL pragma_version()",
    "EXPORT DATABASE 'out'",
    "VACUUM",
    "CHECKPOINT",
    "DESCRIBE inventory",
    "SHOW TABLES",
    "SUMMARIZE inventory",
    "EXPLAIN SELECT sku FROM inventory",
    "/* c */ DROP TABLE inventory",
    "-- c\nDELETE FROM inventory",
    "WITH x AS (SELECT sku FROM inventory) INSERT INTO inventory SELECT * FROM inventory",
    "WITH x AS (SELECT sku FROM inventory) DELETE FROM inventory",
    "WITH x AS (SELECT sku FROM inventory) UPDATE inventory SET quantity_kg = 0",
    "SELECT sku FROM inventory FOR UPDATE",
    "",
    "   ",
    "-- only a comment",
]

READERS = [
    "SELECT * FROM read_csv('x.csv')",
    "SELECT * FROM read_csv_auto('x.csv')",
    "SELECT * FROM read_parquet('x.parquet')",
    "SELECT * FROM read_json('x.json')",
    "SELECT * FROM read_ndjson('x.json')",
    "SELECT * FROM read_text('x.txt')",
    "SELECT * FROM read_blob('x.bin')",
    "SELECT * FROM glob('*')",
    "SELECT * FROM parquet_metadata('x.parquet')",
    "SELECT * FROM sniff_csv('x.csv')",
    "SELECT * FROM iceberg_scan('x')",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM read_csv('https://example.invalid/x.csv')",
    "SELECT * FROM 'x.csv'",
    "SELECT * FROM 'x.parquet'",
    "SELECT * FROM '/etc/passwd'",
    'SELECT * FROM "x.csv"',
    "SELECT * FROM 'https://example.invalid/x.csv'",
    "SELECT * FROM 's3://bucket/x.parquet'",
    "SELECT sku FROM inventory, 'x.csv'",
    "SELECT sku FROM inventory JOIN 'x.csv' ON TRUE",
    "SELECT sku FROM inventory, read_csv('x.csv')",
    "SELECT sku FROM inventory, glob('*')",
    "SELECT sku FROM inventory CROSS JOIN read_text('x.txt')",
    "SELECT sku FROM inventory WHERE sku IN (SELECT column0 FROM read_csv('x.csv'))",
    "SELECT (SELECT COUNT(*) FROM read_csv('x.csv')) AS n FROM inventory",
    "WITH f AS (SELECT * FROM read_csv('x.csv')) SELECT sku FROM inventory",
    "SELECT sku FROM inventory UNION ALL SELECT column0 FROM read_csv('x.csv')",
    "SELECT * FROM inventory, LATERAL (SELECT * FROM read_text('x.txt')) l",
    # A table function reached through LATERAL is not on a table node.
    "SELECT c.alert_id AS a, i.sku FROM inventory i JOIN LATERAL query_table('alerts') c ON TRUE",
    "SELECT c.name AS n, i.sku FROM inventory i, LATERAL duckdb_settings() c",
    "SELECT c.Delimiter AS d, i.sku FROM inventory i JOIN LATERAL sniff_csv('x.csv') c ON TRUE",
    "SELECT c.name AS n, i.sku FROM inventory i CROSS JOIN LATERAL pragma_table_info('alerts') c",
]

LITERAL_ONLY = [
    "SELECT 1",
    "SELECT 42 AS answer",
    "SELECT 'a' AS s",
    "SELECT * FROM (SELECT 1 AS n) t",
    "SELECT * FROM (VALUES (1), (2)) t(x)",
    "VALUES (1)",
    "SELECT * FROM range(10)",
    "SELECT * FROM generate_series(1, 3)",
    "SELECT unnest([1, 2, 3]) AS x",
    "SELECT 1 UNION SELECT 2",
    "WITH a AS (SELECT sku FROM inventory) SELECT 42 AS answer",
    "WITH a AS (SELECT 1 AS n) SELECT n FROM a",
    "SELECT sku FROM inventory UNION ALL SELECT 'ghost'",
    # A real table in FROM is not a data source for a number nothing computed.
    "SELECT 4242 FROM inventory",
    "SELECT 4242 AS total FROM inventory WHERE sku = 'A'",
    "SELECT 1 FROM inventory LIMIT 1",
    "SELECT 4242 AS revenue, sku FROM inventory",
    "SELECT (4000 + 242) * 1 AS revenue FROM inventory",
    "SELECT CAST(4242 AS DOUBLE) AS revenue, sku FROM inventory",
    "WITH a AS (SELECT * FROM inventory) SELECT 4242 AS n FROM a",
    "SELECT sku FROM inventory UNION ALL SELECT 4242 FROM inventory",
    "SELECT sku, 0 AS revenue FROM inventory",
    "SELECT unnest([1, 2, 3]) AS x, sku FROM inventory",
    # The same literal, wrapped: CTE, derived table, column lists, scalar
    # subquery, functions, a CASE that always takes one branch, an aggregate
    # of a literal.
    "WITH x AS (SELECT 4242 AS revenue FROM inventory) SELECT revenue FROM x",
    "WITH x(v) AS (SELECT 4242 FROM inventory) SELECT v FROM x",
    "SELECT v FROM (SELECT 4242 AS v FROM inventory) s",
    "SELECT v FROM (SELECT 4242 AS a FROM inventory) s(v)",
    "SELECT * FROM (SELECT 4242 AS v FROM inventory) s",
    "SELECT (SELECT 4242) AS revenue FROM inventory",
    "SELECT ABS(4242) AS revenue FROM inventory",
    "SELECT ROUND(4242.5, 0) AS revenue FROM inventory",
    "SELECT GREATEST(4242, 1) AS revenue FROM inventory",
    "SELECT COALESCE(NULL, 4242) AS revenue FROM inventory",
    "SELECT CASE WHEN 1 = 1 THEN 4242 END AS revenue FROM inventory",
    "SELECT CAST('4242' AS INTEGER) AS revenue FROM inventory",
    "SELECT sku, revenue FROM inventory, (SELECT 4242 AS revenue) r",
    (
        "WITH x AS (SELECT quantity_kg AS v FROM inventory "
        "UNION ALL SELECT 4242 FROM inventory) SELECT v FROM x"
    ),
    # A window states its function's value; ORDER BY only picks the row.
    "SELECT MAX(4242) OVER () AS revenue FROM inventory",
    "SELECT FIRST_VALUE(4242) OVER (ORDER BY quantity_kg) AS revenue FROM inventory",
    # A lateral alias, a star passing a literal through, a generator column
    # beside a real table, rows counted from a generator.
    "SELECT 4242 AS x, x AS revenue FROM inventory",
    "SELECT sku, revenue FROM (SELECT * FROM inventory, (SELECT 4242 AS revenue) r) s",
    "SELECT range AS revenue FROM range(4242, 4243), inventory",
    "SELECT COUNT(*) AS revenue FROM range(4242) WHERE EXISTS (SELECT 1 FROM inventory)",
    # Matched by result name: a star arm, UNION BY NAME.
    "WITH t AS (SELECT sku, category FROM inventory) SELECT *, 4242 AS revenue FROM t",
    (
        "SELECT sku, quantity_kg FROM inventory "
        "UNION ALL BY NAME SELECT 4242 AS quantity_kg, sku FROM inventory"
    ),
    # A star traced to the CTE or derived table that states the literal.
    "WITH t AS (SELECT sku, 0 AS revenue FROM inventory) SELECT * FROM t ORDER BY sku",
    "SELECT * FROM (SELECT sku, 4242 AS revenue FROM inventory) s",
    # The selected CTE decides, not the first CTE in scope with that column.
    (
        "WITH actual AS (SELECT category, SUM(quantity_kg) AS kg FROM inventory GROUP BY 1), "
        "target AS (SELECT category, 5000 AS kg FROM inventory GROUP BY 1) "
        "SELECT category, kg FROM target"
    ),
    (
        "WITH t AS (SELECT * FROM inventory) SELECT COUNT(*) AS revenue FROM range(4242) "
        "WHERE EXISTS (SELECT 1 FROM inventory)"
    ),
    # An unnest or generator column is data only when what it carries is.
    "SELECT sku, unnest AS revenue FROM inventory, unnest([4242])",
    "SELECT i.sku, u.x AS revenue FROM inventory i, LATERAL unnest([4242]) u(x)",
    "SELECT i.sku, g.n AS revenue FROM inventory i CROSS JOIN LATERAL generate_series(1, 3) g(n)",
    # A filter or an order picks rows; the figure it returns is still invented.
    "SELECT MAX(4242) FILTER (WHERE quantity_kg > 0) AS revenue FROM inventory",
    "SELECT FIRST(4242 ORDER BY quantity_kg) AS revenue FROM inventory",
    "SELECT s.a AS revenue FROM (SELECT {'a': 4242} AS s FROM inventory) t",
    # DuckDB binds the CTE's column, not the lateral alias of the same name.
    (
        "WITH c AS (SELECT sku, 4242 AS sku_n FROM inventory) "
        "SELECT sku AS sku_n, sku_n AS revenue FROM c"
    ),
    # A literal or a counter carried through a recursion that reads no real rows.
    (
        "WITH RECURSIVE r(n) AS (SELECT 4242 FROM inventory UNION ALL SELECT n FROM r "
        "WHERE FALSE) SELECT n AS revenue FROM r"
    ),
    (
        "WITH RECURSIVE h(sku, lvl) AS (SELECT sku, 0 FROM inventory "
        "UNION ALL SELECT h.sku, h.lvl + 1 FROM h WHERE h.lvl < 2) SELECT sku, lvl FROM h"
    ),
]

LEGIT = [
    "SELECT sku, SUM(quantity_kg) AS q FROM inventory GROUP BY sku ORDER BY q DESC LIMIT 5",
    "SELECT sku FROM inventory;",
    "SELECT sku FROM inventory -- trailing; comment",
    "SELECT sku FROM inventory /* c; d */",
    "SELECT 'a;b' AS s, sku FROM inventory",
    "WITH a AS (SELECT sku, quantity_kg FROM inventory) SELECT * FROM a",
    (
        "WITH a AS (SELECT sku FROM inventory), b AS (SELECT supplier_id FROM suppliers) "
        "SELECT a.sku FROM a JOIN b ON a.sku = b.supplier_id"
    ),
    "WITH a AS (SELECT sku FROM inventory), b AS (SELECT sku FROM a) SELECT sku FROM b",
    "SELECT sku, ROW_NUMBER() OVER (PARTITION BY sku ORDER BY quantity_kg) AS rn FROM inventory",
    "SELECT i.sku FROM inventory i JOIN suppliers s ON i.supplier_id = s.supplier_id",
    "SELECT i.sku FROM inventory i LEFT JOIN locations l ON i.location_id = l.location_id",
    "SELECT sku FROM inventory WHERE sku IN (SELECT sku FROM transactions)",
    "SELECT * FROM inventory, range(3)",
    "SELECT r.range, COUNT(*) AS n FROM range(3) r JOIN inventory i ON TRUE GROUP BY 1",
    "SELECT * FROM inventory CROSS JOIN generate_series(1, 3)",
    "SELECT (SELECT SUM(quantity_kg) FROM inventory) AS total",
    "SELECT sku FROM inventory UNION ALL SELECT sku FROM transactions",
    "(SELECT sku FROM inventory) UNION ALL (SELECT sku FROM transactions)",
    "SELECT CAST(quantity_kg AS DOUBLE) / 2 AS half FROM inventory WHERE sku LIKE 'A%'",
    "SELECT sku FROM inventory ORDER BY sku LIMIT 3 OFFSET 1",
    "SELECT * FROM main.inventory",
    "SELECT COUNT(*) AS n FROM inventory",
    "SELECT 'Total' AS label, SUM(quantity_kg) AS q FROM inventory",
    "SELECT sku, TRUE AS in_stock FROM inventory",
    "SELECT sku, NULL AS note FROM inventory",
    "SELECT sku, quantity_kg * 2 AS doubled FROM inventory",
    # A literal label beside a computed figure is a label, not an invention.
    "SELECT sku, 2024 AS year, SUM(quantity_kg) AS q FROM inventory GROUP BY sku",
    "SELECT sku, 1 AS rank_, quantity_kg FROM inventory",
    "SELECT sku, 12 AS months, quantity_kg / 12 AS per_month FROM inventory",
    "SELECT sku, SUM(quantity_kg) OVER () AS tot, 1 AS one FROM inventory",
    "SELECT sku, ROW_NUMBER() OVER () AS rn FROM inventory",
    "SELECT sku, CAST(NULL AS INTEGER) AS n FROM inventory",
    "SELECT sku, DATE '2024-01-01' AS d, quantity_kg FROM inventory",
    "SELECT COUNT(1) AS n FROM inventory",
    "SELECT a, b FROM (SELECT sku, quantity_kg FROM inventory) s(a, b)",
    "WITH x(v) AS (SELECT quantity_kg FROM inventory) SELECT v FROM x",
    "SELECT i.* FROM inventory i",
    "SELECT sku FROM inventory i WHERE quantity_kg > (SELECT AVG(quantity_kg) FROM inventory)",
    # A sum of a constant counts rows; COLUMNS picks table columns by name.
    "SELECT category, SUM(1) AS n FROM inventory GROUP BY category",
    "SELECT SUM(1) AS n FROM inventory",
    "SELECT COLUMNS('quantity.*') FROM inventory",
    "SELECT sku, COLUMNS('quantity.*') FROM inventory",
    "SELECT MIN(COLUMNS(['quantity_kg', 'reorder_level_kg'])) FROM inventory",
    "SELECT sku, RANK() OVER (ORDER BY quantity_kg) AS r FROM inventory",
    "SELECT sku, NTILE(4) OVER (ORDER BY quantity_kg) AS quartile FROM inventory",
    # Recursion driven by real rows: a hierarchy walk, a running value seeded
    # from data, a month spine joined to the facts.
    (
        "WITH RECURSIVE h(loc, lvl) AS (SELECT location_id, 0 FROM locations UNION ALL "
        "SELECT i.location_id, h.lvl + 1 FROM inventory i JOIN h ON i.location_id = h.loc "
        "WHERE h.lvl < 2) SELECT loc, lvl FROM h"
    ),
    (
        "WITH RECURSIVE r(i, q) AS (SELECT 1, quantity_kg FROM inventory UNION ALL "
        "SELECT r.i + 1, r.q * 2 FROM r WHERE r.i < 3) SELECT i, q FROM r"
    ),
    (
        "WITH RECURSIVE m(d) AS (SELECT DATE '2026-01-01' UNION ALL SELECT d + INTERVAL 1 MONTH "
        "FROM m WHERE d < DATE '2026-06-01') SELECT m.d, COUNT(t.txn_id) AS n FROM m "
        "LEFT JOIN transactions t ON date_trunc('month', t.ts) = m.d GROUP BY m.d"
    ),
    # Only the CTE a query selects from is read, whatever other CTEs are in scope.
    (
        "WITH params AS (SELECT 30 AS days), recent AS (SELECT sku, SUM(quantity_kg) AS days "
        "FROM transactions GROUP BY sku) SELECT sku, days FROM recent"
    ),
    (
        "WITH placeholder AS (SELECT sku, 0 AS kg FROM inventory), actual AS (SELECT sku, "
        "quantity_kg AS kg FROM inventory) SELECT sku, kg FROM actual"
    ),
    # DuckDB binds a real column before an alias of the same name.
    "SELECT sku, NULL AS quantity_kg, quantity_kg AS q FROM inventory",
    "SELECT st.* FROM (SELECT {'k': sku, 'v': quantity_kg} AS st FROM inventory)",
    (
        "SELECT sku, quantity_kg FROM inventory "
        "UNION ALL BY NAME SELECT quantity_kg, 'TXN' AS sku FROM transactions"
    ),
    "SELECT d.day, COUNT(t.txn_id) AS n FROM range(1, 4) d(day) LEFT JOIN transactions t ON TRUE "
    "GROUP BY d.day",
    "SELECT * REPLACE (ROUND(quantity_kg, 1) AS quantity_kg) FROM inventory",
    # A self-join without aliases: sqlglot refuses the repeated name, DuckDB runs it.
    "SELECT COUNT(*) AS n FROM inventory JOIN inventory USING (sku)",
    # One CTE read by two UNION arms: the rows and their total.
    (
        "WITH c AS (SELECT category, SUM(quantity_kg) AS kg FROM inventory GROUP BY category) "
        "SELECT category, kg FROM c UNION ALL SELECT 'TOTAL', SUM(kg) FROM c"
    ),
    # A recursion that starts from real rows and only transforms them.
    (
        "WITH RECURSIVE d(sku, kg, step) AS (SELECT sku, quantity_kg, 0 FROM inventory "
        "UNION ALL SELECT sku, kg * 0.9, step + 1 FROM d WHERE step < 3) SELECT * FROM d"
    ),
    (
        "WITH RECURSIVE d(sku, step) AS (SELECT sku, 0 FROM inventory "
        "UNION ALL SELECT sku, step + 1 FROM d WHERE step < 3) SELECT COUNT(*) AS n FROM d"
    ),
    # A literal 1 per real row summed is a row count.
    "WITH f AS (SELECT sku, 1 AS cnt FROM inventory) SELECT SUM(cnt) AS n FROM f",
    # Swapped names: DuckDB binds each to the real column, not the other alias.
    "SELECT sku AS quantity_kg, quantity_kg AS sku FROM inventory",
    "WITH x AS (SELECT sku, quantity_kg FROM inventory) (SELECT * FROM x)",
    # Generators and subqueries through LATERAL.
    "SELECT i.sku, u.x FROM inventory i, LATERAL unnest([i.quantity_kg]) u(x)",
    "SELECT i.sku, u.v FROM inventory i, unnest([i.quantity_kg]) u(v)",
    (
        "SELECT i.sku, i.quantity_kg * g.n AS q FROM inventory i "
        "CROSS JOIN LATERAL generate_series(1, 3) g(n)"
    ),
    (
        "SELECT i.sku, t.n FROM inventory i, LATERAL (SELECT COUNT(*) AS n FROM transactions t "
        "WHERE t.sku = i.sku) t"
    ),
]

#: Pass the shape layer on purpose. Each states an invented figure next to a
#: computed one, which only a figure verifier can tell apart from a label.
#: Close one, then move it to LITERAL_ONLY.
SHAPE_RESIDUAL = [
    "SELECT sku, 4242 AS revenue, quantity_kg FROM inventory",
    "SELECT 4242 + 0 * SUM(quantity_kg) AS revenue FROM inventory",
    "SELECT SUM(4242) AS revenue FROM inventory",
    "SELECT category, CASE category WHEN 'x' THEN 5000 ELSE 4242 END AS target_kg "
    "FROM inventory GROUP BY category",
    "SELECT ARG_MAX(4242, quantity_kg) AS revenue FROM inventory",
    "SELECT category, 'RM 4242' AS revenue FROM inventory GROUP BY category",
    "SELECT * FROM inventory i JOIN (SELECT 4242 AS revenue) r ON TRUE",
    "SELECT COLUMNS('rev.*') FROM inventory, (SELECT 4242 AS revenue) r",
    "SELECT COUNT(*) AS revenue FROM range(4242) r LEFT JOIN inventory i ON FALSE",
    (
        "SELECT sku, quantity_kg FROM inventory "
        "UNION ALL SELECT * FROM (SELECT sku, 4242 AS q FROM inventory) s"
    ),
    # A sum over real rows depends on how many there are, so the walk counts
    # it as computed even where an arm or the row set is a literal.
    (
        "WITH x AS (SELECT quantity_kg AS v FROM inventory UNION ALL SELECT 4242 FROM inventory) "
        "SELECT SUM(v) FROM x"
    ),
    "SELECT SUM(v) FROM (SELECT 4242 AS v FROM inventory LIMIT 1) s",
    "SELECT SUM(r.range) AS revenue FROM range(93) r LEFT JOIN inventory i ON FALSE",
    # A recursive CTE's column is data when one arm makes it so (a depth starts
    # as 0 and counts joined rows), so a constant either arm carries passes.
    (
        "WITH RECURSIVE h(loc, tag) AS (SELECT location_id, 4242 FROM locations UNION ALL "
        "SELECT i.location_id, h.tag FROM inventory i JOIN h ON i.location_id = h.loc "
        "WHERE FALSE) SELECT loc, tag AS revenue FROM h"
    ),
    (
        "WITH RECURSIVE r(n) AS (SELECT quantity_kg FROM inventory UNION ALL "
        "SELECT 4242 FROM r WHERE n <> 4242) SELECT n AS revenue FROM r"
    ),
]


@pytest.mark.parametrize("sql", STACKED)
def test_stacked_statements_are_refused_before_anything_runs(sql: str, lake: Path) -> None:
    why = _why(sql, lake)
    assert why is not None, sql
    assert why.startswith("hostile_sql:"), (sql, why)


@pytest.mark.parametrize("sql", NOT_SELECT)
def test_anything_that_is_not_a_select_is_refused(sql: str, lake: Path) -> None:
    why = _why(sql, lake)
    assert why is not None, sql
    assert why.startswith("hostile_sql:"), (sql, why)


@pytest.mark.parametrize("sql", READERS)
def test_file_url_and_table_function_readers_are_refused(sql: str, lake: Path) -> None:
    why = _why(sql, lake)
    assert why is not None, sql
    assert why.startswith("hostile_sql:"), (sql, why)
    # The refusal names a code, never the function, path or URL the SQL used.
    for leaked in ("read_", "glob", "x.csv", "x.parquet", "passwd", "example.invalid", "s3://"):
        assert leaked not in why, (sql, why)


@pytest.mark.parametrize("sql", LITERAL_ONLY)
def test_a_query_that_reads_no_granted_table_has_no_authority(sql: str, lake: Path) -> None:
    why = _why(sql, lake)
    assert why is not None, sql
    assert why.startswith("hostile_sql:"), (sql, why)


@pytest.mark.parametrize("sql", LEGIT)
def test_legitimate_selects_still_pass(sql: str, lake: Path) -> None:
    assert _why(sql, lake) is None, sql


@pytest.mark.parametrize("sql", SHAPE_RESIDUAL)
def test_named_residuals_pass_the_shape_layer(sql: str, lake: Path) -> None:
    assert _why(sql, lake) is None, sql


#: Deeper than the lineage walk can trace. Each must come back as a code,
#: never as an exception that turns the ask into a 503.
TOO_DEEP = [
    "SELECT sku, " + " + ".join(["quantity_kg"] * 600) + " AS v FROM inventory",
    " UNION ALL ".join(["SELECT sku, quantity_kg FROM inventory"] * 400),
]


@pytest.mark.parametrize("sql", TOO_DEEP, ids=["long_chain", "wide_union"])
def test_sql_too_deep_to_trace_fails_closed_with_a_code(sql: str, lake: Path) -> None:
    assert _why(sql, lake) == "hostile_sql:sql_not_analyzable"


def _cte_chain(n: int, base: str) -> str:
    """CTEs whose columns each read two columns of the CTE before."""
    parts = [f"c0 AS (SELECT {base} FROM inventory)"]
    for i in range(1, n):
        parts.append(f"c{i} AS (SELECT x.a + x.b AS a, x.b + x.a AS b FROM c{i - 1} x)")
    return "WITH " + ", ".join(parts) + f" SELECT c.a FROM c{n - 1} c"


def test_the_lineage_walk_is_linear_in_the_query(lake: Path) -> None:
    # Without the memo each level doubles the paths walked: 2**24 here.
    started = time.monotonic()
    invented = _why(_cte_chain(25, "1 AS a, 1 AS b"), lake)
    computed = _why(_cte_chain(25, "quantity_kg AS a, reorder_level_kg AS b"), lake)
    assert time.monotonic() - started < 5
    assert invented == "hostile_sql:literal_output"
    assert computed is None


def test_a_figure_check_that_cannot_parse_fails_closed() -> None:
    from dms_executor.sql_shape import literal_figure_reason

    assert literal_figure_reason("SELECT (", ["INTEGER"], ["n"]) == "hostile_sql:sql_not_analyzable"


@pytest.mark.parametrize(
    "sql",
    [
        # Inside its own body a CTE's name is the real table (DuckDB binds the
        # base term of a recursive CTE, and a plain CTE body, to the table).
        "WITH RECURSIVE alerts(a) AS (SELECT alert_id FROM alerts UNION ALL "
        "SELECT a FROM alerts WHERE FALSE) SELECT a FROM alerts",
        "WITH alerts(a) AS (SELECT alert_id FROM alerts) SELECT a FROM alerts",
        # DuckDB's base term is everything left of the last UNION.
        "WITH RECURSIVE alerts(a) AS (SELECT sku FROM inventory UNION ALL SELECT alert_id "
        "FROM alerts UNION ALL SELECT a FROM alerts WHERE FALSE) SELECT a FROM alerts",
        # Subqueries sqlglot gives no scope are still read.
        "SELECT unnest((SELECT list(message) FROM alerts)) AS leaked, sku FROM inventory",
        "SELECT sku, ((SELECT COUNT(*) FROM alerts)) AS n FROM inventory",
        "WITH suppliers AS (SELECT alert_id FROM alerts) (SELECT * FROM suppliers)",
        # Only a plain UNION recurses; under these DuckDB reads the table.
        "WITH RECURSIVE alerts(message) AS (SELECT 'x' AS message UNION BY NAME "
        "SELECT message FROM alerts) SELECT message FROM alerts",
        "WITH RECURSIVE alerts(n) AS (SELECT 1 FROM inventory EXCEPT "
        "SELECT length(message) FROM alerts) SELECT n FROM alerts",
        "WITH RECURSIVE alerts(s) AS (SELECT sku FROM inventory INTERSECT "
        "SELECT severity FROM alerts) SELECT s FROM alerts",
    ],
)
def test_a_cte_named_like_a_table_still_grant_checks_that_table(sql: str, lake: Path) -> None:
    assert _why(sql, lake) == "ungranted:alerts"


def test_comma_and_cte_joins_cannot_smuggle_an_ungranted_table(lake: Path) -> None:
    comma = _why("SELECT i.sku FROM inventory i, secrets s", lake)
    assert comma is not None and comma.startswith("ungranted:"), comma
    cte = _why(
        "WITH a AS (SELECT sku FROM inventory) SELECT a.sku FROM a JOIN secrets s ON TRUE",
        lake,
    )
    assert cte is not None and cte.startswith("ungranted:"), cte
    # A CTE that shadows a name in one scope does not hide the same name used
    # as a real table in another scope.
    shadow = _why(
        "SELECT * FROM secrets, (WITH secrets AS (SELECT 1 AS n) SELECT n FROM secrets) x",
        lake,
    )
    assert shadow is not None and shadow.startswith("ungranted:"), shadow


def test_a_cte_name_is_not_an_ungranted_table(lake: Path) -> None:
    assert _why("WITH cte_one AS (SELECT sku FROM inventory) SELECT sku FROM cte_one", lake) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sku FROM inventory WHERE (",
        "SELECT sku INTO copy_of_inventory FROM inventory",
    ],
)
def test_an_engine_syntax_error_still_reaches_the_repair_loop(sql: str, lake: Path) -> None:
    # The engine cannot read it, so nothing can run; the loop sees its message.
    why = _why(sql, lake)
    assert why is not None and why.startswith("explain:"), why


# ---------------------------------------------------------------------------
# Through POST /v1/chat/ask. The stub Cortex plants ``query_sql`` on the
# Insights seam and records every submit. A refused statement must reach no
# submit, and the customer envelope must be an abstention with no rows.
# ---------------------------------------------------------------------------

#: Not a certified-pack phrase, so the ask reaches the generative seam.
_QUESTION = "How many distinct categories do we stock?"
_PLANTED_ROWS = [{"n": 7}]


@dataclass
class _PlantedSqlCortex:
    sql: str
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)

    def query_submits(self) -> list[Any]:
        """Submits that carry a query. The session bind is not one."""
        out = []
        for req in self.submits:
            plan = getattr(req, "plan", None)
            kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
            if kind != "session_bind":
                out.append(req)
        return out

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "query_sql": self.sql,
            "plan_source": "ontology_plan",
            "status": "ABSTAIN",
            "phase": "generate",
            "generative": {"ok": True, "sql": self.sql},
            "ontology": {"ok": True, "metrics": []},
        }

    def submit(self, req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        self.submits.append(req)
        return QueryResult(
            ok=True, status="ok", run_id="run_safe01", output={"rows": list(_PLANTED_ROWS)}
        )

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_safe01", hash="hash_safe01_not_entry")

    def ask(self, req: Any) -> Any:
        self.asks.append(req)
        raise AssertionError("SAFE-01: the contract ask must not answer a refused statement")


@pytest.fixture()
def _minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    from cortex_contract.execution import Manifest
    from dms_executor.manifest import ManifestMinter, SessionAcl

    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-23T00:00:00+00:00",
            expires_at="2026-09-23T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _ask(minter: Any, cortex: _PlantedSqlCortex, session: str) -> dict[str, Any]:
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_executor import Executor
    from dms_executor.envelope import assert_envelope_valid
    from fastapi.testclient import TestClient

    app = create_app()
    exe = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    r = TestClient(app).post("/v1/chat/ask", json={"question": _QUESTION, "session_id": session})
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env  # type: ignore[no-any-return]


API_HOSTILE = [
    "SELECT sku FROM inventory; CALL pragma_version()",
    "SELECT sku FROM inventory; VACUUM",
    "SELECT sku FROM inventory /* ; */ ; CHECKPOINT",
    "WITH x AS (SELECT sku FROM inventory) DELETE FROM inventory",
    "SELECT * FROM read_text('x.txt')",
    "SELECT * FROM 'x.csv'",
    "SELECT sku FROM inventory, glob('*')",
    "SELECT 4242 AS answer",
    "WITH a AS (SELECT sku FROM inventory) SELECT 4242 AS answer",
    "WITH x AS (SELECT 4242 AS n FROM inventory) SELECT n FROM x",
    "SELECT sku, 4242 AS n FROM inventory",
    *TOO_DEEP,
]


@pytest.mark.parametrize("sql", API_HOSTILE)
def test_hostile_sql_through_the_ask_route_never_reaches_cortex_submit(
    sql: str, _minter: Any
) -> None:
    cortex = _PlantedSqlCortex(sql=sql)
    env = _ask(_minter, cortex, "ses_safe01_hostile")
    assert cortex.query_submits() == [], sql
    assert cortex.appends == [], sql
    assert env["badge"] == "ABSTAIN", (sql, env.get("badge"))
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert env.get("sql_used") is None
    blob = json.dumps(env)
    for leaked in ("4242", "x.txt", "x.csv", "read_text", "glob("):
        assert leaked not in env["text"], (sql, env["text"])
    assert "4242" not in blob


def test_a_cte_select_over_a_granted_table_is_served_through_the_ask_route(
    _minter: Any,
) -> None:
    sql = "WITH s AS (SELECT category FROM inventory) SELECT COUNT(DISTINCT category) AS n FROM s"
    cortex = _PlantedSqlCortex(sql=sql)
    env = _ask(_minter, cortex, "ses_safe01_legit")
    assert len(cortex.query_submits()) == 1
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"] == _PLANTED_ROWS
    assert "7" in env["text"]
