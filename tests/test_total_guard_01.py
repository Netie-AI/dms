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
the first generate, the extract loop, and the contract ask's ``sql_used``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.manifest import ManifestMinter
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

#: The issue's Problem table, row by row: name -> (sql, the figure it returns).
#: The true figure is 107 in every row.
ISSUE_ROWS: dict[str, tuple[str, int]] = {
    "r01_rollup": (_GROUPED.format("ROLLUP(school_id)"), 214),
    "r02_cube": (_GROUPED.format("CUBE(school_id)"), 214),
    "r03_cube_two_columns": (_GROUPED.format("CUBE(school_id, enrollment_id)"), 428),
    "r04_grouping_sets_two_empty": (f"SELECT SUM(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY GROUPING SETS ((), ()))", 214),
    "r05_grouping_sets_three_empty": (f"SELECT SUM(t) AS total FROM (SELECT SUM({N}) AS t FROM {T} GROUP BY GROUPING SETS ((), (), ()))", 321),
    "r06_grouping_sets_same_column_twice": (_GROUPED.format("GROUPING SETS ((school_id), (school_id))"), 214),
    "r07_cte_rollup": (f"WITH c AS (SELECT school_id, SUM({N}) AS t FROM {T} GROUP BY ROLLUP(school_id)) SELECT SUM(t) AS total FROM c", 214),
    "r08_unpivot_two_columns": (f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT (n FOR k IN (a, b))", 214),
    "r09_unpivot_three_columns": (f"SELECT SUM(n) AS total FROM {_THREE} UNPIVOT (n FOR k IN (a, b, c))", 321),
    "r10_list_concat": (f"SELECT list_sum(list_concat(list({N}), list({N}))) AS total FROM {T}", 214),
    "r11_list_pipe": (f"SELECT list_sum(list({N}) || list({N})) AS total FROM {T}", 214),
    "r12_window_total_summed_per_row": (f"SELECT SUM(w) AS total FROM (SELECT SUM({N}) OVER () AS w FROM {T})", 428),
    "r13_rollup_same_column_twice": (_GROUPED.format("ROLLUP(school_id, school_id)"), 321),
    "r14_unpivot_columns_star": (f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT (n FOR k IN (COLUMNS(*)))", 214),
    "r14_unpivot_statement_on_columns_star": (f"SELECT SUM(n) AS total FROM (UNPIVOT {_TWO} ON COLUMNS(*) INTO NAME k VALUE n)", 214),
    "r15_unpivot_include_nulls": (f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT INCLUDE NULLS (n FOR k IN (a, b))", 214),
    "r16_unpivot_paired": (f"SELECT SUM(n) AS total FROM {_TWO} UNPIVOT ((n, m) FOR k IN ((a, b), (b, a)))", 214),
}


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


def _assert_abstain_named(env: dict[str, Any], rig: od._Space, figures: tuple[int, ...]) -> str:
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
        assert not re.search(rf"\b{figure}\b", text), (figure, text)
    assert "total=" not in text and "The total is" not in text, text
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"
    return code


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
    """Clause 1: each row of the issue's table, through each door."""
    sql, wrong = ISSUE_ROWS[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    # The danger is real: the statement runs and returns the wrong figure.
    assert od._oracle(rig.lake, sql) == [{"total": wrong}]
    assert od._oracle(rig.lake, TRUE_SQL) == [{"total": TRUE}]
    assert wrong != TRUE
    _assert_abstain_named(env, rig, (wrong, TRUE))
