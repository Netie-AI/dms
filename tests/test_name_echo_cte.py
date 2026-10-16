"""CTE references are not relations: the scrub must not eat them.

A name that a WITH clause defines is a local alias, never a table that needs
a grant. A real ungranted table behind a CTE is still a relation, still
scrubbed, never named. Synthetic SQL over the demo tables only.
"""

from __future__ import annotations

import pytest
from dms_executor.name_echo import _sql_grant_names, _sql_parts, _tables_in_sql, hide_echo

from tests.test_name_echo_01 import _name_hits, _post_ask

_GRANTED_CTE = "WITH t AS (SELECT COUNT(*) AS n FROM locations) SELECT n FROM t"
_HIDDEN = "WITH t AS (SELECT 1 AS n FROM hr_payroll) SELECT n FROM t"


def _names(sql: str) -> set[str]:
    return {".".join(p) for p in _tables_in_sql(sql)}


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (_GRANTED_CTE, {"locations"}),
        (_HIDDEN, {"hr_payroll"}),
        (
            "WITH a AS (SELECT 1 AS x FROM locations), b AS (SELECT x FROM a) "
            "SELECT x FROM b",
            {"locations"},
        ),
        (
            "SELECT a FROM (WITH r AS (SELECT 1 AS a FROM locations) SELECT a FROM r) q",
            {"locations"},
        ),
        # A CTE does not see itself unless RECURSIVE: the inner name is the table.
        (
            "WITH hr_payroll AS (SELECT 1 AS x FROM hr_payroll) SELECT x FROM hr_payroll",
            {"hr_payroll"},
        ),
        # A CTE is not visible outside the WITH that owns it.
        (
            "SELECT 1 FROM (WITH r AS (SELECT 1 AS a FROM locations) SELECT a FROM r) q "
            "JOIN r ON 1 = 1",
            {"locations", "r"},
        ),
        # A schema-qualified name is never a CTE reference.
        (
            "WITH t AS (SELECT 1 AS n FROM locations) SELECT n FROM bronze.t",
            {"locations", "bronze.t"},
        ),
        (
            "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 3) "
            "SELECT n FROM c",
            set(),
        ),
    ],
    ids=[
        "granted_cte",
        "ungranted_behind_cte",
        "cte_chain",
        "nested_scope",
        "cte_shadow_reads_table",
        "cte_out_of_scope",
        "qualified_not_cte",
        "recursive",
    ],
)
def test_tables_in_sql_skips_cte_references_only(sql: str, expected: set[str]) -> None:
    assert _names(sql) == expected


def test_sql_parts_never_lists_cte_alias_as_ungranted() -> None:
    assert _sql_parts({"sql_used": _GRANTED_CTE}, None) == []
    assert _sql_parts({"sql_used": _HIDDEN}, None) == [("hr_payroll",)]


def test_sql_grant_names_does_not_grant_or_need_cte_alias() -> None:
    """The grant set stays the validator's set: an alias is neither added nor needed."""
    grants = _sql_grant_names({}, None)
    assert "t" not in grants
    assert "locations" in grants
    assert _sql_parts({"sql_used": _GRANTED_CTE}, None) == []


def test_hide_echo_keeps_text_that_shares_a_cte_name() -> None:
    env = {
        "text": "Revenue by plant: Revenue was 5",
        "assumptions": ["Revenue was summed per plant"],
        "sql_used": (
            "WITH revenue AS (SELECT COUNT(*) AS s FROM shipments) SELECT s FROM revenue"
        ),
    }
    before = {k: (list(v) if isinstance(v, list) else v) for k, v in env.items()}
    out = hide_echo(env, "")
    assert out == before


def test_hide_echo_scrubs_real_table_behind_cte_and_keeps_alias() -> None:
    env = {
        "text": "Used t over hr_payroll: 1",
        "assumptions": ["t reads hr_payroll"],
        "sql_used": _HIDDEN,
    }
    out = hide_echo(env, "")
    blob = repr(out)
    assert "hr_payroll" not in blob
    assert "WITH t AS" in out["sql_used"]
    assert out["sql_used"].rstrip().endswith("FROM t")
    assert "t " in out["text"] and "Used t" in out["text"]


@pytest.mark.parametrize("name", ["t", "revenue_by_region"])
def test_http_generated_answer_with_cte_is_intact(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Envelope over HTTP: a granted CTE answer is not mangled."""
    sql = f"WITH {name} AS (SELECT COUNT(*) AS n FROM locations) SELECT n FROM {name}"
    body = _post_ask(monkeypatch, "How many locations", sql, lane="generated")
    assert body.get("route") == "generated"
    assert body.get("abstained") is False
    assert body.get("sql_used") == sql
    assert body.get("text") == sql
    assert body.get("rows") == [{"n": 1}]
    assert body.get("badge") not in (None, "")


def test_http_generated_ungranted_behind_cte_is_never_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _post_ask(monkeypatch, "How many staff", _HIDDEN, lane="generated")
    assert _name_hits(body, "hr_payroll") == []
    sql_used = str(body.get("sql_used"))
    assert "WITH t AS" in sql_used and sql_used.rstrip().endswith("FROM t")
    assert "WITH t AS" in str(body.get("text"))
