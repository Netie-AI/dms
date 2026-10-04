"""Gate gaps on the two demo flags: DMS_ASK_MODE=demo and DMS_DEMO_FALLBACK=1.

Both flags are off by default (``DMS_ASK_MODE=live``, ``DMS_DEMO_FALLBACK=0``) and
both are bannered (hard rule 11), so these are dormant gaps, not live defects.
They matter because each flag swaps the governed answer path for a hard-coded
router (``answer_demo_question``) that runs none of the gates the live path runs.

Gap ``demo-lane-bypasses-all-gates``
    The demo router answers from fixed DuckDB templates. It never resolves the
    Space's grants, and a year it cannot filter on is silently dropped.
      * test 1 (G1): a Space that does not grant ``transactions`` is served
        revenue computed from ``transactions``.
      * test 2 (G8): "revenue in 2099" returns the all-time total at L2_VALIDATED.
    Entry points: ``chat-ask:demo-mode`` (ask mode flag) and
    ``chat-ask:demo-fallback-no-cortex`` (fallback flag, no Cortex client).

Gap ``demo-fallback-converts-live-refusal-to-demo-numbers``
    With the fallback flag on, ``chat_ask`` answers a failed live attempt with
    demo numbers unless the error is a screened policy code. The in-code rule is
    "never mask policy refusals with demo numbers".
      * test 3: three 403 policy codes the screen omits (``_POLICY_CODES``).
      * test 4: failures that arrive as a plain ``Exception`` (a steward query's
        ``SubmitError``, an envelope-invariant ``AssertionError``) skip the screen
        entirely.
    Entry points: ``chat-ask:demo-fallback-ask-error`` and
    ``chat-ask:demo-fallback-exception``.

Every control runs a sibling request that must already pass today, so the gap
assertion cannot fail because of a broken fixture. Assertions are on the
customer-visible response: HTTP status, ``badge``, ``abstained``, ``text``,
``rows``, ``values``. Not on SQL.

Run: ``pytest -m gate_gap tests/gate_matrix/test_gap_demo_flags.py``
"""

from __future__ import annotations

from typing import Any

import pytest
from _harness import (
    FINANCE,
    WAREHOUSE_OPS,
    assert_envelope,
    control,
    gap,
    require_envelope,
)

REVENUE_Q = "What was total revenue?"
REVENUE_2099_Q = "What was total revenue in 2099?"
PREDICTIVE_Q = "What will revenue be next quarter?"

#: The steward SQL registered for the verified-query variant of test 4. It reads
#: ``transactions``, which Finance grants, so the Space check passes and the only
#: thing that fails is what the engine returns.
REVENUE_SQL = (
    "SELECT SUM(quantity_kg * unit_cost_myr) AS revenue_myr "
    "FROM transactions WHERE txn_type = 'outbound'"
)

PATH_NOT_ALLOWED = "path_not_allowed: table 'transactions' is not named by this manifest"

GAP_DEMO_LANE = "demo-lane-bypasses-all-gates"
GAP_FALLBACK = "demo-fallback-converts-live-refusal-to-demo-numbers"

#: The two ways an ask reaches the demo router without a failed live attempt.
LANES = ["demo_mode", "no_cortex_fallback"]


# --------------------------------------------------------------------- helpers


def _enter_lane(h: Any, lane: str) -> None:
    """Switch an already-built live harness onto a demo lane."""
    if lane == "demo_mode":
        h.set_env(DMS_ASK_MODE="demo")
    else:
        # Lifespan skipped / no Cortex configured: app.state.cortex is None, so
        # chat_ask never reaches live_ask and the fallback flag decides.
        h.set_env(DMS_DEMO_FALLBACK="1")
        h.app.state.cortex = None


def _all_time_revenue_text(h: Any) -> str:
    """The figure the demo router prints for 'total revenue', as the user reads it."""
    from dms_executor.demo_warehouse import total_outbound_revenue

    return f"{total_outbound_revenue(path=h.warehouse):,.2f}"


def _bound_tables(h: Any) -> dict[str, set[str]]:
    """Per Space, the tables named by the manifest the live path bound to Cortex."""
    return {
        b.manifest.space_id: set(b.manifest.row_predicates) for b in h.cortex.bind_submits
    }


def _refused_without_figures(body: Any) -> bool:
    """A 200 ABSTAIN envelope that carries no figure: how a refusal looks to the user."""
    return (
        isinstance(body, dict)
        and body.get("abstained") is True
        and body.get("badge") == "ABSTAIN"
        and not body.get("values")
        and not body.get("rows")
    )


def _detail_code(body: Any) -> Any:
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail.get("code") if isinstance(detail, dict) else None


def _seen(status: int, body: Any) -> str:
    """What the customer got, for an assertion message."""
    if not isinstance(body, dict):
        return f"HTTP {status} {body!r}"
    return (
        f"HTTP {status} badge={body.get('badge')} abstained={body.get('abstained')} "
        f"ask_mode={body.get('ask_mode')} text={body.get('text')!r} "
        f"detail={body.get('detail')!r}"
    )


# ------------------------------------------------- gap: demo-lane-bypasses-all-gates


@pytest.mark.parametrize("lane", LANES)
@gap(
    row="chat-ask:demo-mode,chat-ask:demo-fallback-no-cortex",
    gate="G1 grant / Space check",
    ticket="new",
    gap_id=GAP_DEMO_LANE,
)
def test_demo_lane_serves_revenue_to_a_space_with_no_transactions_grant(harness, lane):  # type: ignore[no-untyped-def]
    """Warehouse Ops does not grant ``transactions``; the demo lane answers anyway."""
    h = harness
    all_time = _all_time_revenue_text(h)

    # CONTROL 1 - the live lane enforces the Space grant on this same question.
    # The manifest it binds to Cortex for Warehouse Ops does not name
    # ``transactions`` (Finance's does), and the customer gets an ABSTAIN with no
    # figure and no revenue number in the text.
    s, env = h.ask(REVENUE_Q, space_id=WAREHOUSE_OPS, session_id=f"ctl_wops_{lane}")
    control(s == 200, f"live ask in Warehouse Ops: {_seen(s, env)}")
    assert_envelope(env)
    control(env["ask_mode"] == "live", f"expected the live lane: {_seen(s, env)}")
    control(
        _refused_without_figures(env) and all_time not in env["text"],
        f"live lane did not refuse revenue in Warehouse Ops: {_seen(s, env)}",
    )
    s, env = h.ask(REVENUE_Q, space_id=FINANCE, session_id=f"ctl_fin_{lane}")
    control(s == 200, f"live ask in Finance: {_seen(s, env)}")
    bound = _bound_tables(h)
    control(
        "transactions" not in bound.get(WAREHOUSE_OPS, {"transactions"}),
        f"live manifest for Warehouse Ops names transactions: {bound}",
    )
    control(
        "transactions" in bound.get(FINANCE, set()),
        f"live manifest for Finance does not name transactions: {bound}",
    )

    # CONTROL 2 - the demo lane is in effect and serves the figure where the grant
    # exists (Finance), so the gap request below is about the Space, not the lane.
    _enter_lane(h, lane)
    s, env = h.ask(REVENUE_Q, space_id=FINANCE, session_id=f"ctl_demo_fin_{lane}")
    require_envelope(s, env)
    control(
        env["ask_mode"] == "demo"
        and env["abstained"] is False
        and env["badge"] == "L2_VALIDATED"
        and all_time in env["text"],
        f"demo lane did not answer revenue in Finance: {_seen(s, env)}",
    )

    # GAP - the same question in the Space with no transactions grant.
    s, env = h.ask(REVENUE_Q, space_id=WAREHOUSE_OPS, session_id=f"gap_demo_wops_{lane}")
    require_envelope(s, env)
    assert _refused_without_figures(env) and all_time not in env["text"], (
        "demo lane answered a Warehouse Ops revenue ask from transactions, which "
        f"that Space does not grant: {_seen(s, env)} rows={env.get('rows')!r}"
    )


@pytest.mark.parametrize("lane", LANES)
@gap(
    row="chat-ask:demo-mode,chat-ask:demo-fallback-no-cortex",
    gate="G8 named abstain on failure",
    ticket="new",
    gap_id=GAP_DEMO_LANE,
)
def test_demo_lane_answers_a_year_with_no_data_with_the_all_time_total(harness, lane):  # type: ignore[no-untyped-def]
    """'revenue in 2099' is a near-miss of 'total revenue'; it must abstain, not echo."""
    from dms_executor.demo_warehouse import execute_sql

    h = harness
    all_time = _all_time_revenue_text(h)
    _enter_lane(h, lane)

    # CONTROL 1 - the warehouse really has no 2099 rows, so a correct gate has
    # nothing to answer with.
    n_2099 = execute_sql(
        "SELECT COUNT(*)::INTEGER AS n FROM transactions WHERE year(ts) = 2099",
        path=h.warehouse,
    )[0]["n"]
    control(n_2099 == 0, f"seed holds {n_2099} transactions dated 2099")

    # CONTROL 2 - the demo lane's own abstain gate works: a future-period ask in
    # the same Space is refused with no figure.
    s, env = h.ask(PREDICTIVE_Q, space_id=FINANCE, session_id=f"ctl_pred_{lane}")
    require_envelope(s, env)
    control(
        env["ask_mode"] == "demo" and _refused_without_figures(env),
        f"demo lane did not abstain on a forecast ask: {_seen(s, env)}",
    )

    # CONTROL 3 - the unqualified ask returns the all-time total: the figure the
    # 2099 ask is about to repeat.
    s, env = h.ask(REVENUE_Q, space_id=FINANCE, session_id=f"ctl_total_{lane}")
    require_envelope(s, env)
    control(
        env["ask_mode"] == "demo"
        and env["badge"] == "L2_VALIDATED"
        and all_time in env["text"],
        f"demo lane did not return the all-time total: {_seen(s, env)}",
    )

    # GAP - a year the warehouse has no data for.
    s, env = h.ask(REVENUE_2099_Q, space_id=FINANCE, session_id=f"gap_2099_{lane}")
    require_envelope(s, env)
    assert _refused_without_figures(env) and all_time not in env["text"], (
        "demo lane ignored the year and answered a 2099 revenue ask with the "
        f"all-time total: {_seen(s, env)} values={env.get('values')!r}"
    )


# ------------------------- gap: demo-fallback-converts-live-refusal-to-demo-numbers

#: 403 policy codes in chat.py's ``_STATUS_BY_CODE`` that ``_POLICY_CODES`` omits,
#: so the fallback screen lets them through to the demo router.
UNSCREENED_POLICY_CODES = ["manifest_malformed", "sql_not_analyzable", "manifest_not_yet_valid"]


@pytest.mark.parametrize("code", UNSCREENED_POLICY_CODES)
@gap(
    row="chat-ask:demo-fallback-ask-error",
    gate="G8 named abstain on failure",
    ticket="new",
    gap_id=GAP_FALLBACK,
)
def test_fallback_flag_answers_an_unscreened_policy_refusal_with_demo_numbers(harness, code):  # type: ignore[no-untyped-def]
    """Cortex refuses with a 403 policy code; flag on, the customer gets revenue."""
    h = harness
    refusal = RuntimeError(f"ask failed: {code}: engine refused the manifest")

    # CONTROL 1 - flag off, the refusal is named: HTTP 403 with this code. This is
    # what the flag-on request below must also return, and it proves the injected
    # error classifies to this code.
    h.cortex.ask_raises = refusal
    s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id=f"ctl_off_{code}")
    control(
        s == 403 and _detail_code(body) == code,
        f"flag off, {code} should be a named 403: {_seen(s, body)}",
    )

    # CONTROL 2 - flag on, a screened policy code (path_not_allowed) is refused,
    # not masked: no demo envelope, no figure.
    h.set_env(DMS_DEMO_FALLBACK="1")
    h.cortex.ask_raises = RuntimeError(f"ask failed: {PATH_NOT_ALLOWED}")
    s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id=f"ctl_pna_{code}")
    control(s == 200, f"flag on, path_not_allowed: {_seen(s, body)}")
    assert_envelope(body)
    control(
        _refused_without_figures(body) and not body.get("demo_fallback_used"),
        f"flag on, path_not_allowed was masked: {_seen(s, body)}",
    )

    # GAP - flag on, same Space and question, one of the unscreened codes.
    asks_before = len(h.cortex.asks)
    h.cortex.ask_raises = refusal
    s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id=f"gap_{code}")
    control(
        len(h.cortex.asks) == asks_before + 1,
        "the live attempt did not reach Cortex.ask, so the refusal was not injected",
    )
    assert s == 403 and _detail_code(body) == code, (
        f"flag on, Cortex refused with {code} and the customer was not told: "
        f"{_seen(s, body)} values={body.get('values')!r}"
    )


@pytest.mark.parametrize("cause", ["verified_query_submit_refused", "envelope_invariant_E7"])
@gap(
    row="chat-ask:demo-fallback-exception",
    gate="G8 named abstain on failure",
    ticket="new",
    gap_id=GAP_FALLBACK,
)
def test_fallback_flag_answers_a_failed_live_attempt_with_demo_numbers(harness, cause):  # type: ignore[no-untyped-def]
    """A live failure that is a plain Exception skips the policy screen entirely."""
    from cortex_client.models import AskResponse
    from dms_executor.verified_queries import register_verified_query

    h = harness

    if cause == "verified_query_submit_refused":
        # CONTROL 1 - flag on, the same policy refusal arriving through the ask
        # path is refused (Space-boundary ABSTAIN), never answered with demo numbers.
        h.set_env(DMS_DEMO_FALLBACK="1")
        h.cortex.ask_raises = RuntimeError(f"ask failed: {PATH_NOT_ALLOWED}")
        s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id="ctl_ask_pna")
        control(s == 200, f"flag on, ask-path refusal: {_seen(s, body)}")
        assert_envelope(body)
        control(
            _refused_without_figures(body) and not body.get("demo_fallback_used"),
            f"flag on, ask-path path_not_allowed was masked: {_seen(s, body)}",
        )

        # The steward registered this exact question; Cortex refuses the SQL.
        # maybe_verified_ask does not catch the SubmitError, so it propagates out of
        # live_ask as a plain Exception.
        h.cortex.ask_raises = None
        register_verified_query(
            space_id=FINANCE, question=REVENUE_Q, sql=REVENUE_SQL, path=h.warehouse
        )
        h.cortex.submit_raises = RuntimeError(f"submit failed: {PATH_NOT_ALLOWED}")

        # CONTROL 2 - flag off, the failure is named and reaches Cortex: the steward
        # SQL was submitted and refused, and the customer gets a 503 that says so.
        h.set_env(DMS_DEMO_FALLBACK="0")
        s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id="ctl_vq_off")
        control(
            s == 503
            and _detail_code(body) == "live_ask_failed"
            and "path_not_allowed" in str(body["detail"].get("message")),
            f"flag off, verified-query refusal should be a named 503: {_seen(s, body)}",
        )
        control(
            h.cortex.submitted_sql() == [REVENUE_SQL],
            f"steward SQL was not submitted exactly once: {h.cortex.submitted_sql()}",
        )

        attempts_before = len(h.cortex.sql_submits)
        h.set_env(DMS_DEMO_FALLBACK="1")
        s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id="gap_vq_on")
        control(
            len(h.cortex.sql_submits) == attempts_before + 1,
            "the live attempt did not submit the steward SQL, so the refusal was not injected",
        )
    else:
        sources = [
            {
                "ref_id": "ref_gm",
                "name": "transactions",
                "kind": "sql",
                "container": "transactions",
                "row_count": 1,
                "contribution": 1.0,
                "origin_uri": "duckdb://dms_demo/transactions",
            }
        ]
        good = AskResponse(
            answer="Total revenue was RM 5.00.",
            badge="query_skill",
            sql_used="SELECT 5 AS r",
            values=[{"id": "v", "value": 5.0}],
            rows=[{"r": 5.0}],
            audit_id="aud_gm_e7",
            route="sql",
            drillthrough_token="tok_gm_0123456789",
            contributing_sources=sources,
        )
        # Same engine answer with a drillthrough token E7 will not accept.
        bad = good.model_copy(update={"drillthrough_token": "abc"})

        # CONTROL 1 - flag on, the engine answer with a well-formed token is served
        # as the live answer: the fixture response is otherwise valid.
        h.set_env(DMS_DEMO_FALLBACK="1")
        h.cortex.ask_response = good
        s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id="ctl_e7_good")
        require_envelope(s, body)
        control(
            body["ask_mode"] == "live"
            and not body.get("demo_fallback_used")
            and body["badge"] == "L2_VALIDATED"
            and "RM 5.00" in body["text"],
            f"valid engine answer was not served live: {_seen(s, body)}",
        )

        # CONTROL 2 - flag off, the E7 violation is a named 503 that quotes E7.
        h.cortex.ask_response = bad
        h.set_env(DMS_DEMO_FALLBACK="0")
        s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id="ctl_e7_off")
        control(
            s == 503
            and _detail_code(body) == "live_ask_failed"
            and "E7" in str(body["detail"].get("message")),
            f"flag off, an E7 violation should be a named 503: {_seen(s, body)}",
        )

        attempts_before = len(h.cortex.asks)
        h.set_env(DMS_DEMO_FALLBACK="1")
        s, body = h.ask(REVENUE_Q, space_id=FINANCE, session_id="gap_e7_on")
        control(
            len(h.cortex.asks) == attempts_before + 1,
            "the live attempt did not reach Cortex.ask, so the invalid answer was not injected",
        )

    # GAP - flag on: a failed live attempt must come back refused, not as a
    # confident answer (HTTP 200, abstained false) the customer will quote.
    served_a_confident_answer = (
        s == 200 and isinstance(body, dict) and body.get("abstained") is False
    )
    assert not served_a_confident_answer, (
        f"flag on, the live attempt failed ({cause}) and the customer was handed "
        f"demo numbers: {_seen(s, body)} values={body.get('values')!r} "
        f"assumptions={body.get('assumptions')!r}"
    )
