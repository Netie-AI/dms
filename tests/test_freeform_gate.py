"""The free-form gate must be able to fail, and its abstain half must be able to fail.

R-0007: verify a gate can fail before trusting it green. The abstain cases added
to ``verify_freeform_demo.DEMO_SET`` make a strong demand - that the product
refuse - and a demand like that is only safe if the machinery enforcing it is
itself checked. Two ways it could be quietly useless:

  * ``judge`` could treat every abstention as free, in which case an abstain
    case is decoration and a stack that answers everything still scores clean.
  * ``prove_unanswerable`` could pass on a warehouse where the question has
    become answerable, in which case the gate demands a refusal that is now the
    wrong behaviour - R-0005 pointed at ourselves.

The DuckDB fixture is built here rather than read from ``D:\\Cortex``: the check
must run on any machine and must never skip (R-0002). It carries the one hazard
that matters - a dimension table at a finer grain than the fact table - because
that is the shape that produced the ~15x inflation this gate exists to catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from verify_freeform_demo import (  # noqa: E402
    DEMO_SET,
    OracleBroken,
    _close,
    _perturb,
    expects_abstention,
    judge,
    oracle,
    prove_unanswerable,
    resolve_measure_column,
    self_check,
)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Two SKUs, one of which is stocked in three lots. Joining fans it out 3x."""
    import duckdb

    path = tmp_path_factory.mktemp("ff") / "mini.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE lots (sku VARCHAR, category VARCHAR, qty DOUBLE)")
    con.execute(
        "INSERT INTO lots VALUES "
        "('SKU-1','ALPHA',10),('SKU-1','ALPHA',20),('SKU-1','ALPHA',30),"
        "('SKU-2','BETA',40)"
    )
    con.execute("CREATE TABLE sales (sku VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO sales VALUES ('SKU-1',100),('SKU-2',50)")
    con.close()
    return path


def _env(rows: list[dict[str, object]] | None, *, abstained: bool = False) -> dict[str, object]:
    return {
        "badge": "ABSTAIN" if abstained else "L2_VALIDATED",
        "abstained": abstained,
        "rows": rows or [],
        "text": "Abstained." if abstained else "Here is the answer.",
    }


# --------------------------------------------------------------------------
# judge: abstention means opposite things on the two kinds of case
# --------------------------------------------------------------------------


def test_abstaining_on_an_unanswerable_question_is_correct() -> None:
    outcome, _ = judge(_env(None, abstained=True), [], expect_abstain=True)
    assert outcome == "correct"


def test_answering_an_unanswerable_question_is_wrong() -> None:
    """The P0 shape for this half: a plausible number under a confident badge."""
    planted = [{"category": "MEDICAL", "stock_value_myr": 0.0}]
    outcome, detail = judge(_env(planted), [], expect_abstain=True)
    assert outcome == "WRONG"
    assert "unanswerable" in detail
    assert "L2_VALIDATED" in detail, "the badge must be named in the failure line"


def test_answering_an_unanswerable_question_is_wrong_even_with_no_rows() -> None:
    """Refusal *prose* under a confident badge is still a confident answer."""
    outcome, detail = judge(_env(None), [], expect_abstain=True)
    assert outcome == "WRONG"
    assert "unanswerable" in detail


def test_abstention_is_still_free_on_an_ordinary_case() -> None:
    """R-0005 - the gate must never create pressure to guess on answerable work."""
    outcome, _ = judge(_env(None, abstained=True), [("ALPHA", 100.0)], expect_abstain=False)
    assert outcome == "abstained"


def test_expect_abstain_defaults_off_so_existing_cases_are_unchanged() -> None:
    outcome, _ = judge(_env(None, abstained=True), [("ALPHA", 100.0)])
    assert outcome == "abstained"


# --------------------------------------------------------------------------
# prove_unanswerable: the proof must fail when the premise stops holding
# --------------------------------------------------------------------------


def test_ambiguity_proof_fails_when_the_two_readings_agree(warehouse: Path) -> None:
    """If a data change makes the readings converge, refusing is no longer right."""
    same = "SELECT 'x' AS k, SUM(amount) AS v FROM sales"
    case = {
        "id": "converged",
        "expect": "abstain",
        "unanswerable": {"kind": "ambiguous", "why": "-", "reading_a": same, "reading_b": same},
    }
    with pytest.raises(OracleBroken) as exc:
        prove_unanswerable(case, warehouse)
    assert "AGREE" in str(exc.value)
    assert "Retire this case" in str(exc.value)


def test_ambiguity_proof_passes_when_the_readings_genuinely_differ(warehouse: Path) -> None:
    case = {
        "id": "fanout_vs_dedup",
        "expect": "abstain",
        "unanswerable": {
            "kind": "ambiguous",
            "why": "-",
            "reading_a": (
                "SELECT 'joined' AS k, SUM(s.amount) AS v "
                "FROM sales s JOIN lots l ON s.sku = l.sku"
            ),
            "reading_b": "SELECT 'plain' AS k, SUM(amount) AS v FROM sales",
        },
    }
    assert "disagree" in prove_unanswerable(case, warehouse)


def test_empty_proof_fails_once_the_filter_starts_matching(warehouse: Path) -> None:
    """The dangerous direction: stock arrives, and the gate keeps demanding a refusal."""
    case = {
        "id": "no_longer_empty",
        "expect": "abstain",
        "unanswerable": {
            "kind": "empty",
            "why": "nothing is in category ALPHA",
            "sql": "SELECT COUNT(*) FROM lots WHERE category = 'ALPHA'",
            "expect_value": 0,
        },
    }
    with pytest.raises(OracleBroken) as exc:
        prove_unanswerable(case, warehouse)
    assert "may now be answerable" in str(exc.value)


def test_abstain_case_without_a_proof_is_rejected(warehouse: Path) -> None:
    with pytest.raises(OracleBroken):
        prove_unanswerable({"id": "bare", "expect": "abstain"}, warehouse)


# --------------------------------------------------------------------------
# conservation: the check that caught the ~15x inflation must still catch it
# --------------------------------------------------------------------------


def test_conservation_identity_catches_a_fanned_out_oracle(warehouse: Path) -> None:
    """The exact defect fixed in 03bcf2d, rebuilt small enough to read.

    ``lots`` holds three rows for SKU-1, so joining sales to it counts SKU-1's
    100 three times. The grouped total is 350; the independent total is 150.
    """
    case = {
        "id": "fanned",
        "oracle_sql": (
            "SELECT l.category, SUM(s.amount) AS v FROM sales s "
            "JOIN lots l ON s.sku = l.sku GROUP BY l.category ORDER BY v DESC"
        ),
        "top_n": 2,
        "conservation": {"sql": "SELECT SUM(amount) FROM sales", "why": "must sum to sales"},
    }
    with pytest.raises(OracleBroken) as exc:
        oracle(case, warehouse)
    assert "350.00" in str(exc.value) and "150.00" in str(exc.value)


def test_conservation_identity_passes_on_the_deduplicated_oracle(warehouse: Path) -> None:
    case = {
        "id": "deduped",
        "oracle_sql": (
            "SELECT l.category, SUM(s.amount) AS v FROM sales s "
            "JOIN (SELECT DISTINCT sku, category FROM lots) l ON s.sku = l.sku "
            "GROUP BY l.category ORDER BY v DESC"
        ),
        "top_n": 2,
        "conservation": {"sql": "SELECT SUM(amount) FROM sales", "why": "must sum to sales"},
    }
    assert oracle(case, warehouse) == [("ALPHA", 100.0), ("BETA", 50.0)]


# --------------------------------------------------------------------------
# the shipped set: structural claims that need no warehouse
# --------------------------------------------------------------------------


def test_every_case_declares_why_and_a_question() -> None:
    for case in DEMO_SET:
        assert case.get("id"), "a case without an id cannot be reported on"
        assert case.get("why"), f"{case['id']} does not say why it is in the set"
        assert case.get("question"), f"{case['id']} has no question"


def test_case_ids_are_unique() -> None:
    ids = [c["id"] for c in DEMO_SET]
    assert len(ids) == len(set(ids)), "duplicate case id - one would silently shadow the other"


def test_every_abstain_case_carries_an_executable_proof() -> None:
    """An abstain case is only allowed to demand a refusal it can justify."""
    for case in DEMO_SET:
        if not expects_abstention(case):
            continue
        spec = case.get("unanswerable")
        assert isinstance(spec, dict), f"{case['id']} demands a refusal with no proof"
        kind = spec.get("kind")
        assert kind in {"ambiguous", "empty", "absent"}, f"{case['id']} kind={kind!r}"
        assert spec.get("why"), f"{case['id']} proof does not say what it proves"
        if kind == "ambiguous":
            assert spec.get("reading_a") and spec.get("reading_b")
        else:
            assert spec.get("sql") and "expect_value" in spec


def test_every_answerable_case_carries_an_oracle_and_a_top_n() -> None:
    for case in DEMO_SET:
        if expects_abstention(case):
            assert "oracle_sql" not in case, (
                f"{case['id']} both demands a refusal and declares an oracle"
            )
            continue
        assert case.get("oracle_sql"), f"{case['id']} has no oracle"
        assert int(case.get("top_n", 0)) >= 1, f"{case['id']} has no top_n"


def test_no_oracle_anchors_itself_to_the_wall_clock() -> None:
    """A gate anchored to today changes its own answer overnight and then fails.

    Temporal cases must anchor to a value derived from the data, e.g.
    ``(SELECT MAX(timestamp) FROM transactions)``.
    """
    banned = ("current_date", "current_timestamp", "now()", "today()")
    for case in DEMO_SET:
        blob = " ".join(
            str(v) for k, v in case.items() if k in {"oracle_sql", "conservation", "unanswerable"}
        ).lower()
        for token in banned:
            assert token not in blob, f"{case['id']} anchors an oracle to {token}"


def test_the_set_contains_both_kinds_in_meaningful_numbers() -> None:
    """A precision-only set cannot see the answer-everything failure mode."""
    abstain = [c["id"] for c in DEMO_SET if expects_abstention(c)]
    answerable = [c["id"] for c in DEMO_SET if not expects_abstention(c)]
    assert len(abstain) >= 4, f"only {len(abstain)} must-abstain cases: {abstain}"
    assert len(answerable) >= 10, f"only {len(answerable)} answerable cases"


# --------------------------------------------------------------------------
# exact / exact_rows - the two holes the tolerant comparison left open
# --------------------------------------------------------------------------


def test_off_by_one_on_a_count_passes_without_exact() -> None:
    """Documents why the flag exists, so nobody removes it as redundant.

    REL_TOL is 0.5 pct. Half a percent of any count above 200 is more than one,
    so the tolerant comparison cannot tell 496 from 497.
    """
    outcome, _ = judge(_env([{"carrier": "City-Link", "n": 496}]), [("City-Link", 497.0)])
    assert outcome == "correct", "precondition changed; this test no longer proves anything"


def test_off_by_one_on_a_count_is_wrong_when_exact() -> None:
    outcome, detail = judge(
        _env([{"carrier": "City-Link", "n": 496}]), [("City-Link", 497.0)], exact=True
    )
    assert outcome == "WRONG"
    assert "496" in detail and "497" in detail


def test_exact_still_accepts_the_right_count() -> None:
    outcome, _ = judge(
        _env([{"carrier": "City-Link", "n": 497}]), [("City-Link", 497.0)], exact=True
    )
    assert outcome == "correct"


def test_exact_does_not_leak_into_money_cases() -> None:
    """Money computed in a different order lands a fraction apart and is still right."""
    outcome, _ = judge(
        _env([{"category": "MEDICAL", "v": 1_385_709.40}]), [("MEDICAL", 1_385_709.41)]
    )
    assert outcome == "correct"


def test_extra_rows_are_wrong_when_the_answer_is_the_set() -> None:
    """Four warehouses are above 90 pct. Returning eight is a wrong answer.

    Without exact_rows the claim is truncated to the oracle's length before
    comparison, so the four correct leading rows would have scored correct.
    """
    expected = [("A", 95.21), ("B", 95.13), ("C", 91.65), ("D", 91.17)]
    too_many = _env(
        [{"loc": k, "pct": v} for k, v in expected]
        + [{"loc": "E", "pct": 88.0}, {"loc": "F", "pct": 87.0}]
    )
    assert judge(too_many, expected)[0] == "correct", "precondition: truncation hides it"
    outcome, detail = judge(too_many, expected, exact_rows=True)
    assert outcome == "WRONG"
    assert "6 rows" in detail and "4" in detail


def test_missing_rows_are_wrong_when_the_answer_is_the_set() -> None:
    expected = [("A", 95.21), ("B", 95.13), ("C", 91.65), ("D", 91.17)]
    outcome, _ = judge(
        _env([{"loc": k, "pct": v} for k, v in expected[:3]]), expected, exact_rows=True
    )
    assert outcome == "WRONG"


def test_every_count_case_declares_exact() -> None:
    """A count is not approximate. Catches the next count case that forgets."""
    for case in DEMO_SET:
        sql = str(case.get("oracle_sql") or "")
        if "COUNT(" not in sql.upper():
            continue
        # A ratio built from COUNT is still a real number, not a count.
        if "/" in sql:
            continue
        assert case.get("exact"), (
            f"{case['id']} measures a count but does not declare exact=True, so the "
            "0.5 pct relative tolerance would accept an off-by-one"
        )


# --------------------------------------------------------------------------
# diagnosability - a red run must say which of the two things went wrong
# --------------------------------------------------------------------------


def test_the_measure_column_is_resolved_not_assumed_to_be_first() -> None:
    """An engine returning cost beside percentage is right, and must score right.

    Grading the first numeric column rejected correct answers on five cases.
    Returning supporting columns beside the measure is ordinary SQL.
    """
    expected = [("DELIVERED", 49.89), ("IN_TRANSIT", 23.59)]
    env = _env(
        [
            {"status": "DELIVERED", "total_cost_myr": 15_000_000.0, "pct_of_spend": 49.89},
            {"status": "IN_TRANSIT", "total_cost_myr": 7_000_000.0, "pct_of_spend": 23.59},
        ]
    )
    assert judge(env, expected)[0] == "correct"


def test_the_oracle_names_the_measure_column_it_wants_graded() -> None:
    """Name resolution beats value matching, so a coincidence cannot decide it."""
    rows = [{"carrier": "GDEX", "shipments": 50, "on_time_percentage": 50.0}]
    col, how = resolve_measure_column(rows, [("GDEX", 50.0)], measure="on_time_percentage")
    assert col == "on_time_percentage"
    assert "named by the oracle" in how


def test_a_supporting_count_column_does_not_fail_a_correct_scalar() -> None:
    """ff_hazardous_value and ff_coldchain_breach_kg both failed on this shape."""
    env = _env([{"scope": "hazardous", "lots": 299, "total_value_myr": 26_009_301.33}])
    assert judge(env, [("hazardous", 26_009_301.33)])[0] == "correct"


def test_a_genuinely_wrong_number_is_not_excused_as_a_column_problem() -> None:
    expected = [("DELIVERED", 49.89), ("IN_TRANSIT", 23.59)]
    env = _env(
        [
            {"status": "DELIVERED", "pct": 61.10, "other": 3.0},
            {"status": "IN_TRANSIT", "pct": 18.20, "other": 4.0},
        ]
    )
    outcome, detail = judge(env, expected)
    assert outcome == "WRONG"
    assert "wrong column" not in detail


def test_a_guess_and_a_stated_assumption_are_distinguishable() -> None:
    """Both fail, but they are different products and the run must show which."""
    rows = [{"carrier": "GDEX", "pct": 50.0}]
    guessed = _env(rows)
    assumed = dict(_env(rows), assumptions=["counted delivered shipments only"])

    out_g, detail_g = judge(guessed, [], expect_abstain=True)
    out_a, detail_a = judge(assumed, [], expect_abstain=True)

    assert out_g == "WRONG" and out_a == "WRONG"
    assert "this is a guess" in detail_g
    assert "assumption stated" in detail_a
    assert "delivered shipments only" in detail_a


# --------------------------------------------------------------------------
# the adversarial round: defects found by agents that did not write this gate
# --------------------------------------------------------------------------


def test_a_composite_grain_is_matched_by_tokens_not_by_the_oracle_separator() -> None:
    """The oracle writes 'MEDIUM / LOW_STOCK'. An engine returns two columns.

    Requiring the engine to reproduce a separator this file invented failed a
    correct answer with every count exactly right.
    """
    expected = [("MEDIUM / LOW_STOCK", 33.0), ("MEDIUM / CAPACITY_WARNING", 25.0)]
    env = _env(
        [
            {"severity": "MEDIUM", "alert_type": "LOW_STOCK", "alert_count": 33},
            {"severity": "MEDIUM", "alert_type": "CAPACITY_WARNING", "alert_count": 25},
        ]
    )
    assert judge(env, expected, exact=True)[0] == "correct"


def test_an_unordered_question_is_graded_as_a_set() -> None:
    """'in each status' names no ranking; alphabetical is not wrong."""
    expected = [("DELIVERED", 49.89), ("IN_TRANSIT", 23.59), ("CANCELLED", 3.06)]
    alphabetical = _env(
        [
            {"status": "CANCELLED", "pct": 3.06},
            {"status": "DELIVERED", "pct": 49.89},
            {"status": "IN_TRANSIT", "pct": 23.59},
        ]
    )
    assert judge(alphabetical, expected, ordered=False)[0] == "correct"


def test_an_unordered_failure_names_the_right_key() -> None:
    """Positional zipping reported 'DELIVERED: answered 3.06' when 3.06 was right."""
    expected = [("DELIVERED", 49.89), ("CANCELLED", 3.06)]
    wrong = _env([{"status": "CANCELLED", "pct": 3.06}, {"status": "DELIVERED", "pct": 61.10}])
    outcome, detail = judge(wrong, expected, ordered=False)
    assert outcome == "WRONG"
    assert "DELIVERED" in detail and "61.10" in detail
    assert "3.06" not in detail, "the failure must not name a figure that was correct"


def test_ordering_is_still_enforced_when_the_question_asks_for_a_ranking() -> None:
    expected = [("A", 10.0), ("B", 5.0)]
    swapped = _env([{"k": "B", "v": 5.0}, {"k": "A", "v": 10.0}])
    assert judge(swapped, expected)[0] == "WRONG"


def test_a_refusal_under_a_confident_badge_is_wrong_not_correct() -> None:
    """CLAUDE.md 10a: a green badge on abstention prose is a P0.

    judge scored this 'correct' because abstained was set, which is the half of
    the defect the customer never sees.
    """
    env = {"badge": "L2_VALIDATED", "abstained": True, "rows": [], "text": "Could not answer."}
    outcome, detail = judge(env, [], expect_abstain=True)
    assert outcome == "WRONG"
    assert "E1" in detail


def test_a_proper_refusal_still_scores_correct() -> None:
    env = {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "Could not answer."}
    assert judge(env, [], expect_abstain=True)[0] == "correct"


def test_rows_without_a_numeric_column_are_not_reported_as_no_rows() -> None:
    """The old message said 'no executed rows' when rows were plainly present."""
    env = _env([{"location_name": "Johor South Hub"}, {"location_name": "Melaka Gateway"}])
    outcome, detail = judge(env, [("Johor South Hub", 95.21)])
    assert outcome == "WRONG"
    assert "no executed rows" not in detail
    assert "no numeric column" in detail


def test_per_case_tolerance_can_be_tightened() -> None:
    """ff_network_utilization: 8 of 20 single-warehouse omissions passed at 0.5 pct."""
    dropped_a_warehouse = _env([{"label": "network", "utilization_pct": 68.21}])
    assert judge(dropped_a_warehouse, [("network", 68.26)])[0] == "correct"
    assert judge(dropped_a_warehouse, [("network", 68.26)], rel_tol=0.0005)[0] == "WRONG"


def test_the_tightened_case_still_accepts_the_right_figure() -> None:
    env = _env([{"label": "network", "utilization_pct": 68.26}])
    assert judge(env, [("network", 68.26)], rel_tol=0.0005)[0] == "correct"


def test_every_ambiguous_proof_can_actually_converge() -> None:
    """The retirement guard compares result tuples.

    An earlier version gave the two readings different hardcoded labels, so
    a == b was unreachable at any data and the promised retirement could never
    fire. Labels must match between readings or the guard is decoration.
    """
    import re as _re

    for case in DEMO_SET:
        spec = case.get("unanswerable") or {}
        if spec.get("kind") != "ambiguous":
            continue
        lits = {
            k: set(_re.findall(r"'([a-z0-9_]{4,})'\s+AS\s+\w*label", str(spec[k]), _re.I))
            for k in ("reading_a", "reading_b")
        }
        assert lits["reading_a"] == lits["reading_b"], (
            f"{case['id']}: readings hardcode different labels {lits}, so the "
            "convergence guard in prove_unanswerable can never fire"
        )


# --------------------------------------------------------------------------
# the self-check: R-0007 applied to the whole shipped set
# --------------------------------------------------------------------------


def _synthetic(**over: object) -> tuple[dict[str, object], list[tuple[str, float]]]:
    case: dict[str, object] = {"id": "syn", "measure": "v", "top_n": 2}
    case.update(over)
    return case, [("A", 10.0), ("B", 5.0)]


def test_self_check_passes_a_well_formed_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert self_check([_synthetic()]) == 0
    assert "PASS" in capsys.readouterr().out


def test_self_check_catches_a_case_that_cannot_test_its_own_ranking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two identical rows make the swap probe a no-op, so ordering is untested."""
    case: dict[str, object] = {"id": "syn_dup", "measure": "v", "top_n": 2}
    assert self_check([(case, [("A", 10.0), ("A", 10.0)])]) == 1
    out = capsys.readouterr().out
    assert "swapped ranking rejected" in out
    assert "cannot fail" in out


def test_self_check_requires_supporting_columns_to_be_tolerated(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An engine returning a row count beside the measure must still pass.

    This probe is the one that would have caught the first-numeric-column
    defect, which rejected correct answers on five shipped cases.
    """
    case, truth = _synthetic(measure="row_count_seen")
    assert self_check([(case, truth)]) == 1
    assert "supporting column tolerated" in capsys.readouterr().out


def test_self_check_exercises_abstain_cases_including_the_green_badge_p0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = {"id": "syn_abstain", "expect": "abstain"}
    assert self_check([(case, [])]) == 0
    assert "abstain probes: 3" in capsys.readouterr().out


def test_perturbation_always_clears_the_tolerance_that_applies() -> None:
    """A perturbation inside tolerance would make every 'can it fail' probe a lie."""
    for case in DEMO_SET:
        if expects_abstention(case):
            continue
        for value in (0.0, 1.0, 68.26, 497.0, 80_375_993.99, -110.0):
            moved = _perturb(value, case)
            assert not _close(
                moved,
                value,
                exact=bool(case.get("exact")),
                rel_tol=case.get("rel_tol"),
            ), f"{case['id']}: perturbing {value} to {moved} stays inside tolerance"
