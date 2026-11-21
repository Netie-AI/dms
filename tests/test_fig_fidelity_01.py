"""FIG-FIDELITY-01 (dms#508): the figure the customer reads is the figure the query returned.

Rules 10 and 10a: every clause is asserted on the envelope from
``POST /v1/chat/ask`` (rendered text, rows, values, badge) and through
``assert_envelope_valid``. The verified-query and generated renderers are
asserted twice: on the envelope their function returns, and through the route.

The class under test: a figure the answer states is never served rewritten.
A mask that rewrites one withholds the answer; the mask itself is never undone
(dms#318, masking only widens). E14 fails for a rendered figure that is not in
the returned rows. PII must still be masked (A4).
"""

# ruff: noqa: F811  (warehouse and minter are fixtures imported from the pack tests)
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import QueryResult
from dms_core.pii import mask_payload
from dms_executor.demo_pack import SHIPMENT_COST_Q, SHIPMENT_COST_SQL, SPEND_BY_COUNTRY_SQL
from dms_executor.envelope import assert_envelope_valid, figure_fidelity_violations
from dms_executor.manifest import ManifestMinter
from test_demo_pack_followup import (  # noqa: F401  (warehouse and minter are fixtures)
    FINANCE,
    WAREHOUSE_OPS,
    _live_client,
    _PackCortex,
    minter,
    warehouse,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fig_fidelity"

# The three floats of the live run on 2026-11-06, then 17 plain ones: 20 rows.
NOISY = (1610511.0500000012, 1578918.4499999997, 1517620.660000002)
COSTS = [*NOISY, *(1000.5 + i for i in range(17))]
ROWS = [{"location_code": f"WH-{i:02d}", "total_cost_myr": c} for i, c in enumerate(COSTS)]

LISTED = re.compile(r"^\s+- (.+)$", re.M)


def _close(a: float, b: float) -> bool:
    """The tolerance of ``envelope._close``, restated so the test owns its oracle."""
    return abs(a - b) < 0.011 or abs(a - b) / max(abs(b), 1e-9) < 1e-4


@dataclass
class _FixedPack(_PackCortex):
    """The pack metric's SQL submit returns exactly these rows."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_fig",
                output={"rows": copy.deepcopy(self.rows)},
            )
        return QueryResult(ok=True, status="bound", run_id="run_fig_bind")


@dataclass
class _GenCortex(_PackCortex):
    """The model writes valid SQL; Cortex submit returns exactly these rows."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        del question, kwargs
        return {
            "phase": "generate",
            "query_sql": _GEN_SQL,
            "served_model": "fake-model",
            "served_provider": "fake-provider",
            "ov_key_id": "ovk-fake",
        }

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_fig_bind")
        return QueryResult(
            ok=True, status="ok", run_id="run_fig_gen", output={"rows": copy.deepcopy(self.rows)}
        )


@dataclass
class _ProseCortex:
    """Pack miss: Cortex answers with its own text over its own rows."""

    answer: str
    rows: list[dict[str, Any]]
    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(ok=False, status="rejected", run_id="run_miss")
        return QueryResult(ok=True, status="bound", run_id="run_bind")

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        return LedgerAppendResponse(entry_id="led_fig", hash="led_fig")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer=self.answer,
            badge="certified",
            sql_used=SPEND_BY_COUNTRY_SQL,
            rows=copy.deepcopy(self.rows),
            assumptions="cq_spend_by_country",
            audit_id="aud_fig_prose",
            route="certified_metric",
            contributing_sources=[
                {
                    "ref_id": "src_inv",
                    "container": "inventory",
                    "member": "category",
                    "kind": "sql",
                    "row_count": 3,
                    "contribution": 1.0,
                }
            ],
            drillthrough_token="dt_fig_prose",
        )


def _ask_l1(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    cortex = _FixedPack(warehouse=warehouse, rows=rows)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask",
        json={"question": SHIPMENT_COST_Q, "space_id": WAREHOUSE_OPS, "session_id": "ses_fig"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ask_prose(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    client = _live_client(warehouse, minter, monkeypatch, _ProseCortex(answer=answer, rows=rows))
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "Show total spend by supplier country from the lake",
            "space_id": FINANCE,
            "session_id": "ses_fig_prose",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _listed_pairs(text: str) -> list[dict[str, str]]:
    return [
        dict(part.split("=", 1) for part in line.split(", "))
        for line in LISTED.findall(text)
    ]


def _served(env: dict[str, Any]) -> bool:
    return env["abstained"] is False and env["badge"] != "ABSTAIN"


def _no_corrupt_figure_served(env: dict[str, Any]) -> None:
    """The P0: a served answer never shows a mask where the rows hold a number."""
    if not _served(env):
        assert env["rows"] == [] and env["values"] == []
        return
    assert_envelope_valid(env)
    assert not re.search(r"\d\.?DMSMASK_|DMSMASK_[a-z]+_\d+\.\d", env["text"]), env["text"]


# --- A1, A2, A3: the listing door, through HTTP --------------------------------


def test_a1_listing_shows_the_figures_the_rows_hold(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask_l1(warehouse, minter, monkeypatch, ROWS)
    assert_envelope_valid(env)
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["rows"] == ROWS
    assert env["sql_used"] == SHIPMENT_COST_SQL
    text = env["text"]
    assert "DMSMASK_" not in text, text
    assert "total_cost_myr=1610511.05\n" in text
    assert "total_cost_myr=1578918.45\n" in text
    assert "total_cost_myr=1517620.66\n" in text
    assert "0500000012" not in text and "4499999997" not in text


def test_a2_a_cut_listing_says_how_many_it_shows(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask_l1(warehouse, minter, monkeypatch, ROWS)
    assert_envelope_valid(env)
    assert len(env["rows"]) == 20
    assert env["text"].startswith("Found 20 row(s). Showing 12 of 20.\n"), env["text"]
    assert len(LISTED.findall(env["text"])) == 12


def test_a2_a_whole_listing_does_not_claim_a_cut(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask_l1(warehouse, minter, monkeypatch, ROWS[:5])
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["text"].startswith("Found 5 row(s).\n"), env["text"]
    assert "Showing" not in env["text"]
    assert len(LISTED.findall(env["text"])) == 5


def test_a3_every_listed_number_round_trips_to_its_cell(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask_l1(warehouse, minter, monkeypatch, ROWS)
    pairs = _listed_pairs(env["text"])
    assert len(pairs) == 12
    for shown, row in zip(pairs, env["rows"], strict=False):
        assert shown["location_code"] == row["location_code"]
        assert _close(float(shown["total_cost_myr"]), row["total_cost_myr"]), (shown, row)


# Served: the listing prints the figure without float noise, so no person
# pattern sees it. Withheld: the figure's own digits still read as a phone or
# card, the mask takes them, and the answer abstains instead of showing a mask.
# Widening the scanner to keep these is PR-a in dms#508 and needs the founder.
OUT_SERVED, OUT_WITHHELD = "served", "withheld"


@pytest.mark.parametrize(
    ("value", "outcome"),
    [
        (1610511.0500000012, OUT_SERVED),  # MY landline in the fraction
        (1578918.4499999997, OUT_SERVED),  # NANP in the fraction
        (234567.8901, OUT_WITHHELD),  # NANP across the decimal point
        (12.3456789012, OUT_WITHHELD),  # MY mobile shape in the fraction
        (-1610511.0500000012, OUT_SERVED),
        (0.30000000000000004, OUT_SERVED),
        (3.141592653589793, OUT_SERVED),
        (900101.145678, OUT_SERVED),  # IC shape across the point
        (4111.111111111111, OUT_WITHHELD),  # card-like run in the fraction
        (1e-07, OUT_SERVED),
        (1.5e16, OUT_SERVED),
    ],
)
def test_a3_a_non_integral_figure_is_shown_exactly_or_withheld(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    value: float,
    outcome: str,
) -> None:
    rows = [{"location_code": "WH-A", "total_cost_myr": value}]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    assert "DMSMASK_" not in env["text"], env["text"]
    if outcome == OUT_WITHHELD:
        assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
        assert env["rows"] == [] and env["values"] == []
        assert any("mask rewrote a figure" in a for a in env["assumptions"]), env["assumptions"]
        return
    assert env["abstained"] is False, env["text"]
    assert env["rows"] == rows
    (shown,) = _listed_pairs(env["text"])
    assert _close(float(shown["total_cost_myr"]), value), env["text"]


# --- the prose door: Cortex writes the text ------------------------------------


def test_cortex_prose_with_a_raw_float_is_withheld_not_corrupted(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cortex prints the raw float. Its fraction reads as a MY landline, the mask
    takes it, and the answer abstains. Serving it unmasked is PR-a (founder yes)."""
    rows = [{"country": "MY", "total_spend_myr": 1610511.0500000012}]
    env = _ask_prose(
        warehouse,
        minter,
        monkeypatch,
        "Total spend for MY is 1610511.0500000012 MYR (1,610,511.05).",
        rows,
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert env["rows"] == [] and env["values"] == []
    assert "DMSMASK_" not in env["text"], env["text"]
    assert "1610511" not in env["text"] and "1,610,511" not in env["text"], env["text"]
    assert any("mask rewrote a figure" in a for a in env["assumptions"]), env["assumptions"]


def test_cortex_listing_with_a_wrong_integer_is_withheld(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E14 (b): bare integers count. E4 never looked at them."""
    rows = [{"country": "MY", "orders": 7}, {"country": "SG", "orders": 3}]
    env = _ask_prose(
        warehouse, minter, monkeypatch, "country=MY, orders=8\ncountry=SG, orders=3", rows
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert env["rows"] == [] and env["values"] == []
    assert "orders=8" not in env["text"]
    assert any("E14" in a for a in env["assumptions"]), env["assumptions"]


def test_cortex_listing_with_the_right_integers_is_served(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"country": "MY", "orders": 7}, {"country": "SG", "orders": 3}]
    env = _ask_prose(
        warehouse, minter, monkeypatch, "country=MY, orders=7\ncountry=SG, orders=3", rows
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False, env["text"]
    assert env["rows"] == rows
    assert "orders=7" in env["text"]


def test_cortex_found_count_that_is_not_the_row_count_is_withheld(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"country": "MY", "orders": 7}, {"country": "SG", "orders": 3}]
    env = _ask_prose(
        warehouse, minter, monkeypatch, "Found 3 row(s).\n  - country=MY, orders=7", rows
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True, env["text"]
    assert any("E14" in a for a in env["assumptions"]), env["assumptions"]


# --- a figure the masker still takes is withheld, never shown as a mask --------


@pytest.mark.parametrize("value", [5552345678, 60123456789.0])
def test_an_integer_figure_with_a_person_shape_is_withheld_not_corrupted(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, value: float
) -> None:
    """The masker keeps every integer shape (A4). The answer then abstains."""
    env = _ask_l1(warehouse, minter, monkeypatch, [{"location_code": "WH-A", "ref_total": value}])
    _no_corrupt_figure_served(env)
    assert str(int(value)) not in env["text"]
    assert env["abstained"] is True, env["text"]
    assert any("E14" in a for a in env["assumptions"]), env["assumptions"]


def test_an_integer_the_rows_mask_too_is_served_with_one_token(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IC-shaped integer is masked in the row and in the text alike. Not a corruption."""
    rows = [{"location_code": "WH-A", "ref_total": 900101145678}]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    assert "900101145678" not in json.dumps(env)
    assert env["abstained"] is False, env["text"]
    token = env["rows"][0]["ref_total"]
    assert token.startswith("DMSMASK_nric_") and f"ref_total={token}" in env["text"]


def test_cortex_prose_integer_taken_by_the_masker_is_withheld(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"country": "MY", "total_units": 5552345678}]
    env = _ask_prose(warehouse, minter, monkeypatch, "The total for MY is 5552345678.", rows)
    assert_envelope_valid(env)
    assert "5552345678" not in env["text"]
    assert env["abstained"] is True, env["text"]
    assert any("E14" in a for a in env["assumptions"]), env["assumptions"]


# --- A4: privacy must hold ------------------------------------------------------

PHONES = ("03-5555 0101", "Tel.0355550101", "+60 12-345 6789")


@pytest.mark.parametrize("phone", PHONES)
def test_a4_a_phone_in_a_listed_cell_is_still_masked(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, phone: str
) -> None:
    rows = [
        {"location_code": "WH-A", "handler_note": phone, "total_cost_myr": 1610511.0500000012},
        {"location_code": "WH-B", "handler_note": "none", "total_cost_myr": 12.5},
    ]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    digits = re.sub(r"\D", "", phone)
    blob = json.dumps(env)
    assert phone not in blob
    assert digits not in re.sub(r"[\s-]", "", blob)
    assert env["abstained"] is False, env["text"]
    assert "total_cost_myr=1610511.05" in env["text"]
    assert env["rows"][0]["total_cost_myr"] == 1610511.0500000012
    assert env["rows"][0]["handler_note"].startswith("DMSMASK_phone_")


def test_a4_a_float_of_integer_phone_is_still_masked_in_the_text(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"location_code": "WH-A", "handler_ref": 60123456789.0, "total_cost_myr": 12.5}]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    assert "60123456789" not in env["text"]
    _no_corrupt_figure_served(env)


@pytest.mark.parametrize(
    ("text", "taken"),
    [
        ("03-5555 0101", "5555 0101"),
        ("Tel.0355550101", "0355550101"),
        ("+60 12-345 6789", "345 6789"),
        ("ref=60123456789.0", "60123456789"),
        ("call 03-5555 0101.5 now", "5555 0101"),
        ("call +60 12-345 6789 1610511.0500000012", "345 6789"),
        ("note 1610511.0500000012 Tel.0355550101", "0355550101"),
        ("IC 900101-14-5678 paid 12.5", "900101-14-5678"),
        ("1.0123456789", "0123456789"),  # the rows hold 12.5 and 5.04, not this
        ("x 5.0355550101", "0355550101"),
    ],
)
def test_a4_the_figure_shield_never_uncovers_a_person_value(text: str, taken: str) -> None:
    """Rows that hold figures do not change what the scanner takes around them."""
    rows = [{"k": 1610511.0500000012}, {"k": 12.5}, {"k": 101.5}, {"k": 5.04}]
    assert taken not in mask_payload(text=text)["text"]
    got = mask_payload(text=text, rows=rows)
    assert taken not in got["text"], got["text"]
    assert got["rows"] == rows


def test_a4_a_figure_the_rows_show_is_still_masked_and_counted() -> None:
    """Masking never narrows for a figure. A mask inside any decimal is counted:
    no person value is written with a decimal point, so that mask rewrote a figure."""
    rows = [{"k": 5.01}]
    other = mask_payload(text="x 5.0123456789", rows=rows)
    assert "0123456789" not in other["text"] and other["figure_hits"] == 1
    other = mask_payload(text="x 7.0355550101", rows=rows)
    assert "0355550101" not in other["text"] and other["figure_hits"] == 1
    phone = mask_payload(text="call 03-5555 0101 now", rows=rows)
    assert "5555 0101" not in phone["text"] and phone["figure_hits"] == 0
    own = mask_payload(text="x 7.0355550101", rows=[{"k": 7.0355550101}])
    assert "0355550101" not in own["text"], own["text"]
    assert own["figure_hits"] == 1 and own["rows"] == [{"k": 7.0355550101}]


# --- A5: E14 itself, and proof that it can fail ---------------------------------


@pytest.fixture()
def good(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    rows = [{**row, "pallets": 40 + i} for i, row in enumerate(ROWS)]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    return env


SHOWN = "total_cost_myr=1610511.05,"
MUTATIONS = {
    "mask_in_fraction": (SHOWN, "total_cost_myr=1610511.DMSMASK_phone_01,"),
    "mask_before_fraction": (SHOWN, "total_cost_myr=DMSMASK_phone_01.05,"),
    "whole_figure_masked": (SHOWN, "total_cost_myr=DMSMASK_phone_01,"),
    "digits_swapped": (SHOWN, "total_cost_myr=1601511.05,"),
    "figure_truncated": (SHOWN, "total_cost_myr=161051,"),
    "bare_integer_changed": ("pallets=40\n", "pallets=41\n"),
    "bare_integer_of_another_row": ("pallets=40\n", "pallets=45\n"),
    "bare_integer_masked": ("pallets=40\n", "pallets=DMSMASK_account_01\n"),
    "found_count_wrong": ("Found 20 row(s).", "Found 19 row(s)."),
    "cut_not_stated": (" Showing 12 of 20.", ""),
    "cut_states_wrong_total": ("Showing 12 of 20.", "Showing 12 of 12."),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_a5_e14_fails_when_a_rendered_figure_is_corrupted(good: dict[str, Any], name: str) -> None:
    old, new = MUTATIONS[name]
    assert old in good["text"], (name, good["text"])
    bad = {**good, "text": good["text"].replace(old, new, 1)}
    assert figure_fidelity_violations(bad["text"], bad["rows"]), name
    assert not figure_fidelity_violations(good["text"], good["rows"])
    # E4 reads a decimal first and reports it. E14 alone reads integers, masks and counts.
    code = "E4|E14" if name == "digits_swapped" else "E14"
    with pytest.raises(AssertionError, match=code):
        assert_envelope_valid(bad)


def test_a5_e14_passes_the_unmutated_envelope(good: dict[str, Any]) -> None:
    assert_envelope_valid(good)
    assert "total_cost_myr=1610511.05, pallets=40\n" in good["text"]


def test_a5_e14_does_not_read_an_abstain(good: dict[str, Any]) -> None:
    """E14 is a rule about served figures. An abstain serves none."""
    from dms_executor.envelope import build_answer_envelope

    env = build_answer_envelope(
        answer_id="ans_fig_abstain",
        text="Found 3 row(s). total=1.DMSMASK_phone_01",
        badge="ABSTAIN",
        abstained=True,
        sql_used=None,
        as_of="2026-11-06T00:00:00Z",
        ask_mode="live",
        route="abstain",
    )
    assert_envelope_valid(env)


def test_a5_the_saved_probe_envelope_fails_e14() -> None:
    """The envelope ebe65ccd served for the probe question, recaptured from the route.

    The live q04 file is not in the repo. This is the same question, metric and
    three floats through ``POST /v1/chat/ask`` on ebe65ccd with a fake Cortex.
    """
    env = json.loads((FIXTURES / "q04_ops_shipment_cost.json").read_text(encoding="utf-8"))
    assert env["badge"] == "L1_GOVERNED_METRIC" and env["abstained"] is False
    assert "total_cost_myr=1610511.DMSMASK_phone_01" in env["text"]
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(env)


# --- the other listing renderers: same text rules ------------------------------


def test_verified_query_listing_follows_the_same_rules() -> None:
    from dms_executor.verified_queries import envelope_from_verified_submit

    env = envelope_from_verified_submit(
        asset_id="vq_fig",
        sql_text="SELECT location_code, SUM(cost_myr) AS total_cost_myr FROM shipments GROUP BY 1",
        result=QueryResult(ok=True, status="ok", run_id="run_vq", output={"rows": ROWS}),
        question="shipment cost by destination",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED" and env["abstained"] is False
    assert env["rows"] == ROWS
    assert env["text"].startswith("Found 20 row(s). Showing 12 of 20.\n")
    assert "total_cost_myr=1610511.05\n" in env["text"] and "DMSMASK_" not in env["text"]


def test_generated_listing_follows_the_same_rules() -> None:
    from dms_executor.generative_ask import _l2_envelope
    from dms_executor.ontology import NO_SILENT_PAD, Coverage

    env = _l2_envelope(
        sql="SELECT location_code, SUM(cost_myr) AS total_cost_myr FROM shipments GROUP BY 1",
        result=QueryResult(ok=True, status="ok", run_id="run_l2", output={"rows": ROWS}),
        question="shipment cost by destination",
        space_id=None,
        session_id=None,
        audit_id="aud_fig_l2",
        notes=(),
        coverage=Coverage(include=("all shipments",), exclude=(NO_SILENT_PAD,), unsure=()),
        lead="Here is the cost by destination.",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED" and env["abstained"] is False
    assert env["rows"] == ROWS
    assert "Found 20 row(s). Showing 12 of 20.\n" in env["text"]
    assert "total_cost_myr=1610511.05\n" in env["text"] and "DMSMASK_" not in env["text"]


# The same two renderers, driven through POST /v1/chat/ask (rules 10 and 10a).

_VQ_SQL = SHIPMENT_COST_SQL
_VQ_QUESTION = "FIG-FIDELITY steward: shipment cost by destination?"
_GEN_QUESTION = "What is the total shipment cost for each destination"
_GEN_SQL = SHIPMENT_COST_SQL


def _assert_listing_envelope(env: dict[str, Any], badge: str) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == badge and env["abstained"] is False, env["text"]
    assert env["rows"] == ROWS
    assert "Found 20 row(s). Showing 12 of 20.\n" in env["text"], env["text"]
    assert "total_cost_myr=1610511.05\n" in env["text"], env["text"]
    assert "DMSMASK_" not in env["text"], env["text"]


def test_verified_query_listing_over_http_follows_the_same_rules(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import verified_queries as vq
    from dms_executor.verified_queries import register_verified_query
    from test_vq02_verified_register import _MarkerCortex

    register_verified_query(
        space_id=WAREHOUSE_OPS, question=_VQ_QUESTION, sql=_VQ_SQL, path=warehouse, dialect="duckdb"
    )
    calls: list[dict[str, Any]] = []
    real = vq.envelope_from_verified_submit

    def _spy(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(vq, "envelope_from_verified_submit", _spy)
    cortex = _MarkerCortex(sql_output={"rows": copy.deepcopy(ROWS)})
    client = _live_client(warehouse, minter, monkeypatch, cortex)  # type: ignore[arg-type]
    r = client.post(
        "/v1/chat/ask",
        json={"question": _VQ_QUESTION, "space_id": WAREHOUSE_OPS, "session_id": "ses_fig_vq"},
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert len(calls) == 1, "the verified-query renderer was not the lane that served this"
    assert cortex.asks == []
    assert env["sql_used"] == _VQ_SQL
    _assert_listing_envelope(env, "L0_CERTIFIED")


def test_generated_listing_over_http_follows_the_same_rules(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_api import settings as settings_mod
    from dms_executor import generative_ask as gen

    for key, val in (
        ("DMS_CLOOP_B", "1"),
        ("DMS_SCHEMA_CONTEXT", "1"),
        ("DMS_HARNESS_ASK_PATHS", "1"),
        ("DMS_LANE_ONTOLOGY_RANKED", "0"),
        ("DMS_ASK_MODE", "live"),
        ("DMS_DEMO_FALLBACK", "0"),
    ):
        monkeypatch.setenv(key, val)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings_mod.get_settings.cache_clear()

    cortex = _GenCortex(warehouse=warehouse, rows=ROWS)
    calls: list[dict[str, Any]] = []
    real = gen._l2_envelope

    def _spy(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(gen, "_l2_envelope", _spy)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": _GEN_QUESTION,
            "space_id": WAREHOUSE_OPS,
            "session_id": "ses_fig_gen",
            "ask_path": "generative",
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert len(calls) == 1, (env["badge"], env["text"], env["assumptions"])
    assert cortex.asks == []
    _assert_listing_envelope(env, "L2_VALIDATED")


def test_no_renderer_builds_its_own_listing() -> None:
    """One listing writer. A sixth copy is how the cut and the float noise came back."""
    root = Path(__file__).parents[1] / "packages" / "executor" / "dms_executor"
    owners = sorted(
        p.name for p in root.glob("*.py") if "row(s)" in p.read_text(encoding="utf-8")
    )
    assert owners == ["row_listing.py"], owners


# --- adversary round (three Sonnet attackers, none of them the author) ----------

_COUNTRY = [{"country": "MY", "total_spend_myr": 1610511.05}]

WITHHELD = {
    # number formats: a figure within the old 1e-4 tolerance is still a wrong figure
    "off_by_111": ("country=MY, total_spend_myr=1610400.05", _COUNTRY),
    "sign_flipped": ("country=MY, rate=-0.004", [{"country": "MY", "rate": 0.004}]),
    "tiny_wrong": ("country=MY, rate=0.009", [{"country": "MY", "rate": 0.004}]),
    "label_names_no_row": ("Found 1 row(s).\n  - country=TH, total_spend_myr=1610511.05", _COUNTRY),
    "cut_of_wrong_total": (
        "Found 1 row(s). Showing 1 of 99.\n  - country=MY, total_spend_myr=1610511.05",
        _COUNTRY,
    ),
    "phone_with_a_decimal_tail": ("Call 0123456789.5 now", [{"country": "MY", "v": 123456789.5}]),
    # adjacency: a phone match that takes a figure with it
    "phone_swallows_a_count": (
        "Contact 0123456789 7 shipments delayed",
        [{"country": "MY", "delayed": 7}],
    ),
    "phone_cuts_a_grouped_count": (
        "Contact 0123456789 1,234 shipments delayed",
        [{"country": "MY", "delayed": 1234}],
    ),
    # a figure that is also the last group of a phone cannot be told apart: fails closed
    "phone_ends_in_a_figure_of_the_rows": (
        "MY has 6789 orders, contact +60 12-345 6789.",
        [{"country": "MY", "orders": 6789}],
    ),
    "phone_runs_into_a_figure": (
        "Total 1425139.6393028127 for 9123 4567-1425139.6393028127",
        [{"country": "MY", "v": 1425139.6393028127}],
    ),
}

SERVED = {
    # ordinary prose that names a column must not be read as a broken listing
    "column_in_brackets": ("Share is 12.5% (n=4).", [{"share": 12.5, "n": 4}]),
    "value_with_a_unit": ("average=12.5 MYR over the period", [{"average": 12.5}]),
    "two_values_with_units": ("qty=12 units, price=1.5", [{"qty": 12, "price": 1.5}]),
    "reworded_label": ("country=MY (largest), orders=7", [{"country": "MY", "orders": 7}]),
    "two_rows_on_one_line": (
        "country=MY, orders=7; country=SG, orders=3",
        [{"country": "MY", "orders": 7}, {"country": "SG", "orders": 3}],
    ),
    "phone_beside_a_figure": (
        "Total for MY is 1610511.05, contact +60 12-345 6789.",
        [{"country": "MY", "total_spend_myr": 1610511.05, "orders": 12}],
    ),
    "insights_bullets_under_a_listing": (
        "Found 2 row(s).\n  \u00b7 country=MY, orders=7\n  \u00b7 country=SG, orders=3\n\n"
        "Insights:\n- MY is the largest",
        [{"country": "MY", "orders": 7}, {"country": "SG", "orders": 3}],
    ),
}


@pytest.mark.parametrize("name", sorted(WITHHELD))
def test_adversary_a_figure_the_rows_do_not_hold_is_withheld(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    answer, rows = WITHHELD[name]
    env = _ask_prose(warehouse, minter, monkeypatch, answer, rows)
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert env["rows"] == [] and env["values"] == []
    assert not re.search(r"\d{4}", env["text"]), env["text"]


@pytest.mark.parametrize("name", sorted(SERVED))
def test_adversary_a_correct_answer_is_still_served(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    answer, rows = SERVED[name]
    env = _ask_prose(warehouse, minter, monkeypatch, answer, rows)
    assert_envelope_valid(env)
    assert env["abstained"] is False, (env["text"], env["assumptions"])
    assert env["rows"] == rows
    assert "345 6789" not in env["text"]
    kept = re.sub(r"\+60 12-345 6789", "DMSMASK_phone_01", answer)
    assert env["text"] == kept, env["text"]


def test_adversary_a_leading_zero_literal_is_a_phone_not_a_figure() -> None:
    rows = [{"a": 123456789.5}]
    for text in ("Call 0123456789.5 now", "+0123456789.5", "ref 1,0123456789.5"):
        assert "0123456789" not in mask_payload(text=text, rows=rows)["text"]


def test_adversary_a_nan_cell_does_not_withhold_a_correct_listing() -> None:
    """AVG over an empty group. The listing shows nan, and the row holds nan."""
    from dms_executor.row_listing import found_rows_text

    rows = [{"loc": "A", "m": 1.5, "avg": float("nan")}, {"loc": "B", "m": 2.5, "avg": 4.0}]
    assert figure_fidelity_violations(found_rows_text(rows), rows) == []
    assert figure_fidelity_violations("Found 2 row(s).\n  - loc=A, m=1.5, avg=4", rows)


def test_adversary_a_figure_with_no_float_noise_keeps_every_digit() -> None:
    """dms#540 acceptance 3: a binary float is never stated past 15 significant
    digits. 12345678901234.56 is not exactly a double; the note says so."""
    from dms_executor.row_listing import cell_text, float_precision_note

    assert cell_text(12345678901234.56) == "12345678901234.6"
    assert float_precision_note([{"x": 12345678901234.56}])
    assert float_precision_note([{"x": 38.9876}, {"x": 12.5}]) is None
    assert cell_text(1610511.0500000012) == "1610511.05"
    assert cell_text(0.30000000000000004) == "0.3"
    assert cell_text(38.9876) == "38.9876"


def test_adversary_e14_takes_a_correct_rounding_and_refuses_a_wrong_one() -> None:
    """E14 alone. E4 has its own, older tolerance for a decimal in prose."""
    rows = [{"on_time_pct": 38.9876}]
    for shown in ("39.0", "39", "38.99%", "38.9876 pct"):
        assert figure_fidelity_violations(f"on_time_pct={shown}", rows) == [], shown
    for shown in ("39.1", "38.98", "38", "3.9e1x", "RM 39"):
        assert figure_fidelity_violations(f"on_time_pct={shown}", rows), shown


# --- verifier round (Opus, 2026-11-20): what the fix itself broke -----------------


@pytest.mark.parametrize(
    "label",
    ["KL ", " Kuala Lumpur", "Acme Sdn Bhd,", "Penang;"],
)
def test_verifier_a_padded_or_punctuated_label_does_not_withhold_a_correct_listing(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    """Rule 12: labels carry the source's own spacing. E14 must not read that as a wrong figure."""
    rows = [{"location_code": label, "total_cost_myr": 12.5}]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    assert env["abstained"] is False, (env["text"], env["assumptions"])
    assert env["rows"] == rows
    assert "total_cost_myr=12.5" in env["text"], env["text"]


def test_verifier_a_huge_exponent_is_withheld_not_a_server_error(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"country": "MY", "rate": 0.004}]
    env = _ask_prose(warehouse, minter, monkeypatch, "country=MY, rate=1e9999999", rows)
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert env["rows"] == [] and "1e9999999" not in env["text"]


def test_verifier_a_decimal_the_mask_replaces_whole_is_withheld(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """212.5551234 reads as a NANP phone end to end. A figure in no row, masked whole,
    must not reach the reader as a mask token under a green badge."""
    rows = [{"country": "MY", "total_spend_myr": 12.5}]
    env = _ask_prose(
        warehouse, minter, monkeypatch, "Average cost for MY is 212.5551234 per unit.", rows
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert "DMSMASK_" not in env["text"] and "5551234" not in env["text"], env["text"]
    assert env["rows"] == [] and env["values"] == []


def test_verifier_masking_a_long_text_stays_linear() -> None:
    """The figure check ran a regex over the whole text once per mask: 38.7 s here."""
    import time

    text = " ".join(f"call 03-5555 {1000 + i % 9000:04d} paid 12.5" for i in range(16_000))
    started = time.perf_counter()
    got = mask_payload(text=text, rows=[{"k": 12.5}])
    elapsed = time.perf_counter() - started
    assert "5555 1000" not in got["text"]
    assert elapsed < 5.0, elapsed


@pytest.mark.parametrize(
    ("answer", "rows"),
    [
        (
            "Found 2 row(s).\n  - country=MY, orders=8\n  - country=MY , orders=7",
            [{"country": "MY", "orders": 7}, {"country": "MY ", "orders": 8}],
        ),
        (
            "country=MY, total_spend_myr=1610511.05",
            [
                {"country": "MY", "total_spend_myr": 12.5},
                {"country": "MY,", "total_spend_myr": 1610511.05},
            ],
        ),
    ],
)
def test_verifier_a_figure_under_a_near_twin_label_is_withheld(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    rows: list[dict[str, Any]],
) -> None:
    """Two groups that differ by an edge space or a comma are two groups. A figure
    from one shown under the other is a wrong figure."""
    env = _ask_prose(warehouse, minter, monkeypatch, answer, rows)
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert env["rows"] == [] and env["values"] == []


def test_verifier_a_label_that_reads_as_a_figure_is_withheld(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A label cell holding "total_cost_myr=999" prints a second figure for the row."""
    rows = [{"location_code": "x, total_cost_myr=999", "total_cost_myr": 12.5}]
    env = _ask_l1(warehouse, minter, monkeypatch, rows)
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert "999" not in env["text"]
