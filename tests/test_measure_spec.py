"""Pure measure spec: hostile-alphabet property test, R1..R8 by code, SQLISH vectors.

sqlglot is used HERE (the test), never in measure_spec.py.
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest
import sqlglot
from dms_core.control_plane.onto_store import measure_definition_hash
from dms_executor import measure_basis, measure_spec
from dms_executor.measure_spec import (
    MeasureSpecDraft,
    definition_hash,
    definition_text,
    description_failures,
    is_numeric_type,
    is_temporal_type,
    validate_measure_spec,
)
from dms_executor.ontology import _ident, measure_expression
from sqlglot import exp

ALPHABET = [
    '"', "'", ";", "--", "/*", "*/", "\n", "\r", "\t", " ", "(", ")", ",", ".", "*", "\\",
    "ALTER", "ATTACH", "COPY", "read_csv", "DROP", "SELECT", "FROM", "UNION", "é", "ß", "名",
    "‮", "\x00", "f.", "a", "b", "1", "_",
]  # fmt: skip


def _word(rng: random.Random) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 8)))


def test_hostile_alphabet_expression_is_one_select_one_agg_one_column() -> None:
    rng = random.Random(283)
    aggs = ["sum", "avg", "min", "max", "count", "count_distinct", "median", "x; y", "", "SUM"]
    raised = built = 0
    for _ in range(3000):
        agg = rng.choice(aggs)
        col = rng.choice(["*", _word(rng), _word(rng)])
        try:
            sql = measure_expression(agg, col)
        except ValueError:
            raised += 1
            continue
        built += 1
        # quote parity: the column appears only as ontology._ident(col)
        if col != "*":
            assert f"f.{_ident(col)}" in sql
        stmts = sqlglot.parse(f"SELECT {sql} AS m FROM t f", read="duckdb")
        assert len(stmts) == 1, sql
        sel = stmts[0]
        assert isinstance(sel, exp.Select), sql
        assert len(sel.expressions) == 1
        assert len(list(sel.find_all(exp.AggFunc))) == 1, sql
        assert [t.name for t in sel.find_all(exp.Table)] == ["t"], sql
        assert not list(sel.find_all(exp.Subquery)), sql
        assert not list(sel.find_all(exp.TableFromRows, exp.Anonymous)), sql
        cols = list(sel.find_all(exp.Column))
        if col == "*":
            assert not cols and sel.find(exp.Star) is not None
        else:
            assert len(cols) == 1 and cols[0].table == "f", sql
            assert cols[0].name == col, (sql, col)
        # a quoted '--' or '/*' is identifier data, never a comment
        assert not any(n.comments for n in sel.walk()), sql
    assert raised > 0 and built > 1000


@pytest.mark.parametrize("agg", ["sum", "avg", "min", "max", "count", "count_distinct"])
def test_quote_parity_with_ident(agg: str) -> None:
    for col in ["amount", 'we"ird', "a b", "O'Brien", 'x";y']:
        sql = measure_expression(agg, col)
        assert sql.endswith(f"f.{_ident(col)})")


def test_measure_spec_has_no_sqlglot_import() -> None:
    tree = ast.parse(Path(measure_spec.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any("sqlglot" in n for n in names)


def test_measure_basis_has_no_imports() -> None:
    tree = ast.parse(Path(measure_basis.__file__).read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_ontology_does_not_import_measure_spec() -> None:
    import dms_executor.ontology as onto

    src = Path(onto.__file__).read_text()
    assert "measure_spec" not in src


SQLISH_HITS = [
    "select a from b",
    "SELECT * FROM t WHERE x=1",
    "insert into t values (1)",
    "delete from t",
    "drop table x",
    "DROP SCHEMA s",
    "update t set a=1",
    "attach 'x.db'",
    "attach database 'x'",
    "copy t to 'f'",
    "create or replace view v",
    "create table x",
    "pragma threads",
    "read_csv('x')",
    "read_parquet ('x')",
    "select\n1\nfrom\nt",
]
SQLISH_MISSES = [
    "lots below reorder level; one per lot".replace(";", ","),
    "Budget of each school",
    "one row per school; amount in MYR".replace(";", ","),
    "update the figures",
    "selection from the list",
]


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\u0085", "\x9f", "\n", "\t"])
def test_description_rejects_unicode_line_separators_and_controls(sep: str) -> None:
    assert description_failures(f"Total amount{sep}per sale")


@pytest.mark.parametrize("text", SQLISH_HITS)
def test_sqlish_vectors_hit(text: str) -> None:
    assert measure_spec.SQLISH_COPY.search(text)
    assert description_failures(text)


@pytest.mark.parametrize("text", SQLISH_MISSES)
def test_sqlish_vectors_miss(text: str) -> None:
    assert not measure_spec.SQLISH_COPY.search(text)
    assert not description_failures(text)


# -- R1..R8 -------------------------------------------------------------------

BODY = {
    "objects": {
        "bronze.public_schools": {
            "key": ["school_id"],
            "attributes": [
                {"name": "school_id", "type": "VARCHAR"},
                {"name": "district_id", "type": "BIGINT"},
                {"name": "budget", "type": "DECIMAL(18,2)"},
                {"name": "rating", "type": "DOUBLE"},
                {"name": "opened", "type": "DATE"},
                {"name": "name", "type": "VARCHAR"},
            ],
        },
        "bronze.districts": {"key": ["district_id"], "attributes": [
            {"name": "district_id", "type": "BIGINT"}, {"name": "region", "type": "VARCHAR"}]},
    },
    "links": {
        "school_district": {
            "from": "bronze.public_schools",
            "from_columns": ["district_id"],
            "to": "bronze.districts",
            "to_columns": ["district_id"],
        }
    },
}  # fmt: skip


def draft(**kw: str) -> MeasureSpecDraft:
    base = {
        "name": "school_budget_sum",
        "grain": "bronze.public_schools",
        "aggregate": "sum",
        "column": "budget",
        "description": "Budget allocated to one school",
    }
    base.update(kw)
    return MeasureSpecDraft(**base)


def codes(d: MeasureSpecDraft, **kw) -> list[str]:  # noqa: ANN003
    return [c for c, _ in validate_measure_spec(d, BODY, kw.pop("violations", ()), **kw)]


def test_valid_draft_passes() -> None:
    assert codes(draft()) == []
    assert codes(draft(aggregate="count", column="*", name="school_count")) == []
    assert codes(draft(aggregate="max", column="opened", name="school_latest_open")) == []


def test_r1_aggregate() -> None:
    assert "aggregate_invalid" in codes(draft(aggregate="median"))
    assert "aggregate_invalid" in codes(draft(aggregate="SUM"))


def test_r2_grain() -> None:
    assert "unknown_object" in codes(draft(grain="nope"))
    v = [{"check": "key_unique", "subject": "bronze.public_schools", "detail": "x"}]
    assert "grain_unverified" in codes(draft(), violations=v)


def test_r3_column() -> None:
    assert "column_missing" in codes(draft(column="BUDGET"))  # case-sensitive
    assert "column_missing" in codes(draft(column="_src"))
    assert "column_missing" in codes(draft(column="_ingest_id", aggregate="count"))
    assert "column_missing" in codes(draft(column="*", aggregate="sum"))
    assert "column_missing" in codes(draft(column="budget; DROP"))


def test_r4_types() -> None:
    assert "column_not_numeric" in codes(draft(column="name"))
    assert "column_not_numeric" in codes(draft(column="name", aggregate="avg"))
    assert "column_not_numeric" in codes(draft(column="opened"))
    assert "column_type_unsupported" in codes(draft(column="name", aggregate="min"))
    assert "column_type_unsupported" in codes(draft(column="rating", aggregate="count_distinct"))
    assert codes(draft(column="name", aggregate="count_distinct", name="school_names")) == []
    assert codes(draft(column="name", aggregate="count", name="school_names")) == []
    assert codes(draft(column="budget", aggregate="count_distinct", name="school_budgets")) == []


def test_r5_key_columns() -> None:
    assert "key_column_aggregate" in codes(draft(column="district_id", aggregate="max"))
    assert "key_column_aggregate" in codes(draft(column="school_id", aggregate="min"))
    assert codes(draft(column="school_id", aggregate="count_distinct", name="school_ids")) == []


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Budget", "name_invalid"),
        ("1abc", "name_invalid"),
        ("a", "name_invalid"),
        ("a" * 49, "name_invalid"),
        ("a b", "name_invalid"),
        ("x;y", "name_invalid"),
        ("amt_total\n", "name_invalid"),
        ("ab\n", "name_invalid"),
        ("budget", "name_reserved"),  # a column
        ("district_id", "name_reserved"),
        ("public_schools", "name_reserved"),  # object tail
        ("districts", "name_reserved"),
        ("stock_value_myr", "name_reserved"),  # demo measure
        ("total_spend", "name_reserved"),  # demo alias
        ("TOTAL_SPEND".lower(), "name_reserved"),
    ],
)
def test_r6_names(name: str, code: str) -> None:
    assert code in codes(draft(name=name))


def test_r6_taken() -> None:
    assert "name_taken" in codes(draft(), taken_names=["school_budget_sum"])
    assert "name_taken" not in codes(draft(), taken_names=["other_one"])


@pytest.mark.parametrize(
    "desc",
    ["", "  ", "x" * 201, "a\nb", "a\tb", "a`b", "a{b", "a}b", "a<b", "a>b", "a$b", "a;b",
     "a--b", "a/*b", "select x from y", "a\x00b"],
)  # fmt: skip
def test_r7_description(desc: str) -> None:
    assert "description_invalid" in codes(draft(description=desc))


def test_r7_boundary_ok() -> None:
    assert codes(draft(description="x" * 200)) == []


def test_r8_caps() -> None:
    assert "measure_limit" in codes(draft(), confirming=True, confirmed_count=80)
    assert "measure_limit" not in codes(draft(), confirming=True, confirmed_count=79)
    assert "measure_limit" in codes(draft(), proposed_count=60)
    assert "measure_limit" not in codes(draft(), proposed_count=59)


def test_all_failures_returned_together() -> None:
    d = draft(aggregate="sum", column="name", name="Bad Name", description="")
    got = set(codes(d))
    assert {"column_not_numeric", "name_invalid", "description_invalid"} <= got


def test_type_classes() -> None:
    for t in ["TINYINT", "BIGINT", "HUGEINT", "UBIGINT", "DECIMAL(18,3)", "DOUBLE", "REAL", "INT"]:
        assert is_numeric_type(t)
    for t in ["VARCHAR", "DATE", "BOOLEAN", "", "TIMESTAMP"]:
        assert not is_numeric_type(t)
    for t in ["DATE", "TIMESTAMP", "TIME", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_NS"]:
        assert is_temporal_type(t)
    assert not is_temporal_type("VARCHAR")


def test_hash_glue_and_text() -> None:
    d = draft()
    h = definition_hash(d)
    assert h == measure_definition_hash(d.name, d.grain, d.aggregate, d.column, d.description)
    assert len(h) == 64
    assert definition_hash(draft(description="other")) != h
    assert definition_text("sum", "budget", "bronze.public_schools") == (
        "SUM of budget per bronze.public_schools row"
    )
    assert definition_text("count", "*", "g") == "Count of g rows"
