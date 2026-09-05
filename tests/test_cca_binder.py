"""The CCA binder certifies against landed values, or abstains naming the gap.

Hard rule 12 lives here: a pack that proposes ``Malaysia`` against a column
encoded ``MY`` must still bind, and a pack whose members appear nowhere in the
data must not bind at all. Both directions are asserted, because only one of
them is the dangerous one - a filter that matches nothing still returns a
number.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.cca.binder import TermPack, certify_pack, norm_value, scan_landed_columns

SEA = TermPack(
    name="geo_region_members.sea",
    kind="geo region",
    column_names=("country", "country_code"),
    members={
        "Malaysia": ("MY", "MYS"),
        "Singapore": ("SG", "SGP"),
        "Thailand": ("TH", "THA"),
        "Brunei": ("BN", "BRN", "Brunei Darussalam"),
    },
)


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    db = tmp_path / "cca.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (country VARCHAR, amount DOUBLE)")
    con.execute(
        "INSERT INTO sales VALUES ('MY', 10.0), ('  singapore ', 20.0), "
        "('TH', 5.0), ('Japan', 99.0)"
    )
    con.execute("CREATE TABLE payroll (staff VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO payroll VALUES ('A', 1.0)")
    con.close()
    return db


def test_norm_value_folds_encoding_noise_not_content() -> None:
    assert norm_value("  Kuala   Lumpur ") == "kuala lumpur"
    assert norm_value("SKU-BETA") == "sku beta"
    assert norm_value("Côte d'Ivoire") == "cote d ivoire"
    # Content is content: two different places do not collapse together.
    assert norm_value("Malaysia") != norm_value("Micronesia")


def test_certifies_across_code_and_name_encodings(lake: Path) -> None:
    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=["sales"],
    )
    assert res.status == "CERTIFIED"
    # 'MY' and '  singapore ' are the same membership question in two encodings.
    assert set(res.matched) == {"Malaysia", "Singapore", "Thailand"}
    assert res.matched["Malaysia"] == ("MY",)
    # The filter carries the column's own spelling, not the pack's.
    assert "'MY'" in (res.binding_text() or "")
    assert "'Malaysia'" not in (res.binding_text() or "")


def test_absent_member_is_disclosed_not_dropped(lake: Path) -> None:
    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=["sales"],
    )
    assert res.absent == ("Brunei",)
    note = res.coverage_note()
    assert "3 of 4" in note
    assert "Brunei" in note
    assert "sales.country" in note


def test_unmatched_landed_values_are_reported(lake: Path) -> None:
    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=["sales"],
    )
    assert "Japan" in res.unmatched_sample
    assert "Japan" not in res.values


def test_abstains_when_no_column_carries_the_encoding(lake: Path) -> None:
    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=["payroll"],
    )
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "country" in res.reasons[0]
    assert "payroll" in res.reasons[0]


def test_abstains_when_column_exists_but_carries_no_member(tmp_path: Path) -> None:
    db = tmp_path / "eu.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (country VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO sales VALUES ('France', 1.0), ('Spain', 2.0)")
    con.close()
    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=db,
        tables=["sales"],
    )
    # The dangerous case: the filter would have parsed, executed and matched
    # nothing. It must not reach an answer as a number.
    assert res.status == "ABSTAIN"
    assert res.values == ()
    assert "no value matching" in res.reasons[0]
    assert "France" in res.unmatched_sample


def test_scan_never_opens_a_table_outside_the_grant(lake: Path) -> None:
    found = scan_landed_columns(lake, tables=["payroll"], column_names=("country",))
    assert found == []
    found = scan_landed_columns(lake, tables=["sales"], column_names=("country",))
    assert [c.ref for c in found] == ["sales.country"]


def test_no_granted_table_abstains_rather_than_scanning_everything(lake: Path) -> None:
    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=[],
    )
    assert res.status == "ABSTAIN"
    assert "no granted table" in res.reasons[0]


def test_unknown_stage_refuses(lake: Path) -> None:
    res = certify_pack(
        stage="not_a_stage",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=["sales"],
    )
    assert res.status == "REFUSE"


def test_constraint_shape_parses_under_cca_01(lake: Path) -> None:
    from dms_executor.constraint_cascade import parse_trace

    res = certify_pack(
        stage="geo",
        constraint_id="geo-1",
        candidate="SEA",
        pack=SEA,
        warehouse=lake,
        tables=["sales"],
    )
    # CCA-01 gates on prior stages, so a geo verdict only parses inside a trace
    # that carries the stages ahead of it. That gate is the point: geo cannot be
    # certified over an unresolved sense.
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

    with pytest.raises(Exception, match="must not be CERTIFIED"):
        parse_trace([{**prior[0], "status": "ABSTAIN"}, prior[1], res.to_constraint()])


def test_coverage_sentence_trims_the_list_but_never_the_count() -> None:
    """A 31-member taxonomy must not print 27 absent names into an answer.

    The count stays exact because that is the honesty-carrying part; the
    enumeration is what gets trimmed.
    """
    wide = TermPack(
        name="segment.wide",
        kind="segment",
        column_names=("segment",),
        members={f"M{i:02d}": () for i in range(20)},
    )
    res = certify_pack(
        stage="asset_class",
        constraint_id="c1",
        candidate="wide",
        pack=wide,
        warehouse=None,
        tables=["t"],
    )
    assert res.status == "ABSTAIN"

    from dms_executor.cca.binder import BinderResult

    certified = BinderResult(
        stage="asset_class",
        constraint_id="c1",
        candidate="wide",
        pack=wide.name,
        status="CERTIFIED",
        matched={"M00": ("M00",)},
        absent=tuple(f"M{i:02d}" for i in range(1, 20)),
        columns=("t.segment",),
        tables=("t",),
    )
    note = certified.coverage_note()
    assert "1 of 20" in note
    assert "and 13 more" in note
    assert "M19" not in note


def test_two_granted_columns_abstain_rather_than_naming_one_and_listing_both(
    tmp_path: Path,
) -> None:
    """The multi-column lie, found by an independent run.

    ``deals.country`` held MY and ``leads.nation`` held Singapore and Thailand.
    binding_text() names columns[0] and lists the union, so the predicate read
    ``deals.country IN ('MY', 'Thailand', 'Singapore')`` - it parses, it
    executes, and it matches a third of what it claims. Refusing costs an
    answer; certifying costs a plausible wrong number under a green badge.
    """
    db = tmp_path / "two.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (country VARCHAR)")
    con.execute("INSERT INTO deals VALUES ('MY')")
    con.execute("CREATE TABLE leads (country VARCHAR)")
    con.execute("INSERT INTO leads VALUES ('Singapore'), ('Thailand')")
    con.close()

    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=db,
        tables=["deals", "leads"],
    )
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "not decidable" in res.reasons[0]
    assert "deals.country" in res.reasons[0]
    assert "leads.country" in res.reasons[0]


def test_absent_says_not_matched_because_that_is_what_it_knows(tmp_path: Path) -> None:
    """A spelling the pack does not know is present, not absent.

    The sentence read "Not present in this data: Myanmar" over a column holding
    'Myanmar (Burma)'. Myanmar was present. This sentence can only speak for
    what the pack recognised, so it now says so, and it names the landed values
    the filter left out rather than reporting a clean cover over them.
    """
    db = tmp_path / "burma.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (country VARCHAR)")
    con.execute("INSERT INTO sales VALUES ('MY'), ('Myanmar (Burma)')")
    con.close()

    res = certify_pack(
        stage="geo",
        constraint_id="c1",
        candidate="SEA",
        pack=SEA,
        warehouse=db,
        tables=["sales"],
    )
    note = res.coverage_note()
    assert "Not matched in this data" in note
    assert "Not present in this data" not in note
    # The other half: the reader is told which landed value this filter drops.
    assert "left out of this filter" in note
    assert "Myanmar (Burma)" in note
