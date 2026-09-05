"""CCA-08 - an industry segment binds to the sub-segments the data actually carries.

The buyer's sentence is the specification: ask for agricultural, get plantation
*and* the animals, and be told which tables, which columns and which types of
business were counted. Two of these tests exist for the opposite reason - a
column of "Crop Insurance Services" must not become an agriculture bind, and a
Space with no segment encoding must abstain rather than answer. A false bind
here is not a missing feature, it is a wrong number wearing a green badge.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.cca.segment import (
    SEGMENT_PACKS,
    bind_segment,
    disclosure,
    included_business_types,
    propose_segment,
)


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    """A Space holding one segmented book, one F&B book and one table with no segment."""
    db = tmp_path / "segment.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE accounts (business_type VARCHAR, sales DOUBLE)")
    # Landed spellings a Malaysian book would really carry: a plant business, an
    # animal business, a farmed-water business, a forest business, and one row
    # that is none of those.
    con.execute(
        "INSERT INTO accounts VALUES ('Oil Palm Plantation', 120.0), "
        "('Poultry Farming', 40.0), ('Aquaculture', 25.0), ('Logging', 10.0), "
        "('Software Development', 900.0)"
    )
    con.execute("CREATE TABLE tenants (sector VARCHAR, sales DOUBLE)")
    con.execute(
        "INSERT INTO tenants VALUES ('Cafe', 5.0), ('Bakery', 7.0), "
        "('Catering Services', 3.0), ('Retail Pharmacy', 11.0)"
    )
    con.execute("CREATE TABLE payroll (staff VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO payroll VALUES ('A', 1.0)")
    con.close()
    return db


def test_surface_forms_map_to_one_canonical_segment() -> None:
    assert propose_segment("top sales in agricultural") == "agriculture"
    assert propose_segment("agri portfolio by state") == "agriculture"
    assert propose_segment("which F&B tenants pay the most") == "food_and_beverage"
    # "manufacturing" is strict, so it names the segment next to a cue. This
    # used to read "manufacturing revenue 2024", which no longer proposes:
    # "rank manufacturing suppliers by risk score" is the same shape and is a
    # supplier question, and one rule has to decide both.
    assert propose_segment("revenue by manufacturing segment") == "manufacturing"
    assert propose_segment("top sales last quarter") is None


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("top sales in agricultural across SEA", "agriculture"),
        ("SEA countries top sales in agricultural", "agriculture"),
        ("revenue by manufacturing segment", "manufacturing"),
    ],
)
def test_segment_asks_still_propose(question: str, expected: str) -> None:
    """The buyer's own sentences, in both word orders, plus a cued strict term.

    The first two are the epic's headline ask: "agricultural" is plain, so it
    proposes wherever it sits in the sentence. The third is the strict path -
    "segment" one token after "manufacturing" is what makes it a filter.
    """
    assert propose_segment(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Which farms are late on delivery?",
        "Rank manufacturing suppliers by risk score",
        "How many factories do we ship to?",
        "Who is the manufacturer of SKU-BETA?",
    ],
)
def test_counterparty_and_site_nouns_are_not_a_segment_ask(question: str) -> None:
    """A delivery question, a supplier ranking, a site count and a lookup.

    None of these asks for an industry slice of the book; each merely contains a
    word that can name one, and a verification run watched all four engage the
    cascade and abstain. Refusing work the product answers today is a failure
    of the control, not a demonstration of it (R-0005).
    """
    assert propose_segment(question) is None


@pytest.mark.parametrize(
    ("singular", "plural"),
    [
        ("sales in farm only", "sales in farms only"),
        ("sales in factory only", "sales in factories only"),
        ("revenue by manufacturer segment", "revenue by manufacturers segment"),
        ("sales in plantation only", "sales in plantations only"),
    ],
)
def test_singular_and_plural_read_the_same(singular: str, plural: str) -> None:
    """The list carried "farms" and "factories" without their singulars.

    A lexicon that recognises one number and not the other is unaudited rather
    than deliberate, and the buyer sees it as the same question working or not
    working depending on how they wrote the noun.
    """
    assert propose_segment(singular) == propose_segment(plural) is not None


def test_agriculture_binds_plants_and_animals_together(lake: Path) -> None:
    res = bind_segment(
        "SEA countries top sales in agricultural", warehouse=lake, tables=["accounts"]
    )
    assert res.status == "CERTIFIED"
    included = set(included_business_types(res))
    # The buyer's requirement in one assertion: a plant business and an animal
    # business are both agriculture, and neither was dropped.
    assert "Oil Palm" in included
    assert "Poultry" in included
    assert {"Aquaculture", "Logging and Timber"} <= included
    # The filter carries the column's own spellings, not the pack's labels.
    binding = res.binding_text() or ""
    assert "accounts.business_type IN (" in binding
    assert "'Oil Palm Plantation'" in binding
    assert "'Poultry Farming'" in binding
    assert "'Software Development'" not in binding
    assert "Software Development" in res.unmatched_sample


def test_partial_coverage_is_disclosed_not_hidden(lake: Path) -> None:
    res = bind_segment("agriculture top sales", warehouse=lake, tables=["accounts"])
    # Sub-segments the pack proposes and this data does not carry are named, so
    # nobody reads "agriculture" as "all of agriculture".
    assert "Rubber" in res.absent
    assert "Dairy" in res.absent
    assert "Oil Palm" not in res.absent

    note = disclosure(res)
    assert "accounts" in note
    assert "accounts.business_type" in note
    for member in ("Oil Palm", "Poultry", "Aquaculture", "Logging and Timber"):
        assert member in note
    # binder.coverage_note now says "Not matched", not "Not present": a column
    # carrying "Myanmar (Burma)" was reporting Myanmar as absent when it was
    # there and merely unrecognised. The disclosure still has to name it.
    assert "Not matched in this data" in note
    assert "Rubber" in note
    # What the column carried but the segment excluded is stated too.
    assert "Software Development" in note


def test_included_business_types_is_stable_across_scan_order(tmp_path: Path) -> None:
    """Pack order, not the order DuckDB happened to return distinct values in."""
    expected = ["Oil Palm", "Poultry", "Aquaculture", "Logging and Timber"]
    for name, rows in (
        ("a", "('Oil Palm Plantation',),('Poultry Farming',),('Aquaculture',),('Logging',)"),
        ("b", "('Logging',),('Aquaculture',),('Poultry Farming',),('Oil Palm Plantation',)"),
    ):
        db = tmp_path / f"{name}.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE book (industry VARCHAR)")
        con.execute(f"INSERT INTO book VALUES {rows}")
        con.close()
        res = bind_segment("agricultural book", warehouse=db, tables=["book"])
        assert res.status == "CERTIFIED"
        assert included_business_types(res) == expected


def test_lookalike_finance_rows_are_not_agriculture(tmp_path: Path) -> None:
    """Precision: the word is not the industry. Substring matching would bind both."""
    db = tmp_path / "bank.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE book (business_type VARCHAR, sales DOUBLE)")
    con.execute(
        "INSERT INTO book VALUES ('Crop Insurance Services', 50.0), "
        "('Agricultural Bank Loans', 70.0)"
    )
    con.close()
    res = bind_segment("top sales in agricultural", warehouse=db, tables=["book"])
    assert res.status == "ABSTAIN"
    assert res.values == ()
    assert res.binding_text() is None
    assert "no value matching" in res.reasons[0]
    # The honest outcome: a steward sees the encoding and decides, deliberately,
    # whether either of these belongs in the pack. The matcher does not guess.
    assert "Crop Insurance Services" in res.unmatched_sample
    assert "Agricultural Bank Loans" in res.unmatched_sample


def test_abstains_when_the_grant_carries_no_segment_column(lake: Path) -> None:
    res = bind_segment("agricultural top sales", warehouse=lake, tables=["payroll"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "industry segment encoding" in res.reasons[0]
    assert "business_type" in res.reasons[0]
    assert "payroll" in res.reasons[0]


def test_abstains_when_the_question_names_no_segment(lake: Path) -> None:
    res = bind_segment("what were the top sales last quarter", warehouse=lake, tables=["accounts"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "names no industry segment" in res.reasons[0]
    assert included_business_types(res) == []
    assert "not bound" in disclosure(res)


def test_a_second_pack_binds_so_the_module_is_not_agriculture_shaped(lake: Path) -> None:
    res = bind_segment("which F&B tenants had top sales", warehouse=lake, tables=["tenants"])
    assert res.status == "CERTIFIED"
    assert res.pack == SEGMENT_PACKS["food_and_beverage"].name
    assert included_business_types(res) == ["Cafe", "Catering", "Bakery"]
    assert "'Catering Services'" in (res.binding_text() or "")
    assert "Retail Pharmacy" in res.unmatched_sample
    assert "tenants.sector" in disclosure(res)


def test_constraint_parses_under_cca_01_after_a_certified_sense(lake: Path) -> None:
    from dms_executor.constraint_cascade import parse_trace

    res = bind_segment(
        "top sales in agricultural", warehouse=lake, tables=["accounts"], constraint_id="segment-1"
    )
    sense = {
        "constraint_id": "sense-0",
        "type": "sense",
        "candidate": "top sales in agricultural",
        "binding": None,
        "evidence": [],
        "status": "CERTIFIED",
        "reasons": [],
    }
    parsed = parse_trace([sense, res.to_constraint()])
    # A segment verdict is the asset_class stage: what class of thing a row is.
    assert parsed[-1]["type"] == "asset_class"
    assert parsed[-1]["status"] == "CERTIFIED"
    assert "accounts.business_type" in (parsed[-1]["binding"] or "")

    # And it cannot outrank an unresolved sense.
    with pytest.raises(Exception, match="must not be CERTIFIED"):
        parse_trace([{**sense, "status": "ABSTAIN"}, res.to_constraint()])
