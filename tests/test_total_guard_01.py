# ruff: noqa: E501 - the SQL shapes read best one per line
"""TOTAL-GUARD-01 (dms#531): one table's total, after the query multiplied its own rows.

One read of one base table has nothing to join, so rule 16's join allowlist
passes it. The statement can still hand the same input value to its outer
aggregate more than once (more than one grouping set, a column-to-row turn, a
window total summed once per row, a list concatenated with itself) and the
multiplied figure was served ``L2_VALIDATED``.

Every case posts ``POST /v1/chat/ask`` in the Space with a verified ontology
(``test_onto_derive_01._space``) and asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows). The Cortex fake runs
the SQL on the lake, so the wrong figure each statement would show is real,
and each case states it beside the true one (107 students enrolled). Doors:
the first generate, the extract loop, the contract ask's ``sql_used``, and the
held Yes door (``DMS_RECONFIRM_RUN_HELD``) in a file Space, where SQL is parked.

The mechanism is the class rule in ``dms_executor.total_guard`` (structure
only, nothing re-derived). ``OPEN`` holds what it cannot see, as strict
expected failures: scalar arithmetic the statement wrote.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb
import pytest
import test_connect_ask_03 as ca
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import (
    load_space_ontology,
    relation_columns,
    unverified_join_reason,
)
from dms_executor.total_guard import repeated_input_reason
from test_connect_ask_03 import _flags  # noqa: F401 - fixture: every case states its flags
from test_onto_derive_01 import minter, store  # noqa: F401 - fixtures

T = od.ENROLL
N = "CAST(students AS INTEGER)"
Q = "What is the total?"
TRUE = 107
TRUE_SQL = f"SELECT SUM({N}) AS total FROM {T}"
DOORS = ("generate", "loop", "contract")

_GROUPED = f"SELECT SUM(t) AS total FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY {{}})"
_TWO = f"(SELECT {N} AS a, {N} AS b FROM {T})"
_THREE = f"(SELECT {N} AS a, {N} AS b, {N} AS c FROM {T})"

_W = f"(SELECT SUM({N}) OVER () AS w FROM {T})"

GROUPING = "grouping_sets_under_aggregate"
TURN = "row_turn_under_aggregate"
WINDOW = "window_value_under_aggregate"
COLLECTION = "collection_under_function"
SET_OP = "set_operation_under_aggregate"
GENERATOR = "row_generator_under_aggregate"
SELF_JOIN = "self_join_under_aggregate"

#: The issue's Problem table, row by row: name -> (sql, the figure it returns,
#: the rule that refuses it). The true figure is 107 in every row.
ISSUE_ROWS: dict[str, tuple[str, int, str]] = {
    "r01_rollup": (_GROUPED.format("ROLLUP(school_id)"), 214, GROUPING),
    "r02_cube": (_GROUPED.format("CUBE(school_id)"), 214, GROUPING),
    "r03_cube_two_columns": (_GROUPED.format("CUBE(school_id, enrollment_id)"), 428, GROUPING),
    "r04_grouping_sets_two_empty": (
        f"SELECT SUM(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY GROUPING SETS ((), ()))",
        214,
        GROUPING,
    ),
    "r05_grouping_sets_three_empty": (
        f"SELECT SUM(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY GROUPING SETS ((), (), ()))",
        321,
        GROUPING,
    ),
    "r06_grouping_sets_same_column_twice": (
        _GROUPED.format("GROUPING SETS ((school_id), (school_id))"),
        214,
        GROUPING,
    ),
    "r07_cte_rollup": (
        f"WITH c AS (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)) SELECT SUM(t) AS total FROM c",
        214,
        GROUPING,
    ),
    "r08_unpivot_two_columns": (
        f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT (n FOR k IN (a, b))",
        214,
        TURN,
    ),
    "r09_unpivot_three_columns": (
        f"SELECT SUM(n) AS total FROM {_THREE} UNPIVOT (n FOR k IN (a, b, c))",
        321,
        TURN,
    ),
    "r10_list_concat": (
        f"SELECT list_sum(list_concat(list({N}), list({N}))) AS total FROM {T}",
        214,
        COLLECTION,
    ),
    "r11_list_pipe": (
        f"SELECT list_sum(list({N}) || list({N})) AS total FROM {T}",
        214,
        COLLECTION,
    ),
    "r12_window_total_summed_per_row": (f"SELECT SUM(w) AS total FROM {_W}", 428, WINDOW),
    "r13_rollup_same_column_twice": (
        _GROUPED.format("ROLLUP(school_id, school_id)"),
        321,
        GROUPING,
    ),
    "r14_unpivot_columns_star": (
        f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT (n FOR k IN (COLUMNS(*)))",
        214,
        TURN,
    ),
    "r14_unpivot_statement_on_columns_star": (
        f"SELECT SUM(n) AS total FROM (UNPIVOT {_TWO} ON COLUMNS(*) INTO NAME k VALUE n)",
        214,
        TURN,
    ),
    "r15_unpivot_include_nulls": (
        f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT INCLUDE NULLS (n FOR k IN (a, b))",
        214,
        TURN,
    ),
    # The issue's (students, students) pairs do not bind in DuckDB 2.0.0 (sum(VARCHAR)).
    "r16_unpivot_paired": (
        f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT ((n, m) FOR k IN ((a, b), (b, a)))",
        214,
        TURN,
    ),
}

COUNT_Q = "How many enrollments are there?"
#: More ways to hand one value to an aggregate twice, written by the runner:
#: name -> (question, sql, the figure it returns, the true figure, the rule).
#: The window rows try every way a window value can change its name on the
#: way up. Rule 3 denies by default, so a name it cannot follow refuses.
MORE_SHAPES: dict[str, tuple[str, str, int, int, str]] = {
    "window_renamed_by_a_column_list": (
        Q,
        f"SELECT SUM(c) AS total FROM {_W} AS q(c)",
        428,
        107,
        WINDOW,
    ),
    "window_by_position": (
        Q,
        f"SELECT SUM(#1) AS total FROM (SELECT SUM({N}) OVER () FROM {T})",
        428,
        107,
        WINDOW,
    ),
    "window_by_columns_star": (Q, f"SELECT SUM(COLUMNS(*)) AS total FROM {_W}", 428, 107, WINDOW),
    "window_qualified": (Q, f"SELECT SUM(q.w) AS total FROM {_W} q", 428, 107, WINDOW),
    "window_in_a_struct_field": (
        Q,
        f"SELECT SUM(s.w) AS total FROM (SELECT {{'w': SUM({N}) OVER ()}} AS s FROM {T})",
        428,
        107,
        WINDOW,
    ),
    "window_through_a_whole_row": (
        Q,
        f"SELECT SUM(q.w) AS total FROM (SELECT q FROM {_W} q)",
        428,
        107,
        WINDOW,
    ),
    "window_by_its_generated_name": (
        Q,
        f'SELECT SUM("sum(CAST(students AS INTEGER)) OVER ()") AS total FROM (SELECT SUM({N}) OVER () FROM {T})',
        428,
        107,
        WINDOW,
    ),
    "window_named_in_a_window_clause": (
        Q,
        f"SELECT SUM(w) AS total FROM (SELECT SUM({N}) OVER win AS w FROM {T} WINDOW win AS ())",
        428,
        107,
        WINDOW,
    ),
    "window_through_a_star": (
        Q,
        f"SELECT SUM(w) AS total FROM (SELECT * FROM {_W})",
        428,
        107,
        WINDOW,
    ),
    # Unnamed in the inner SELECT, then through a star: only the carried kind knows (M09).
    "window_unnamed_through_a_star_then_by_generated_name": (
        Q,
        f'SELECT SUM("sum(CAST(students AS INTEGER)) OVER ()") AS total FROM (SELECT * FROM (SELECT SUM({N}) OVER () FROM {T}))',
        428,
        107,
        WINDOW,
    ),
    "window_through_star_rename": (
        Q,
        f"SELECT SUM(z) AS total FROM (SELECT * RENAME (w AS z) FROM {_W})",
        428,
        107,
        WINDOW,
    ),
    "window_through_star_replace": (
        Q,
        f"SELECT SUM(students) AS total FROM (SELECT * REPLACE (SUM({N}) OVER () AS students) FROM {T})",
        428,
        107,
        WINDOW,
    ),
    "window_through_a_columns_selector": (
        Q,
        f"SELECT SUM(w) AS total FROM (SELECT COLUMNS('w') FROM {_W})",
        428,
        107,
        WINDOW,
    ),
    "window_through_from_first": (Q, f"SELECT SUM(w) AS total FROM (FROM {_W})", 428, 107, WINDOW),
    "window_through_a_correlated_scalar": (
        Q,
        f"SELECT (SELECT SUM(q.w)) AS total FROM {_W} q",
        428,
        107,
        WINDOW,
    ),
    "window_through_a_lateral_alias": (
        Q,
        f"SELECT SUM(v) AS total FROM (SELECT SUM({N}) OVER () AS w, w * 1 AS v FROM {T})",
        428,
        107,
        WINDOW,
    ),
    "window_through_a_cte_column_list": (
        Q,
        f"WITH c(x) AS (SELECT SUM({N}) OVER () FROM {T}) SELECT SUM(x) AS total FROM c",
        428,
        107,
        WINDOW,
    ),
    "window_of_a_window": (
        Q,
        f"SELECT DISTINCT SUM(w) OVER () AS total FROM {_W}",
        428,
        107,
        WINDOW,
    ),
    "window_running_total_summed": (
        Q,
        f"SELECT SUM(r) AS total FROM (SELECT SUM({N}) OVER (ORDER BY enrollment_id) AS r FROM {T})",
        287,
        107,
        WINDOW,
    ),
    "window_lag_added_per_row": (
        Q,
        f"SELECT SUM(x) AS total FROM (SELECT {N} + LAG({N}, 1, 0) OVER (ORDER BY enrollment_id) AS x FROM {T})",
        202,
        107,
        WINDOW,
    ),
    "window_over_rollup_in_one_select": (
        Q,
        f"SELECT DISTINCT SUM(SUM({N})) OVER () AS total FROM {T} GROUP BY ROLLUP(school_id)",
        214,
        107,
        GROUPING,
    ),
    "rollup_under_an_untyped_aggregate": (
        Q,
        f"SELECT fsum(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id))",
        214,
        107,
        GROUPING,
    ),
    "rollup_through_a_filtering_select": (
        Q,
        f"SELECT SUM(t) AS total FROM (SELECT t FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY CUBE(school_id)) WHERE t > 0)",
        214,
        107,
        GROUPING,
    ),
    "grouping_sets_beside_a_plain_key": (
        Q,
        f"SELECT SUM(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY school_id, GROUPING SETS ((enrollment_id), ()))",
        214,
        107,
        GROUPING,
    ),
    "rollup_cte_in_a_scalar_subquery": (
        Q,
        f"WITH c AS (SELECT SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)) SELECT (SELECT SUM(t) FROM c) AS total",
        214,
        107,
        GROUPING,
    ),
    "rollup_rows_counted": (
        "How many schools have enrollments?",
        f"SELECT COUNT(*) AS total FROM (SELECT school_id FROM {T} GROUP BY ROLLUP(school_id))",
        5,
        4,
        GROUPING,
    ),
    "rollup_total_row_taken_as_the_largest": (
        "What is the largest total for one school?",
        f"SELECT MAX(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id))",
        107,
        40,
        GROUPING,
    ),
    "unpivot_rows_counted": (
        COUNT_Q,
        f"SELECT COUNT(*) AS total FROM {_TWO} UNPIVOT (n FOR k IN (a, b))",
        8,
        4,
        TURN,
    ),
    "unpivot_through_a_cte": (
        Q,
        f"WITH u AS (SELECT * FROM {_TWO} UNPIVOT (n FOR k IN (a, b))) SELECT SUM(n) AS total FROM u",
        214,
        107,
        TURN,
    ),
    # Adversary round (windows and lists): a string is a collection once split.
    "string_agg_doubled_and_split": (
        COUNT_Q,
        f"SELECT len(string_split(string_agg(students, ',') || ',' || string_agg(students, ','), ',')) AS total FROM {T}",
        8,
        4,
        COLLECTION,
    ),
    "string_agg_through_a_cte": (
        COUNT_Q,
        f"WITH s AS (SELECT string_agg(students, ',') AS j FROM {T}) SELECT len(string_split(j || ',' || j, ',')) AS total FROM s",
        8,
        4,
        COLLECTION,
    ),
    "listagg_concat_ws": (
        COUNT_Q,
        f"SELECT len(string_split(concat_ws(',', listagg(students, ','), listagg(students, ',')), ',')) AS total FROM {T}",
        8,
        4,
        COLLECTION,
    ),
    "scalar_subquery_summed_per_row": (
        Q,
        f"SELECT SUM((SELECT SUM({N}) FROM {T})) AS total FROM {T}",
        428,
        107,
        WINDOW,
    ),
    # Adversary round (grouping sets): SUMMARIZE aggregates its whole input.
    "summarize_over_a_rollup": (
        Q,
        f"SELECT CAST(avg AS DOUBLE) * count AS total FROM (SUMMARIZE SELECT t FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)))",
        214,
        107,
        GROUPING,
    ),
    # Adversary round (doors): a row multiplier listed beside the value.
    "unlist_listed_beside_the_value": (
        COUNT_Q,
        f"SELECT COUNT(*) AS total FROM (SELECT {N} AS x, unlist([1, 2]) AS g FROM {T})",
        8,
        4,
        GENERATOR,
    ),
    "generate_subscripts_listed_beside_the_value": (
        Q,
        f"SELECT SUM(x) AS total FROM (SELECT {N} AS x, generate_subscripts([1, 2], 1) AS g FROM {T})",
        214,
        107,
        GENERATOR,
    ),
    "list_concat_counted": (
        COUNT_Q,
        f"SELECT len(list({N}) || list({N})) AS total FROM {T}",
        8,
        4,
        COLLECTION,
    ),
    "list_concat_counted_typed_functions": (
        COUNT_Q,
        f"SELECT array_length(array_concat(list({N}), list({N}))) AS total FROM {T}",
        8,
        4,
        COLLECTION,
    ),
    "array_agg_concat_counted": (
        COUNT_Q,
        f"SELECT len(array_agg({N}) || array_agg({N})) AS total FROM {T}",
        8,
        4,
        COLLECTION,
    ),
    "list_from_a_derived_table": (
        COUNT_Q,
        f"SELECT len(l || l) AS total FROM (SELECT list({N}) AS l FROM {T})",
        8,
        4,
        COLLECTION,
    ),
    "list_through_a_lateral_alias": (
        COUNT_Q,
        f"SELECT list({N}) AS l, len(l || l) AS total FROM {T}",
        8,
        4,
        COLLECTION,
    ),
    "union_all_summed": (
        Q,
        f"SELECT SUM(n) AS total FROM (SELECT {N} AS n FROM {T} UNION ALL SELECT {N} FROM {T})",
        214,
        107,
        SET_OP,
    ),
    "unnest_pair_summed": (
        Q,
        f"SELECT SUM(n) AS total FROM (SELECT UNNEST([{N}, {N}]) AS n FROM {T})",
        214,
        107,
        GENERATOR,
    ),
}

_R = f"SELECT 'x' AS k, SUM({N}) AS s FROM {T} GROUP BY ROLLUP(school_id)"
#: Adversary round (step 7). Four runs on Sonnet at high effort, each with its own lens and
#: none the author of the check, wrote statements to get a wrong figure past it. These are
#: the ones that did on the first build, each now refused. name -> same shape as MORE_SHAPES.
ADVERSARY_SHAPES: dict[str, tuple[str, str, int | float, int | float, str]] = {
    # Lens A: an aggregate written inside a PIVOT clause was never inspected.
    "pivot_clause_over_rollup": (
        Q,
        f"SELECT * FROM ({_R}) PIVOT (SUM(s) FOR k IN ('x'))",
        214,
        107,
        GROUPING,
    ),
    "pivot_clause_over_rollup_cte": (
        Q,
        f"WITH r AS ({_R}) SELECT * FROM r PIVOT (SUM(s) FOR k IN ('x'))",
        214,
        107,
        GROUPING,
    ),
    "pivot_statement_using_over_rollup": (Q, f"PIVOT ({_R}) ON k USING SUM(s)", 214, 107, GROUPING),
    "pivot_clause_over_union": (
        Q,
        f"SELECT * FROM (SELECT 'x' AS k, {N} AS s FROM {T} UNION ALL SELECT 'x', {N} FROM {T}) PIVOT (SUM(s) FOR k IN ('x'))",
        214,
        107,
        SET_OP,
    ),
    "pivot_clause_over_window": (
        Q,
        f"SELECT * FROM (SELECT 'x' AS k, SUM({N}) OVER () AS s FROM {T}) PIVOT (SUM(s) FOR k IN ('x'))",
        428,
        107,
        WINDOW,
    ),
    "pivot_clause_counts_unpivoted_rows": (
        COUNT_Q,
        f"SELECT * FROM (SELECT 'x' AS k, v FROM (UNPIVOT (SELECT {N} a, {N} b FROM {T}) ON a, b INTO NAME n VALUE v)) PIVOT (COUNT(v) FOR k IN ('x'))",
        8,
        4,
        TURN,
    ),
    # Lens B: collections sqlglot does not type, list forms of scalar aggregates, a
    # collection or a window value behind a scalar subquery, a renamed duplicate column.
    "histogram_keys_concatenated": (
        Q,
        f"SELECT list_sum(map_keys(histogram({N})) || map_keys(histogram({N}))) AS total FROM {T}",
        214,
        107,
        COLLECTION,
    ),
    "quantile_list_concatenated": (
        Q,
        f"SELECT list_sum(list_concat(quantile_disc({N}, [0.0, 0.34, 0.67, 1.0]), quantile_disc({N}, [0.0, 0.34, 0.67, 1.0]))) AS total FROM {T}",
        214,
        107,
        COLLECTION,
    ),
    "collection_in_a_scalar_subquery": (
        Q,
        f"SELECT list_sum((SELECT list({N}) FROM {T}) || (SELECT list({N}) FROM {T})) AS total",
        214,
        107,
        COLLECTION,
    ),
    "scalar_subquery_as_sum_argument": (
        Q,
        f"SELECT SUM((SELECT SUM({N}) FROM {T})) AS total FROM {T}",
        428,
        107,
        WINDOW,
    ),
    "scalar_subquery_as_avg_argument": (
        "What is the average per enrollment?",
        f"SELECT AVG((SELECT SUM({N}) FROM {T})) AS total FROM {T}",
        107.0,
        26.75,
        WINDOW,
    ),
    "duplicate_output_name_renamed": (
        Q,
        f"SELECT SUM(c_1) AS total FROM (SELECT {N} AS c, SUM({N}) OVER () AS c FROM {T})",
        428,
        107,
        WINDOW,
    ),
    # Lens C: row generators that are not UNNEST in a FROM, and a table read twice.
    "generate_subscripts_in_a_projection": (
        Q,
        f"SELECT SUM(x) AS total FROM (SELECT {N} AS x, generate_subscripts([1, 2], 1) AS g FROM {T})",
        214,
        107,
        GENERATOR,
    ),
    "method_unnest_in_a_projection": (
        Q,
        f"SELECT SUM(x) AS total FROM (SELECT [{N}, {N}].unnest() AS x FROM {T})",
        214,
        107,
        GENERATOR,
    ),
    "schema_qualified_unnest_of_range": (
        Q,
        f"SELECT SUM({N}) AS total FROM (SELECT students, main.unnest(range(2)) AS k FROM {T})",
        214,
        107,
        GENERATOR,
    ),
    "self_join_on_true": (
        Q,
        f"SELECT SUM(CAST(a.students AS INTEGER)) AS total FROM {T} a JOIN {T} b ON true",
        428,
        107,
        SELF_JOIN,
    ),
    "self_cross_join_counted": (
        COUNT_Q,
        f"SELECT COUNT(*) AS total FROM {T} a CROSS JOIN {T} b",
        16,
        4,
        SELF_JOIN,
    ),
    "table_cross_joined_to_its_own_sum": (
        Q,
        f"WITH tot AS (SELECT SUM({N}) AS t FROM {T}) SELECT SUM(t) AS total FROM {T} CROSS JOIN tot",
        428,
        107,
        SELF_JOIN,
    ),
    "two_derived_tables_over_one_table": (
        Q,
        f"SELECT SUM(x) AS total FROM (SELECT {N} AS x FROM {T}) t1, (SELECT school_id FROM {T}) t2",
        428,
        107,
        SELF_JOIN,
    ),
    # A collection encoded as text and turned back into rows: string_agg collects.
    "string_agg_concatenated_then_unnested": (
        Q,
        f"SELECT SUM(CAST(v AS INTEGER)) AS total FROM (SELECT UNNEST(string_split(string_agg(students, ',') || ',' || string_agg(students, ','), ',')) AS v FROM {T})",
        214,
        107,
        COLLECTION,
    ),
    "cte_read_twice_and_summed": (
        Q,
        f"WITH c AS (SELECT {N} AS x FROM {T}) SELECT SUM(a.x) AS total FROM c a, c b",
        428,
        107,
        SELF_JOIN,
    ),
}
MORE_SHAPES.update(ADVERSARY_SHAPES)

LIST_Q = "List the enrollments"
#: Clause 4. Correct one-table statements: name -> (question, sql). Each
#: answers exactly as on the parent commit (this test passes there too).
CORRECT: dict[str, tuple[str, str]] = {
    "plain_sum": (Q, TRUE_SQL),
    "plain_count": (COUNT_Q, f"SELECT COUNT(*) AS total FROM {T}"),
    "plain_avg": (Q, f"SELECT AVG({N}) AS total FROM {T}"),
    "plain_min": (Q, f"SELECT MIN({N}) AS total FROM {T}"),
    "plain_max": (Q, f"SELECT MAX({N}) AS total FROM {T}"),
    "one_group_by": (
        LIST_Q,
        f"SELECT school_id, SUM({N}) AS total FROM {T} GROUP BY school_id ORDER BY school_id",
    ),
    "outer_sum_over_one_group_by": (
        Q,
        f"SELECT SUM(t) AS total FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY school_id)",
    ),
    "outer_avg_over_one_group_by": (
        Q,
        f"SELECT AVG(t) AS total FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY school_id)",
    ),
    "rollup_listing_with_its_total_row": (
        LIST_Q,
        f"SELECT school_id, SUM({N}) AS total FROM {T} GROUP BY ROLLUP(school_id) ORDER BY school_id",
    ),
    "rollup_listing_through_a_select": (
        LIST_Q,
        f"SELECT COALESCE(school_id, 'all') AS school, t AS total FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)) ORDER BY 1",
    ),
    "filter": (Q, f"SELECT SUM({N}) AS total FROM {T} WHERE school_id <> '9'"),
    "count_distinct": (Q, f"SELECT COUNT(DISTINCT school_id) AS total FROM {T}"),
    "select_distinct": (LIST_Q, f"SELECT DISTINCT school_id FROM {T} ORDER BY school_id"),
    "having": (
        LIST_Q,
        f"SELECT school_id, SUM({N}) AS total FROM {T} GROUP BY school_id HAVING SUM({N}) > 20 ORDER BY school_id",
    ),
    "window_total_listed": (
        LIST_Q,
        f"SELECT enrollment_id, SUM({N}) OVER () AS total FROM {T} ORDER BY enrollment_id",
    ),
    "share_of_total_window_over_groups": (
        LIST_Q,
        f"SELECT school_id, t * 100 / SUM(t) OVER () AS pct FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY school_id) ORDER BY school_id",
    ),
    "sum_after_row_number_filter": (
        Q,
        f"SELECT SUM(n) AS total FROM (SELECT {N} AS n, ROW_NUMBER() OVER (PARTITION BY school_id ORDER BY enrollment_id) AS rn FROM {T}) WHERE rn = 1",
    ),
    "sum_after_row_number_filter_star": (
        Q,
        f"SELECT SUM({N}) AS total FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY school_id ORDER BY enrollment_id) AS rn FROM {T}) WHERE rn = 1",
    ),
    "sum_after_row_number_filter_star_exclude": (
        Q,
        f"SELECT SUM({N}) AS total FROM (SELECT * EXCLUDE (school_id), ROW_NUMBER() OVER (ORDER BY enrollment_id) AS rn FROM {T}) WHERE rn <= 4",
    ),
    "qualify": (
        LIST_Q,
        f"SELECT enrollment_id, students FROM {T} QUALIFY ROW_NUMBER() OVER (ORDER BY {N} DESC) = 1",
    ),
    "collections_listed": (
        LIST_Q,
        f"SELECT school_id, list(enrollment_id) AS ids, string_agg(enrollment_id, ',') AS joined FROM {T} GROUP BY school_id ORDER BY school_id",
    ),
    "unpivot_listing": (
        LIST_Q,
        f"SELECT * FROM (SELECT enrollment_id, {N} AS a, {N} AS b FROM {T}) UNPIVOT (n FOR k IN (a, b)) ORDER BY enrollment_id, k",
    ),
}

#: The price of the rule, pinned so it is never silent (clause 4): correct
#: statements that answered on the parent and abstain now.
#: name -> (question, sql, the correct figure, the rule that refuses it).
NEWLY_ABSTAIN: dict[str, tuple[str, str, Any, str]] = {
    "max_of_a_running_total": (
        Q,
        f"SELECT MAX(r) AS total FROM (SELECT SUM({N}) OVER (ORDER BY enrollment_id) AS r FROM {T})",
        107,
        WINDOW,
    ),
    "sum_of_window_shares": (
        Q,
        f"SELECT SUM(share) AS total FROM (SELECT {N} * 100 / SUM({N}) OVER () AS share FROM {T})",
        100.0,
        WINDOW,
    ),
    "sum_of_rollup_rows_without_the_total_row": (
        Q,
        f"SELECT SUM(t) AS total FROM (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)) WHERE school_id IS NOT NULL",
        107,
        GROUPING,
    ),
    "one_grouping_set_spelled_as_grouping_sets": (
        Q,
        f"SELECT SUM(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY GROUPING SETS ((school_id)))",
        107,
        GROUPING,
    ),
    "max_over_an_unpivot_of_one_column": (
        Q,
        f"SELECT MAX(CAST(v AS INTEGER)) AS total FROM (SELECT students FROM {T}) UNPIVOT (v FOR k IN (students))",
        40,
        TURN,
    ),
    "length_of_a_distinct_list": (
        Q,
        f"SELECT len(list(DISTINCT school_id)) AS total FROM {T}",
        4,
        COLLECTION,
    ),
    "first_element_of_a_sorted_list": (
        Q,
        f"SELECT list({N} ORDER BY {N} DESC)[1] AS total FROM {T}",
        40,
        COLLECTION,
    ),
}

#: Clause 5. What the class rule cannot see: arithmetic the statement wrote.
#: Served with a wrong figure today, strict expected failures. name -> (sql, figure).
OPEN: dict[str, tuple[str, int]] = {
    "sum_plus_the_same_sum": (f"SELECT SUM({N}) + SUM({N}) AS total FROM {T}", 214),
    "sum_times_two": (f"SELECT SUM({N}) * 2 AS total FROM {T}", 214),
    "sum_of_a_value_added_to_itself": (f"SELECT SUM({N} + {N}) AS total FROM {T}", 214),
    "one_column_projected_twice_and_added": (f"SELECT SUM(a) + SUM(b) AS total FROM {_TWO}", 214),
    "aggregate_alias_added_to_itself": (
        f"SELECT s + s AS total FROM (SELECT SUM({N}) AS s FROM {T})",
        214,
    ),
    "window_total_times_window_count_listed": (
        f"SELECT DISTINCT SUM({N}) OVER () * COUNT(*) OVER () AS total FROM {T}",
        428,
    ),
    # Adversary round: a pick among a ROLLUP's mixed rows (a choice, not a repeated value).
    "rollup_listing_cut_to_its_smallest_row": (
        f"SELECT SUM({N}) AS total FROM {T} GROUP BY ROLLUP(school_id) ORDER BY total LIMIT 1",
        12,
    ),
    "rollup_rows_cut_to_one_by_an_outer_select": (
        f"SELECT t AS total FROM (SELECT school_id, SUM({N}) AS t FROM {T} "
        "GROUP BY ROLLUP(school_id)) ORDER BY t LIMIT 1",
        12,
    ),
}
OPEN_REASON = (
    "open by name (dms#531 clause 5): scalar arithmetic the statement wrote. No operator "
    "hands a value on twice, so the class rule has nothing to refuse, and no conservation "
    "check can tell this figure from a wanted one."
)


def _ask(
    door: str,
    sql: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    question: str = Q,
) -> tuple[od._Space, dict[str, Any]]:
    """Carry ``sql`` to the envelope through one door."""
    if door == "loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = od._space(tmp_path, monkeypatch, minter)
    if door != "contract":
        return rig, rig.ask(question, {"query_sql": sql, "plan_source": "ontology_plan"})
    rows = od._oracle(rig.lake, sql)
    figure = next(iter(rows[0].values())) if rows else None

    def _contract(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer=f"The total is {figure}.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=sql,
            rows=rows,
        )

    monkeypatch.setattr(rig.cortex, "ask", _contract)
    env = rig.ask(question, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    return rig, env


def _assert_abstain_named(env: dict[str, Any], rig: Any, figures: tuple[Any, ...]) -> str:
    """ABSTAIN with a named reason: no row, no value, no figure in the text (E9).

    Returns the reason code on the ``gap:`` line.
    """
    served = f"served {env.get('rows')} as {env.get('badge')}"
    assert env["badge"] == "ABSTAIN", (served, env.get("text"), env.get("assumptions"))
    text = od._assert_abstain(env, "")
    gap = next(line for line in text.splitlines() if "gap: " in line)
    code = gap.split("gap: ", 1)[1].strip()
    assert code, gap
    for figure in figures:
        assert not re.search(rf"(?<![\w.]){re.escape(str(figure))}(?![\w.])", text), (figure, text)
    assert "total=" not in text and "The total is" not in text, text
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"
    return code


def _join_why(rig: od._Space, sql: str) -> str | None:
    """What rule 16's join allowlist says. It runs first, so its reason is the one shown."""
    onto = load_space_ontology(rig.space_id)
    assert onto is not None
    con = duckdb.connect(str(rig.lake))
    try:
        violations = onto.verify(con)
    finally:
        con.close()
    return unverified_join_reason(
        sql, onto, violations, columns_of=relation_columns(onto, rig.lake)
    )


def _assert_refused_by_the_rule(
    env: dict[str, Any], rig: od._Space, sql: str, why: str, figures: tuple[Any, ...]
) -> None:
    """The envelope abstains, and the class rule itself refuses ``sql`` for ``why``."""
    code = _assert_abstain_named(env, rig, figures)
    reason = repeated_input_reason(sql) or ""
    assert reason.startswith(f"repeated_input:{why}"), (why, reason)
    joined = _join_why(rig, sql)
    if joined is None:
        # Only the class rule stands between this statement and the customer.
        assert f"repeated_input:{why}" in code, code
        assert reason in od._reasons(env), env.get("assumptions")
    else:
        # JOIN-HOLE-01 (#529) refuses it first; its reason is the one shown.
        assert "unverified_join" in code, code


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(ISSUE_ROWS))
def test_issue_row_abstains(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Clauses 1 and 2: each row of the issue's table, through each door.

    On the parent (integration base 583377f) 42 of these 51 are served with
    the figure stated in ``ISSUE_ROWS``; on origin/main (ebe65cc) all 51 are.
    """
    sql, wrong, why = ISSUE_ROWS[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    # The danger is real: the statement runs and returns the wrong figure.
    assert od._oracle(rig.lake, sql) == [{"total": wrong}]
    assert od._oracle(rig.lake, TRUE_SQL) == [{"total": TRUE}]
    assert wrong != TRUE
    _assert_refused_by_the_rule(env, rig, sql, why, (wrong, TRUE))


def test_only_three_issue_rows_are_shown_under_the_join_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Every other row has no refusal but the class rule (so removing it serves them)."""
    rig = od._space(tmp_path, monkeypatch, minter)
    shown_by_join_rule = {n for n, (sql, _w, _y) in ISSUE_ROWS.items() if _join_why(rig, sql)}
    assert shown_by_join_rule == {
        "r10_list_concat",
        "r11_list_pipe",
        "r14_unpivot_statement_on_columns_star",
    }


@pytest.mark.parametrize("name", sorted(MORE_SHAPES))
def test_more_ways_to_repeat_a_value_abstain(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    question, sql, wrong, true, why = MORE_SHAPES[name]
    rig, env = _ask("generate", sql, tmp_path, monkeypatch, minter, question)
    rows = od._oracle(rig.lake, sql)
    first = rows[0].get("total", next(iter(rows[0].values()))) if rows else None
    assert first == wrong and wrong != true, rows
    _assert_refused_by_the_rule(env, rig, sql, why, (wrong,))


SHADOWED_CTE: dict[str, tuple[str, int, str]] = {
    "cte_shadowed_by_a_later_sibling": (
        f"WITH c AS (SELECT SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)) SELECT SUM(t) AS total FROM (WITH d AS (SELECT t FROM c), c AS (SELECT 1 AS t) SELECT t FROM d)",
        214,
        GROUPING,
    ),
    "window_cte_shadowed_by_a_later_sibling": (
        f"WITH c AS (SELECT SUM({N}) OVER () AS t FROM {T}) SELECT SUM(t) AS total FROM (WITH d AS (SELECT t FROM c), c AS (SELECT 1 AS t) SELECT t FROM d)",
        428,
        WINDOW,
    ),
}


@pytest.mark.parametrize("name", sorted(SHADOWED_CTE))
def test_cte_shadowed_by_a_later_sibling_abstains_at_the_contract_door(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Adversary round: a CTE sees only the CTEs written before it.

    The inner ``c`` is the OUTER dirty one, not the later clean sibling. Served
    428/214 at the contract door before the scope fix; the generate and loop
    doors refused it by accident (``ungranted``), so only this door is asserted.
    """
    sql, wrong, why = SHADOWED_CTE[name]
    rig, env = _ask("contract", sql, tmp_path, monkeypatch, minter)
    assert od._oracle(rig.lake, sql) == [{"total": wrong}]
    _assert_refused_by_the_rule(env, rig, sql, why, (wrong, TRUE))


# --- the held Yes door ------------------------------------------------------------

_ORDER_N = "CAST(amount AS INTEGER)"
#: Held SQL in a file Space (no ontology): name -> (sql, the figure it returns, the rule).
#: The true total is 250 (``test_connect_ask_03.ONE_TABLE_TRUTH``).
HELD_SHAPES: dict[str, tuple[str, int, str]] = {
    "rollup": (
        f"SELECT SUM(t) AS total FROM (SELECT customer_id, SUM({_ORDER_N}) AS t FROM {ca.ORDERS} GROUP BY ROLLUP(customer_id))",
        500,
        GROUPING,
    ),
    "unpivot": (
        f"SELECT SUM(n) AS total FROM (SELECT {_ORDER_N} AS a, {_ORDER_N} AS b FROM {ca.ORDERS}) UNPIVOT (n FOR k IN (a, b))",
        500,
        TURN,
    ),
    "window_total": (
        f"SELECT SUM(w) AS total FROM (SELECT SUM({_ORDER_N}) OVER () AS w FROM {ca.ORDERS})",
        1000,
        WINDOW,
    ),
}


@pytest.mark.parametrize("name", sorted(HELD_SHAPES))
def test_held_sql_confirmed_with_yes_abstains(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    """Clause 2, the held Yes door: a Yes is not a proof. On the parent the Yes serves the figure.

    Tested where SQL is parked today (``DMS_DEF_VALIDATE`` in a file Space).
    Held SQL in a Space with an ontology is #524; the same call guards it.
    """
    sql, wrong, why = HELD_SHAPES[name]
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    rig = ca._file_space(tmp_path, monkeypatch, minter)
    rig.cortex.suggestion = ca.ONE_TABLE_Q
    assert od._oracle(rig.lake, sql) == [{"total": wrong}] and wrong != ca.ONE_TABLE_TRUTH
    offer = rig.ask(ca.ONE_TABLE_Q, ca._gen(sql))
    assert offer["badge"] == "ABSTAIN" and offer["rows"] == [] and offer["values"] == []
    from dms_executor.reconfirm import held_ready, peek_pending

    pending = peek_pending(offer["reconfirm"]["token"])
    assert pending is not None and held_ready(pending), "no SQL was parked for a Yes"
    assert pending["sql"] == sql
    env = rig.confirm(offer["reconfirm"]["token"], yes=True)
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("rows"), env.get("text"))
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert not env.get("drillthrough_token")
    assert f"repeated_input:{why}" in od._reasons(env), env.get("assumptions")
    assert not re.search(rf"\b{wrong}\b", str(env.get("text") or "")), env.get("text")
    assert rig.cortex.executed == []


def test_held_correct_sql_confirmed_with_yes_still_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    """The held door still serves a correct one-table total, as on the parent."""
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    rig = ca._file_space(tmp_path, monkeypatch, minter)
    rig.cortex.suggestion = ca.ONE_TABLE_Q
    offer = rig.ask(ca.ONE_TABLE_Q, ca._gen(ca.ONE_TABLE_SQL))
    assert offer["badge"] == "ABSTAIN" and offer["rows"] == []
    env = rig.confirm(offer["reconfirm"]["token"], yes=True)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == [{"total": ca.ONE_TABLE_TRUTH}]


# --- clause 3: the refusal follows the ladder --------------------------------------


def _shape(env: dict[str, Any], head: str) -> dict[str, Any]:
    block = env.get("reconfirm")
    code = str(block.get("reason_code") or "") if isinstance(block, dict) else ""
    return {
        "keys": sorted(env),
        "badge": env.get("badge"),
        "abstained": env.get("abstained"),
        "route": env.get("route"),
        "served_route": env.get("served_route"),
        "ladder_rung": env.get("ladder_rung"),
        "model_calls": env.get("model_calls"),
        "reconfirm_keys": sorted(block) if isinstance(block, dict) else None,
        "reconfirm_code": code.split(head, 1)[0] + "<reason>" if head in code else code,
        "suggested_question": block.get("suggested_question") if isinstance(block, dict) else None,
    }


@pytest.mark.parametrize("door", ["generate", "loop"])
def test_refusal_feeds_the_retry_and_ends_in_reconfirm(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Rule 15: the same ladder a join refusal climbs, ending in the reconfirm block."""
    if door == "loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    suggestion = "How many students are enrolled?"
    sql, _wrong, why = ISSUE_ROWS["r01_rollup"]
    envs: dict[str, dict[str, Any]] = {}
    calls: dict[str, list[dict[str, Any]]] = {}
    prompts: dict[str, list[str]] = {}
    for kind, statement in (("join", od.WRONG_JOIN_SQL), ("total", sql)):
        (tmp_path / kind).mkdir()
        rig = od._space(tmp_path / kind, monkeypatch, minter)
        seen: list[str] = []

        def _complete(prompt: str, seen: list[str] = seen) -> str:
            seen.append(prompt)
            return suggestion

        monkeypatch.setattr(rig.cortex, "complete", _complete, raising=False)
        envs[kind] = rig.ask(Q, {"query_sql": statement, "plan_source": "ontology_plan"})
        calls[kind], prompts[kind] = list(rig.cortex.insights), seen
        assert rig.cortex.executed == []
    env = envs["total"]
    reason = repeated_input_reason(sql) or ""
    assert reason.startswith(f"repeated_input:{why}")
    block = env["reconfirm"]
    # Not a new direct abstain: the reconfirm block, with the closest question.
    assert block["suggested_question"] == suggestion and block["token"]
    assert block["reason_code"].endswith(reason), block
    assert reason in prompts["total"][-1], "the reconfirm writer was not given the reason"
    if door == "loop":
        assert block["reason_code"] == f"loop_exhausted:checker:{reason}"
        # The reason went back to the model before the ladder gave up.
        retries = [c for c in calls["total"] if "sql_feedback" in c]
        assert retries and "repeated_input" in str(retries[0]["sql_feedback"]), calls["total"]
        assert sql in str(retries[0]["sql_feedback"])
    # Same envelope shape and the same number of model calls as a join refusal.
    assert _shape(env, "repeated_input") == _shape(envs["join"], "unverified_join")
    assert len(calls["total"]) == len(calls["join"])
    _assert_abstain_named(env, rig, (214, TRUE))


# --- clause 4: correct statements ---------------------------------------------------


def _assert_served(
    env: dict[str, Any], rig: od._Space, sql: str, door: str
) -> list[dict[str, Any]]:
    oracle = od._oracle(rig.lake, sql)
    assert oracle, sql
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert od._multiset(env["rows"]) == od._multiset(oracle)
    text = str(env.get("text") or "")
    first = oracle[0]
    if door == "contract":
        assert f"The total is {next(iter(first.values()))}." in text, text
    else:
        assert ", ".join(f"{k}={v}" for k, v in first.items()) in text, text
        assert rig.cortex.executed == [sql]
    return oracle


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(CORRECT))
def test_correct_one_table_statement_still_answers(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    question, sql = CORRECT[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    _assert_served(env, rig, sql, door)
    assert repeated_input_reason(sql) is None


def test_correct_figures_are_the_tables_figures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """The served rows of the clause 4 totals, stated from the fixture rows, not from SQL."""
    students = [int(r[2]) for r in od.ENROLL_ROWS]
    assert sum(students) == TRUE
    want = {
        "plain_sum": TRUE,
        "plain_count": len(students),
        "plain_avg": TRUE / len(students),
        "plain_min": min(students),
        "plain_max": max(students),
        "outer_sum_over_one_group_by": TRUE,
        "filter": TRUE - 12,
        "count_distinct": 4,
        "sum_after_row_number_filter": TRUE,
        "sum_after_row_number_filter_star": TRUE,
    }
    rig = od._space(tmp_path, monkeypatch, minter)
    for name, figure in want.items():
        question, sql = CORRECT[name]
        env = rig.ask(question, {"query_sql": sql, "plan_source": "ontology_plan"})
        assert env["badge"] == "L2_VALIDATED", (name, env.get("text"))
        assert env["rows"] == [{"total": figure}], (name, env["rows"])
    rollup = rig.ask(
        LIST_Q,
        {
            "query_sql": CORRECT["rollup_listing_with_its_total_row"][1],
            "plan_source": "ontology_plan",
        },
    )
    # A ROLLUP listing keeps its total row: four schools and the 107.
    assert sorted(r["total"] for r in rollup["rows"]) == sorted([*students, TRUE])


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(NEWLY_ABSTAIN))
def test_correct_statements_that_now_abstain_are_named(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Served on the parent with the correct figure; the rule cannot tell them from the wrong ones."""
    question, sql, figure, why = NEWLY_ABSTAIN[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    assert od._oracle(rig.lake, sql) == [{"total": figure}]
    _assert_refused_by_the_rule(env, rig, sql, why, ())
    assert _join_why(rig, sql) is None, "the class rule is what refuses this one"


# --- untyped functions: the guard adds no false abstain -----------------------------

#: One-table statements with a function sqlglot does not type, or an aggregate
#: spelled by a name the guard has no list entry for. Correct on the table.
#: #529's function rule decides some of them today (``unproven_function``);
#: this guard must not change a single outcome. name -> (question, sql).
UNTYPED: dict[str, tuple[str, str]] = {
    "date_part_in_a_filter": (
        Q,
        f"SELECT SUM({N}) AS total FROM {T} WHERE date_part('year', DATE '2024-05-01') = 2024",
    ),
    "dayname_in_a_filter": (
        Q,
        f"SELECT SUM({N}) AS total FROM {T} WHERE dayname(DATE '2024-05-01') = 'Wednesday'",
    ),
    "date_part_in_a_projection": (
        Q,
        f"SELECT SUM({N}) AS total, date_part('year', DATE '2024-05-01') AS y FROM {T}",
    ),
    "mean": (Q, f"SELECT mean({N}) AS total FROM {T}"),
    "product": (Q, f"SELECT product({N}) AS total FROM {T}"),
    "mode": (Q, f"SELECT mode({N}) AS total FROM {T}"),
    "geomean": (Q, f"SELECT geomean({N}) AS total FROM {T}"),
    "hash_listed": (
        LIST_Q,
        f"SELECT enrollment_id, hash(school_id) AS h FROM {T} ORDER BY enrollment_id",
    ),
    "mean_of_one_group_by": (
        Q,
        f"SELECT mean(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY school_id)",
    ),
}


def _no_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor
    import dms_executor.generative_ask as ga

    monkeypatch.setattr(ga, "repeated_input_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(dms_executor, "repeated_input_reason", lambda *_a, **_k: None)


@pytest.mark.parametrize("door", ["generate", "contract"])
@pytest.mark.parametrize("name", sorted(UNTYPED))
def test_untyped_function_outcome_is_not_changed_by_the_guard(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """An unknown function is not by itself a multiplied total (before = after)."""
    question, sql = UNTYPED[name]
    assert repeated_input_reason(sql) is None
    (tmp_path / "with").mkdir()
    rig, after = _ask(door, sql, tmp_path / "with", monkeypatch, minter, question)
    with monkeypatch.context() as m:
        _no_guard(m)
        (tmp_path / "without").mkdir()
        _rig0, before = _ask(door, sql, tmp_path / "without", m, minter, question)
    assert (after["badge"], after["abstained"], after["rows"]) == (
        before["badge"],
        before["abstained"],
        before["rows"],
    ), (name, before.get("text"), after.get("text"))
    assert "repeated_input" not in od._reasons(after) + str(after.get("text") or "")
    if after["abstained"]:
        # Refused by the join rule's function rule (#529), not by this guard.
        assert "unverified_join" in str(after.get("text") or ""), after.get("text")
    else:
        assert od._multiset(after["rows"]) == od._multiset(od._oracle(rig.lake, sql))


#: Correct figures the guard refuses on its own, because a function is applied to
#: a collection (rule 4). Today #529's function rule refuses them first
#: (``list_sum`` is untyped); once that rule is narrowed these are new abstains.
#: name -> (sql, the correct figure).
LISTS_REFUSED: dict[str, tuple[str, float]] = {
    "list_sum_of_one_list": (f"SELECT list_sum(list({N})) AS total FROM {T}", 107),
    "list_avg_of_one_list": (f"SELECT list_avg(list({N})) AS total FROM {T}", 26.75),
    "len_of_one_list": (f"SELECT len(list({N})) AS total FROM {T}", 4),
}


@pytest.mark.parametrize("name", sorted(LISTS_REFUSED))
def test_function_over_one_list_is_refused_by_the_guard_and_named(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql, figure = LISTS_REFUSED[name]
    rig, env = _ask("generate", sql, tmp_path, monkeypatch, minter)
    assert od._oracle(rig.lake, sql)[0]["total"] == figure
    assert (repeated_input_reason(sql) or "").startswith("repeated_input:collection_under_function")
    _assert_abstain_named(env, rig, ())


# --- clause 5: open, by name --------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=OPEN_REASON)
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(OPEN))
def test_open_scalar_arithmetic_is_still_served(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Expected to fail: these are served today with the figure in ``OPEN``, not 107."""
    sql, wrong = OPEN[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    assert od._oracle(rig.lake, sql) == [{"total": wrong}] and wrong != TRUE
    _assert_abstain_named(env, rig, (wrong, TRUE))


def test_open_rows_are_not_refused_by_the_rule() -> None:
    """The limit, pinned: the class rule says nothing about scalar arithmetic."""
    for name, (sql, _wrong) in OPEN.items():
        assert repeated_input_reason(sql) is None, name


# --- the rule's own edges -----------------------------------------------------------


def test_row_generator_as_a_from_source_refuses_on_its_own() -> None:
    """The join rule refuses these first at the envelope; the guard must not rely on it (M07)."""
    for sql in (
        f"SELECT SUM(o.n) AS total FROM (SELECT {N} AS n FROM {T}) o, UNNEST([1, 2]) u(x)",
        f"SELECT SUM(o.n) AS total FROM (SELECT {N} AS n FROM {T}) o CROSS JOIN (VALUES (1), (2)) v(x)",
        f"SELECT SUM(o.n) AS total FROM (SELECT {N} AS n FROM {T}) o, range(3) r",
    ):
        assert (repeated_input_reason(sql) or "").startswith(
            "repeated_input:row_generator_under_aggregate"
        ), sql


def test_statement_the_analysis_cannot_finish_refuses() -> None:
    assert repeated_input_reason("SELECT SUM(") == "repeated_input:parse"
    assert repeated_input_reason("SELECT 1; SELECT 2") == "repeated_input:statements"


def test_rule_reads_positions_not_words() -> None:
    """The words alone do nothing: a ROLLUP, an UNPIVOT and a window are listings here."""
    for sql in (
        f"SELECT school_id, SUM({N}) AS total FROM {T} GROUP BY ROLLUP(school_id)",
        f"SELECT school_id, SUM({N}) AS total FROM {T} GROUP BY CUBE(school_id, enrollment_id)",
        f"SELECT * FROM {_TWO} UNPIVOT (n FOR k IN (a, b))",
        f"SELECT enrollment_id, SUM({N}) OVER () AS rollup_unpivot_window FROM {T}",
        f"SELECT list({N}) AS list_concat FROM {T}",
    ):
        assert repeated_input_reason(sql) is None, sql
    # And no word is needed to refuse: GROUP BY ALL is one set, a comma of sets is not.
    assert (
        repeated_input_reason(f"SELECT SUM(t) FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY ALL)")
        is None
    )


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT COUNT(*) AS n FROM {T} i, LATERAL (SELECT 1 AS one) s",
        "WITH c AS (TABLE enrollments) SELECT COUNT(*) AS n FROM c",
    ],
)
def test_lateral_subquery_and_table_shorthand_are_one_read(sql: str) -> None:
    """Found by the full suite (test_grant_struct_02, file Space): sqlglot files a
    LATERAL subquery under UDTF and reads ``TABLE t`` as an alias. Neither
    repeats a value, so the class rule must not refuse them. Asserted on the
    guard: in the ontology Space the join rule refused both before the guard
    existed (``derived_table_over_join``; ``TABLE schema.t`` does not parse),
    and test_grant_struct_02 asserts the served rows in the Space where they
    answer."""
    assert repeated_input_reason(sql) is None


# --- a legitimate one-table total with a date filter still answers ------------------

_AMT = "CAST(amount AS INTEGER)"
_FEB_ON = sum(a for (_o, _c, a), d in zip(ca.ORDER_ROWS, ca.ORDER_DATES, strict=True) if d >= "2026-02-01")
_JANUARY = sum(a for (_o, _c, a), d in zip(ca.ORDER_ROWS, ca.ORDER_DATES, strict=True) if d[5:7] == "01")
#: name -> (question, sql, the truth from the CSV constants). Typed date
#: expressions only: an untyped one (``date_part``, ``dayname``) is refused today
#: by #529's function rule, which is being narrowed; the guard itself passes it
#: (``test_date_function_filter_is_not_the_guards_refusal``).
DATE_FILTERED: dict[str, tuple[str, str, int]] = {
    "date_literal_comparison": (
        "What is the total order amount for orders dated 2026-02-01 or later?",
        f"SELECT SUM({_AMT}) AS total FROM {ca.ORDERS} WHERE CAST(order_date AS DATE) >= DATE '2026-02-01'",
        _FEB_ON,
    ),
    "extract_month": (
        "What is the total order amount for the first month of 2026?",
        f"SELECT SUM({_AMT}) AS total FROM {ca.ORDERS} WHERE EXTRACT(YEAR FROM CAST(order_date AS DATE)) = 2026 AND EXTRACT(MONTH FROM CAST(order_date AS DATE)) = 1",
        _JANUARY,
    ),
}


@pytest.mark.parametrize("name", sorted(DATE_FILTERED))
def test_one_table_sum_with_a_date_filter_still_answers(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    question, sql, truth = DATE_FILTERED[name]
    assert truth not in (0, ca.ONE_TABLE_TRUTH)
    assert repeated_input_reason(sql) is None
    rig = ca._file_space(tmp_path, monkeypatch, minter)
    env = rig.ask(question, ca._gen(sql))
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert env["rows"] == [{"total": truth}]
    assert f"total={truth}" in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == [sql]


def test_date_function_filter_is_not_the_guards_refusal() -> None:
    """``date_part`` in a filter or a listing is one value per row: the guard passes it."""
    for sql in (
        f"SELECT SUM({_AMT}) AS total FROM {ca.ORDERS} WHERE date_part('month', CAST(order_date AS DATE)) = 1",
        f"SELECT order_id, dayname(CAST(order_date AS DATE)) AS weekday FROM {ca.ORDERS} ORDER BY order_id",
        f"SELECT date_trunc('month', CAST(order_date AS DATE)) AS m, SUM({_AMT}) AS total FROM {ca.ORDERS} GROUP BY 1 ORDER BY 1",
    ):
        assert repeated_input_reason(sql) is None, sql


def test_one_row_lateral_and_table_form_are_one_read_for_the_guard() -> None:
    """test_grant_struct_02 serves these with exact rows; the guard must not refuse them.

    A LATERAL subquery that reads no relation adds no row, and ``TABLE t`` is
    ``SELECT * FROM t``. A LATERAL that reads another relation is a join: rule 16's.
    """
    for sql in (
        f"SELECT COUNT(*) AS n FROM {T} i, LATERAL (SELECT 1 AS one) s",
        f"SELECT COUNT(*) AS n FROM {T} i CROSS JOIN LATERAL (SELECT i.school_id) s",
        f"SELECT SUM(s.v) AS total FROM {T} i, LATERAL (SELECT {N} AS v) s",
        "WITH c AS (TABLE enrollments) SELECT COUNT(*) AS n FROM c",
    ):
        assert repeated_input_reason(sql) is None, sql
    # The TABLE form is followed like any CTE body: a ROLLUP over it still refuses.
    rollup = f"WITH c AS (TABLE enrollments) SELECT SUM(x) AS total FROM (SELECT SUM({N}) AS x FROM c GROUP BY ROLLUP(school_id))"
    assert (repeated_input_reason(rollup) or "").startswith(f"repeated_input:{GROUPING}")
    # sqlglot cannot parse ``TABLE schema.t``: it refuses, fail closed.
    assert repeated_input_reason(f"WITH c AS (TABLE {T}) SELECT COUNT(*) AS n FROM c") == "repeated_input:parse"


#: A LATERAL subquery passes on what the sources before it hold (integration
#: verifier fix): name -> (sql, the figure DuckDB returns, the rule). Truth 107.
LATERAL_SHAPES: dict[str, tuple[str, int, str]] = {
    "window_value_passed_through_a_lateral": (f"SELECT SUM(s.v) AS total FROM {_W} q, LATERAL (SELECT q.w AS v) s", 428, WINDOW),
    "window_value_computed_on_in_a_lateral": (f"SELECT SUM(s.v) AS total FROM {_W} q, LATERAL (SELECT q.w + 0 AS v) s", 428, WINDOW),
    "lateral_union_summed": (f"SELECT SUM({N}) AS total FROM {T} i, LATERAL (SELECT 1 AS k UNION ALL SELECT 2) s", 214, SET_OP),
    "lateral_unnest_summed": (f"SELECT SUM({N}) AS total FROM {T} i, LATERAL (SELECT UNNEST([1, 2]) AS k) s", 214, GENERATOR),
}


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(LATERAL_SHAPES))
def test_lateral_shapes_abstain(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql, wrong, why = LATERAL_SHAPES[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    assert od._oracle(rig.lake, sql) == [{"total": wrong}] and wrong != TRUE
    _assert_refused_by_the_rule(env, rig, sql, why, (wrong,))


# --- verifier round on the merged head: the demo Space, where the guard stands alone --

#: In a demo Space rule 16 does not run, so the class rule is the only check.
#: name -> (sql, the rule). Truth: SUM(quantity_kg) over the demo inventory.
DEMO_Q = "What is the total inventory quantity?"
DEMO_TRUE_SQL = "SELECT SUM(quantity_kg) AS total FROM inventory"
DEMO_HOLES: dict[str, tuple[str, str]] = {
    "table_form_names_a_window_cte": ("WITH w AS (SELECT SUM(quantity_kg) OVER () AS w FROM inventory), c AS (TABLE w) SELECT SUM(w) AS total FROM c", WINDOW),
    "table_form_names_a_rollup_cte": ("WITH r AS (SELECT category, SUM(quantity_kg) AS x FROM inventory GROUP BY ROLLUP(category)), c AS (TABLE r) SELECT SUM(x) AS total FROM c", GROUPING),
    "table_form_names_an_unpivot_cte": ("WITH u AS (SELECT * FROM (SELECT quantity_kg AS a, quantity_kg AS b FROM inventory) UNPIVOT (n FOR k IN (a, b))), c AS (TABLE u) SELECT SUM(n) AS total FROM c", TURN),
    "table_form_names_a_union_cte": ("WITH u AS (SELECT quantity_kg FROM inventory UNION ALL SELECT quantity_kg FROM inventory), c AS (TABLE u) SELECT SUM(quantity_kg) AS total FROM c", SET_OP),
    "table_form_self_join": ("WITH c AS (TABLE inventory) SELECT SUM(a.quantity_kg) AS total FROM c a, c b", "self_join_under_aggregate"),
    "json_group_array_concatenated": ("SELECT list_sum(CAST(json_group_array(quantity_kg) AS DOUBLE[]) || CAST(json_group_array(quantity_kg) AS DOUBLE[])) AS total FROM inventory", COLLECTION),
    "geomean_macro_over_a_window_value": ("SELECT geomean(w) AS total FROM (SELECT SUM(quantity_kg) OVER () AS w FROM inventory)", WINDOW),
    # Verifier round 2: ARRAY(SELECT ...) aggregates the query's rows into a list.
    "array_of_rollup_rows_summed": ("SELECT list_sum(ARRAY(SELECT SUM(quantity_kg) AS x FROM inventory GROUP BY ROLLUP(category))) AS total", GROUPING),
    "array_of_a_rollup_cte_summed": ("WITH r AS (SELECT SUM(quantity_kg) AS x FROM inventory GROUP BY ROLLUP(category)) SELECT list_sum(ARRAY(SELECT x FROM r)) AS total", GROUPING),
    "array_of_window_values_summed": ("SELECT list_sum(ARRAY(SELECT w FROM (SELECT SUM(quantity_kg) OVER () AS w FROM inventory))) AS total", WINDOW),
    "array_of_a_union_summed": ("SELECT list_sum(ARRAY(SELECT quantity_kg FROM inventory UNION ALL SELECT quantity_kg FROM inventory)) AS total", SET_OP),
    "two_arrays_concatenated": ("SELECT list_sum(ARRAY(SELECT quantity_kg FROM inventory) || ARRAY(SELECT quantity_kg FROM inventory)) AS total", COLLECTION),
    # Verifier round 3: a PIVOT whose aggregate collects turns out collection columns,
    # and a self-join is counted whatever qualifiers spell the table.
    # Verifier round 4: a collection reached by position, by COLUMNS(), or by
    # outer reference inside a LATERAL body.
    "list_reached_by_position": ("SELECT list_sum(#1 || #1) AS total FROM (SELECT list(quantity_kg) AS l FROM inventory)", COLLECTION),
    "list_reached_by_columns": ("SELECT list_sum(COLUMNS('l') || COLUMNS('l')) AS total FROM (SELECT list(quantity_kg) AS l FROM inventory)", COLLECTION),
    "list_reached_from_a_lateral_body": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, LATERAL (SELECT list_sum(s.l || s.l) AS v) u", COLLECTION),
    # Verifier round 5: a derived table that reads an earlier source without the
    # LATERAL keyword is bound laterally by DuckDB.
    "implicit_lateral_by_comma": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, (SELECT list_sum(s.l || s.l) AS v) u", COLLECTION),
    "implicit_lateral_by_join_on_true": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s JOIN (SELECT list_sum(s.l || s.l) AS v) u ON true", COLLECTION),
    "implicit_lateral_nested": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, (SELECT x.v FROM (SELECT list_sum(s.l || s.l) AS v) x) u", COLLECTION),
    "implicit_lateral_window_value_summed": ("SELECT SUM(u.v) AS total FROM (SELECT SUM(quantity_kg) OVER () AS w FROM inventory) s, (SELECT s.w AS v) u", WINDOW),
    # Verifier round 6: an unqualified or shadowed outer reference in a later
    # derived table; every later derived table is now analysed as a LATERAL.
    "implicit_lateral_unqualified_outer_column": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, (SELECT list_sum(l || l) AS v FROM (SELECT 1 AS one) z) u", COLLECTION),
    "implicit_lateral_unqualified_window_value": ("SELECT SUM(u.v) AS total FROM (SELECT SUM(quantity_kg) OVER () AS w FROM inventory) s, (SELECT w AS v FROM (SELECT 1 AS one) z) u", WINDOW),
    "implicit_lateral_alias_shadowed_inside": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, (SELECT list_sum(s.l || s.l) AS v FROM (SELECT 1 AS one) z WHERE EXISTS (SELECT 1 FROM (SELECT 1) s)) u", COLLECTION),
    "implicit_lateral_through_using": ("SELECT u.v AS total FROM (SELECT 1 AS k, list(quantity_kg) AS l FROM inventory) s JOIN (SELECT one AS k, list_sum(l || l) AS v FROM (SELECT 1 AS one) z) u USING (k)", COLLECTION),
    "implicit_lateral_through_natural_join": ("SELECT SUM(u.v) AS total FROM (SELECT 1 AS k, SUM(quantity_kg) OVER () AS w FROM inventory) s NATURAL JOIN (SELECT one AS k, w AS v FROM (SELECT 1 AS one) z) u", WINDOW),
    # Verifier round 7: a nested lateral keeps the outermost kind; VALUES reading
    # an outer collection is inspected.
    "nested_lateral_reads_the_outermost_source": ("SELECT u.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, (SELECT x.v FROM (SELECT 1 AS one) z, (SELECT list_sum(s.l || s.l) AS v) x) u", COLLECTION),
    "values_row_reads_an_outer_collection": ("SELECT t.v AS total FROM (SELECT list(quantity_kg) AS l FROM inventory) s, (VALUES (list_sum(s.l || s.l))) t(v)", COLLECTION),
    "pivot_list_concatenated": ("SELECT list_sum(x || x) AS total FROM (SELECT 'x' AS k, quantity_kg FROM inventory) PIVOT (list(quantity_kg) FOR k IN ('x'))", COLLECTION),
    "pivot_list_concatenated_by_alias": ("SELECT list_sum(p.x || p.x) AS total FROM (SELECT 'x' AS k, quantity_kg FROM inventory) PIVOT (list(quantity_kg) FOR k IN ('x')) p", COLLECTION),
    "self_join_through_a_schema_qualifier": ("SELECT SUM(a.quantity_kg) AS total FROM inventory a, main.inventory b", "self_join_under_aggregate"),
    "self_join_through_a_qualified_derived_table": ("SELECT SUM(a.quantity_kg) AS total FROM inventory a, (SELECT * FROM main.inventory) b", "self_join_under_aggregate"),
    "self_join_on_a_column_through_a_qualifier": ("SELECT SUM(a.quantity_kg) AS total FROM main.inventory a JOIN inventory b ON a.category = b.category", "self_join_under_aggregate"),
}


def _demo_ask(
    sql: str, door: str, space_id: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter  # noqa: F811
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    client, cortex, _app, lake = ca._app(tmp_path, monkeypatch, minter)
    rig = ca._Rig(client, cortex, space_id, lake)
    truth = ca._oracle(lake, DEMO_TRUE_SQL)
    figure = ca._oracle(lake, sql)
    if door == "contract":

        def _contract(req: Any) -> AskResponse:
            return AskResponse(
                answer=f"The total is {figure[0]['total']}.",
                abstained=False,
                badge="generated",
                route="generated",
                sql_used=sql,
                rows=figure,
            )

        monkeypatch.setattr(cortex, "ask", _contract)
        return rig.ask(DEMO_Q, {}), truth, figure
    return rig.ask(DEMO_Q, ca._gen(sql)), truth, figure


@pytest.mark.parametrize("door", ["generate", "contract"])
@pytest.mark.parametrize("space_id", [None, ca.DEMO_FINANCE])
@pytest.mark.parametrize("name", sorted(DEMO_HOLES))
def test_demo_space_shapes_abstain_on_the_guard_alone(
    name: str,
    space_id: str | None,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    """On the merged head each was served L2_VALIDATED with the multiplied figure."""
    sql, why = DEMO_HOLES[name]
    env, truth, figure = _demo_ask(sql, door, space_id, tmp_path, monkeypatch, minter)
    assert figure != truth, (figure, truth)  # the danger is real
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("rows"), env.get("text"))
    assert env["abstained"] is True and env["rows"] == [] and env["values"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    # Since JOIN-HOLE-01 (#527) a demo statement that reads demo tables in two
    # places meets rule 16 first; either named refusal is an abstain, and the
    # guard's own verdict is asserted below whichever reason the envelope shows.
    shown = od._reasons(env) + " " + text
    assert f"repeated_input:{why}" in shown or "unverified_join:" in shown, (why, env.get("assumptions"))
    wrong = figure[0]["total"]
    assert str(wrong) not in text and f"{float(wrong):g}" not in text, (wrong, text)
    assert (repeated_input_reason(sql) or "").startswith(f"repeated_input:{why}")


@pytest.mark.parametrize("door", ["generate", "contract"])
@pytest.mark.parametrize("space_id", [None, ca.DEMO_FINANCE])
def test_demo_space_true_total_still_answers(
    space_id: str | None,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    env, truth, figure = _demo_ask(DEMO_TRUE_SQL, door, space_id, tmp_path, monkeypatch, minter)
    assert figure == truth
    assert env["badge"] != "ABSTAIN" and env["abstained"] is False, env.get("text")
    assert od._multiset(env["rows"]) == od._multiset(truth), env["rows"]
    # The TABLE forms are followed, not refused, by the guard. In the demo ask
    # path another gate decides them (the grant parser reads a table named TABLE).
    for sql in (
        "WITH c AS (TABLE inventory) SELECT SUM(quantity_kg) AS total FROM c",
        "SELECT SUM(quantity_kg) AS total FROM (TABLE inventory)",
    ):
        assert repeated_input_reason(sql) is None, sql
    pair = "SELECT SUM(a.quantity_kg) AS total FROM (TABLE inventory) a, (TABLE inventory) b"
    assert (repeated_input_reason(pair) or "").startswith("repeated_input:self_join_under_aggregate")


#: Correct statements the guard refuses (verifier round): named, never silent
#: (#531 clause 4). The guard cannot tell them from the wrong ones in their class.
#: name -> (sql, the correct figure on the fixture, the rule).
GUARD_NEW_ABSTAINS: dict[str, tuple[str, Any, str]] = {
    # Verifier round 2, the price of the ARRAY rule: the same as len(list(x)).
    "length_of_an_array_of_one_column": (f"SELECT array_length(ARRAY(SELECT school_id FROM {T})) AS total", 4, COLLECTION),
    "json_array_length_of_json_group_array": (f"SELECT json_array_length(json_group_array(school_id)) AS total FROM {T}", 4, COLLECTION),
    "avg_of_a_lag_gap": (f"SELECT AVG(gap) AS total FROM (SELECT {N} - LAG({N}) OVER (ORDER BY enrollment_id) AS gap FROM {T})", -6.0, WINDOW),
    "array_length_of_split_string_agg": (f"SELECT array_length(string_split(string_agg(students, ','), ',')) AS total FROM {T}", 4, COLLECTION),
    "same_table_semi_join": (f"SELECT SUM({N}) AS total FROM {T} a SEMI JOIN {T} b ON a.enrollment_id = b.enrollment_id", 107, "self_join_under_aggregate"),
    "aggregate_over_a_pivot_statement": (f'SELECT SUM("1") AS total FROM (PIVOT {T} ON school_id USING SUM({N}))', 30, TURN),
}


@pytest.mark.parametrize("name", sorted(GUARD_NEW_ABSTAINS))
def test_guard_new_abstains_are_named(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql, figure, why = GUARD_NEW_ABSTAINS[name]
    rig = od._space(tmp_path, monkeypatch, minter)
    assert od._oracle(rig.lake, sql) == [{"total": figure}]
    assert (repeated_input_reason(sql) or "").startswith(f"repeated_input:{why}")


@pytest.mark.parametrize("door", DOORS)
def test_array_of_rollup_rows_counted_abstains_in_the_ontology_space(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Verifier round 2: rule 16 passes it, so the guard alone refuses it. Served 5 for 4."""
    sql = f"SELECT array_length(ARRAY(SELECT school_id FROM {T} GROUP BY ROLLUP(school_id))) AS total"
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, "How many schools have enrollments?")
    assert od._oracle(rig.lake, sql) == [{"total": 5}]
    assert od._oracle(rig.lake, f"SELECT COUNT(DISTINCT school_id) AS total FROM {T}") == [{"total": 4}]
    _assert_refused_by_the_rule(env, rig, sql, GROUPING, (5,))
    assert _join_why(rig, sql) is None, "only the guard stands in front of this one"


@pytest.mark.parametrize("door", DOORS)
def test_pivot_list_concatenated_abstains_in_the_ontology_space(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Verifier round 3: rule 16 passes it, so the guard alone refuses it. Served 8 for 4."""
    sql = f"SELECT array_length(x || x) AS total FROM (SELECT 'x' AS k, school_id FROM {T}) PIVOT (list(DISTINCT school_id) FOR k IN ('x'))"
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, "How many schools have enrollments?")
    assert od._oracle(rig.lake, sql) == [{"total": 8}]
    _assert_refused_by_the_rule(env, rig, sql, COLLECTION, (8,))
    assert _join_why(rig, sql) is None, "only the guard stands in front of this one"


def test_a_collection_column_listed_as_it_is_passes_the_guard() -> None:
    """Rule 4 as written: a collection may be listed as it is, also out of a PIVOT."""
    for sql in (
        f"SELECT x FROM (SELECT 'x' AS k, school_id FROM {T}) PIVOT (list(school_id) FOR k IN ('x'))",
        f"SELECT x AS ids FROM (SELECT 'x' AS k, school_id FROM {T}) PIVOT (list(school_id) FOR k IN ('x'))",
        f"SELECT l FROM (SELECT list(school_id) AS l FROM {T})",
    ):
        assert repeated_input_reason(sql) is None, sql
    for sql in (
        f"SELECT len(l) AS n FROM (SELECT list(school_id) AS l FROM {T})",
        f"SELECT l FROM (SELECT list(school_id) AS l FROM {T}) WHERE len(l) > 1",
    ):
        assert (repeated_input_reason(sql) or "").startswith(f"repeated_input:{COLLECTION}"), sql


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT array_length(#1 || #1) AS total FROM (SELECT list(DISTINCT school_id) AS l FROM {T})",
        f"SELECT array_length(COLUMNS('l') || COLUMNS('l')) AS total FROM (SELECT list(DISTINCT school_id) AS l FROM {T})",
    ],
)
def test_list_reached_by_position_or_columns_abstains_in_the_ontology_space(
    sql: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Verifier round 4: rule 16 passes both, so the guard alone refuses them. Served 8 for 4."""
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, "How many schools have enrollments?")
    assert od._oracle(rig.lake, sql) == [{"total": 8}]
    _assert_refused_by_the_rule(env, rig, sql, COLLECTION, (8,))
    assert _join_why(rig, sql) is None, "only the guard stands in front of this one"


#: Correct statements the guard refuses (verifier rounds 3 and 4), named (#531
#: clause 4): sql -> the rule.
GUARD_NEW_ABSTAINS_LISTINGS: dict[str, str] = {
    # Verifier round 7, the price of treating every later derived table as a
    # LATERAL: once the first source carries a window value, an aggregate over a
    # later derived table refuses (correct figure here: the capacity of each
    # location once).
    "SELECT SUM(l.capacity_kg) FROM (SELECT location_id, ROW_NUMBER() OVER (PARTITION BY location_id ORDER BY sku) AS rn FROM inventory) i JOIN (SELECT location_id, capacity_kg FROM locations) l USING (location_id) WHERE i.rn = 1": WINDOW,
    # A function over a collection: the same price as len(list(x)).
    f"SELECT school_id, list_sort(list(enrollment_id)) AS ids FROM {T} GROUP BY school_id": COLLECTION,
    # The table key is the name alone, so one table read twice through a third
    # is a self-join even when the join keys make it one row each.
    f"SELECT a.enrollment_id, SUM(CAST(b.students AS INTEGER)) AS n FROM {T} a JOIN {od.SCHOOLS} s ON s.school_id = a.school_id JOIN {T} b ON b.enrollment_id = a.enrollment_id GROUP BY a.enrollment_id": "self_join_under_aggregate",
}


def test_guard_new_abstains_in_listings_are_named() -> None:
    for sql, why in GUARD_NEW_ABSTAINS_LISTINGS.items():
        assert (repeated_input_reason(sql) or "").startswith(f"repeated_input:{why}"), sql
    # Listed as they are, a positional or COLUMNS collection passes.
    for sql in (
        f"SELECT #1 FROM (SELECT list(school_id) AS l FROM {T})",
        f"SELECT COLUMNS('l') FROM (SELECT list(school_id) AS l FROM {T})",
    ):
        assert repeated_input_reason(sql) is None, sql


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT COUNT(*) AS total FROM {od.SCHOOLS} USING SAMPLE 2 ROWS",
        f"SELECT COUNT(*) AS total FROM (SELECT * FROM {od.SCHOOLS} USING SAMPLE 2 ROWS)",
        f"SELECT COUNT(*) AS total FROM {od.SCHOOLS} TABLESAMPLE 2 ROWS",
    ],
)
def test_an_aggregate_over_a_sample_abstains(
    sql: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Routed by the parent orchestrator (P0 in this class): a one-table total that is
    not the table's total. Served 2 for a true 4 through every door on the parent."""
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, "How many schools are there?")
    assert od._oracle(rig.lake, sql) == [{"total": 2}]
    assert od._oracle(rig.lake, f"SELECT COUNT(*) AS total FROM {od.SCHOOLS}") == [{"total": 4}]
    _assert_refused_by_the_rule(env, rig, sql, "sample_under_aggregate", (2,))
    assert _join_why(rig, sql) is None, "only the guard stands in front of this one"
    # Sampled rows listed as they are pass the guard.
    assert repeated_input_reason(f"SELECT * FROM {od.SCHOOLS} USING SAMPLE 2 ROWS") is None
