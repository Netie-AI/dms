"""Gate-matrix theme "followup": the session follow-up lane ("average of them", "add N").

The lane answers from ``Executor._turns``, a cache of the figures of the previous
answered turn, keyed ``(session_id, space_id)``. It runs before every other lane
(``dms_executor/__init__.py:578-590``), so a follow-up never reaches Cortex submit,
the ledger, the grant functions or the ontology. These tests ask the way a customer
does, through ``POST /v1/chat/ask`` (and ``POST /v1/mcp/call`` for the MCP entry),
and assert what comes back.

Every test is a CONTROL then a GAP. The control is a sibling request on an already
gated path that must pass today. The gap assertion is what a gated follow-up would
return. It fails with a plain ``AssertionError`` on this commit and the strict xfail
turns that into XFAIL. A fixed gate makes the body pass, strict XPASS fails the run,
and the ``@gap`` marker is deleted so the test becomes a regression test.

Gaps (ids from the Plan E trace):
  followup-no-submit                      G7  FOLLOWUP-CONTRACT-01
  followup-no-grant-recheck               G1  FOLLOWUP-CONTRACT-01 (two tests)
  followup-unit-relabelled-rm             G2  new
  followup-stale-prior-after-failed-turn  G8  new
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from _harness import (
    FINANCE,
    WAREHOUSE_OPS,
    control,
    gap,
    require_envelope,
)

FOLLOWUP_AVG = "average of them"


def _pack() -> Any:
    """The demo pack questions.

    Imported lazily so ``dms_api`` import stays under conftest's guard.
    """
    from dms_executor import demo_pack

    return demo_pack


def _ask_via(harness: Any, entry: str, question: str, **kw: Any) -> tuple[int, Any]:
    """Ask through ``/v1/chat/ask`` or through the MCP ``ask`` tool. Returns (status, envelope)."""
    if entry == "chat":
        return harness.ask(question, **kw)
    arguments = {"question": question, **{k: v for k, v in kw.items() if v is not None}}
    r = harness.client.post("/v1/mcp/call", json={"name": "ask", "arguments": arguments})
    try:
        payload = r.json()
    except ValueError:
        return r.status_code, {"_raw_body": r.text}
    if r.status_code == 200 and isinstance(payload, dict) and payload.get("ok") is True:
        return r.status_code, payload.get("result")
    return r.status_code, payload


def _canon_sql(sql: Any) -> str:
    """Whitespace/case/trailing-semicolon-insensitive SQL text, for comparing two statements."""
    return " ".join(str(sql or "").split()).rstrip(";").strip().lower()


def _numeric_values(env: dict[str, Any]) -> list[float]:
    """The numeric figures a customer is shown in ``values[]``, in order."""
    return [
        float(v["value"])
        for v in env.get("values") or []
        if isinstance(v.get("value"), (int, float)) and not isinstance(v.get("value"), bool)
    ]


def _rm_amount(text: str) -> float | None:
    """The amount the rendered text states, read back from "... RM 9,946.67." (None if absent)."""
    m = re.search(r"RM\s+(-?[\d,]+(?:\.\d+)?)", text)
    return float(m.group(1).replace(",", "")) if m else None


def _named(env: dict[str, Any]) -> str:
    """Everything the customer can read that could name an abstain reason."""
    return " ".join([str(env.get("text") or ""), *[str(a) for a in env.get("assumptions") or []]])


def _seed_bronze_sheet(harness: Any, filename: str, space_id: str) -> tuple[str, str]:
    """A bronze sheet registered to ``space_id`` the way the ingest registry records one.

    Same seed as ``tests/test_bronze_grant_01.py``. Returns (bronze ident, question).
    """
    import duckdb
    from dms_executor.bronze import _ensure_registry, bronze_table_for_sheet
    from dms_executor.lake_schema import ensure_lake_schemas

    table = bronze_table_for_sheet(filename, "Sales")
    ident = table.split(".", 1)[-1]
    con = duckdb.connect(str(harness.warehouse))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(f'CREATE TABLE bronze."{ident}" (category VARCHAR, sales_value_myr DOUBLE)')
        con.execute(
            f'INSERT INTO bronze."{ident}" VALUES '
            "('Electronics', 1500.5), ('Home', 20.0), ('Sports', 5.0)"
        )
        _ensure_registry(con)
        con.execute(
            """
            INSERT INTO bronze._ingest_registry
              (table_name, filename, sha256, ingest_id, created_at, space_id)
            VALUES (?, ?, 'gm', 'ing-gm-followup', now(), ?)
            """,
            [ident, filename, space_id],
        )
    finally:
        con.close()
    question = f"In {filename} sheet Sales, what are the top 3 categories by sales_value_myr?"
    return ident, question


def _rehome_bronze_sheet(harness: Any, ident: str, space_id: str) -> None:
    """Move the upload's registration to another Space: the first Space loses the grant."""
    import duckdb

    con = duckdb.connect(str(harness.warehouse))
    try:
        con.execute(
            "UPDATE bronze._ingest_registry SET space_id = ? WHERE table_name = ?",
            [space_id, ident],
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# followup-no-submit  (G7)
# ---------------------------------------------------------------------------


@gap(
    "chat-ask:followup-average,chat-ask:followup-add,cache:session-turns,mcp:ask",
    "G7",
    "FOLLOWUP-CONTRACT-01",
    "followup-no-submit",
)
@pytest.mark.parametrize(
    ("entry", "parent_q", "followup_q"),
    [
        pytest.param("chat", "SPEND_BY_COUNTRY_Q", FOLLOWUP_AVG, id="chat-average"),
        pytest.param("chat", "TOTAL_SPEND_Q", "add 2000", id="chat-add"),
        pytest.param("mcp", "SPEND_BY_COUNTRY_Q", FOLLOWUP_AVG, id="mcp-average"),
    ],
)
def test_followup_figure_goes_through_cortex_submit_and_the_ledger(
    harness_factory: Any, entry: str, parent_q: str, followup_q: str
) -> None:
    """A follow-up's L2 figure needs a Cortex submit and a ledger entry, or it abstains.

    Today the follow-up SQL (``SELECT ROUND((a + b + c) / 3.0, 2)``) runs in DMS's own
    DuckDB, the envelope is L2_VALIDATED, and ``audit_id`` is the literal "ans_followup".
    That id names nothing in the ledger.

    The follow-up is judged on what the customer receives. Its rendered text, ``rows`` and
    ``values`` must state the figure derived from the PRIOR turn's own ``values`` (so the
    expectation moves with the seed, nothing is hard-coded); that precondition passes today
    and sits behind ``control`` so a wrong figure after a fix is a hard failure, never an
    absorbed xfail. The gap is then the Cortex side of that same request: a submit whose SQL
    is the follow-up arithmetic the envelope displays (``sql_used``), and a ledger append for
    that SQL whose entry id is the ``audit_id`` shown. The count of calls is not asserted,
    only that those two exist among the calls made after the parent turn.
    """
    h = harness_factory(env={"DMS_MCP": "1"})
    cx = h.cortex
    parent_question = getattr(_pack(), parent_q)

    s, parent = _ask_via(h, entry, parent_question, space_id=FINANCE, session_id="ses_fu_submit")
    require_envelope(s, parent)
    # CONTROL: the parent turn is a gated answer, and the fake records when the code calls it.
    control(
        parent["badge"] == "L1_GOVERNED_METRIC"
        and parent["abstained"] is False
        and parent["values"],
        f"parent is not a governed figure: {parent.get('badge')} {parent.get('text')!r}",
    )
    control(
        len(cx.sql_submits) == 1 and len(cx.appends) == 1,
        f"parent turn should be exactly 1 SQL submit + 1 ledger append, saw "
        f"{len(cx.sql_submits)} + {len(cx.appends)}",
    )
    control(
        parent["audit_id"] == "led_gm",
        f"parent audit_id {parent['audit_id']!r} is not the ledger entry id the fake returned",
    )
    # CONTROL: the matching used on the follow-up below recognises a real submit and a real
    # ledger append for an answer's displayed SQL (the parent's), so "no match" later means
    # the follow-up never reached Cortex, not that the matcher cannot see it.
    parent_sql = _canon_sql(parent["sql_used"])
    control(
        parent_sql
        and parent_sql in [_canon_sql(q) for q in cx.submitted_sql()]
        and any(_canon_sql(p.get("sql")) == parent_sql for p in cx.ledger_payloads()),
        f"parent sql_used {parent['sql_used']!r} was not found among the submits "
        f"{cx.submitted_sql()!r} and ledger payloads {cx.ledger_payloads()!r}",
    )
    prior = _numeric_values(parent)
    control(prior, f"parent shows no numeric figure to follow up on: {parent['values']!r}")
    submits_before, appends_before = len(cx.sql_submits), len(cx.appends)
    # The fake hands the same entry id to every append. Change it now, so an audit_id equal
    # to this one can only have come from the follow-up's own ledger append, never a copy
    # of the parent's.
    cx.ledger_entry_id = "led_gm_followup"

    s, env = _ask_via(h, entry, followup_q, space_id=FINANCE, session_id="ses_fu_submit")
    require_envelope(s, env)

    if env["abstained"] is False:
        # What the customer sees. Expected figure comes from the prior turn's own values.
        if followup_q == FOLLOWUP_AVG:
            expected = sum(prior) / len(prior)
            control(
                f"prior {len(prior)} figures" in env["text"],
                f"follow-up text should say it averaged {len(prior)} figures: {env['text']!r}",
            )
        else:
            control(len(prior) == 1, f"'add N' needs a single prior figure, got {prior!r}")
            expected = prior[0] + float(followup_q.split()[1])
        tol = 0.005 + 1e-9  # the lane rounds to 2 dp
        stated = _rm_amount(env["text"])
        control(
            stated is not None and abs(stated - expected) <= tol,
            f"follow-up text states {stated!r}, expected about {expected:.2f} from the prior "
            f"figures {prior!r}: {env['text']!r}",
        )
        cells = [
            c
            for row in env["rows"]
            for c in row.values()
            if isinstance(c, (int, float)) and not isinstance(c, bool)
        ]
        control(
            len(env["rows"]) == 1 and len(cells) == 1 and abs(cells[0] - expected) <= tol,
            f"follow-up rows should be the one figure {expected:.2f}: {env['rows']!r}",
        )
        control(
            any(abs(v - expected) <= tol for v in _numeric_values(env)),
            f"follow-up values should carry {expected:.2f}: {env['values']!r}",
        )
        control(env["sql_used"], "an answered follow-up envelope carries sql_used")

    # THE GAP - the follow-up reached Cortex: its displayed SQL was submitted, and a ledger
    # append for that SQL produced the audit_id the customer holds.
    # The lane's own "no prior numeric answer" abstain is not the gate. The parent just
    # answered with figures, so that abstain means the lane regressed, and it must not be
    # absorbed as an expected failure (nor satisfy the gap assertion below).
    control(
        not (env["abstained"] and env["answer_id"] == "ans_followup_abstain"),
        f"the follow-up lane abstained for lack of a prior answer even though the parent "
        f"just answered with {prior!r}: {env['text']!r}",
    )
    served_sql = _canon_sql(env["sql_used"])
    new_submits = [_canon_sql(q) for q in cx.submitted_sql()[submits_before:]]
    new_ledger = cx.ledger_payloads()[appends_before:]
    submitted = bool(served_sql) and served_sql in new_submits
    ledgered = (
        bool(served_sql)
        and any(_canon_sql(p.get("sql")) == served_sql for p in new_ledger)
        and env["audit_id"] == "led_gm_followup"
    )
    assert env["abstained"] is True or (submitted and ledgered), (
        f"follow-up {followup_q!r} was served as {env['badge']} {env['text']!r} "
        f"(rows={env['rows']!r}) with audit_id={env['audit_id']!r}; its displayed SQL "
        f"{env['sql_used']!r} was {'' if submitted else 'NOT '}submitted to Cortex "
        f"(SQL submitted after the parent: {new_submits!r}) and "
        f"{'' if ledgered else 'NOT '}ledgered with that audit_id "
        f"(ledger payload SQL after the parent: {[p.get('sql') for p in new_ledger]!r})"
    )


# ---------------------------------------------------------------------------
# followup-no-grant-recheck  (G1)
# ---------------------------------------------------------------------------

_GRANT_ROWS = (
    "chat-ask:followup-average,chat-ask:followup-add,chat-ask:followup-abstain,cache:session-turns"
)


@gap(_GRANT_ROWS, "G1", "FOLLOWUP-CONTRACT-01", "followup-no-grant-recheck")
def test_followup_without_a_space_abstains_no_space(harness: Any) -> None:
    """A follow-up with no Space is refused ``no_space``, as the bronze lane refuses it.

    Today the cache key is ``(session_id, "")`` and ``"add 2000"`` returns
    "Prior figure plus 2000 is RM 31,840.00." as L2_VALIDATED. No grant function runs.
    The expected abstain is the dms#303 FOLLOWUP-CONTRACT-01 item 3 shape (shared grant
    function, ``ungranted_table`` / ``no_space``).
    """
    pack = _pack()

    # CONTROL 1: with a Space and the grants intact the same follow-up works.
    s, parent = harness.ask(pack.TOTAL_SPEND_Q, space_id=FINANCE, session_id="ses_fu_ctl")
    require_envelope(s, parent)
    control(parent["badge"] == "L1_GOVERNED_METRIC", f"control parent: {parent['text']!r}")
    s, ctl = harness.ask("add 2000", space_id=FINANCE, session_id="ses_fu_ctl")
    require_envelope(s, ctl)
    control(
        ctl["abstained"] is False and "Prior figure plus 2000" in ctl["text"],
        f"control follow-up in FINANCE should compute: {ctl['badge']} {ctl['text']!r}",
    )

    # CONTROL 2: the bronze lane in the same ladder already abstains no_space for
    # this request shape.
    _ident, bronze_q = _seed_bronze_sheet(harness, "gm_followup_nospace.xlsx", FINANCE)
    s, bronze = harness.ask(bronze_q, session_id="ses_fu_bronze")
    require_envelope(s, bronze)
    control(
        bronze["abstained"] is True and "no_space" in _named(bronze),
        f"bronze lane without a Space should abstain no_space: "
        f"{bronze['badge']} {bronze['text']!r}",
    )

    # The pack lane answers a no-Space ask under the company default; the follow-up
    # on it is the gap.
    s, parent = harness.ask(pack.TOTAL_SPEND_Q, session_id="ses_fu_nospace")
    require_envelope(s, parent)
    control(
        parent["badge"] == "L1_GOVERNED_METRIC"
        and parent["abstained"] is False
        and parent["values"],
        f"no-Space parent should be answered by the pack lane: "
        f"{parent['badge']} {parent['text']!r}",
    )
    s, env = harness.ask("add 2000", session_id="ses_fu_nospace")
    require_envelope(s, env)

    assert env["abstained"] is True and "no_space" in _named(env), (
        f"follow-up with no Space was served as {env['badge']} {env['text']!r}; "
        "expected an ABSTAIN naming no_space"
    )


@gap(_GRANT_ROWS, "G1", "FOLLOWUP-CONTRACT-01", "followup-no-grant-recheck")
def test_followup_after_the_grant_is_revoked_abstains_ungranted_table(harness: Any) -> None:
    """The follow-up re-reads grants: a table the Space no longer holds cannot feed it.

    The upload is registered to FINANCE, answered, then re-homed to WAREHOUSE_OPS. A fresh
    ask for the same sheet is refused ``ungranted_table``. The follow-up in the session that
    already holds the old figures still averages them: the cache is read with no grant call.
    """
    ident, bronze_q = _seed_bronze_sheet(harness, "gm_followup_revoke.xlsx", FINANCE)

    s, parent = harness.ask(bronze_q, space_id=FINANCE, session_id="ses_fu_revoke")
    require_envelope(s, parent)
    control(
        parent["abstained"] is False
        and parent["badge"] == "L0_CERTIFIED"
        and len(parent["values"]) == 3,
        f"bronze sheet should answer in FINANCE while granted: "
        f"{parent['badge']} {parent['text']!r}",
    )

    _rehome_bronze_sheet(harness, ident, WAREHOUSE_OPS)

    # CONTROL: the gated bronze lane honours the revocation (other session, so the
    # cache is untouched).
    s, ctl = harness.ask(bronze_q, space_id=FINANCE, session_id="ses_fu_revoke_ctl")
    require_envelope(s, ctl)
    control(
        ctl["abstained"] is True and "ungranted_table" in _named(ctl),
        f"bronze lane after revocation should abstain ungranted_table: "
        f"{ctl['badge']} {ctl['text']!r}",
    )

    s, env = harness.ask(FOLLOWUP_AVG, space_id=FINANCE, session_id="ses_fu_revoke")
    require_envelope(s, env)

    assert env["abstained"] is True and "ungranted_table" in _named(env), (
        f"follow-up over a revoked table was served as {env['badge']} {env['text']!r}; "
        "expected an ABSTAIN naming ungranted_table"
    )


# ---------------------------------------------------------------------------
# followup-unit-relabelled-rm  (G2)
# ---------------------------------------------------------------------------


@gap(
    "chat-ask:followup-average,chat-ask:followup-add,cache:session-turns",
    "G2",
    "new",
    "followup-unit-relabelled-rm",
)
@pytest.mark.parametrize(
    ("parent_q", "parent_label", "followup_q"),
    [
        pytest.param("CAPACITY_UTILISATION_Q", "pct_used", FOLLOWUP_AVG, id="percent-average"),
        pytest.param("COLD_STORAGE_Q", "row_count", "add 5", id="count-add"),
    ],
)
def test_followup_does_not_label_a_non_ringgit_figure_as_ringgit(
    harness: Any, parent_q: str, parent_label: str, followup_q: str
) -> None:
    """A mean of percentages, or a row count plus 5, is not "RM x" under L2_VALIDATED.

    Today ``session_followup`` keeps only the floats (``numeric_values``) and hard-codes
    "RM" in the text and ``average_myr`` / ``adjusted_myr`` as the value label.
    """
    pack = _pack()

    # CONTROL: when the prior figures really are ringgit the lane keeps RM and works.
    s, stock = harness.ask(pack.STOCK_BY_CATEGORY_Q, space_id=WAREHOUSE_OPS, session_id="ses_fu_rm")
    require_envelope(s, stock)
    control(
        stock["abstained"] is False
        and all(v["label"] == "stock_value_myr" for v in stock["values"]),
        f"stock-value parent should be ringgit figures: {stock['text']!r}",
    )
    s, ctl = harness.ask(FOLLOWUP_AVG, space_id=WAREHOUSE_OPS, session_id="ses_fu_rm")
    require_envelope(s, ctl)
    control(
        ctl["abstained"] is False and ctl["badge"] == "L2_VALIDATED" and "RM" in ctl["text"],
        f"follow-up over ringgit figures should keep RM: {ctl['badge']} {ctl['text']!r}",
    )

    # The parent is not ringgit. Prove it from its own envelope before judging the follow-up.
    s, parent = harness.ask(
        getattr(pack, parent_q), space_id=WAREHOUSE_OPS, session_id="ses_fu_unit"
    )
    require_envelope(s, parent)
    control(
        parent["abstained"] is False
        and parent["values"]
        and all(v["label"] == parent_label for v in parent["values"]),
        f"parent values should all be {parent_label!r}: {parent['values']!r}",
    )
    control(
        "RM" not in parent["text"] and "myr" not in parent["text"].lower(),
        f"parent text should not claim ringgit: {parent['text']!r}",
    )

    s, env = harness.ask(followup_q, space_id=WAREHOUSE_OPS, session_id="ses_fu_unit")
    require_envelope(s, env)

    labelled_ringgit = "RM" in env["text"] or any(
        "myr" in str(v.get("label") or "").lower() for v in env["values"]
    )
    assert env["abstained"] is True or not labelled_ringgit, (
        f"{followup_q!r} after a {parent_label!r} answer was served as {env['badge']} "
        f"{env['text']!r} with value labels {[v.get('label') for v in env['values']]!r}"
    )


# ---------------------------------------------------------------------------
# followup-stale-prior-after-failed-turn  (G8)
# ---------------------------------------------------------------------------


@gap(
    "chat-ask:followup-average,chat-ask:followup-add,cache:session-turns",
    "G8",
    "new",
    "followup-stale-prior-after-failed-turn",
)
@pytest.mark.parametrize(
    ("failure", "status"),
    [
        pytest.param(
            RuntimeError("path_not_allowed: table 'transactions' is not named by this manifest"),
            200,
            id="space-refusal",
        ),
        pytest.param(RuntimeError("statement_timeout: engine timed out"), 504, id="timeout"),
    ],
)
def test_followup_after_a_failed_turn_does_not_reuse_the_older_figures(
    harness: Any, failure: BaseException, status: int
) -> None:
    """A turn that failed by exception must not leave the previous turn's figures live.

    Turn 1 answers, turn 2 fails (a Space refusal envelope, or a 504), turn 3 is "average
    of them". Today the cache still holds turn 1, so turn 3 is "Average of the prior 4
    figures is RM 7,460.00." and reads as an average over whatever turn 2 asked for.
    """
    pack = _pack()

    # CONTROL: a turn the lane itself abstains on clears the cache, so the follow-up abstains.
    s, parent = harness.ask(
        pack.STOCK_BY_CATEGORY_Q, space_id=WAREHOUSE_OPS, session_id="ses_fu_ctl"
    )
    require_envelope(s, parent)
    control(parent["abstained"] is False and len(parent["values"]) == 4, parent["text"])
    s, trap = harness.ask(pack.HOW_FULL_TRAP_Q, space_id=WAREHOUSE_OPS, session_id="ses_fu_ctl")
    require_envelope(s, trap)
    control(trap["abstained"] is True, f"planted refusal should abstain: {trap['text']!r}")
    s, ctl = harness.ask(FOLLOWUP_AVG, space_id=WAREHOUSE_OPS, session_id="ses_fu_ctl")
    require_envelope(s, ctl)
    control(
        ctl["abstained"] is True and not ctl["text"].startswith("Average of the prior"),
        f"follow-up after a lane abstain should abstain: {ctl['badge']} {ctl['text']!r}",
    )

    # The gap sequence: answer, failure by exception, follow-up.
    s, parent = harness.ask(
        pack.STOCK_BY_CATEGORY_Q, space_id=WAREHOUSE_OPS, session_id="ses_fu_stale"
    )
    require_envelope(s, parent)
    control(parent["abstained"] is False and len(parent["values"]) == 4, parent["text"])

    harness.cortex.ask_raises = failure
    s, failed = harness.ask(
        "Top 5 selling SKUs by revenue", space_id=WAREHOUSE_OPS, session_id="ses_fu_stale"
    )
    harness.cortex.ask_raises = None
    control(s == status, f"turn 2 should fail with HTTP {status}, got {s}: {failed!r}")
    if status == 200:
        control(
            failed.get("abstained") is True and "no access" in str(failed.get("text")),
            f"turn 2 should be the Space-refusal envelope: {failed!r}",
        )

    s, env = harness.ask(FOLLOWUP_AVG, space_id=WAREHOUSE_OPS, session_id="ses_fu_stale")
    require_envelope(s, env)

    assert env["abstained"] is True and not env["text"].startswith("Average of the prior"), (
        f"follow-up after the failed turn was served as {env['badge']} {env['text']!r}, "
        "computed from the figures of the turn before it"
    )
