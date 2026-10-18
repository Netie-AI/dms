"""Second reading: a different instruction, same checker, reconfirm on a split.

Must fail when the second prompt is the first plus a suffix, when a retry skips
``validate_compiled_sql``, or when the vote module grows a regex or a phrase list.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.sql_vote import (
    empty_plan,
    prompt_hash,
    prompts_differ_in_substance,
    reading_prompt,
    readings_agree,
    sql_equivalent,
)

ROOT = Path(__file__).resolve().parents[1]
_VOTE = ROOT / "packages" / "executor" / "dms_executor" / "sql_vote.py"
_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_COLD = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_COUNT = "SELECT COUNT(*) AS n FROM inventory"
_REGEX_ATTRS = frozenset(
    {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "split"}
)


def _loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")


def _names(**extra: Any) -> dict[str, Any]:
    base = {
        "served_model": "fake",
        "served_provider": "groq",
        "phase": "generate",
    }
    base.update(extra)
    return base


def _ask(tmp_path: Path, question: str, compute) -> dict[str, Any] | None:
    begin_answer_model_calls()
    db = tmp_path / "vote.duckdb"
    ensure_demo_warehouse(db)

    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_vote", output={"rows": rows})

    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=submit,
        ledger_append=lambda _p: SimpleNamespace(entry_id="led", hash="h"),
        ontology=load_verified_ontology(db, demo_ontology(db)),
        dialect=SERVING_DIALECT,
    )


def test_the_two_prompts_differ_in_instruction_not_by_a_suffix() -> None:
    """A different hash is not enough. A marker glued on the first prompt fails."""
    from dms_executor import _insights_compute_seam

    question = "How many locations are cold storage?"
    direct = reading_prompt(question, reading=1)
    planned = reading_prompt(question, reading=2, plan=empty_plan())
    suffix = "reading=2\n" + direct
    prefix = direct + "\nreading=2"
    assert prompts_differ_in_substance(direct, planned)
    assert prompt_hash(direct) != prompt_hash(suffix)
    assert not prompts_differ_in_substance(direct, suffix)
    assert not prompts_differ_in_substance(direct, prefix)
    assert not readings_agree(
        prompt_a=direct,
        prompt_b=suffix,
        sql_a=_COUNT,
        sql_b=_COUNT,
        rows_a=[{"n": 1}],
        rows_b=[{"n": 1}],
        dialect="duckdb",
    )
    seen: list[str] = []

    class _Cortex:
        def compute_insights(self, asked: str, **_kwargs: Any) -> dict[str, Any]:
            seen.append(asked)
            return {}

    _insights_compute_seam(
        _Cortex(),
        question,
        session_id=None,
        space_id=None,
        ontology={"sql_vote_prompt": direct, "sql_vote_kind": "direct"},
    )
    _insights_compute_seam(
        _Cortex(),
        question,
        session_id=None,
        space_id=None,
        ontology={"sql_vote_prompt": planned, "sql_vote_kind": "reading"},
    )
    assert seen[0] == direct
    assert seen[1] == planned
    assert prompts_differ_in_substance(seen[0], seen[1])
    assert seen[0].split("\nQuestion:\n", 1)[0] != seen[1].split("\nQuestion:\n", 1)[0]


def test_equivalent_sql_and_equal_rows_agree_only_from_independent_prompts() -> None:
    rows = [{"n": 3}]
    left = "SELECT COUNT(*) AS n FROM inventory"
    right = "select  count(*)  as n  from inventory;"
    assert sql_equivalent(left, right, "duckdb")
    first = reading_prompt("How many inventory rows?", reading=1)
    second = reading_prompt("How many inventory rows?", reading=2)
    assert readings_agree(
        prompt_a=first,
        prompt_b=second,
        sql_a=left,
        sql_b=right,
        rows_a=rows,
        rows_b=rows,
        dialect="duckdb",
    )
    assert not readings_agree(
        prompt_a=first,
        prompt_b=first,
        sql_a=left,
        sql_b=right,
        rows_a=rows,
        rows_b=rows,
        dialect="duckdb",
    )


def test_equivalent_sql_with_different_rows_does_not_agree() -> None:
    question = "How many?"
    sql = "SELECT COUNT(*) AS n FROM inventory"
    assert not readings_agree(
        prompt_a=reading_prompt(question, reading=1),
        prompt_b=reading_prompt(question, reading=2),
        sql_a=sql,
        sql_b=sql,
        rows_a=[{"n": 1}],
        rows_b=[{"n": 2}],
        dialect="duckdb",
    )


def test_the_vote_module_has_no_regex_or_phrase_list() -> None:
    tree = ast.parse(_VOTE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "re"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if isinstance(node, ast.Attribute) and node.attr in _REGEX_ATTRS:
            root = node.value
            assert not (isinstance(root, ast.Name) and root.id == "re")
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            phrases = [
                elt.value
                for elt in node.elts
                if isinstance(elt, ast.Constant)
                and isinstance(elt.value, str)
                and " " in elt.value
            ]
            assert len(phrases) < 3, phrases


def test_identical_sql_from_the_second_reading_still_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old identical-SQL exclusion abstained. Independent prompts serve."""
    _loop(monkeypatch)
    prompts: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        prompt = ctx.get("sql_vote_prompt")
        if isinstance(prompt, str):
            prompts.append(prompt)
        return _names(query_sql=_COLD)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["rows"]
    assert len(prompts) >= 2
    assert prompts_differ_in_substance(prompts[0], prompts[-1])
    assert not prompts_differ_in_substance(prompts[0], "reading=2\n" + prompts[0])


def test_a_retry_still_calls_validate_compiled_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second reading's retry is the same checker. A bypass fails this."""
    _loop(monkeypatch)
    monkeypatch.setattr("dms_executor.sql_loop.recorded_model_calls", lambda: 99)
    import dms_executor.generative_ask as generative_ask

    seen: list[str] = []
    real = generative_ask.validate_compiled_sql

    def spy(sql: str, **kwargs: Any) -> str | None:
        seen.append(sql)
        return real(sql, **kwargs)

    monkeypatch.setattr(generative_ask, "validate_compiled_sql", spy)
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("sql_vote_kind") == "reconfirm":
            return {
                "reconfirm": {
                    "why": "the second reading was refused",
                    "closest_question": "Which locations are cold storage?",
                }
            }
        if ctx.get("sql_vote_kind") == "reading":
            if ctx.get("sql_loop_feedback"):
                return _names(query_sql="SELECT * FROM read_csv('z.csv')")
            return _names(query_sql="SELECT nope FROM locations")
        return _names(query_sql=_COLD)

    db = tmp_path / "vote.duckdb"
    ensure_demo_warehouse(db)

    def submit(sql: str) -> Any:
        submitted.append(sql)
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_vote", output={"rows": rows})

    begin_answer_model_calls()
    env = maybe_generative_ask(
        "Which locations are cold storage?",
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=submit,
        ledger_append=lambda _p: SimpleNamespace(entry_id="led", hash="h"),
        ontology=load_verified_ontology(db, demo_ontology(db)),
        dialect=SERVING_DIALECT,
    )
    assert any("read_csv" in sql for sql in seen)
    assert all("read_csv" not in sql for sql in submitted)
    assert env is not None
    assert env["abstained"] is True
    assert env["reconfirm"]["why"] == "the second reading was refused"
    assert env["reconfirm"]["closest_question"] == "Which locations are cold storage?"
    assert env["reconfirm_yes"] == "yes"
    assert "Yes runs that question." in env["text"]
    assert env["rows"] == []


def test_disagreement_reconfirms_instead_of_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("sql_vote_kind") == "reconfirm":
            return {
                "reconfirm": {
                    "why": "the readings returned different rows",
                    "closest_question": "How many inventory rows are there?",
                }
            }
        if ctx.get("sql_vote_kind") == "reading":
            return _names(query_sql=_COUNT)
        return _names(query_sql=_COLD)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env["sql_used"] is None
    assert env["reconfirm"] == {
        "why": "the readings returned different rows",
        "closest_question": "How many inventory rows are there?",
    }
    assert env["reconfirm_yes"] == "yes"
    assert "Yes runs that question." in env["text"]
    assert "I cannot certify an ontology-grounded query" not in env["text"]


@pytest.mark.parametrize(
    "failure",
    ["pre_run_refusal", "explain_fail", "empty_rows"],
)
def test_second_reading_retry_recovers_the_failure(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 21-case types: the retry SQL still goes through the checker."""
    _loop(monkeypatch)
    import dms_executor.generative_ask as generative_ask

    checked: list[str] = []
    real = generative_ask.validate_compiled_sql

    def spy(sql: str, **kwargs: Any) -> str | None:
        checked.append(sql)
        return real(sql, **kwargs)

    monkeypatch.setattr(generative_ask, "validate_compiled_sql", spy)
    reasons: list[str] = []
    empty_sql = "SELECT location_code FROM locations WHERE location_code = 'NO_SUCH'"

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        kind = ctx.get("sql_vote_kind")
        if kind == "plan":
            return {
                "vote_plan": {
                    "tables": ["locations"],
                    "joins": [],
                    "measure": "location_code",
                    "filters": ["is_cold_storage"],
                }
            }
        if kind != "reading":
            return _names(query_sql=_COLD)
        feedback = ctx.get("sql_loop_feedback")
        if isinstance(feedback, dict):
            reasons.append(str(feedback.get("reason") or ""))
            return _names(query_sql=_COLD)
        if failure == "pre_run_refusal":
            return _names()
        if failure == "explain_fail":
            return _names(query_sql="SELECT nope FROM locations")
        return _names(query_sql=empty_sql)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["rows"]
    assert _COLD in checked
    assert reasons
    if failure == "pre_run_refusal":
        assert reasons[0].startswith("pre_run_refusal:")
    elif failure == "explain_fail":
        assert reasons[0].startswith("checker:")
        assert "explain:" in reasons[0]
    else:
        assert reasons[0].startswith("empty_result_unverified:")


def test_paired_score_self_check_is_not_the_held_out_split() -> None:
    spec = importlib.util.spec_from_file_location(
        "score_sql_vote", ROOT / "scripts" / "score_sql_vote.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.self_check() == 0
    report = module.paired_score(module._FIXTURE)
    assert report["live"] is False
    assert report["correct"] == 2 and report["wrong"] == 1
    assert report["recovered"] == ["q2"]
    assert report["new_wrongs"] == ["q3"]
    recovered = module.retry_recovery(module.failure_fixture())
    assert recovered["counts"] == {
        "pre_run_refusal": 10,
        "explain_fail": 7,
        "empty_rows": 4,
    }
    assert len(recovered["recovered"]) == 21
    assert recovered["live"] is False
    stub = module.main([])
    assert stub == 0
    harness_spec = importlib.util.spec_from_file_location(
        "sql_vote_heldout", ROOT / "scripts" / "verify" / "sql_vote_heldout.py"
    )
    assert harness_spec and harness_spec.loader
    harness = importlib.util.module_from_spec(harness_spec)
    harness_spec.loader.exec_module(harness)
    assert harness.self_check() == 0
    assert harness.live_blockers({})
    assert harness.main(["--live"]) == 2
