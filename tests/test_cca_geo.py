"""SEA is bound to landed countries or it is not answered at all.

The failure under test is a confident regional total. An ask that says "across
SEA" invites a model to supply eleven countries from memory; this suite asserts
that the eleven are only ever a proposal, that the filter is written in the
column's own encoding, that the members the warehouse does not carry are said
out loud, and that a region nothing in the data evidences abstains instead of
returning a number.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.cca.geo import (
    GEO_REGION_MEMBERS,
    SEA_PACK,
    bind_geo,
    propose_countries,
    propose_region,
)

# The membership this repo claims, written out separately from the pack so a
# later edit to either one has to face the other. Basis: the eleven sovereign
# states of Southeast Asia, which is ASEAN's membership since Timor-Leste
# acceded on 26 October 2025.
ELEVEN = (
    "Brunei",
    "Cambodia",
    "Indonesia",
    "Laos",
    "Malaysia",
    "Myanmar",
    "Philippines",
    "Singapore",
    "Thailand",
    "Timor-Leste",
    "Vietnam",
)

ASK = "top sales across SEA"


@pytest.fixture()
def iso2_lake(tmp_path: Path) -> Path:
    """Three SEA countries as ISO alpha-2, plus two countries outside the region."""
    db = tmp_path / "iso2.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (country VARCHAR, amount DOUBLE)")
    con.execute(
        "INSERT INTO sales VALUES ('MY', 10.0), ('SG', 20.0), ('TH', 5.0), "
        "('Japan', 99.0), ('Australia', 7.0)"
    )
    con.execute("CREATE TABLE payroll (staff VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO payroll VALUES ('A', 1.0)")
    con.close()
    return db


@pytest.fixture()
def mixed_lake(tmp_path: Path) -> Path:
    """One column, three encodings: a full name, an ISO code and an alternate spelling."""
    db = tmp_path / "mixed.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE orders (country_name VARCHAR, amount DOUBLE)")
    con.execute(
        "INSERT INTO orders VALUES ('Malaysia', 1.0), ('SG', 2.0), ('Viet Nam', 3.0)"
    )
    con.close()
    return db


@pytest.fixture()
def europe_lake(tmp_path: Path) -> Path:
    db = tmp_path / "eu.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (country VARCHAR, amount DOUBLE)")
    con.execute(
        "INSERT INTO sales VALUES ('France', 1.0), ('Spain', 2.0), ('Germany', 3.0)"
    )
    con.close()
    return db


def test_certifies_against_iso2_encoding_and_filters_in_that_encoding(
    iso2_lake: Path,
) -> None:
    res = bind_geo(ASK, warehouse=iso2_lake, tables=["sales"])
    assert res.status == "CERTIFIED"
    assert set(res.matched) == {"Malaysia", "Singapore", "Thailand"}
    binding = res.binding_text() or ""
    # Hard rule 12: the filter carries what the column holds. 'Malaysia' against
    # a column of ISO codes parses, executes and matches nothing.
    assert "'MY'" in binding
    assert "'Malaysia'" not in binding
    assert res.values == ("MY", "SG", "TH")


def test_mixed_encodings_in_one_column_bind_to_canonical_members(
    mixed_lake: Path,
) -> None:
    res = bind_geo(ASK, warehouse=mixed_lake, tables=["orders"])
    assert res.status == "CERTIFIED"
    assert set(res.matched) == {"Malaysia", "Singapore", "Vietnam"}
    assert res.matched["Vietnam"] == ("Viet Nam",)
    assert res.matched["Singapore"] == ("SG",)
    assert res.columns == ("orders.country_name",)


def test_partial_coverage_is_disclosed_not_rounded_up(iso2_lake: Path) -> None:
    res = bind_geo(ASK, warehouse=iso2_lake, tables=["sales"])
    # Three of eleven landed. An answer that called this "SEA" without saying so
    # is the failure this epic exists to prevent.
    assert set(res.absent) == set(ELEVEN) - {"Malaysia", "Singapore", "Thailand"}
    note = res.coverage_note()
    # The count is the honesty-carrying part and is never trimmed. The sentence
    # names the first few absent members and says how many more it did not list,
    # because a reader who stops halfway through eight country names has learned
    # nothing; res.absent stays complete for anyone who needs all of them.
    assert "3 of 11" in note
    # Asserted on the disclosure, not on the wording that carries it: the binder
    # owns that sentence and has already reworded it once ("not present" claimed
    # more than the data supports, since a spelling the pack does not know is
    # present and unmatched). What must not change is that the members left out
    # are named in the sentence the buyer reads.
    assert "in this data: Brunei" in note
    assert "and 2 more" in note
    named = [m for m in res.absent if m in note]
    assert len(named) == 6


def test_non_member_countries_are_excluded_and_reported(iso2_lake: Path) -> None:
    res = bind_geo(ASK, warehouse=iso2_lake, tables=["sales"])
    assert "Japan" not in res.values
    assert "Australia" not in res.values
    # Reported rather than dropped, so a steward can see the encoding in use and
    # widen the pack deliberately if the region was wrong.
    assert "Japan" in res.unmatched_sample
    assert "Australia" in res.unmatched_sample


def test_abstains_when_the_grant_carries_no_country_column(iso2_lake: Path) -> None:
    res = bind_geo(ASK, warehouse=iso2_lake, tables=["payroll"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "country" in res.reasons[0]
    assert "payroll" in res.reasons[0]


def test_abstains_on_a_european_book_rather_than_matching_zero_rows(
    europe_lake: Path,
) -> None:
    res = bind_geo(ASK, warehouse=europe_lake, tables=["sales"])
    assert res.status == "ABSTAIN"
    assert res.values == ()
    assert res.binding_text() is None
    assert "membership was proposed, never landed" in res.reasons[0]
    assert "France" in res.unmatched_sample


def test_abstains_when_the_ask_names_no_region(iso2_lake: Path) -> None:
    res = bind_geo("top sales last quarter", warehouse=iso2_lake, tables=["sales"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "no region" in res.reasons[0]


def test_propose_region_reads_the_spellings_it_documents() -> None:
    for ask in (
        "top sales across SEA",
        "revenue in South East Asia",
        "southeast asia margin",
        "S.E. Asia headcount",
    ):
        assert propose_region(ask) == "sea", ask
    assert propose_region("asean revenue") == "asean"
    assert propose_region("ASEAN revenue") == "asean"
    # Lowercase "sea" is the ocean far more often than the region, and a region
    # invented from a shipping question would attach a country filter nobody
    # asked for.
    assert propose_region("sea freight cost by lane") is None
    assert propose_region("overseas revenue") is None
    assert propose_region("top sales last quarter") is None


def test_pack_membership_is_exactly_the_eleven_documented_states() -> None:
    # A silent edit to the membership goes red here rather than in a customer's
    # regional total.
    assert tuple(SEA_PACK.members) == ELEVEN
    assert len(ELEVEN) == 11
    # Every member is reachable by ISO alpha-2 and alpha-3, since those are the
    # encodings a warehouse most often lands.
    index = SEA_PACK.alias_index()
    for iso2, iso3, member in (
        ("BN", "BRN", "Brunei"),
        ("KH", "KHM", "Cambodia"),
        ("ID", "IDN", "Indonesia"),
        ("LA", "LAO", "Laos"),
        ("MY", "MYS", "Malaysia"),
        ("MM", "MMR", "Myanmar"),
        ("PH", "PHL", "Philippines"),
        ("SG", "SGP", "Singapore"),
        ("TH", "THA", "Thailand"),
        ("TL", "TLS", "Timor-Leste"),
        ("VN", "VNM", "Vietnam"),
    ):
        assert index[iso2.casefold()] == member
        assert index[iso3.casefold()] == member
    # Alternate names a source system may spell.
    for alias, member in (
        ("east timor", "Timor-Leste"),
        ("burma", "Myanmar"),
        ("lao pdr", "Laos"),
        ("viet nam", "Vietnam"),
        ("the philippines", "Philippines"),
        ("brunei darussalam", "Brunei"),
    ):
        assert index[alias] == member
    # SEA and ASEAN denote the same eleven states on today's membership, so they
    # are one pack under two keys, not two lists that can drift apart.
    assert GEO_REGION_MEMBERS["asean"] is GEO_REGION_MEMBERS["sea"]


def test_constraint_parses_as_stage_two_under_certified_priors(iso2_lake: Path) -> None:
    from dms_executor.constraint_cascade import parse_trace

    res = bind_geo(ASK, warehouse=iso2_lake, tables=["sales"], constraint_id="geo-1")
    prior = [
        {
            "constraint_id": f"{stage}-0",
            "type": stage,
            "candidate": "n/a",
            "binding": None,
            "evidence": [],
            "status": "CERTIFIED",
            "reasons": [],
        }
        for stage in ("sense", "asset_class")
    ]
    parsed = parse_trace([*prior, res.to_constraint()])
    assert parsed[-1]["type"] == "geo"
    assert parsed[-1]["status"] == "CERTIFIED"
    assert "'MY'" in (parsed[-1]["binding"] or "")

    # Geo cannot certify over an unresolved asset class: the cascade gates it.
    with pytest.raises(Exception, match="must not be CERTIFIED"):
        parse_trace(
            [prior[0], {**prior[1], "status": "ABSTAIN"}, res.to_constraint()]
        )


# --------------------------------------------------------------------------
# Four defects found by independent verification runs. Each test states the
# customer-visible outcome, not the internal step that produced it.
# --------------------------------------------------------------------------


@pytest.fixture()
def market_lake(tmp_path: Path) -> Path:
    """A "market" column holding US city codes. 'LA' is also the ISO code for Laos."""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (market VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO deals VALUES ('LA', 10.0), ('NY', 20.0), ('SF', 30.0)")
    con.close()
    return db


def test_a_market_column_of_city_codes_does_not_certify_a_region(
    market_lake: Path,
) -> None:
    """D1. 'LA' is Los Angeles here, and the customer was told it was Laos."""
    res = bind_geo("total sales across SEA", warehouse=market_lake, tables=["deals"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert res.matched == {}
    assert "Laos" not in res.coverage_note()
    # The abstention names the encodings a steward could land, and "market" is
    # no longer one of them.
    assert "market" not in res.reasons[0]
    assert "country" in res.reasons[0]


def test_bare_sea_names_the_region_only_next_to_a_cue() -> None:
    """D2. 'Show SEA freight cost' is a shipping question, not eleven countries."""
    assert propose_region("Show SEA freight cost") is None
    # Engaging and then abstaining is the same outage to the customer, so the
    # stage has to stay out of this ask entirely.
    assert propose_region("SEA freight rates by lane") is None
    # And the asks that do mean the region still mean it.
    assert propose_region("top sales across SEA") == "sea"
    assert propose_region("SEA countries top sales in agricultural") == "sea"
    assert propose_region("revenue in SEA") == "sea"
    assert propose_region("SEA markets by revenue") == "sea"
    # Spelled out, it needs no cue: nobody writes "Southeast Asia" about a sea.
    assert propose_region("Southeast Asia freight cost") == "sea"
    # ASEAN collides with no English word, so it stays plain too.
    assert propose_region("ASEAN freight cost") == "asean"


def test_a_named_country_binds_in_the_columns_own_spelling(iso2_lake: Path) -> None:
    """D3. The commonest geo ask in this domain used to derive no filter at all."""
    res = bind_geo(
        "rental in Malaysia, commercial only", warehouse=iso2_lake, tables=["sales"]
    )
    assert res.status == "CERTIFIED"
    assert set(res.matched) == {"Malaysia"}
    binding = res.binding_text() or ""
    assert "'MY'" in binding
    assert "'SG'" not in binding
    assert "'Malaysia'" not in binding
    assert propose_countries("rental in Malaysia, commercial only") == ("Malaysia",)


def test_iso_codes_name_a_country_only_when_written_as_codes() -> None:
    """Two-letter tokens are noise: 'my' is a pronoun far more often than Malaysia."""
    assert propose_countries("total sales in MY") == ("Malaysia",)
    assert propose_countries("rental in my region") == ()
    assert propose_countries("my top sales this quarter") == ()
    assert propose_countries("MY headcount") == ()
    # Full names need no cue.
    assert propose_countries("Viet Nam revenue by month") == ("Vietnam",)


def test_a_country_outside_the_pack_binds_nothing(iso2_lake: Path) -> None:
    """Japan is not in the pack, so no member is proposed and nothing is bound."""
    assert propose_countries("sales in Japan") == ()
    res = bind_geo("sales in Japan", warehouse=iso2_lake, tables=["sales"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "Japan" in res.reasons[0]


def test_a_region_that_excludes_the_country_asked_about_is_refused(
    iso2_lake: Path,
) -> None:
    """No column a steward lands makes Japan a member of SEA."""
    res = bind_geo("top sales across SEA in Japan", warehouse=iso2_lake, tables=["sales"])
    assert res.status == "REFUSE"
    assert res.binding_text() is None
    assert "Japan" in res.reasons[0]
    assert "SEA" in res.reasons[0]


def test_a_geo_exclusion_abstains_rather_than_binding_the_excluded_country(
    iso2_lake: Path,
) -> None:
    """D4. The ask removed Singapore; the binding included it and called it covered."""
    res = bind_geo(
        "rental across SEA excluding Singapore", warehouse=iso2_lake, tables=["sales"]
    )
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert res.matched == {}
    # The sentence the buyer reads says the region was not bound, and never
    # names Singapore as covered.
    note = res.coverage_note()
    assert note.startswith("not Singapore: not bound.")
    reason = res.reasons[0]
    assert "Singapore" in reason
    assert "does not constrain the executed query" in reason


@pytest.mark.parametrize(
    "question",
    [
        "rental across SEA excluding Singapore",
        "sales in SEA, ignore Thailand",
        "revenue in Malaysia without Singapore",
        "top sales across SEA, not Vietnam",
    ],
)
def test_every_shape_of_geo_exclusion_fails_closed(
    question: str, iso2_lake: Path
) -> None:
    res = bind_geo(question, warehouse=iso2_lake, tables=["sales"])
    assert res.status == "ABSTAIN", question
    assert res.binding_text() is None, question
    assert "exclusion cannot be certified" in res.reasons[0], question
