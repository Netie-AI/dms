"""Plan E gate matrix, theme "generative-doc": four answer entries that skip a gate.

Every test is a strict-xfail gap (``_harness.gap``): it fails on its own plain
``assert`` today and turns into a hard FAILURE the day the gate exists, which is
the signal to delete the marker. Each one opens with ``control(...)`` requests on
an already-gated sibling path, so a broken fixture raises ``ControlFailed`` (not an
``AssertionError``) and can never be absorbed as an expected failure.

All assertions are on what the customer receives from ``POST /v1/chat/ask``
(CLAUDE.md rule 10/10a): the rendered text, the returned rows, the badge, the
envelope keys. Generated SQL is only ever an addition.

The Insights seam is armed by giving the recording fake a ``compute_insights``
method, exactly the attribute ``_insights_compute_seam`` looks for. That models an
armed deployment (a real CORTEX_API_KEY): the default demo key is refused by
``generate_bearer_refuse`` before any SQL could arrive, so the two generative
paths are config-gated in production and unreachable on the default config.

Synthetic data only: the seeded demo warehouse plus one invented orphan row.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _harness import (  # noqa: E402
    FINANCE,
    WAREHOUSE_OPS,
    assert_envelope,
    control,
    gap,
    require_envelope,
)

# ----------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------


class _Insights:
    """Scripted Cortex Insights: whatever ``payload`` holds is what the next ask gets."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def __call__(self, question: str, **_kw: Any) -> dict[str, Any] | None:
        self.calls.append(question)
        return self.payload


def _arm_insights(harness: Any, payload: dict[str, Any] | None = None) -> _Insights:
    """Arm the generative lane on the recording fake (see module docstring)."""
    insights = _Insights(payload)
    harness.cortex.compute_insights = insights
    return insights


def _canon(rows: list[dict[str, Any]]) -> list[str]:
    """Order-insensitive, type-tolerant row set for comparing two result sets."""
    return sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)


def _rerun(harness: Any, sql: str) -> list[dict[str, Any]]:
    """Re-execute SQL on the seeded warehouse exactly as the fake Cortex executed it."""
    from dms_executor.demo_warehouse import execute_sql

    return execute_sql(sql, path=harness.warehouse)


# ----------------------------------------------------------------------------
# 1. G2 - generated SQL ships L2_VALIDATED over a lake whose ontology failed verify
# ----------------------------------------------------------------------------

#: One grain ("supplier"), so the multi-grain compile does not preempt. Joins
#: transactions -> inventory -> suppliers with the model's own aggregate.
_REVENUE_BY_SUPPLIER_SQL = (
    "SELECT s.supplier_name, "
    "SUM(CASE WHEN t.txn_type = 'outbound' THEN t.quantity_kg * t.unit_cost_myr END) "
    "AS revenue "
    "FROM transactions t JOIN inventory i ON t.sku = i.sku "
    "JOIN suppliers s ON i.supplier_id = s.supplier_id "
    "GROUP BY s.supplier_name"
)
_REVENUE_BY_SUPPLIER_Q = "revenue by supplier"
#: Typed plan for the same ask: the path that already refuses on a failed ontology.
_REVENUE_BY_SUPPLIER_PLAN = {
    "query_plan": {
        "measure": "outbound_value_myr",
        "group_by": [["supplier", "supplier_name"]],
    }
}


def _add_orphan_supplier_lot(harness: Any) -> None:
    """An inventory lot whose supplier does not exist, with outbound value on it.

    The lot is the orphan FK (``lot_from_supplier``); the transaction gives the
    INNER JOIN a value to silently drop.
    """
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(harness.warehouse)
    try:
        con.execute(
            "INSERT INTO inventory VALUES "
            "('SKU-ORPH', 'WH-A', 100, 10, 1.0, 'SUP-99', 'RAW', NULL)"
        )
        con.execute(
            "INSERT INTO transactions VALUES "
            "('T099', 'SKU-ORPH', 'WH-A', 'outbound', 500, 10.0, '2026-07-26 10:00:00')"
        )
    finally:
        con.close()


@gap(
    "chat-ask:generative-insights-sql",
    "G2",
    "#258",
    "generative-sql-ships-over-failed-verify-ontology",
)
def test_insights_sql_over_a_lake_whose_ontology_failed_verify_abstains(harness) -> None:  # type: ignore[no-untyped-def]
    """A2-02 (#258) on the live path: Insights SQL must not ship L2 over an orphan FK.

    ``Executor._live_ask`` calls ``maybe_generative_ask`` with no ``ontology=``, so
    the A2-02 guard (``violations_cited_by_sql``, which needs a declared ontology)
    never runs. The default demo ontology that failed verify is just dropped to
    None and the SQL proceeds.
    """
    from dms_executor.demo_warehouse import connect_file
    from dms_executor.ontology import demo_ontology

    insights = _arm_insights(harness, {"query_sql": _REVENUE_BY_SUPPLIER_SQL})

    # CONTROL 1 - clean seed: the same SQL ask is answered L2_VALIDATED with real
    # rows. Proves the armed lane and the fixture work, so the gap below is not a
    # broken ask.
    status, clean = harness.ask(
        _REVENUE_BY_SUPPLIER_Q, space_id=FINANCE, session_id="ses_g2_clean"
    )
    require_envelope(status, clean)
    control(
        clean["badge"] == "L2_VALIDATED" and clean["abstained"] is False and clean["rows"],
        f"clean-seed insights SQL should answer L2: {clean.get('badge')} {clean.get('text')!r}",
    )
    control(
        len(insights.calls) == 1 and len(harness.cortex.sql_submits) == 1,
        "the clean ask must have gone through Insights and exactly one Cortex SQL submit",
    )

    # Poison the lake: one orphan supplier FK, with outbound value behind it.
    _add_orphan_supplier_lot(harness)
    true_outbound = float(
        _rerun(
            harness,
            "SELECT SUM(quantity_kg * unit_cost_myr) AS v FROM transactions "
            "WHERE txn_type = 'outbound'",
        )[0]["v"]
    )

    # CONTROL 2 - the lake's default ontology really fails verify, on the link the
    # SQL joins through. (Same check ``load_verified_ontology`` runs on the live path.)
    onto = demo_ontology(harness.warehouse)
    con = connect_file(harness.warehouse)
    try:
        violations = onto.verify(con)
    finally:
        con.close()
    control(
        any(v.check == "fk_intact" and v.subject == "lot_from_supplier" for v in violations),
        f"expected an fk_intact violation on lot_from_supplier, got "
        f"{[(v.check, v.subject) for v in violations]}",
    )

    # CONTROL 3 - the typed-plan path on the very same orphan lake already abstains
    # ontology_unverified: the gate exists on one generative lane and not the other.
    insights.payload = _REVENUE_BY_SUPPLIER_PLAN
    status, plan_env = harness.ask(
        _REVENUE_BY_SUPPLIER_Q, space_id=FINANCE, session_id="ses_g2_plan"
    )
    require_envelope(status, plan_env)
    control(
        plan_env["abstained"] is True
        and plan_env["badge"] == "ABSTAIN"
        and "ontology_unverified" in plan_env["text"],
        f"typed plan on an orphan lake should abstain ontology_unverified: "
        f"{plan_env.get('badge')} {plan_env.get('text')!r}",
    )

    # THE GAP - Insights-authored SQL, same question, same orphan lake.
    insights.payload = {"query_sql": _REVENUE_BY_SUPPLIER_SQL}
    sql_submits_before = len(harness.cortex.sql_submits)
    status, env = harness.ask(
        _REVENUE_BY_SUPPLIER_Q, space_id=FINANCE, session_id="ses_g2_gap"
    )
    require_envelope(status, env)  # HTTP 200 + E1-E9, so this is the answer path

    shipped_total = sum(float(r.get("revenue") or 0) for r in env["rows"])
    shipped = (
        f"shipped badge={env['badge']} abstained={env['abstained']} "
        f"rows={len(env['rows'])} total={shipped_total} but the lake's outbound value "
        f"is {true_outbound} (the orphan's value was dropped by the INNER JOIN); "
        f"text={env['text']!r}"
    )
    # A correct gate abstains, naming the broken check and link, like the declared
    # ontology path does (tests/test_a2_02_declared_ontology_refuses_sql.py).
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", shipped
    assert env["rows"] == [], shipped
    assert "ontology_unverified" in env["text"], shipped
    assert "lot_from_supplier" in env["text"], shipped
    # and the join over the failed link is never executed.
    assert len(harness.cortex.sql_submits) == sql_submits_before, shipped


# ----------------------------------------------------------------------------
# 2. G7 - keep_gt: DMS keeps rows in Python, the ledgered SQL returns more
# ----------------------------------------------------------------------------

_UTILISATION_Q = "Which locations are above 70 percent utilisation?"
_UTILISATION_PLAN = {
    "measure": "utilisation_pct",
    "group_by": [["location", "location_id"]],
}


@gap(
    "chat-ask:generative-ontology-plan",
    "G7",
    "new",
    "generative-keep-gt-rows-not-in-ledgered-sql",
)
def test_keep_gt_rows_shown_are_what_the_displayed_and_ledgered_sql_returns(harness) -> None:  # type: ignore[no-untyped-def]
    """The audit trail must reproduce the rows the customer was shown.

    On a typed plan with ``keep_gt`` Cortex executes the compiled SQL and returns
    every row, DMS then keeps the rows over the threshold in Python
    (generative_ask.py ``_submit_validated``), and ``sql_used`` plus the ledger
    payload still carry the unfiltered query. Re-running what was displayed or
    ledgered gives more rows than the answer showed.
    """
    insights = _arm_insights(harness, {"query_plan": dict(_UTILISATION_PLAN)})

    # CONTROL - the same plan without keep_gt: the rendered rows ARE what the
    # displayed SQL returns, and the ledger holds that same SQL.
    status, plain = harness.ask(_UTILISATION_Q, space_id=FINANCE, session_id="ses_g7_plain")
    require_envelope(status, plain)
    control(
        plain["badge"] == "L2_VALIDATED" and plain["abstained"] is False and plain["rows"],
        f"plan without keep_gt should answer L2: {plain.get('badge')} {plain.get('text')!r}",
    )
    control(
        plain["sql_used"] and _canon(_rerun(harness, plain["sql_used"])) == _canon(plain["rows"]),
        "without keep_gt the displayed SQL must reproduce the displayed rows",
    )
    control(
        harness.cortex.ledger_payloads("ask.generated_ontology")[-1]["sql"] == plain["sql_used"],
        "the ledger holds the SQL that was displayed",
    )

    # THE GAP - the plan now carries keep_gt (the 'above N percent' threshold).
    insights.payload = {"query_plan": {**_UTILISATION_PLAN, "keep_gt": 70}}
    status, env = harness.ask(_UTILISATION_Q, space_id=FINANCE, session_id="ses_g7_keep")
    require_envelope(status, env)
    control(
        env["badge"] == "L2_VALIDATED" and env["abstained"] is False and env["rows"],
        f"keep_gt plan should still answer L2: {env.get('badge')} {env.get('text')!r}",
    )
    control(env["sql_used"], "an answered generative envelope carries sql_used")
    ledgered_sql = harness.cortex.ledger_payloads("ask.generated_ontology")[-1]["sql"]

    shown = _canon(env["rows"])
    displayed_returns = _rerun(harness, env["sql_used"])
    ledgered_returns = _rerun(harness, ledgered_sql)
    detail = (
        f"customer was shown {len(env['rows'])} row(s) ({env['text'].splitlines()[0]!r}) "
        f"but the displayed SQL returns {len(displayed_returns)} and the ledgered SQL "
        f"returns {len(ledgered_returns)}"
    )
    # A correct gate either puts the keep predicate into the SQL it displays and
    # ledgers, or shows exactly what that SQL returns.
    assert _canon(displayed_returns) == shown, detail
    assert _canon(ledgered_returns) == shown, detail


# ----------------------------------------------------------------------------
# 3. G8 - doc retrieval: E9/E4 only treat decimal / thousand-separated numbers as figures
# ----------------------------------------------------------------------------

_DOC_Q = "What is the total in the Q3 walkthrough notes?"
_DOC_SNIPPET = "Q3 walkthrough notes: the Bay-3 leak was flagged during inspection."


def _doc_answer(answer: str, snippet: str = _DOC_SNIPPET) -> Any:
    """A Cortex doc-retrieval answer: no sql_used, one cited snippet, a drillthrough token."""
    from cortex_client.models import AskResponse

    return AskResponse(
        answer=answer,
        audit_id="aud_doc_rag_gm",
        route="doc_rag",
        provenance={"badge": "query_skill", "layer": "L2"},
        rows=[{"excerpt": "Q3 walkthrough notes"}],
        drillthrough_token="dt_doc_rag_gm_token",
        contributing_sources=[
            {
                "ref_id": "src_notes",
                "filename": "notes_a.csv",
                "contribution_pct": 100,
                "content": snippet,
                "chunk_index": 0,
            }
        ],
    )


@gap(
    "chat-ask:cortex-doc-retrieval",
    "G8",
    "new",
    "e9-bare-integer-uncited-in-doc-lane",
)
def test_doc_answer_with_an_uncited_bare_integer_total_is_demoted(harness) -> None:  # type: ignore[no-untyped-def]
    """E9: a retrieval path may quote a figure it can point at, never compute one.

    ``_money_like`` skips figures with no ',' or '.', so a model-composed integer
    total in prose is not checked against the cited snippet and the answer keeps
    its badge. The same total written '12,500.00' is already withheld.
    """
    # CONTROL A - decimal / separated total, no matching snippet: withheld today.
    harness.cortex.ask_response = _doc_answer("The total is 12,500.00")
    status, withheld = harness.ask(_DOC_Q, space_id=FINANCE, session_id="ses_g8_dec")
    require_envelope(status, withheld)
    control(
        withheld["abstained"] is True
        and withheld["badge"] == "ABSTAIN"
        and "12,500.00" not in withheld["text"],
        f"an uncited '12,500.00' should be withheld: {withheld.get('badge')} "
        f"{withheld.get('text')!r}",
    )

    # CONTROL B - a figure the snippet really contains is quoted, not withheld: the
    # doc lane is serving, so control A is the figure check and not a blanket refusal.
    harness.cortex.ask_response = _doc_answer(
        "The total is 12,500.00",
        snippet="Q3 walkthrough notes: the penalty total is 12,500.00 per the contract.",
    )
    status, quoted = harness.ask(_DOC_Q, space_id=FINANCE, session_id="ses_g8_quoted")
    require_envelope(status, quoted)
    control(
        quoted["badge"] == "L2_VALIDATED"
        and quoted["abstained"] is False
        and "12,500.00" in quoted["text"]
        and quoted["contributing_sources"],
        f"a quoted figure should be served: {quoted.get('badge')} {quoted.get('text')!r}",
    )

    # THE GAP - the same total as a bare integer, not in the snippet.
    control("12500" not in _DOC_SNIPPET, "the cited snippet must not contain the figure")
    harness.cortex.ask_response = _doc_answer("The total is 12500")
    status, env = harness.ask(_DOC_Q, space_id=FINANCE, session_id="ses_g8_bare")
    require_envelope(status, env)
    control(
        env["route"] == "doc_rag" and env["sql_used"] == "-- document retrieval (no SQL)",
        f"expected the doc-retrieval lane (no executed query): route={env.get('route')!r} "
        f"sql_used={env.get('sql_used')!r}",
    )

    shipped = (
        f"shipped badge={env['badge']} abstained={env['abstained']} text={env['text']!r}; "
        f"the cited snippet does not contain 12500"
    )
    # A correct gate demotes the answer (ABSTAIN) or withholds the figure from the
    # rendered text. Today the integer is served under a confident badge.
    demoted = env["abstained"] is True and env["badge"] == "ABSTAIN"
    withheld_figure = "12500" not in env["text"]
    assert demoted or withheld_figure, shipped


@gap(
    "chat-ask:cortex-doc-retrieval",
    "G8",
    "new",
    "e9-bare-integer-uncited-in-doc-lane",
)
def test_doc_abstain_with_an_empty_engine_answer_names_a_reason(harness) -> None:  # type: ignore[no-untyped-def]
    """Secondary facet of the same gap: the no-hit doc abstain says only 'Abstained.'.

    When the engine abstains and sends no text, ``map_ask_response_to_envelope``
    falls back to the bare word and records no reason beyond 'live Cortex ask'.
    The sibling demotion in this lane (an uncited figure) names its reason.
    """
    from cortex_client.models import AskResponse

    # CONTROL - this lane's own demotion names why it abstained.
    harness.cortex.ask_response = _doc_answer("The total is 12,500.00")
    status, named = harness.ask(_DOC_Q, space_id=FINANCE, session_id="ses_g8_named")
    require_envelope(status, named)
    control(
        named["abstained"] is True and "could not certify a figure" in named["text"],
        f"the figure-demotion abstain should name its reason: {named.get('text')!r}",
    )

    # THE GAP - the engine found no matching chunk and returned no answer text.
    harness.cortex.ask_response = AskResponse(
        answer="",
        badge="abstain",
        abstained=True,
        audit_id="aud_doc_no_hit_gm",
        route="abstain",
    )
    status, env = harness.ask(
        "What does the Q3 walkthrough say about forklift batteries?",
        space_id=FINANCE,
        session_id="ses_g8_nohit",
    )
    require_envelope(status, env)
    control(
        env["abstained"] is True and env["badge"] == "ABSTAIN",
        f"a no-hit doc ask must abstain: {env.get('badge')}",
    )

    text = env["text"].strip()
    # A correct gate names why (no matching document, nothing to cite) instead of
    # the bare sentinel.
    assert text.rstrip(".").lower() != "abstained", (
        f"customer reads {text!r}; assumptions={env.get('assumptions')}"
    )


# ----------------------------------------------------------------------------
# 4. G6 - the Space-refusal envelope is built outside live_ask: no served_attribution
# ----------------------------------------------------------------------------

_SERVED_ATTR_VALUES = {"reported", "missing", "none"}
_SPACE_Q = "Top 5 selling SKUs by revenue"


@gap(
    "chat-ask:space-refusal",
    "G6",
    "#305",
    "space-refusal-envelope-no-served-attribution",
)
def test_space_refusal_envelope_carries_served_attribution(harness) -> None:  # type: ignore[no-untyped-def]
    """SERVED-ATTR-01 (#305): every ask envelope says whether a model was served.

    The stamp is applied on ``Executor.live_ask``'s return. The Space-boundary
    refusal is raised out of ``live_ask`` as ``AskServiceError('path_not_allowed')``
    and rendered by ``routes/chat.py`` ``_space_refusal_envelope``, so it never
    sees the stamp, although a Cortex ask (a model call) ran before the refusal.
    """
    from dms_core.ask import AskServiceError

    # CONTROL 1 - an ordinary live_ask envelope (the engine abstains) carries the
    # attribution, and so does a certified pack answer.
    status, abstain_env = harness.ask(_SPACE_Q, space_id=WAREHOUSE_OPS, session_id="ses_g6_a")
    require_envelope(status, abstain_env)
    control(
        abstain_env["abstained"] is True
        and abstain_env.get("served_attribution") in _SERVED_ATTR_VALUES,
        f"a live_ask abstain envelope should carry served_attribution: "
        f"{abstain_env.get('served_attribution')!r} {abstain_env.get('text')!r}",
    )
    status, pack_env = harness.ask(
        "What is our total spend by supplier country?",
        space_id=FINANCE,
        session_id="ses_g6_b",
    )
    require_envelope(status, pack_env)
    control(
        pack_env["badge"] == "L1_GOVERNED_METRIC"
        and pack_env.get("served_attribution") in _SERVED_ATTR_VALUES,
        f"a certified envelope should carry served_attribution: "
        f"{pack_env.get('served_attribution')!r}",
    )

    # THE GAP - the engine refuses the Space boundary after the ask reached it.
    asks_before = len(harness.cortex.asks)
    harness.cortex.ask_raises = AskServiceError(
        "path_not_allowed", "table 'transactions' is not named by this manifest"
    )
    status, env = harness.ask(_SPACE_Q, space_id=WAREHOUSE_OPS, session_id="ses_g6_refused")
    require_envelope(status, env)
    control(
        env["abstained"] is True
        and env["badge"] == "ABSTAIN"
        and env["assumptions"] == ["refused by the Space boundary"]
        and "transactions" in env["text"]
        and "Warehouse Ops" in env["text"],
        f"expected the Space-refusal envelope, got {env.get('badge')} {env.get('text')!r} "
        f"{env.get('assumptions')}",
    )
    control(
        len(harness.cortex.asks) == asks_before + 1,
        "the Cortex ask must have been attempted before the refusal was rendered",
    )
    assert_envelope(env)

    assert "served_attribution" in env, (
        f"refusal envelope keys: {sorted(env)}; text={env['text']!r}"
    )
    assert env["served_attribution"] in _SERVED_ATTR_VALUES, env["served_attribution"]
