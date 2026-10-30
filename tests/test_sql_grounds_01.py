"""ONE-PATH-CHECK-01: structural sql_grounds. No question-word lists."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from dms_executor.ontology import NO_SILENT_PAD, Coverage
from dms_executor.sql_grounds import CHECKER_VERSION, sql_grounds

_SUM = "SELECT SUM(amount) AS total FROM sales"


def _one(sql: str):
    grounds = sql_grounds(sql)
    assert grounds.unclear is False
    assert len(grounds.conjuncts) == 1
    return grounds.conjuncts[0]


def test_or_hidden_filter_drops_the_or_and_keeps_the_and() -> None:
    grounds = sql_grounds(
        f"{_SUM} WHERE status = 'open' AND (region = 'KL' OR region = 'JB')"
    )
    assert grounds.unclear is False
    assert len(grounds.conjuncts) == 1
    kept = grounds.conjuncts[0]
    assert (kept.column, kept.operator, kept.literals, kept.polarity) == (
        "status",
        "eq",
        ("open",),
        "pos",
    )
    assert grounds.measures == ("SUM(amount)",)


def test_exists_drops_the_conjunct() -> None:
    grounds = sql_grounds(
        f"{_SUM} WHERE EXISTS (SELECT 1 FROM other AS o WHERE o.id = sales.id)"
    )
    assert grounds.unclear is False
    assert grounds.conjuncts == ()


@pytest.mark.parametrize(
    "sql",
    [
        f"{_SUM} WHERE 'X' = 'X'",
        f"{_SUM} WHERE 1 = 1",
        f"{_SUM} WHERE TRUE",
    ],
)
def test_constant_tautology_drops_the_conjunct(sql: str) -> None:
    grounds = sql_grounds(sql)
    assert grounds.unclear is False
    assert grounds.conjuncts == ()


@pytest.mark.parametrize(
    "sql",
    [
        f"{_SUM} WHERE 'X' = 'Y'",
        f"{_SUM} WHERE 1 <> 2",
    ],
)
def test_unequal_constants_drop_the_conjunct(sql: str) -> None:
    grounds = sql_grounds(sql)
    assert grounds.unclear is False
    assert grounds.conjuncts == ()


@pytest.mark.parametrize(
    ("sql", "column", "literal"),
    [
        (f"{_SUM} WHERE 1 = 1 AND status = 'open'", "status", "open"),
        (f"{_SUM} WHERE 2 > 1 AND status = 'open'", "status", "open"),
        (f"{_SUM} WHERE 'a' <> 'b' AND region = 'KL'", "region", "KL"),
    ],
)
def test_constant_comparison_drops_and_keeps_the_sibling(
    sql: str, column: str, literal: str
) -> None:
    kept = _one(sql)
    assert (kept.column, kept.operator, kept.literals, kept.polarity) == (
        column,
        "eq",
        (literal,),
        "pos",
    )


def test_self_compare_drops_the_conjunct() -> None:
    grounds = sql_grounds(f"{_SUM} WHERE category = category")
    assert grounds.unclear is False
    assert grounds.conjuncts == ()


def test_select_only_category_is_not_a_conjunct() -> None:
    grounds = sql_grounds(
        "SELECT category, SUM(amount) AS total FROM sales WHERE region = 'KL'"
    )
    assert grounds.unclear is False
    assert [item.column for item in grounds.conjuncts] == ["region"]
    assert "category" not in {item.column for item in grounds.conjuncts}
    assert grounds.measures == ("SUM(amount)",)


def test_group_by_only_is_not_a_conjunct() -> None:
    grounds = sql_grounds("SELECT SUM(amount) AS total FROM sales GROUP BY category")
    assert grounds.unclear is False
    assert grounds.conjuncts == ()
    assert grounds.measures == ("SUM(amount)",)


def test_order_by_and_alias_are_not_conjuncts() -> None:
    grounds = sql_grounds(
        "SELECT category AS cat, SUM(amount) AS total FROM sales "
        "GROUP BY category ORDER BY cat"
    )
    assert grounds.unclear is False
    assert grounds.conjuncts == ()
    assert grounds.order_by == (("cat", "asc"),)


def test_nested_not_drops_the_conjunct() -> None:
    grounds = sql_grounds(f"{_SUM} WHERE NOT (NOT (category = 'A'))")
    assert grounds.unclear is False
    assert grounds.conjuncts == ()


def test_not_like_gives_no_conjunct() -> None:
    grounds = sql_grounds(
        f"{_SUM} WHERE category NOT LIKE '%a%' AND status = 'open'"
    )
    assert grounds.unclear is False
    assert len(grounds.conjuncts) == 1
    kept = grounds.conjuncts[0]
    assert kept.operator == "eq" and kept.column == "status"


def test_neq_and_not_in_are_negative() -> None:
    grounds = sql_grounds(
        f"{_SUM} WHERE name LIKE 'A%' AND sku IN ('B', 'C') "
        "AND category <> 'D' AND bin NOT IN ('E')"
    )
    assert grounds.unclear is False
    got = [(c.column, c.operator, c.literals, c.polarity) for c in grounds.conjuncts]
    assert got == [
        ("name", "like", ("A%",), "pos"),
        ("sku", "in", ("B", "C"), "pos"),
        ("category", "neq", ("D",), "neg"),
        ("bin", "not_in", ("E",), "neg"),
    ]


def test_join_on_conjunct() -> None:
    grounds = sql_grounds(
        "SELECT SUM(s.amount) AS total FROM sales AS s "
        "JOIN regions AS r ON s.region_id = r.id AND r.name = 'KL'"
    )
    assert grounds.unclear is False
    named = [c for c in grounds.conjuncts if c.literals == ("KL",)]
    assert len(named) == 1
    assert (named[0].column, named[0].operator, named[0].polarity) == (
        "r.name",
        "eq",
        "pos",
    )


def test_having_conjunct() -> None:
    got = _one(
        "SELECT category, SUM(amount) AS total FROM sales "
        "GROUP BY category HAVING SUM(amount) > 10"
    )
    assert (got.column, got.operator, got.literals, got.polarity) == (
        "amount",
        "gt",
        ("10",),
        "pos",
    )


def test_limit_offset_and_order_direction() -> None:
    grounds = sql_grounds(
        "SELECT sku, SUM(v) AS revenue FROM t GROUP BY sku "
        "ORDER BY revenue DESC LIMIT 5 OFFSET 3"
    )
    assert grounds.unclear is False
    assert grounds.conjuncts == ()
    assert grounds.measures == ("SUM(v)",)
    assert grounds.order_by == (("revenue", "desc"),)
    assert grounds.limit == 5
    assert grounds.offset == 3
    body = grounds.as_dict()
    assert body["checker_version"] == CHECKER_VERSION
    assert body["order_by"] == [{"expression": "revenue", "direction": "desc"}]
    assert body["limit"] == 5 and body["offset"] == 3


def test_unparseable_sql_is_unclear() -> None:
    grounds = sql_grounds("SELECT FROM WHERE")
    assert grounds.unclear is True
    assert grounds.conjuncts == ()
    assert grounds.measures == ()
    assert grounds.limit is None and grounds.offset is None


def test_union_root_is_unclear() -> None:
    grounds = sql_grounds("SELECT 1 AS n UNION ALL SELECT 2 AS n")
    assert grounds.unclear is True
    assert grounds.conjuncts == ()


def test_shadow_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.sql_grounds as mod

    def boom(_sql: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "sql_grounds", boom)
    got = mod.served_check_shadow("SELECT 1")
    assert got["checker_version"] == CHECKER_VERSION
    assert got["error"] == "RuntimeError: boom"


def test_l2_serve_keeps_rows_when_the_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.generative_ask as ga

    def boom(_sql: str):
        raise RuntimeError("nope")

    monkeypatch.setattr("dms_executor.sql_grounds.served_check_shadow", boom)
    sql = "SELECT SUM(n) AS n FROM t WHERE category = 'A'"
    env = ga._l2_envelope(
        sql=sql,
        result=SimpleNamespace(ok=True, output={"rows": [{"n": 1}]}),
        question="how many",
        space_id=None,
        session_id=None,
        audit_id="aud-shadow",
        notes=(),
        plan_origin="generate_sql",
        coverage=Coverage(
            include=("rows",),
            exclude=(NO_SILENT_PAD,),
            unsure=(),
        ),
    )
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["route"] == "generated"
    assert env["rows"] == [{"n": 1}]
    assert env["plan_origin"] == "generate_sql"
    assert env["sql_used"] == sql
    shadow = env["served_check_shadow"]
    assert shadow["error"] == "RuntimeError: nope"
    assert "served_attribution" not in env
