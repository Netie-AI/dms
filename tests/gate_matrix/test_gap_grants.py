"""Plan E gate-matrix, theme "grants" (gate G1: the Space grant, G8: a named refusal).

Four gap tests. Each is expected to FAIL on its own assertion today (XFAIL) and to
pass once the gate exists. Each stands on a CONTROL that must pass today: a sibling
request on an already-gated path through the same fixture.

  no-space-ask-widest-grant              no space_id widens the ask to every DEMO table
  ungranted-tick-dropped-not-refused     x2: harness-generative ladder, CCA cascade
  bronze-grant-warehouse-alias-collision the ``warehouse_`` alias in ``table_is_granted``
                                         reads another Space's bronze table

The fake Cortex used by the first test models only what the tests need: an engine that
honours the manifest DMS bound for the session (the ``ManifestEnforcingCortex`` pattern of
tests/test_space_boundary_envelope.py). What the user would see from the REAL engine is
not claimed here; what DMS asked the engine to allow is read from the bound manifest.
"""

from __future__ import annotations

from typing import Any

from _harness import (
    FINANCE,
    WAREHOUSE_OPS,
    control,
    csv_bytes,
    gap,
    require_envelope,
)
from dms_executor.demo_pack import CAPACITY_UTILISATION_Q

ALERTS_Q = "List all open alerts by severity"
LOCATIONS_Q = "List every location with its capacity"


# --------------------------------------------------------------------------
# no-space-ask-widest-grant
# --------------------------------------------------------------------------


def _manifest_honouring_engine(holder: dict[str, Any]):  # type: ignore[no-untyped-def]
    """Cortex.ask stand-in that answers only from tables the BOUND manifest names.

    ``alert`` questions need ``alerts``; ``location`` questions need ``locations``.
    The rows are real, read from the tmp demo warehouse. A question whose table is
    not in the manifest is refused on route ``refused``, the way the engine refuses.
    """
    from cortex_client.models import AskResponse
    from dms_executor.demo_warehouse import execute_sql

    needs = {
        "alert": (
            "alerts",
            "SELECT alert_id, severity, location_id, message FROM alerts "
            "WHERE NOT resolved ORDER BY alert_id",
        ),
        "location": (
            "locations",
            "SELECT location_code, capacity_kg FROM locations ORDER BY location_code",
        ),
    }

    def engine(req: Any) -> Any:
        h = holder["h"]
        bound: dict[str, set[str]] = {}
        for sub in h.cortex.bind_submits:
            bound[sub.manifest.session_id] = set(sub.manifest.row_predicates)
        readable = bound.get(req.session_id, set())
        for word, (table, sql) in needs.items():
            if word in req.question.lower():
                if table not in readable:
                    return AskResponse(
                        answer=(
                            f"I cannot answer that here - this scope has no access to "
                            f"{table!r}. Ask in a Space that does, or request the grant."
                        ),
                        abstained=True,
                        badge="abstain",
                        rows=[],
                        route="refused",
                        audit_id="aud_gm_refused",
                    )
                rows = execute_sql(sql, path=h.warehouse)
                return AskResponse(
                    answer=f"Found {len(rows)} rows.",
                    abstained=False,
                    badge="generated",
                    sql_used=sql,
                    rows=rows,
                    route="sql",
                    audit_id="aud_gm_ok",
                )
        return AskResponse(answer="n/a", abstained=True, badge="abstain", route="abstain")

    return engine


@gap(
    "chat-ask:cortex-contract-ask,chat-ask:verified-query,chat-ask:governed-pack,"
    "chat-ask:harness-generative-miss,chat-ask:generative-named-abstain,"
    "chat-ask:cascade-abstain,chat-ask:cascade-attach,chat-ask:generative-multi-grain,"
    "chat-ask:generative-insights-sql,chat-ask:generative-ontology-plan,"
    "ui:exclusion-auto-confirm,mcp:ask",
    "G1",
    "new",
    "no-space-ask-widest-grant",
)
def test_ask_without_a_space_does_not_widen_to_a_table_no_space_grants(
    harness_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``alerts`` is granted by no Space (demo_grants.py:39-48, A-0007). A named Space
    refuses it; the same ask with no ``space_id`` should too (company_default_tables,
    demo_grants.py:61-82). The lane exercised is the Cortex contract ask; the other
    cells read the same ``grantable_tables(space_id=None)``."""
    holder: dict[str, Any] = {}
    h = harness_factory(ask_fn=_manifest_honouring_engine(holder))
    holder["h"] = h

    # CONTROL 1: a named Space is refused alerts, as an ABSTAIN envelope.
    s, env = h.ask(ALERTS_Q, space_id=FINANCE, session_id="ses_ctl_fin_alerts")
    require_envelope(s, env)
    control(
        env["abstained"] is True and env["badge"] == "ABSTAIN" and not env["rows"],
        f"FINANCE must be refused alerts: {env['badge']} {env['text']!r}",
    )
    control(
        len(h.cortex.asks) == 1 and "alerts" in env["text"],
        f"the refusal must come from the manifest-honouring engine: {env['text']!r}",
    )
    # CONTROL 2: the engine answers when the table IS in the manifest (a Space that
    # grants locations), so the refusal above is the grant and not a blanket no.
    s, env = h.ask(LOCATIONS_Q, space_id=FINANCE, session_id="ses_ctl_fin_loc")
    require_envelope(s, env)
    control(
        env["abstained"] is False and len(env["rows"]) > 0,
        f"FINANCE grants locations and must be answered: {env['badge']} {env['text']!r}",
    )
    # CONTROL 3: no space_id is not blanket-refused. A company-scoped pack metric
    # (locations) is still answered with real rows.
    s, env = h.ask(CAPACITY_UTILISATION_Q, session_id="ses_ctl_nospace_pack")
    require_envelope(s, env)
    control(
        s == 200 and env["abstained"] is False and env["badge"] == "L1_GOVERNED_METRIC"
        and len(env["rows"]) > 0,
        f"no-space company-scoped metric must still answer: {env['badge']} {env['text']!r}",
    )

    # THE GAP: the same alerts ask with no space_id.
    s, env = h.ask(ALERTS_Q, session_id="ses_gap_nospace_alerts")
    require_envelope(s, env)
    bound = [
        sorted(sub.manifest.row_predicates)
        for sub in h.cortex.bind_submits
        if sub.manifest.session_id == "ses_gap_nospace_alerts"
    ]
    assert env["abstained"] is True and not env["rows"], (
        f"no-space ask was served: badge={env['badge']} rows={env['rows']!r}; "
        f"manifest bound for the session named {bound}"
    )


# --------------------------------------------------------------------------
# ungranted-tick-dropped-not-refused
# --------------------------------------------------------------------------


def _refused_as_grounding_not_grantable(status: int, body: Any) -> bool:
    detail = body.get("detail") if isinstance(body, dict) else None
    return (
        status == 403
        and isinstance(detail, dict)
        and detail.get("code") == "grounding_not_grantable"
        and "alerts" in (detail.get("ungrantable_tables") or [])
    )


@gap(
    "chat-ask:harness-generative-miss",
    "G8",
    "#307",
    "ungranted-tick-dropped-not-refused",
)
def test_generative_ladder_refuses_an_ungranted_tick_by_name(harness) -> None:  # type: ignore[no-untyped-def]
    """FINANCE ticks ``alerts`` (no Space grants it). The product ladder refuses with
    403 grounding_not_grantable; the generative ladder (DMS_HARNESS_ASK_PATHS=1)
    drops the tick and answers a generic miss with ``grounded_tables`` empty."""
    harness.set_env(DMS_HARNESS_ASK_PATHS="1")
    tick = {"space_id": FINANCE, "grounded_tables": ["alerts"]}

    # CONTROL 1: the already-gated product ladder refuses the same tick, by name.
    s, body = harness.ask("total stock value", session_id="ses_ctl_prod", **tick)
    control(
        _refused_as_grounding_not_grantable(s, body),
        f"product ladder must refuse the ungranted tick: {s} {body!r}",
    )
    # CONTROL 2: the generative ladder is reachable (flag on), no tick: a named miss.
    s, env = harness.ask(
        "total stock value", space_id=FINANCE, session_id="ses_ctl_gen", ask_path="generative"
    )
    require_envelope(s, env)
    control(
        env["abstained"] is True and "generative miss" in " ".join(env.get("assumptions") or []),
        f"generative ladder must be live and answer a named miss: {env!r}",
    )

    # THE GAP: the same tick on the generative ladder.
    s, body = harness.ask(
        "total stock value", session_id="ses_gap_gen", ask_path="generative", **tick
    )
    assert _refused_as_grounding_not_grantable(s, body), (
        f"generative ladder did not refuse the ungranted tick: HTTP {s}; "
        f"abstained={body.get('abstained')!r} text={body.get('text')!r} "
        f"grounded_tables={body.get('grounded_tables')!r} assumptions={body.get('assumptions')!r}"
    )


CASCADE_Q = "rental across SEA, commercial only"


@gap(
    "chat-ask:cascade-abstain",
    "G8",
    "#307",
    "ungranted-tick-dropped-not-refused",
)
def test_cascade_refuses_an_ungranted_tick_by_name(harness) -> None:  # type: ignore[no-untyped-def]
    """Same defect on the CCA cascade (DMS_CCA_CASCADE=1): a blocked cascade returns
    its ABSTAIN with ``grounded_tables`` empty, and the ungranted tick vanishes."""
    harness.set_env(DMS_CCA_CASCADE="1")
    tick = {"space_id": FINANCE, "grounded_tables": ["alerts"]}

    # CONTROL 1: the cascade is engaged and blocks this question with no tick.
    s, env = harness.ask(CASCADE_Q, space_id=FINANCE, session_id="ses_ctl_casc")
    require_envelope(s, env)
    control(
        env["abstained"] is True and bool(env.get("constraint_trace")),
        f"cascade must engage and block {CASCADE_Q!r}: {env!r}",
    )
    # CONTROL 2: with the cascade off, the already-gated product path refuses the tick.
    harness.set_env(DMS_CCA_CASCADE="0")
    s, body = harness.ask(CASCADE_Q, session_id="ses_ctl_nocasc", **tick)
    control(
        _refused_as_grounding_not_grantable(s, body),
        f"without the cascade the tick must be refused: {s} {body!r}",
    )

    # THE GAP: cascade on, the same tick.
    harness.set_env(DMS_CCA_CASCADE="1")
    s, body = harness.ask(CASCADE_Q, session_id="ses_gap_casc", **tick)
    assert _refused_as_grounding_not_grantable(s, body), (
        f"cascade did not refuse the ungranted tick: HTTP {s}; "
        f"abstained={body.get('abstained')!r} grounded_tables={body.get('grounded_tables')!r} "
        f"text={str(body.get('text'))[:160]!r}"
    )


# --------------------------------------------------------------------------
# bronze-grant-warehouse-alias-collision
# --------------------------------------------------------------------------

BRONZE_Q = "In foo.xlsx sheet Sales, what are the top 3 categories by sales_value_myr?"
OPS_SECRET = "OPS-ONLY-CATEGORY"


@gap(
    "chat-ask:bronze-grant-abstain",
    "G1",
    "new",
    "bronze-grant-warehouse-alias-collision",
)
def test_bronze_ask_does_not_read_another_spaces_table_through_the_warehouse_alias(
    harness,  # type: ignore[no-untyped-def]
) -> None:
    """``table_is_granted`` (ontology.py:398-404) accepts ``warehouse_<t>`` as an alias of
    ``<t>`` on bare names. FINANCE holds ``warehouse_foo_Sales``; only WAREHOUSE_OPS holds
    ``foo_Sales``. A FINANCE ask naming foo.xlsx / Sales passes the grant check and the
    lane then reads ``bronze.foo_Sales`` by name (bronze_sheet_ask.py ``_db_with_table``).

    The uploads are CSVs named after the tables: ingest names an xlsx sheet table
    ``{stem}_{sheet}`` (bronze_table_for_sheet), so ``foo_Sales.csv`` and
    ``warehouse_foo.xlsx`` / sheet Sales land on the same idents the check compares."""
    harness.upload(
        "foo_Sales.csv",
        csv_bytes(
            ["category", "sales_value_myr"],
            [[OPS_SECRET, 900], ["ops-b", 20], ["ops-c", 5]],
        ),
        space_id=WAREHOUSE_OPS,
    )

    # CONTROL 1: the table's own Space is answered from it (the lane and fixture work).
    s, env = harness.ask(BRONZE_Q, space_id=WAREHOUSE_OPS)
    require_envelope(s, env)
    control(
        env["abstained"] is False and env["badge"] == "L0_CERTIFIED"
        and any(r.get("category") == OPS_SECRET for r in env["rows"]),
        f"WAREHOUSE_OPS must be answered from its own foo_Sales: {env!r}",
    )
    # CONTROL 2: FINANCE, which holds nothing named foo_Sales, is refused by name.
    s, env = harness.ask(BRONZE_Q, space_id=FINANCE)
    require_envelope(s, env)
    control(
        env["abstained"] is True and "ungranted_table" in env["text"] and not env["rows"],
        f"FINANCE must be refused foo_Sales: {env['badge']} {env['text']!r}",
    )

    # FINANCE now uploads its OWN, differently named table: warehouse_foo_Sales.
    harness.upload(
        "warehouse_foo_Sales.csv",
        csv_bytes(["category", "sales_value_myr"], [["fin-a", 3]]),
        space_id=FINANCE,
    )
    control(
        "bronze.warehouse_foo_Sales" in harness.executor.grantable_tables(space_id=FINANCE)
        and "bronze.foo_Sales" not in harness.executor.grantable_tables(space_id=FINANCE),
        "fixture: FINANCE must hold warehouse_foo_Sales and must not hold foo_Sales",
    )

    # THE GAP: the same ask from FINANCE.
    s, env = harness.ask(BRONZE_Q, space_id=FINANCE)
    require_envelope(s, env)
    assert env["abstained"] is True and not any(
        r.get("category") == OPS_SECRET for r in env["rows"]
    ), (
        f"FINANCE was served WAREHOUSE_OPS's foo_Sales: badge={env['badge']} "
        f"rows={env['rows']!r} grounded_tables={env.get('grounded_tables')!r}"
    )
