"""Proposed red-team regression tests (Plan C). A PROPOSAL, not a fix.

Every case in WRONG was served by the gate on main with a confident badge, and a verifier
re-derived the correct answer blind from the data (see
docs/subagents_findings/2026-10-05_redteam-gate-01.md). They are strict xfails: today they
fail, so the suite stays green; when a fix makes the gate abstain or answer correctly the
case XPASSes, strict=True turns the suite red, and whoever landed the fix removes the
marker. No fix can land silently.

CONTROLS are correct model SQL that main answers correctly. They must keep passing so a fix
cannot buy safety by refusing everything (a candidate fix refused 24 of 63 such answers, see
the report).

The model SQL is INJECTED (inferred, never observed from a model) through a stubbed Cortex;
Cortex-side enforcement and the F5 gate are not exercised (tests/redteam/harness.py,
"NOT EXERCISED"). The stub pins DuckDB TimeZone to Asia/Kuala_Lumpur (harness.SESSION_TZ):
TZ_SHIFT is silent on a UTC session, which is what CI runs, and the strict xfail would
XPASS. Full corpus (338 cases):
``python scripts/redteam_run.py --cases tests/redteam/corpus/a.yaml --family a --out <dir>
--run-id x``. Local run, unverified until Verify reruns it on the merge commit.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import harness
import pytest
import yaml

CORPUS = Path(__file__).parent / "corpus"

# case id -> (issue class, one-line difference from the verifier's blind derivation)
WRONG: dict[str, tuple[str, str]] = {
    "A-013": (
        "FANOUT",
        "AVG(risk_score) over suppliers x inventory rows weights suppliers by "
        "number of stock lines (0.3282) instead of",
    ),
    "A-021": (
        "FANOUT",
        "Self-join suppliers a JOIN b emits one row per matching partner with no "
        "DISTINCT: 8 rows with duplicates vs 5",
    ),
    "A-025": (
        "FANOUT",
        "AVG(capacity) over locations JOIN inventory weights each warehouse by "
        "its inventory row count (11 rows): 88181",
    ),
    "B-012": (
        "DROPPED_FILTER",
        "Served AVG(quantity_kg) over all 15 transactions (568.0) with no "
        "txn_type='outbound' filter; sales average is",
    ),
    "B-044": (
        "EMPTY_AGG_CONFIDENT",
        "COALESCE(AVG(...),0) turns an empty set for a nonexistent SKU into a "
        "fabricated average unit cost of 0.0 MYR.",
    ),
    "B-045": (
        "UNANSWERABLE_ANSWERED",
        "No customer or revenue data exists; served invents 'revenue' as outbound "
        "qty*unit_cost (a cost basis) grouped",
    ),
    "B-054": (
        "DROPPED_ROWS",
        "SUM(DISTINCT quantity_kg) collapses duplicate quantities (400 appears in "
        "T001 and T015): 5620 served vs correc",
    ),
    "B-057": (
        "OTHER",
        "Filter status = 'Delayed' (capital D) vs stored 'delayed': matches "
        "nothing, count 0 instead of 12.",
    ),
    "C-007": (
        "TZ_SHIFT",
        "CAST(ts AT TIME ZONE 'UTC' AS DATE) treats the naive ts as UTC and "
        "converts to KL date (+8h), pulling in Sep 2",
    ),
    "C-010": (
        "DROPPED_FILTER",
        "The past-30-days predicate is missing entirely: all-time outbound total.",
    ),
    "C-018": (
        "PERIOD_BOUNDARY",
        "expiry_date <= '2026-10-02' uses <= for 'before', adding SKU-ZETA which "
        "expires on the 2nd, not before it.",
    ),
    "C-020": (
        "WRONG_MEASURE",
        "date_part('day', age(...)) returns only the day-of-month remainder of "
        "the interval, not total elapsed days; NU",
    ),
    "C-022": (
        "STALE_OR_FIXED_PERIOD",
        "'last year' hard-coded to year(ts) = 2024 instead of 2025 (previous "
        "calendar year at 2026-10-02).",
    ),
    "C-039": (
        "UNANSWERABLE_ANSWERED",
        "Served counts status='delayed' (2) and drops the '>3 days late' "
        "condition; shipments has no date columns so da",
    ),
    "C-045": (
        "WRONG_GRAIN",
        "Served GROUP BY raw ts (timestamp) so it returns the single largest "
        "transaction (1500 kg at 2026-07-01 08:00);",
    ),
    "C-047": (
        "STALE_OR_FIXED_PERIOD",
        "year(ts)=2023 (three years back, 300 kg); two years before 2026-10-02 is 2024 = 41 kg.",
    ),
    "D-005": (
        "NULL_HANDLING",
        "NOT IN (subquery containing NULL sku from SH-106) is UNKNOWN for all "
        "rows; use NOT EXISTS. Served 0; truth 2 (",
    ),
    "D-012": (
        "UNANSWERABLE_ANSWERED",
        "sums RM + USD + SGD amounts as one number (4625.0) with no FX rate; only "
        "per-currency totals are supportable (",
    ),
    "D-014": (
        "EMPTY_AGG_CONFIDENT",
        "COALESCE(SUM(...),0) turns 'no parseable cost' into 0.0; the only WH-E "
        "shipment SH-105 has cost 'TBC' (unknown",
    ),
    "D-027": (
        "OTHER",
        "country = 'MY' misses 'MY ' and 'my' (2 vs 4); need upper(trim)",
    ),
    "D-028": (
        "NULL_HANDLING",
        "country IS NULL misses the blank '' country (SUP-08): 1 vs 2",
    ),
    "D-031": (
        "DROPPED_ROWS",
        "WHERE txn_type = 'outbound' is exact-case/exact-width; drops 'Outbound', "
        "'OUTBOUND ', 'outbound ' rows (T016,T",
    ),
    "D-047": (
        "TEXT_TYPE_COERCION",
        "MAX over a VARCHAR cost column picks the lexicographic max 'TBC' instead "
        "of parsing money (largest is RM 2,310",
    ),
    "D-060": (
        "PERIOD_BOUNDARY",
        "ts = '2026-07-01' only matches exactly midnight; T004 at 2026-07-01 "
        "08:00 is missed so 0 instead of 1.",
    ),
    "E-001": (
        "TIES_LIMIT",
        "ORDER BY quantity_kg DESC LIMIT 1 silently drops SKU-ETA which ties SKU- "
        "ALPHA at 3400; answer says 1 row.",
    ),
    "E-006": (
        "TIES_LIMIT",
        "LIMIT 1 on a 3-way tie: high, medium, low all have 2 alerts; served "
        "names 'high' as most common.",
    ),
    "E-008": (
        "TIES_LIMIT",
        "ORDER BY ts DESC LIMIT 1 drops T016 which has the identical latest timestamp as T015.",
    ),
    "E-025": (
        "DROPPED_ROWS",
        "Served counts only txn_type='inbound' (2); 'not outbound' also includes "
        "the 'adjustment' row (T016), so 3.",
    ),
    "E-029": (
        "WORDING_MISREAD",
        "Served uses quantity_kg > 900, dropping SKU-BETA at exactly 900; 'at "
        "least' is >=, so 6 SKUs.",
    ),
    "E-038": (
        "WRONG_MEASURE",
        "Served sums inventory quantity*unit_cost (stock value 45565); shipping "
        "spend is SUM(shipments.cost_myr) = 3870",
    ),
    "E-039": (
        "EMPTY_AGG_CONFIDENT",
        "Served filters country='Malaysia' (0); data stores ISO code 'MY' (3 suppliers).",
    ),
    "E-044": (
        "WRONG_GRAIN",
        "Served COUNT(*) of RAW inventory rows (3); question asks warehouses, "
        "i.e. COUNT(DISTINCT location_id) = 2 (WH-",
    ),
    "E-077": (
        "OTHER",
        "Served country <> 'Malaysia' but the column stores ISO codes (MY/SG/TH), "
        "so the predicate matches every row an",
    ),
}

CONTROLS: tuple[str, ...] = ("B-026", "C-033", "D-037", "E-002", "E-017", "E-065", "E-066", "E-081")

_BY_FAMILY: dict[str, list[str]] = {
    "a": ["A-013", "A-021", "A-025"],
    "b": ["B-012", "B-026", "B-044", "B-045", "B-049", "B-051", "B-054", "B-057"],
    "c": ["C-007", "C-010", "C-018", "C-020", "C-022", "C-033", "C-039", "C-045", "C-047"],
    "d": ["D-005", "D-012", "D-014", "D-027", "D-028", "D-031", "D-037", "D-047", "D-060"],
    "e": [
        "E-001",
        "E-002",
        "E-006",
        "E-008",
        "E-017",
        "E-025",
        "E-029",
        "E-038",
        "E-039",
        "E-044",
        "E-065",
        "E-066",
        "E-067",
        "E-069",
        "E-077",
        "E-081",
    ],
}


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Run only the selected cases, one fresh lake per family, strictly sequential."""
    root = tmp_path_factory.mktemp("rt_findings")
    out: dict[str, dict[str, Any]] = {}
    for fam, ids in sorted(_BY_FAMILY.items()):
        raw = yaml.safe_load((CORPUS / f"{fam}.yaml").read_text(encoding="utf-8"))
        keep = [c for c in raw if c["id"] in ids]
        assert {c["id"] for c in keep} == set(ids), f"corpus drift in family {fam}"
        (root / f"{fam}.yaml").write_text(yaml.safe_dump(keep, sort_keys=False), encoding="utf-8")
        shutil.copyfile(CORPUS / f"{fam}.ext.sql", root / f"{fam}.ext.sql")
        res = harness.run_cases(
            root / f"{fam}.yaml", out_dir=root / "out", run_id=f"findings-{fam}"
        )
        assert res.footer["network_attempts"] == [], res.footer
        assert all(res.footer["gold_pristine"].values()), res.footer
        for rec in res.records:
            out[rec["case_id"]] = rec
    return out


def _wrong_params() -> list[Any]:
    return [
        pytest.param(
            cid,
            id=f"{cid}-{issue}",
            marks=pytest.mark.xfail(strict=True, reason=f"{issue}: {diff}"),
        )
        for cid, (issue, diff) in WRONG.items()
    ]


@pytest.mark.parametrize("cid", _wrong_params())
def test_gate_does_not_serve_confident_wrong_answer(
    cid: str, served: dict[str, dict[str, Any]]
) -> None:
    rec = served[cid]
    env = rec.get("envelope") or {}
    verdict = rec["mechanical"]["verdict"]
    assert verdict != "HARNESS_ERROR", rec["mechanical"]["reasons"]
    # badge honesty: a confident badge on a wrong answer is the defect; an abstain or a
    # correct answer is the fix
    assert verdict != "WRONG", (
        f"served badge={env.get('badge')} route={env.get('route')}: "
        f"{rec['mechanical']['reasons'][:2]}"
    )


# SHEET-CERT-FILTER-01: were strict xfails. The same grounding check abstains
# on B-049 (SKU per category) and B-051 (percentage of total). strict=True
# would XPASS those two, so their markers come off with E-067 and E-069.
# Every other WRONG entry is unchanged.
_SHEET_FILTER_FIXED = (
    "B-049",
    "B-051",
    "E-067",
    "E-069",
)


@pytest.mark.parametrize(
    "cid",
    _SHEET_FILTER_FIXED,
    ids=[
        "B-049-WRONG_GRAIN",
        "B-051-WRONG_MEASURE",
        "E-067-DROPPED_FILTER",
        "E-069-WRONG_FILTER_POLARITY",
    ],
)
def test_sheet_filter_is_not_a_confident_wrong(
    cid: str, served: dict[str, dict[str, Any]]
) -> None:
    test_gate_does_not_serve_confident_wrong_answer(cid, served)
    env = served[cid].get("envelope") or {}
    named = " ".join(str(a) for a in (env.get("assumptions") or []))
    named = f"{named} {env.get('text') or ''}"
    assert env.get("badge") == "ABSTAIN" and env.get("abstained") is True
    assert "ungrounded_qualifier:" in named
    assert not env.get("rows")


@pytest.mark.parametrize("cid", CONTROLS)
def test_correct_sql_is_still_answered(cid: str, served: dict[str, dict[str, Any]]) -> None:
    rec = served[cid]
    assert rec["mechanical"]["verdict"] == "CORRECT", (
        f"over-abstention or wrong answer on correct SQL: {rec['mechanical']['reasons'][:2]} "
        f"badge={(rec.get('envelope') or {}).get('badge')}"
    )
