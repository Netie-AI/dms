"""Stage 0 either knows what the ask means or says it does not.

The dangerous case here is not the abstention, it is the silent pick: a
warehouse that carries both a commercial lease book and residential housing rent
answers "what was rent" with a green badge either way, and the buyer cannot see
which half was summed. So both directions are asserted - one landed sense binds
to that column's own spelling, two landed senses bind nothing and name the
spellings that would separate them.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.cca.sense import bind_sense, propose_senses

# The word "rent" points at Lease, the phrase "housing rent" at HousingRent.
# One question, two readings - which is exactly the ask the data must resolve.
AMBIGUOUS = "what was total rent on our housing rent units last quarter"


def _build(db: Path, rows: list[tuple[str, float]]) -> Path:
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (transaction_type VARCHAR, amount DOUBLE)")
    con.executemany("INSERT INTO deals VALUES (?, ?)", rows)
    # A granted table with no tenure encoding at all, to prove the scan does not
    # wander outside the pack's declared column names.
    con.execute("CREATE TABLE payroll (staff VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO payroll VALUES ('A', 1.0)")
    con.close()
    return db


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    """Both senses landed, plus a value the pack does not claim."""
    return _build(
        tmp_path / "sense.duckdb",
        [
            ("LEASE", 100.0),
            ("SALE", 200.0),
            ("HOUSING_RENT", 50.0),
            ("Barter", 7.0),
        ],
    )


@pytest.fixture()
def lease_only(tmp_path: Path) -> Path:
    """Commercial lease book only - no residential rent rows exist."""
    return _build(
        tmp_path / "lease_only.duckdb",
        [("LEASE", 100.0), ("SALE", 200.0)],
    )


def test_one_clear_sense_certifies_with_the_columns_own_spelling(lake: Path) -> None:
    res = bind_sense(
        "how much did we bill on leasing last quarter",
        warehouse=lake,
        tables=["deals"],
    )
    assert propose_senses("how much did we bill on leasing last quarter") == ("Lease",)
    assert res.status == "CERTIFIED"
    assert res.stage == "sense"
    assert res.values == ("LEASE",)
    binding = res.binding_text() or ""
    assert "'LEASE'" in binding
    assert "deals.transaction_type" in binding
    # Certified to one sense only: the Buy and HousingRent rows sitting in the
    # same column are not swept in just because the pack knows those words.
    assert "'SALE'" not in binding
    assert "'HOUSING_RENT'" not in binding


def test_ambiguous_ask_with_both_senses_landed_abstains_naming_both(lake: Path) -> None:
    assert propose_senses(AMBIGUOUS) == ("Lease", "HousingRent")
    res = bind_sense(AMBIGUOUS, warehouse=lake, tables=["deals"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    reasons = " ".join(res.reasons)
    assert "Lease" in reasons
    assert "HousingRent" in reasons
    # The hint has to carry the landed spellings, or the buyer re-asks blind.
    assert "'LEASE'" in reasons
    assert "'HOUSING_RENT'" in reasons
    assert "deals.transaction_type" in reasons


def test_ambiguous_looking_ask_certifies_when_only_one_sense_is_landed(
    lease_only: Path,
) -> None:
    res = bind_sense(AMBIGUOUS, warehouse=lease_only, tables=["deals"])
    # Ambiguity is a property of the data, not of the wording. Only one reading
    # exists here, so abstaining would refuse an answerable question.
    assert res.status == "CERTIFIED"
    assert res.values == ("LEASE",)
    assert res.absent == ("HousingRent",)
    assert "HousingRent" in " ".join(res.reasons)
    assert "absent from this data" in " ".join(res.reasons)
    assert "HousingRent" in " ".join(res.evidence_lines())


def test_abstains_when_no_granted_column_carries_the_vocabulary(lake: Path) -> None:
    res = bind_sense("how much did we bill on leasing", warehouse=lake, tables=["payroll"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert res.values == ()
    assert "transaction_type" in res.reasons[0]
    assert "payroll" in res.reasons[0]


def test_abstains_when_the_column_exists_but_lands_no_sense(tmp_path: Path) -> None:
    db = _build(tmp_path / "other.duckdb", [("Barter", 1.0), ("Swap", 2.0)])
    res = bind_sense("how much did we bill on leasing", warehouse=db, tables=["deals"])
    # The filter would have parsed, executed and matched nothing: hard rule 12.
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "no value matching" in res.reasons[0]
    assert "Barter" in res.unmatched_sample


def test_question_naming_no_sense_at_all_abstains(lake: Path) -> None:
    assert propose_senses("what was headcount by department last quarter") == ()
    res = bind_sense(
        "what was headcount by department last quarter",
        warehouse=lake,
        tables=["deals"],
    )
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "names no lease, buy or housing-rent sense" in res.reasons[0]


def test_substring_lookalikes_are_not_a_sense(lake: Path) -> None:
    # "current" contains "rent" and "letter" contains "let". Token matching, not
    # containment, is what keeps those out of stage 0.
    assert propose_senses("what is our current letter volume") == ()


def test_constraint_parses_as_the_first_stage_of_a_trace(lake: Path) -> None:
    from dms_executor.constraint_cascade import parse_trace

    res = bind_sense(
        "how much did we bill on leasing",
        warehouse=lake,
        tables=["deals"],
        constraint_id="sense-1",
    )
    # Sense is stage 0, so it needs no certified priors: a trace of one parses.
    parsed = parse_trace([res.to_constraint()])
    assert parsed[0]["type"] == "sense"
    assert parsed[0]["status"] == "CERTIFIED"
    assert "'LEASE'" in (parsed[0]["binding"] or "")

    ambiguous = bind_sense(AMBIGUOUS, warehouse=lake, tables=["deals"])
    blocked = parse_trace([ambiguous.to_constraint()])
    assert blocked[0]["status"] == "ABSTAIN"
    assert blocked[0]["binding"] is None
    # And an ambiguous sense cannot be papered over by a later certified stage.
    with pytest.raises(Exception, match="must not be CERTIFIED"):
        parse_trace(
            [
                ambiguous.to_constraint(),
                {
                    "constraint_id": "geo-1",
                    "type": "geo",
                    "candidate": "SEA",
                    "binding": "country IN ('MY')",
                    "evidence": [],
                    "status": "CERTIFIED",
                    "reasons": [],
                },
            ]
        )
