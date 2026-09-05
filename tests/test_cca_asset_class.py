"""CCA-03 - the commercial/residential binder must refuse closed on a missing encoding.

Two failures are asserted here because only one of them looks like a failure.
Binding ``Commercial`` against a column encoded ``COM`` has to work, or the
feature is useless. Binding it against a column that carries no commercial
value at all has to abstain, or the feature is dangerous: that filter parses,
executes, matches nothing, and hands back a plausible number under a green
badge. The second case is the ticket's named proof.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.cca.asset_class import bind_asset_class, parse_class_intent
from dms_executor.constraint_cascade import ConstraintSchemaError, parse_trace


def _warehouse(tmp_path: Path, name: str, values: list[tuple[str, float]]) -> Path:
    db = tmp_path / name
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE assets (asset_class VARCHAR, amount DOUBLE)")
    con.executemany("INSERT INTO assets VALUES (?, ?)", values)
    # A granted table with no class encoding at all, for the missing-column case.
    con.execute("CREATE TABLE payroll (staff VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO payroll VALUES ('A', 1.0)")
    con.close()
    return db


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    """A warehouse that spells the classes as codes, the way they usually land."""
    return _warehouse(
        tmp_path,
        "cca03.duckdb",
        [("COM", 10.0), ("RES", 20.0), ("Mixed Use", 3.0), ("Carpark", 1.0)],
    )


def test_parse_reads_the_epics_phrasings_without_a_regex_cascade() -> None:
    assert parse_class_intent("commercial only") == (("Commercial",), ())
    assert parse_class_intent("residential only") == (("Residential",), ())
    assert parse_class_intent("ignore residential") == ((), ("Residential",))
    assert parse_class_intent("exclude residential") == ((), ("Residential",))
    assert parse_class_intent("total sales excluding housing") == ((), ("Residential",))
    assert parse_class_intent("lease revenue for commercial property, ignore residential") == (
        ("Commercial",),
        ("Residential",),
    )
    # Bare "commercial" is strict now (see STRICT_ALIASES): it names a class
    # only next to a cue, so the exclusion still binds and the include side
    # does not. "commercial revenue" is the same shape as "commercial
    # performance versus target", which must not engage at all.
    assert parse_class_intent("commercial revenue, ignore residential") == (
        (),
        ("Residential",),
    )
    # A cue that governs something else does not reach across the sentence, and
    # bare "commercial" in the middle of a sentence is not a class term.
    assert parse_class_intent("ignore stale rows and total commercial sales") == ((), ())
    assert parse_class_intent("revenue last quarter") == ((), ())


def test_commercial_only_certifies_against_a_code_encoded_column(lake: Path) -> None:
    res = bind_asset_class("commercial only", warehouse=lake, tables=["assets"])
    assert res.status == "CERTIFIED"
    assert res.stage == "asset_class"
    assert res.polarity == "include"
    assert res.matched == {"Commercial": ("COM",)}
    # Hard rule 12: the filter carries the column's spelling, not the question's.
    binding = res.binding_text() or ""
    assert binding == "assets.asset_class IN ('COM')"
    assert "'Commercial'" not in binding
    assert "COM" in res.reasons[0]
    # The residential and mixed rows are visible as what was not selected.
    assert "RES" in res.unmatched_sample


def test_ignore_residential_binds_as_an_exclusion_and_says_which_case_it_is(
    lake: Path, tmp_path: Path
) -> None:
    res = bind_asset_class("ignore residential", warehouse=lake, tables=["assets"])
    assert res.status == "CERTIFIED"
    assert res.polarity == "exclude"
    assert res.values == ("RES",)
    assert res.binding_text() == "assets.asset_class NOT IN ('RES')"
    # CERTIFIED + exclude means the exclusion removes rows that exist, and the
    # reason names the landed spelling it removes.
    assert "removes rows that exist" in res.reasons[0]
    assert "'RES'" in res.reasons[0]

    # Same ask, data with nothing to exclude. This is the other case, and the
    # caller tells them apart by status rather than by reading prose.
    commercial_only = _warehouse(tmp_path, "com.duckdb", [("COM", 10.0)])
    noop = bind_asset_class("ignore residential", warehouse=commercial_only, tables=["assets"])
    assert noop.status == "ABSTAIN"
    assert noop.binding_text() is None
    assert "no-op" in noop.reasons[-1]


def test_abstains_when_the_column_carries_no_commercial_value(tmp_path: Path) -> None:
    db = _warehouse(tmp_path, "res.duckdb", [("RES", 20.0), ("Apartment", 5.0)])
    res = bind_asset_class("commercial only", warehouse=db, tables=["assets"])
    # Refuse closed: this filter would have parsed, executed and matched nothing,
    # and zero rows summed is still a number.
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert res.values == ()
    assert "no value matching" in res.reasons[0]
    assert "RES" in res.unmatched_sample


def test_abstains_naming_the_missing_encoding_when_no_column_carries_a_class(
    lake: Path,
) -> None:
    res = bind_asset_class("commercial only", warehouse=lake, tables=["payroll"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "no asset class encoding" in res.reasons[0]
    assert "asset_class" in res.reasons[0]
    assert "payroll" in res.reasons[0]


def test_a_question_naming_no_class_abstains_rather_than_binding_nothing(lake: Path) -> None:
    res = bind_asset_class("what was revenue last quarter", warehouse=lake, tables=["assets"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "names no asset class" in res.reasons[0]


def test_self_contradicting_ask_refuses(lake: Path) -> None:
    res = bind_asset_class(
        "commercial only, excluding commercial", warehouse=lake, tables=["assets"]
    )
    # No landed value and no steward action fixes this one, so it is not an abstain.
    assert res.status == "REFUSE"
    assert res.binding_text() is None
    assert "Commercial" in res.reasons[0]
    assert "includes and excludes" in res.reasons[0]


def test_constraint_parses_under_cca_01_behind_a_certified_sense(lake: Path) -> None:
    res = bind_asset_class("commercial only", warehouse=lake, tables=["assets"])
    sense = {
        "constraint_id": "sense-0",
        "type": "sense",
        "candidate": "n/a",
        "binding": None,
        "evidence": [],
        "status": "CERTIFIED",
        "reasons": [],
    }
    parsed = parse_trace([sense, res.to_constraint()])
    assert parsed[-1]["type"] == "asset_class"
    assert parsed[-1]["status"] == "CERTIFIED"
    assert parsed[-1]["binding"] == "assets.asset_class IN ('COM')"

    # asset_class is stage 1: it cannot be certified over an unresolved sense.
    with pytest.raises(ConstraintSchemaError, match="must not be CERTIFIED"):
        parse_trace([{**sense, "status": "ABSTAIN"}, res.to_constraint()])


# ---------------------------------------------------------------------------
# The negation rule must not invert the ask (verified defect D1)
# ---------------------------------------------------------------------------


def test_a_cue_two_tokens_ahead_of_a_class_word_does_not_negate_it() -> None:
    """"no matter if commercial or residential" asks for both, not for one.

    A three-token backward reach let ``no`` in "no matter if" exclude
    ``commercial``, and the remaining ``residential`` certified as the include
    side, so the ask that said "everything" bound ``asset_class IN ('RES')`` -
    the answer inverted from the question, under a green badge.
    """
    assert parse_class_intent("everything, no matter if commercial or residential") == ((), ())
    # The reach is what changed, not the cue: one token nearer and it negates.
    assert parse_class_intent("no commercial") == ((), ("Commercial",))


def test_a_negation_after_the_word_negates_that_word_and_not_the_next_one() -> None:
    """"residential excluded, commercial included" is one of each, not the reverse.

    Searching backward only read ``excluded`` as governing ``commercial``,
    which put Residential in the include set and Commercial in the exclude set:
    both members on the wrong side of the same sentence.
    """
    assert parse_class_intent("residential excluded, commercial included") == (
        ("Commercial",),
        ("Residential",),
    )
    assert parse_class_intent("residential omitted") == ((), ("Residential",))
    assert parse_class_intent("residential removed, commercial only") == (
        ("Commercial",),
        ("Residential",),
    )


def test_a_negation_carries_across_a_conjunction_to_the_class_beside_it() -> None:
    """"excluding commercial and residential" excludes both.

    ``residential`` sits three tokens after the cue, so nothing states its
    polarity; the conjunction is the only evidence there is, and reading it as
    an inclusion inverts half the ask exactly as the two cases above did.
    """
    assert parse_class_intent("excluding commercial and residential") == (
        (),
        ("Commercial", "Residential"),
    )


def test_the_inverted_ask_now_binds_the_class_the_caller_asked_for(lake: Path) -> None:
    """Rule 10: assert the binding a caller would execute, not only the parse."""
    res = bind_asset_class(
        "residential excluded, commercial included", warehouse=lake, tables=["assets"]
    )
    assert res.status == "CERTIFIED"
    assert res.binding_text() == "assets.asset_class IN ('COM')"

    # And the ask that filters on nothing binds nothing rather than binding RES.
    everything = bind_asset_class(
        "everything, no matter if commercial or residential",
        warehouse=lake,
        tables=["assets"],
    )
    assert everything.status == "ABSTAIN"
    assert everything.binding_text() is None
    assert "names no asset class" in everything.reasons[0]


# ---------------------------------------------------------------------------
# A loose column name is not a cheap extra chance to bind (verified defect D2)
# ---------------------------------------------------------------------------


def test_a_customer_segment_column_cannot_certify_an_asset_class(tmp_path: Path) -> None:
    """``segment_class`` holds customer segments, and Retail there is not a class.

    The observed failure certified ``deals.segment_class IN ('Retail')`` for
    "total value for commercial property" over ('Retail','Enterprise',
    'Wholesale') and reported "Commercial covered 1 of 1 members". Every step
    of that is plausible and the answer is about the wrong column, so the fix
    is to not scan the column rather than to disclose the residual afterwards.
    """
    db = tmp_path / "segments.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (segment_class VARCHAR, amount DOUBLE)")
    con.executemany(
        "INSERT INTO deals VALUES (?, ?)",
        [("Retail", 10.0), ("Enterprise", 20.0), ("Wholesale", 5.0)],
    )
    con.close()

    res = bind_asset_class("total value for commercial property", warehouse=db, tables=["deals"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "segment_class" not in res.coverage_note()
    assert "no asset class encoding" in res.reasons[0]


# ---------------------------------------------------------------------------
# Ordinary product questions must not engage the stage (verified defect D3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What is commercial performance versus target?",
        "Show commercial vehicles in the fleet",
        "How much warehouse space is free?",
        "Total housing allowance paid last year",
        "Who is the manufacturer of SKU-BETA?",
        "How many factories do we ship to?",
        "commercial team headcount by region",
        "office space utilisation this quarter",
    ],
)
def test_ordinary_business_nouns_do_not_engage_the_class_stage(question: str) -> None:
    """Each of these answers today and none names a property class.

    They engaged the stage on a bare "commercial", "housing" or "warehouse
    space" and then abstained, costing an answer that worked. A control that
    refuses correct work is a failure, not a win (R-0005).
    """
    assert parse_class_intent(question) == ((), ())


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("rental across SEA, commercial only", (("Commercial",), ())),
        (
            "lease revenue for commercial property, ignore residential",
            (("Commercial",), ("Residential",)),
        ),
        ("commercial only", (("Commercial",), ())),
        ("residential only", (("Residential",), ())),
        ("excluding residential", ((), ("Residential",))),
        ("total value for commercial property", (("Commercial",), ())),
        ("across all housing stock", (("Residential",), ())),
    ],
)
def test_the_epics_class_asks_still_parse(
    question: str, expected: tuple[tuple[str, ...], tuple[str, ...]]
) -> None:
    """The strict split costs coverage on bare nouns, never on a cued ask."""
    assert parse_class_intent(question) == expected


def test_a_bare_noun_ask_abstains_instead_of_binding_a_class(lake: Path) -> None:
    """Rule 10 again: the caller-visible effect of the strict split.

    "commercial vehicles" now leaves asset_class unbound, which lets the
    question be answered elsewhere, rather than binding COM over a fleet.
    """
    res = bind_asset_class(
        "show commercial vehicles in the fleet", warehouse=lake, tables=["assets"]
    )
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "names no asset class" in res.reasons[0]
