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
    assert parse_class_intent("commercial revenue, ignore residential") == (
        ("Commercial",),
        ("Residential",),
    )
    # A cue that governs something else does not reach across the sentence.
    assert parse_class_intent("ignore stale rows and total commercial sales") == (
        ("Commercial",),
        (),
    )
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
