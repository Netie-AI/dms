"""STATED-VALUE-01: a value the question states must restrict the SQL, or the ask abstains.

The demo-vocabulary qualifier guard (``cortex_client.qualifiers``) only knows quoted strings
and the demo's warehouse names. On a customer table, "total budget of schools in North" with
model SQL that drops the North filter executed and answered the unfiltered total at L2.
The check is data-backed and structural: a phrase of the question that is a stored value of
a customer text column is honoured only by a top-level ``col = 'v'`` / ``col IN (...)``
conjunct on that column in every block that reads the table. A literal, alias or label that
merely appears in the SQL is not a filter (the adversary round's P0s).

Customer-facing assertions: badge, abstained, rendered text and returned rows against an
independent oracle query on the lake.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.bronze import write_bronze_rows
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.stated_values import dropped_stated_value_reason

SCHOOLS = "bronze.schools"
COLS = ["school_id", "name", "region", "budget", "year"]
ROWS = [
    ["1", "Alder", "North", "100.50", "2023"],
    ["2", "Birch", "North", "50.25", "2024"],
    ["3", "Cedar", "South", "210.00", "2024"],
    ["4", "Dogwood", "North East", "1000.00", "2024"],
    ["5", "Elm", "St. Mary's", "7.00", "2023"],
]
UNFILTERED = "SELECT SUM(CAST(budget AS DOUBLE)) AS total_budget FROM bronze.schools"
FILTERED = UNFILTERED + " WHERE region = 'North'"
IN_NORTH = "What is the total budget of schools in North?"
Tables = dict[str, tuple[list[str], list[list[str]]]]


def _submit(db: Path):
    def submit(sql: str) -> Any:
        con = duckdb.connect(str(db), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_stated", output={"rows": rows})

    return submit


def _ledger(_p: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_stated", hash="hash_stated_not_entry")


def _oracle(db: Path, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _lake(tmp_path: Path, rows: list[list[str]] | None = None, extra: Tables | None = None) -> Path:
    db = tmp_path / "stated.duckdb"
    ensure_demo_warehouse(db)
    write_bronze_rows(table=SCHOOLS, columns=COLS, rows=rows or ROWS, path=db)
    for table, (cols, data) in (extra or {}).items():
        write_bronze_rows(table=table, columns=cols, rows=data, path=db)
    return db


def _ask(
    tmp_path: Path,
    question: str,
    sql: str,
    *,
    rows: list[list[str]] | None = None,
    extra: Tables | None = None,
) -> dict[str, Any]:
    begin_answer_model_calls()
    db = _lake(tmp_path, rows, extra)
    onto = load_verified_ontology(db, demo_ontology(db))
    grant = {SCHOOLS, "schools", *(extra or {}), *(t.split(".")[-1] for t in extra or {})}
    env = maybe_generative_ask(
        question,
        warehouse=db,
        grantable=grant,
        compute=lambda _ctx: {"query_sql": sql, "plan_source": "ontology_plan"},
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


def _abstains(env: dict[str, Any], named: str | None = None) -> None:
    assert env["abstained"] is True, (env["badge"], env.get("text"), env.get("rows"))
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == [] and env["values"] == []
    if named is not None:
        assert f"named_filter={named}" in str(env.get("text") or ""), env.get("text")


def test_sql_that_drops_a_stated_value_abstains(tmp_path: Path) -> None:
    env = _ask(tmp_path, IN_NORTH, UNFILTERED)
    _abstains(env, "North")


def test_sql_that_carries_the_stated_value_answers_with_oracle_rows(tmp_path: Path) -> None:
    env = _ask(tmp_path, IN_NORTH, FILTERED)
    assert env["abstained"] is False, (env["badge"], env.get("text"))
    (oracle,) = _oracle(tmp_path / "stated.duckdb", FILTERED)
    assert oracle[0] == pytest.approx(150.75)
    assert [r["total_budget"] for r in env["rows"]] == [pytest.approx(150.75)]
    assert "150.75" in str(env.get("text") or "")


@pytest.mark.parametrize(
    "sql",
    [
        UNFILTERED + " WHERE lower(region) = 'north'",
        "SELECT SUM(CAST(budget AS DOUBLE)) AS total_budget "
        "FROM (SELECT * FROM bronze.schools WHERE region = 'North') n",
    ],
    ids=["lower_wrapped", "derived_table"],
)
def test_other_honest_filter_shapes_answer(tmp_path: Path, sql: str) -> None:
    env = _ask(tmp_path, IN_NORTH, sql)
    assert env["abstained"] is False, (env["badge"], env.get("text"))
    assert [r["total_budget"] for r in env["rows"]] == [pytest.approx(150.75)]


def test_a_cte_that_filters_honours_the_value(tmp_path: Path) -> None:
    """The SQL validator refuses CTE names today (``ungranted:n``); this check does not."""
    sql = (
        "WITH n AS (SELECT * FROM bronze.schools WHERE region = 'North') "
        "SELECT SUM(CAST(budget AS DOUBLE)) AS total_budget FROM n"
    )
    assert dropped_stated_value_reason(IN_NORTH, sql, _lake(tmp_path)) is None


def test_a_value_every_row_holds_needs_no_filter(tmp_path: Path) -> None:
    rows = [["1", "Alder", "North", "100", "2023"], ["2", "Birch", "North", "50", "2024"]]
    env = _ask(tmp_path, IN_NORTH, UNFILTERED, rows=rows)
    assert env["abstained"] is False, (env["badge"], env.get("text"))
    assert [r["total_budget"] for r in env["rows"]] == [pytest.approx(150.0)]


def test_a_value_every_row_of_an_unread_table_holds_still_abstains(tmp_path: Path) -> None:
    """Districts lists only Kepong, but schools 2 and 3 have no district row at all."""
    extra: Tables = {"bronze.districts": (["school_id", "district"], [["1", "Kepong"]])}
    q = "What is the total budget of schools in Kepong?"
    _abstains(_ask(tmp_path, q, UNFILTERED, extra=extra), "Kepong")


def test_a_capitalised_word_that_is_not_a_stored_value_is_not_a_condition(tmp_path: Path) -> None:
    env = _ask(tmp_path, "What is the total budget of Schools?", UNFILTERED)
    assert env["abstained"] is False, (env["badge"], env.get("text"))
    assert [r["total_budget"] for r in env["rows"]] == [pytest.approx(1367.75)]


def test_lowercase_words_the_sql_names_are_not_conditions(tmp_path: Path) -> None:
    """ "budget" and "schools" are a column and a table the SQL reads, not dropped values."""
    env = _ask(tmp_path, "what is the total budget of all schools", UNFILTERED)
    assert env["abstained"] is False, (env["badge"], env.get("text"))
    assert [r["total_budget"] for r in env["rows"]] == [pytest.approx(1367.75)]


@pytest.mark.parametrize("stated", ["NORTH", "North District", "north", "the north"])
def test_case_variants_of_a_stored_value_are_still_conditions(tmp_path: Path, stated: str) -> None:
    _abstains(_ask(tmp_path, f"What is the total budget of schools in {stated}?", UNFILTERED))


#: A literal or name that appears in the SQL without restricting the figure (adversary P0-1).
NOT_A_FILTER = {
    "select_label": "SELECT 'North' AS region, SUM(CAST(budget AS DOUBLE)) AS total_budget "
    "FROM bronze.schools",
    "or_tautology": FILTERED + " OR 1=1",
    "negated": UNFILTERED + " WHERE region <> 'North'",
    "case_label": "SELECT SUM(CAST(budget AS DOUBLE)) AS total_budget, "
    "MAX(CASE WHEN 1=1 THEN 'North' END) AS r FROM bronze.schools",
    "exists_subquery": UNFILTERED
    + " WHERE EXISTS (SELECT 1 FROM bronze.schools s2 WHERE s2.region = 'North')",
    "like_wider": UNFILTERED + " WHERE region LIKE 'North%'",
    "other_column": UNFILTERED + " WHERE name <> 'North'",
    "alias_ident": "SELECT SUM(CAST(budget AS DOUBLE)) AS North FROM bronze.schools",
    "comment_only": UNFILTERED + " -- region = 'North'",
    "wrong_case_matches_nothing": UNFILTERED + " WHERE region = 'north'",
    "in_widened": UNFILTERED + " WHERE region IN ('North', 'South')",
    "scalar_subquery": "SELECT (SELECT SUM(CAST(budget AS DOUBLE)) FROM bronze.schools) "
    "AS total_budget FROM bronze.schools WHERE region = 'North' LIMIT 1",
    "cross_joined_filter": "SELECT SUM(CAST(s.budget AS DOUBLE)) AS total_budget "
    "FROM bronze.schools s, (SELECT 1 AS k FROM bronze.schools WHERE region = 'North') x",
    "union_branch": "SELECT SUM(CAST(budget AS DOUBLE)) AS total_budget FROM ("
    "SELECT budget FROM bronze.schools WHERE region = 'North' "
    "UNION ALL SELECT budget FROM bronze.schools)",
}


@pytest.mark.parametrize("sql", list(NOT_A_FILTER.values()), ids=list(NOT_A_FILTER))
def test_a_stated_value_that_does_not_restrict_the_figure_abstains(
    tmp_path: Path, sql: str
) -> None:
    _abstains(_ask(tmp_path, IN_NORTH, sql))


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        ("North schools: what is the total budget?", UNFILTERED),
        ("what is the total budget of schools in north east", UNFILTERED),
        ("What is the total budget of North East Schools?", UNFILTERED),
        ("What is the total budget of schools in North-East?", UNFILTERED),
        ("What is the total budget of schools in North East?", FILTERED),
        ("What is the total budget of St. Mary's?", UNFILTERED),
        ("What is the total budget of schools in 2024?", UNFILTERED),
    ],
    ids=["sentence_start", "lower_multiword", "inside_run", "hyphen", "partial", "period", "year"],
)
def test_stated_values_in_any_spelling_are_conditions(
    tmp_path: Path, question: str, sql: str
) -> None:
    """Adversary P0-2, P1-1..P1-3: first word, multi-word, partial literal, punctuation."""
    _abstains(_ask(tmp_path, question, sql))


def test_north_east_filtered_exactly_answers(tmp_path: Path) -> None:
    sql = UNFILTERED + " WHERE region = 'North East'"
    env = _ask(tmp_path, "What is the total budget of schools in North East?", sql)
    assert env["abstained"] is False, (env["badge"], env.get("text"))
    assert [r["total_budget"] for r in env["rows"]] == [pytest.approx(1000.0)]


def test_accented_stored_value_is_a_condition(tmp_path: Path) -> None:
    rows = [["1", "Alder", "Montréal", "100", "2023"], ["2", "Birch", "Laval", "50", "2024"]]
    env = _ask(tmp_path, "What is the total budget of schools in Montreal?", UNFILTERED, rows=rows)
    _abstains(env, "Montréal")


def test_two_letter_code_is_a_condition(tmp_path: Path) -> None:
    rows = [["1", "Alder", "KL", "100", "2023"], ["2", "Birch", "PG", "50", "2024"]]
    _abstains(_ask(tmp_path, "what is the total budget of schools in kl", UNFILTERED, rows=rows))


def test_value_in_a_granted_table_the_sql_does_not_read_abstains(tmp_path: Path) -> None:
    """Adversary P1-5: "Kepong" lives in districts; summing all schools ignores it."""
    extra: Tables = {
        "bronze.districts": (
            ["school_id", "district"],
            [["1", "Kepong"], ["2", "Cheras"], ["3", "Cheras"]],
        )
    }
    q = "What is the total budget of schools in Kepong?"
    _abstains(_ask(tmp_path, q, UNFILTERED, extra=extra), "Kepong")


def test_a_wide_table_is_still_checked(tmp_path: Path) -> None:
    """Adversary P1-4: a column cap that silently passed. 63 text columns are all probed."""
    cols = [f"c{i:02d}" for i in range(61)] + ["zone", "amt"]
    extra: Tables = {
        "bronze.wide": (cols, [["x"] * 61 + ["Upper", "1"], ["x"] * 61 + ["Lower", "2"]])
    }
    sql = "SELECT SUM(CAST(amt AS DOUBLE)) AS amt_total FROM bronze.wide"
    _abstains(_ask(tmp_path, "What is the total amt in Upper?", sql, extra=extra), "Upper")


def test_a_long_question_abstains_unchecked_instead_of_passing(tmp_path: Path) -> None:
    q = " ".join(["region"] * 70) + " North"
    db = _lake(tmp_path)
    assert dropped_stated_value_reason(q, UNFILTERED, db) == (
        "unhonored_qualifier:named_filter=unchecked"
    )


@pytest.mark.parametrize(
    "rel",
    ["bronze_schools", "main.bronze_schools", "stated.main.bronze_schools", '"BRONZE"."SCHOOLS"'],
)
def test_every_spelling_of_a_customer_relation_is_probed(tmp_path: Path, rel: str) -> None:
    db = _lake(tmp_path)
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE main.bronze_schools AS SELECT * FROM bronze.schools")
    con.close()
    sql = f"SELECT SUM(CAST(budget AS DOUBLE)) FROM {rel}"
    assert _oracle(db, sql) == [(pytest.approx(1367.75),)]
    assert dropped_stated_value_reason(IN_NORTH, sql, db) is not None, rel


def test_a_non_demo_view_the_sql_reads_is_probed(tmp_path: Path) -> None:
    db = _lake(tmp_path)
    con = duckdb.connect(str(db))
    con.execute("CREATE VIEW main.schools AS SELECT * FROM bronze.schools")
    con.close()
    sql = "SELECT SUM(CAST(budget AS DOUBLE)) FROM schools"
    assert dropped_stated_value_reason(IN_NORTH, sql, db, grantable={"schools"}) is not None


def test_in_listing_only_stated_values_honours_each(tmp_path: Path) -> None:
    db = _lake(tmp_path)
    sql = (
        "SELECT region, SUM(CAST(budget AS DOUBLE)) AS b FROM bronze.schools "
        "WHERE region IN ('North', 'South') GROUP BY region"
    )
    assert dropped_stated_value_reason("Budget of North and South?", sql, db) is None


def test_top_n_matches_the_limit_not_a_stored_id(tmp_path: Path) -> None:
    db = _lake(tmp_path)
    sql = (
        "SELECT name, CAST(budget AS DOUBLE) AS b FROM bronze.schools "
        "WHERE region = 'North' ORDER BY b DESC LIMIT 2"
    )
    assert dropped_stated_value_reason("top 2 schools in North by budget", sql, db) is None
    assert dropped_stated_value_reason("budget of school 2 in North", sql, db) is not None


def test_the_probe_binds_the_question(tmp_path: Path) -> None:
    db = _lake(tmp_path)
    reason = dropped_stated_value_reason("What is the budget in X' OR 'a'='a?", UNFILTERED, db)
    assert reason is None
    assert _oracle(db, "SELECT count(*) FROM bronze.schools") == [(5,)]


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        (
            "Which region has the highest budget, North or South?",
            "SELECT region, SUM(CAST(budget AS DOUBLE)) AS b FROM bronze.schools "
            "GROUP BY region ORDER BY b DESC LIMIT 1",
        ),
        ("Is Alder in North?", "SELECT name, region FROM bronze.schools WHERE name = 'Alder'"),
        ("What is the budget of Alder?", UNFILTERED + " WHERE school_id = '1'"),
    ],
    ids=["group_by_ranks_unstated_groups", "projection_lists_value", "filter_by_other_key"],
)
def test_known_safe_refusals(tmp_path: Path, question: str, sql: str) -> None:
    """Refused on purpose. The first one would answer "North East" (1000.00), which is
    neither of the two values asked about. The other two do not restrict the stated column."""
    db = _lake(tmp_path)
    assert dropped_stated_value_reason(question, sql, db) is not None
