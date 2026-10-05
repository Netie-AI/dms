"""Plan E gate-matrix, theme "lanes": answer lanes that skip a gate.

Every test here is expected to XFAIL on the commit it was written against
(7a8d6c1 = origin/main). Each does the same three things, in this order:

  1. CONTROL: a sibling request on an already-gated path, behind ``control()``,
     so a broken fixture is a hard FAILURE (``ControlFailed``) and never an
     absorbed XFAIL.
  2. ``require_envelope()`` on the gap request, so a 404/422/503 cannot satisfy
     the gap assertion by accident.
  3. ONE plain ``assert`` on the user-visible output (badge, rendered text,
     returned rows, ledger/submit record). That is the gap.

Gaps: bronze-sheet-trycast-drops-rows-silently (G4), bronze-sheet-l0-minted-locally
(G7), stored-sql-no-fanout-guard (G3), rule12-aggregate-zero-row-green (G8).

``dms_*`` imports are lazy: ``dms_api`` builds an app and starts an Executor at
import time, and conftest's offline guard only covers test bodies.
"""

from __future__ import annotations

import re
from typing import Any

from _harness import (
    FINANCE,
    WAREHOUSE_OPS,
    assert_envelope,
    control,
    gap,
    require_envelope,
)

# --------------------------------------------------------------------------
# shared fixtures: an xlsx sheet ingested into FINANCE through the real ingest
# --------------------------------------------------------------------------

_TOP3 = "In {filename} sheet Sales, what are the top 3 categories by sales_value_myr?"

#: Seven source rows. In the DIRTY copy one Electronics cell is the text "RM 1200".
_CLEAN_ROWS: list[tuple[str, Any]] = [
    ("Electronics", 1000.0),
    ("Electronics", 1200.0),
    ("Electronics", 800.0),
    ("Home", 900.0),
    ("Home", 700.0),
    ("Sports", 300.0),
    ("Misc", 100.0),
]
_DIRTY_ROWS: list[tuple[str, Any]] = [
    (cat, "RM 1200" if val == 1200.0 else val) for cat, val in _CLEAN_ROWS
]

#: What a correct answer shows, computed here from the source rows and NOT from
#: the lane under test ("RM 1200" read as 1200).
_ORACLE_TOP3 = {"Electronics": 3000.0, "Home": 1600.0, "Sports": 300.0}


def _xlsx_bytes(rows: list[tuple[str, Any]]) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["category", "sales_value_myr"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ingest_sales(harness: Any, filename: str, rows: list[tuple[str, Any]]) -> str:
    """Ingest ``filename`` / sheet Sales into FINANCE, as the Studio upload does."""
    from dms_executor.batch_ingest import ingest_batch
    from dms_executor.bronze import bronze_table_for_sheet

    receipt = ingest_batch(
        [(filename, _xlsx_bytes(rows))], path=harness.warehouse, space_id=FINANCE
    )
    table = bronze_table_for_sheet(filename, "Sales")
    control(
        receipt.ingested == 1 and any(f.table == table for f in receipt.files),
        f"{filename} did not land as {table}: {receipt}",
    )
    control(
        table in harness.executor.grantable_tables(space_id=FINANCE),
        f"{table} is not granted to FINANCE after ingest",
    )
    return table


def _column_type_and_rows(harness: Any, table: str) -> tuple[str, list[str]]:
    import duckdb

    ident = table.split(".", 1)[-1]
    con = duckdb.connect(str(harness.warehouse), read_only=True)
    try:
        kind = str(
            next(
                r[1]
                for r in con.execute(f'DESCRIBE bronze."{ident}"').fetchall()
                if r[0] == "sales_value_myr"
            )
        )
        cells = [
            str(r[0])
            for r in con.execute(f'SELECT sales_value_myr FROM bronze."{ident}"').fetchall()
        ]
    finally:
        con.close()
    return kind, cells


def _by_category(env: dict[str, Any]) -> dict[str, float]:
    return {str(r.get("category")): r.get("sales_value_myr") for r in env["rows"]}


# A dropped-row notice names a count of 1: "dropped 1 of 7 rows", "1 row excluded".
# (?<![\w.]) / (?!\w|\.\d) keep "1800.0" and "1.5" from reading as a count of 1.
_DROP_WORD = r"(?:dropp|exclud|skipp|unparse|unparsed|non-?numeric|ignor|discard|omit)\w*"
_ONE = r"(?<![\w.])1(?!\w|\.\d)"
_DROP_NOTICE = re.compile(
    rf"{_DROP_WORD}[^.\n]{{0,60}}{_ONE}|{_ONE}[^.\n]{{0,60}}{_DROP_WORD}", re.I
)


def _disclosure_surface(env: dict[str, Any]) -> list[str]:
    """Every place the customer is told what the answer left out."""
    exclude = (env.get("audit_receipt") or {}).get("exclude") or {}
    return [
        str(env.get("text") or ""),
        *[str(a) for a in env.get("assumptions") or []],
        str(exclude.get("why") or ""),
        *[str(r) for r in exclude.get("reasons") or []],
    ]


def _states_a_dropped_row(env: dict[str, Any]) -> bool:
    return any(_DROP_NOTICE.search(s) for s in _disclosure_surface(env))


# --------------------------------------------------------------------------
# GAP bronze-sheet-trycast-drops-rows-silently (G4 typed ingest)
# --------------------------------------------------------------------------


@gap(
    "chat-ask:bronze-sheet",
    "G4 typed ingest",
    "new",
    "bronze-sheet-trycast-drops-rows-silently",
)
def test_bronze_sheet_sum_does_not_silently_drop_an_unparseable_cell(harness) -> None:  # type: ignore[no-untyped-def]
    # CONTROL 1: the same sheet with every cell numeric is answered exactly, with
    # no dropped-row notice. This is the lane working, and it calibrates the
    # notice detector so it cannot match a clean answer.
    _ingest_sales(harness, "clean_sales.xlsx", _CLEAN_ROWS)
    s, clean = harness.ask(
        _TOP3.format(filename="clean_sales.xlsx"), space_id=FINANCE, session_id="ses_clean"
    )
    require_envelope(s, clean)
    # (No badge check: that is gap bronze-sheet-l0-minted-locally's business.)
    control(
        clean["route"] == "bronze_sheet" and clean["abstained"] is False,
        f"clean sheet was not answered by the bronze lane: {clean['badge']} {clean['text']!r}",
    )
    control(
        _by_category(clean) == _ORACLE_TOP3,
        f"clean sheet totals {_by_category(clean)} != oracle {_ORACLE_TOP3}",
    )
    control("Electronics" in clean["text"] and "3000.0" in clean["text"], clean["text"])
    control(not _states_a_dropped_row(clean), "a clean answer must carry no dropped-row notice")

    # CONTROL 2: the dirty copy really landed with the text cell, in a VARCHAR column.
    dirty_table = _ingest_sales(harness, "granted.xlsx", _DIRTY_ROWS)
    kind, cells = _column_type_and_rows(harness, dirty_table)
    control(
        kind == "VARCHAR" and "RM 1200" in cells and len(cells) == len(_DIRTY_ROWS),
        f"fixture premise: sales_value_myr is {kind}, cells {cells}",
    )

    # The gap request. Same lane, same question shape, one unparseable cell.
    s, env = harness.ask(
        _TOP3.format(filename="granted.xlsx"), space_id=FINANCE, session_id="ses_dirty"
    )
    require_envelope(s, env)
    control(
        env["route"] == "bronze_sheet" and env["rows"],
        f"bronze lane did not answer: {env['text']!r}",
    )

    shown = _by_category(env).get("Electronics")
    # A correct gate does one of three things: abstains, includes the parsed
    # value in the total, or says that a row was left out of the figure.
    assert (
        env["abstained"] is True
        or shown == _ORACLE_TOP3["Electronics"]
        or _states_a_dropped_row(env)
    ), (
        f"{env['badge']} shows Electronics={shown} (the sheet holds 3000.0 with 'RM 1200' read "
        f"as 1200) and says nothing about the one cell it dropped. "
        f"text={env['text']!r} assumptions={env['assumptions']!r}"
    )


# --------------------------------------------------------------------------
# GAP bronze-sheet-l0-minted-locally (G7 Cortex submit: signed manifest + ledger)
# --------------------------------------------------------------------------


@gap(
    "chat-ask:bronze-sheet",
    "G7 Cortex submit (signed manifest + ledger)",
    "new",
    "bronze-sheet-l0-minted-locally",
)
def test_bronze_sheet_l0_is_certified_through_a_cortex_submit_and_ledger_entry(harness) -> None:  # type: ignore[no-untyped-def]
    from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q, SPEND_BY_COUNTRY_SQL

    cx = harness.cortex

    # CONTROL: the pack lane in the same Space. A governed answer there has one SQL
    # submit, one ledger append, and an audit_id that is the ledger entry's id.
    s, pack = harness.ask(SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_pack")
    require_envelope(s, pack)
    control(
        pack["badge"] == "L1_GOVERNED_METRIC" and pack["abstained"] is False and pack["rows"],
        pack["text"],
    )
    control(cx.submitted_sql() == [SPEND_BY_COUNTRY_SQL], f"pack submits: {cx.submitted_sql()}")
    control(cx.ledger_events() == ["ask.governed_metric"], f"pack ledger: {cx.ledger_events()}")
    control(
        pack["audit_id"] == cx.ledger_entry_id,
        f"pack audit_id {pack['audit_id']!r} != ledger entry {cx.ledger_entry_id!r}",
    )
    submits_before, appends_before = len(cx.sql_submits), len(cx.appends)

    # The gap request: a granted workbook sheet, all cells numeric, clean ingest.
    _ingest_sales(harness, "granted.xlsx", _CLEAN_ROWS)
    s, env = harness.ask(
        _TOP3.format(filename="granted.xlsx"), space_id=FINANCE, session_id="ses_bronze"
    )
    require_envelope(s, env)
    control(
        env["route"] == "bronze_sheet" and env["rows"],
        f"bronze lane did not answer: {env['text']!r}",
    )
    control(_by_category(env) == _ORACLE_TOP3, f"bronze rows {_by_category(env)}")

    submitted = len(cx.sql_submits) - submits_before
    appended = len(cx.appends) - appends_before
    # A correct gate either withholds the top trust tier, or earns it the way the
    # pack lane does: Cortex ran the SQL and the ledger holds the entry.
    assert env["badge"] != "L0_CERTIFIED" or (
        submitted == 1 and appended == 1 and env["audit_id"] == cx.ledger_entry_id
    ), (
        f"{env['badge']} with audit_id={env['audit_id']!r}: this lane made {submitted} Cortex SQL "
        f"submit(s) and {appended} ledger append(s); a governed answer carries the ledger "
        f"entry id {cx.ledger_entry_id!r}"
    )


# --------------------------------------------------------------------------
# GAP stored-sql-no-fanout-guard (G3 join rule + fan-out guard)
# --------------------------------------------------------------------------

_ORACLE_SQL = (
    "SELECT location_id, ROUND(SUM(quantity_kg*unit_cost_myr),2) AS revenue_myr "
    "FROM transactions GROUP BY location_id"
)
# inventory holds two rows each for WH-A and WH-B, so this join repeats every
# transactions row of those two warehouses.
_FANOUT_SQL = (
    "SELECT t.location_id, ROUND(SUM(t.quantity_kg*t.unit_cost_myr),2) AS revenue_myr "
    "FROM transactions t JOIN inventory i ON i.location_id = t.location_id "
    "GROUP BY t.location_id"
)
_VQ_ROUTE = "/v1/studio/verified-queries"


def _allow_studio_gate(monkeypatch: Any) -> None:
    """The Studio mutation route fails closed without a reachable F5 gate. The
    fake Cortex has no base_url, so allow it; the gate is not under test here."""
    import dms_api.routes.studio as studio
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(allowed=True, reason="test_allow", action=action),
    )


def _revenue_by_warehouse(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(r["location_id"]): r["revenue_myr"] for r in rows}


@gap(
    "chat-ask:verified-query",
    "G3 join rule + fan-out guard",
    "new",
    "stored-sql-no-fanout-guard",
)
def test_steward_registered_fanout_join_is_not_served_as_a_certified_figure(
    harness, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from dms_executor.demo_warehouse import execute_sql

    _allow_studio_gate(monkeypatch)
    oracle = _revenue_by_warehouse(execute_sql(_ORACLE_SQL, path=harness.warehouse))
    fanned = _revenue_by_warehouse(execute_sql(_FANOUT_SQL, path=harness.warehouse))
    control(
        len(oracle) == 5
        and fanned["WH-A"] == 2 * oracle["WH-A"]
        and fanned["WH-C"] == oracle["WH-C"],
        f"fixture premise: the seed join should double WH-A only where inventory has two rows: "
        f"oracle={oracle} fanned={fanned}",
    )

    # CONTROL: a single-table VQ is accepted and served at L0 with the exact figures.
    reg = harness.client.post(
        _VQ_ROUTE,
        json={
            "space_id": FINANCE,
            "question": "Revenue per warehouse from transactions only",
            "sql": _ORACLE_SQL,
        },
    )
    control(reg.status_code == 200, f"single-table VQ registration: {reg.status_code} {reg.text}")
    s, ctl = harness.ask(
        "Revenue per warehouse from transactions only", space_id=FINANCE, session_id="ses_vq_ctl"
    )
    require_envelope(s, ctl)
    control(
        ctl["route"] == "verified_query"
        and ctl["badge"] == "L0_CERTIFIED"
        and not ctl["abstained"],
        f"single-table VQ not served: {ctl['badge']} {ctl['text']!r}",
    )
    control(
        _revenue_by_warehouse(ctl["rows"]) == oracle,
        f"control rows {ctl['rows']} != oracle {oracle}",
    )
    control(f"location_id=WH-A, revenue_myr={oracle['WH-A']}" in ctl["text"], ctl["text"])
    control(
        len(harness.cortex.sql_submits) == 1
        and harness.cortex.ledger_events() == ["ask.verified_query"],
        f"control submits={harness.cortex.submitted_sql()} ledger={harness.cortex.ledger_events()}",
    )

    # The gap request: a steward registers a join that repeats parent rows.
    reg = harness.client.post(
        _VQ_ROUTE,
        json={"space_id": FINANCE, "question": "Revenue by warehouse", "sql": _FANOUT_SQL},
    )
    control(
        reg.status_code < 500,
        f"registration errored rather than answering: {reg.status_code} {reg.text}",
    )
    if reg.status_code in (400, 422):
        # A correct gate may refuse the asset when it is registered.
        assert reg.json().get("detail"), f"registration refused without a reason: {reg.text}"
        return
    control(
        reg.status_code == 200 and reg.json().get("sql") == _FANOUT_SQL,
        f"fan-out VQ registration: {reg.status_code} {reg.text}",
    )

    s, env = harness.ask("Revenue by warehouse", space_id=FINANCE, session_id="ses_vq_gap")
    require_envelope(s, env)

    # Or it may refuse at serve time, or serve the true figures.
    shown = _revenue_by_warehouse(env["rows"]) if env["rows"] else {}
    assert env["abstained"] is True or (
        shown == oracle and f"location_id=WH-A, revenue_myr={oracle['WH-A']}" in env["text"]
    ), (
        f"{env['badge']} served WH-A revenue_myr={shown.get('WH-A')} and WH-B={shown.get('WH-B')}; "
        f"the transactions table sums to {oracle['WH-A']} and {oracle['WH-B']}. "
        f"The registration was accepted ({reg.status_code}). text={env['text']!r}"
    )


# --------------------------------------------------------------------------
# GAP rule12-aggregate-zero-row-green (G8 named abstain on failure)
# --------------------------------------------------------------------------

_BETA_Q = "total quantity of BETA in stock"
_BETA_SQL = "SELECT SUM(quantity_kg) AS total FROM inventory WHERE sku = 'BETA'"
_SKU_BETA_Q = "total quantity of SKU-BETA in stock"
_SKU_BETA_SQL = "SELECT SUM(quantity_kg) AS total FROM inventory WHERE sku = 'SKU-BETA'"


def _engine_answer(*, text: str, sql: str, rows: list[dict[str, Any]]) -> Any:
    """What the Cortex ask returns for generated SQL: engine badge "generated"."""
    from cortex_client.models import AskResponse

    return AskResponse(
        answer=text, badge="generated", sql_used=sql, rows=rows, route="sql", audit_id="aud_gm_r12"
    )


@gap(
    "chat-ask:cortex-contract-ask",
    "G8 named abstain on failure",
    "new",
    "rule12-aggregate-zero-row-green",
)
def test_zero_match_filter_that_returns_one_null_aggregate_row_is_not_a_confident_answer(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    from dms_executor.demo_warehouse import execute_sql

    cx = harness.cortex

    # Fixture premise: the rows the fake hands back are what that SQL really
    # returns on the seed. The seed stores 'SKU-BETA', so 'BETA' matches nothing
    # and the aggregate is one NULL row.
    control(execute_sql(_BETA_SQL, path=harness.warehouse) == [{"total": None}], "BETA premise")
    control(
        execute_sql(_SKU_BETA_SQL, path=harness.warehouse) == [{"total": 900.0}], "SKU-BETA premise"
    )

    # CONTROL 1: the exact value returns a real total at L2, with the figure shown.
    cx.ask_response = _engine_answer(
        text="Total quantity of SKU-BETA in stock: 900.0 kg.",
        sql=_SKU_BETA_SQL,
        rows=[{"total": 900.0}],
    )
    s, real = harness.ask(_SKU_BETA_Q, space_id=WAREHOUSE_OPS, session_id="ses_r12_real")
    require_envelope(s, real)
    control(
        real["badge"] == "L2_VALIDATED" and real["abstained"] is False,
        f"{real['badge']} {real['text']!r}",
    )
    control(
        real["rows"] == [{"total": 900.0}] and "900.0" in real["text"],
        f"{real['rows']} {real['text']!r}",
    )

    # CONTROL 2: the same wrong-encoding filter with an EMPTY result already abstains,
    # naming the cause. This is the hard-rule-12 net doing its job.
    cx.ask_response = _engine_answer(
        text="Total quantity of BETA in stock.", sql=_BETA_SQL, rows=[]
    )
    s, empty = harness.ask(_BETA_Q, space_id=WAREHOUSE_OPS, session_id="ses_r12_empty")
    require_envelope(s, empty)
    control(
        empty["abstained"] is True and empty["badge"] == "ABSTAIN" and not empty["rows"],
        f"{empty['badge']} {empty['text']!r}",
    )
    control("No matching rows" in empty["text"], empty["text"])

    # The gap request: identical ask and SQL, but the aggregate returns one NULL row.
    cx.ask_response = _engine_answer(
        text="Total quantity of BETA in stock.", sql=_BETA_SQL, rows=[{"total": None}]
    )
    asks_before = len(cx.asks)
    s, env = harness.ask(_BETA_Q, space_id=WAREHOUSE_OPS, session_id="ses_r12_null")
    require_envelope(s, env)
    control(len(cx.asks) == asks_before + 1, "the question must reach Cortex.ask, not another lane")
    assert_envelope(env)

    assert env["abstained"] is True and env["badge"] == "ABSTAIN", (
        f"{env['badge']} with rows={env['rows']!r} values={env['values']!r} for a filter "
        f"that matched no row (SUM over nothing is NULL). text={env['text']!r}"
    )
