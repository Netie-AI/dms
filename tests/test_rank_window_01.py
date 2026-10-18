"""RANK-WINDOW-01: "excluding top 3, whats the next 5 most selling skys".

Founder live ask abstained GEN-01 insights_timeout: the ask had no
deterministic lane, the typed plan had no offset, and a fast top-5 answer
would have been served L2 as ranks 1-5. These tests fail on main 6f7139a3.
"""

from __future__ import annotations

import ast
import builtins
import io
import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskRequest, LedgerAppendRequest, LedgerAppendResponse
from cortex_client.qualifiers import extract_qualifiers, unhonored_qualifier_reason
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import Ontology
from dms_executor.semantic_retrieve import intent_slots

# New names import inside the tests that use them, so this file collects on
# main and every test there fails on behaviour, not on ImportError.
FOUNDER_Q = "excluding top 3, whats the next 5 most selling skys"
NOTE_RANK_WINDOW = "rank_window:deterministic_compile (no generate call)"

# SKU -> (outbound kg, unit cost). kg rank and revenue rank run opposite ways
# so a wrong measure choice names different SKUs, not just different values.
_SKUS: dict[str, tuple[float, float]] = {
    "SKU-01": (1200, 1.0),
    "SKU-02": (1100, 1.25),
    "SKU-03": (1000, 1.5),
    "SKU-04": (900, 1.875),
    "SKU-05": (800, 2.25),
    "SKU-06": (700, 2.75),
    "SKU-07": (600, 3.5),
    "SKU-08": (500, 4.5),
    "SKU-09": (400, 6.0),
    "SKU-10": (300, 8.5),
    "SKU-11": (200, 14.0),
    "SKU-12": (100, 30.0),
}
# Hand-computed oracle. Not derived from the SQL under test.
REVENUE_4_8 = [
    ("SKU-09", 2400.0), ("SKU-08", 2250.0), ("SKU-07", 2100.0),
    ("SKU-06", 1925.0), ("SKU-05", 1800.0),
]
REVENUE_TOP3 = {"SKU-12", "SKU-11", "SKU-10"}
KG_4_8 = [
    ("SKU-04", 900.0), ("SKU-05", 800.0), ("SKU-06", 700.0),
    ("SKU-07", 600.0), ("SKU-08", 500.0),
]
KG_TOP3 = {"SKU-01", "SKU-02", "SKU-03"}

CASES = [
    (FOUNDER_Q, "outbound_value_myr", REVENUE_4_8, REVENUE_TOP3, "'most selling' read as revenue"),
    ("excluding top 3, next 5 best selling skus", "outbound_value_myr", REVENUE_4_8,
     REVENUE_TOP3, "'best selling' read as revenue"),
    ("excluding top 3, next 5 skus sold most", "outbound_value_myr", REVENUE_4_8,
     REVENUE_TOP3, "'sold most' read as revenue"),
    ("excluding top 3, next 5 skus by quantity sold", "outbound_kg", KG_4_8, KG_TOP3,
     "quantity sold (kg)"),
    ("ranks 4-8 skus by quantity sold", "outbound_kg", KG_4_8, KG_TOP3, "quantity sold (kg)"),
]


def _seed(path: Path) -> None:
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        for table in ("transactions", "inventory", "shipments"):
            con.execute(f"DELETE FROM {table}")
        n = 0
        for sku, (kg, cost) in _SKUS.items():
            con.execute(
                "INSERT INTO inventory VALUES (?, 'WH-A', 100, 10, ?, 'SUP-01', 'RAW', NULL)",
                [sku, cost],
            )
            # Two outbound halves (SUM must add them) and an inbound row the
            # measure must ignore.
            for qty, kind in ((kg / 2, "outbound"), (kg / 2, "outbound"), (9999, "inbound")):
                n += 1
                con.execute(
                    "INSERT INTO transactions VALUES (?, ?, 'WH-A', ?, ?, ?, "
                    "TIMESTAMP '2026-07-01 10:00:00')",
                    [f"T{n:03d}", sku, kind, qty, cost],
                )
    finally:
        con.close()


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    path = tmp_path / "rank_window.duckdb"
    _seed(path)
    return path


def _execute(lake: Path, sql: str) -> list[dict[str, Any]]:
    """Stand-in for Cortex submit: runs the submitted SQL on the seeded lake."""
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


@dataclass
class _Harness:
    lake: Path
    submits: list[str] = field(default_factory=list)
    computes: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] | None = None

    def submit(self, sql: str) -> Any:
        self.submits.append(sql)
        rows = _execute(self.lake, sql)
        return SimpleNamespace(ok=True, status="ok", run_id="run_rw", output={"rows": rows})

    def compute(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        self.computes.append(ctx)
        return self.payload

    def ask(self, question: str, onto: Ontology | None = None) -> dict[str, Any] | None:
        return maybe_generative_ask(
            question,
            warehouse=self.lake,
            grantable={"transactions", "inventory", "locations", "suppliers",
                       "shipments", "alerts"},
            compute=self.compute,
            submit=self.submit,
            ledger_append=lambda _p: SimpleNamespace(entry_id="led_rw", hash="hash_rw"),
            ontology=onto,
            bind_on_miss=False,
            dialect="duckdb",
        )


def _blob(env: dict[str, Any]) -> str:
    return " ".join(str(a) for a in env.get("assumptions") or []) + " " + str(env.get("text"))


def _assert_window(
    env: dict[str, Any] | None,
    measure: str,
    want: list[tuple[str, float]],
    excluded: set[str],
) -> None:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED", _blob(env)
    assert env["abstained"] is False
    assert env["rows"] == [{"product_sku": s, measure: v} for s, v in want]
    text = str(env["text"])
    for sku in excluded:
        assert sku not in text
        assert all(r["product_sku"] != sku for r in env["rows"])
    for sku, _v in want:
        assert sku in text
    sql = str(env["sql_used"])
    assert "LIMIT 5 OFFSET 3" in sql
    assert f'ORDER BY "{measure}" DESC, d0."sku" ASC\nLIMIT 5 OFFSET 3' in sql, sql


@pytest.mark.parametrize(("question", "measure", "want", "excluded", "reading"), CASES)
def test_rank_window_answers_ranks_4_to_8_without_generate(
    lake: Path, question: str, measure: str, want: list[tuple[str, float]],
    excluded: set[str], reading: str,
) -> None:
    h = _Harness(lake)
    env = h.ask(question)
    _assert_window(env, measure, want, excluded)
    assert env is not None
    assert h.computes == [], "rank-window ask must not wait on Insights generate"
    assert env["generate_legs"] == {"count": 0, "legs": []}
    assert env["served_attribution"] == "none"
    assert env["plan_source"] == "other"
    assert NOTE_RANK_WINDOW in env["assumptions"]
    assert reading.lower() in env["text"].lower(), env["text"]
    other = "quantity sold" if measure == "outbound_value_myr" else "revenue"
    assert f"not {other}" in env["text"], env["text"]
    if measure == "outbound_kg":
        assert " unit" not in env["text"].lower(), env["text"]
    assert "ranks 1..3 excluded (OFFSET 3)" in env["coverage"]["exclude"]
    assert len(h.submits) == 1


def test_founder_ask_served_by_live_ask_without_insights(
    lake: Path, minter: ManifestMinter
) -> None:
    cortex = _LakeCortex(lake)
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    env = exe.live_ask(FOUNDER_Q, session_id="ses_fa93d955a1e545eb")
    _assert_window(env, "outbound_value_myr", REVENUE_4_8, REVENUE_TOP3)
    assert cortex.insights == []
    assert cortex.asks == []
    assert any("OFFSET 3" in s for s in cortex.sql)
    assert cortex.appends, "answer must append to the Cortex ledger"
    assert "insights_timeout" not in _blob(env)


# --- latent WRONG: a fast top-5 must never be served as ranks 4-8 ---------

_NO_ENTITY_Q = "excluding top 3, what are the next 5 by revenue?"
_TOP5_PLAN = {"measure": "outbound_value_myr", "group_by": [["product", "sku"]], "limit": 5}
_REV = "SUM(CASE WHEN txn_type = 'outbound' THEN quantity_kg * unit_cost_myr ELSE 0 END)"
_BAD_SQL = [
    f"SELECT sku, {_REV} AS revenue FROM transactions GROUP BY sku ORDER BY 2 DESC LIMIT 5",
    f"SELECT sku, {_REV} AS revenue FROM transactions GROUP BY sku ORDER BY 2 DESC LIMIT 8",
    f"SELECT sku, {_REV} AS revenue FROM transactions GROUP BY sku ORDER BY 2 DESC "
    "LIMIT 5 OFFSET 3",
]


def _assert_rank_abstain(env: dict[str, Any] | None) -> None:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", _blob(env)
    assert env["badge"] != "L2_VALIDATED"
    assert env["rows"] == [] and env["values"] == []
    assert "unhonored_qualifier:rank_window=4-8" in _blob(env)


def _lane_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the deterministic lane so the QUAL-GUARD sees the fake plan or SQL.

    raising=False: on main the function does not exist and the ask never
    called it, so the patch changes nothing there.
    """
    import dms_executor.generative_ask as ga

    monkeypatch.setattr(ga, "rank_window_ask", lambda *_a, **_k: None, raising=False)


def test_top5_plan_for_rank_window_is_named_abstain(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane_off(monkeypatch)
    h = _Harness(lake, payload={"query_plan": dict(_TOP5_PLAN), "plan_source": "ontology_plan"})
    env = h.ask(FOUNDER_Q)
    assert h.computes, "guard test: the fake plan must be what is refused"
    _assert_rank_abstain(env)
    assert h.submits == []


@pytest.mark.parametrize("sql", _BAD_SQL, ids=["no_offset", "ranks_1_8", "no_tiebreak"])
def test_sql_ignoring_rank_window_is_named_abstain(
    lake: Path, sql: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane_off(monkeypatch)
    h = _Harness(lake, payload={"query_sql": sql, "plan_source": "ontology_plan"})
    _assert_rank_abstain(h.ask(FOUNDER_Q))
    assert h.submits == []


def test_sql_honouring_rank_window_still_answers(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane_off(monkeypatch)
    sql = (
        f"SELECT sku, {_REV} AS revenue FROM transactions GROUP BY sku "
        "ORDER BY 2 DESC, sku ASC LIMIT 5 OFFSET 3"
    )
    h = _Harness(lake, payload={"query_sql": sql, "plan_source": "ontology_plan"})
    env = h.ask(FOUNDER_Q)
    assert env is not None and env["badge"] == "L2_VALIDATED", _blob(env or {})
    assert [r["sku"] for r in env["rows"]] == [s for s, _v in REVENUE_4_8]


@pytest.mark.parametrize("question", [FOUNDER_Q, *[c[0] for c in CASES[1:]]])
def test_guard_refuses_top5_for_every_rank_window_phrasing(question: str) -> None:
    for kwargs in (
        {"plan": dict(_TOP5_PLAN)},
        {"sql": _BAD_SQL[0]},
    ):
        assert unhonored_qualifier_reason(question, **kwargs) == (
            "unhonored_qualifier:rank_window=4-8"
        )
    ok_plan = {**_TOP5_PLAN, "offset": 3}
    assert unhonored_qualifier_reason(question, plan=ok_plan) is None


# --- ungroundable / ambiguous: fast named abstain, never a timeout --------


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("excluding top 3, next 5 skus by profit margin", "unknown_measure:profit margin"),
        ("excluding top 3, next 5 most selling skus by units and revenue",
         "ambiguous_measure:most_selling"),
    ],
)
def test_ungroundable_rank_window_is_fast_named_abstain(
    lake: Path, question: str, reason: str
) -> None:
    h = _Harness(lake)
    started = time.monotonic()
    env = h.ask(question)
    assert time.monotonic() - started < 5.0
    assert env is not None and env["badge"] == "ABSTAIN"
    blob = _blob(env)
    assert reason in blob, blob
    assert "insights_timeout" not in blob
    assert h.computes == [] and h.submits == []


def test_measure_missing_from_ontology_is_unknown_measure(tmp_path: Path) -> None:
    path = tmp_path / "thin.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO sales VALUES ('T1','A',1),('T2','B',2)")
    con.close()
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("product", "(SELECT DISTINCT sku FROM sales)", ["sku"])
    o.add_link("sale_of_product", "sale", ["sku"], "product", ["sku"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    onto = load_verified_ontology(path, o)
    h = _Harness(path)
    env = h.ask("ranks 4-8 skus by quantity sold", onto=onto)
    assert env is not None and env["badge"] == "ABSTAIN"
    assert "unknown_measure:outbound_kg" in _blob(env)
    assert h.computes == []


# --- slots and plan carry offset; "top 3" is not the limit ----------------


def test_intent_slots_carry_offset_not_excluded_prefix(lake: Path) -> None:
    onto = load_verified_ontology(lake)
    slots = intent_slots(FOUNDER_Q, onto)
    assert slots == {
        "offset": 3,
        "limit": 5,
        "group_by": [["product", "sku"]],
        "measure": "outbound_value_myr",
    }


def test_plan_offset_compiles_limit_offset_with_tiebreak(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan offset is kept only when the question's window asks for it."""
    _lane_off(monkeypatch)
    h = _Harness(lake, payload={
        "query_plan": {**_TOP5_PLAN, "offset": 3}, "plan_source": "ontology_plan",
    })
    env = h.ask(FOUNDER_Q)
    assert env is not None and env["badge"] == "L2_VALIDATED", _blob(env or {})
    assert "LIMIT 5 OFFSET 3" in str(env["sql_used"])
    assert env["rows"] == [{"product_sku": s, "outbound_value_myr": v} for s, v in REVENUE_4_8]


_NON_RANK_QS = ("top 3 selling skus by revenue", "revenue by category",
                "next 5 weeks revenue", "ranks 1-5 skus", "revenue 2024-2025")


def test_non_rank_compile_bytes_unchanged(lake: Path) -> None:
    """Passes on main and head: no offset means the same SQL bytes."""
    onto = load_verified_ontology(lake)
    assert onto is not None
    got = onto.compile("outbound_value_myr", group_by=[("product", "sku")], limit=3)
    sql = getattr(got, "sql", "")
    assert sql.endswith('GROUP BY d0."sku"\nORDER BY "outbound_value_myr" DESC\nLIMIT 3'), sql
    for q in _NON_RANK_QS:
        assert all(k != "rank_window" for k, _v in extract_qualifiers(q))
        assert "offset" not in intent_slots(q, onto)


def test_non_rank_questions_parse_no_window() -> None:
    from cortex_client.qualifiers import parse_rank_window

    assert [q for q in _NON_RANK_QS if parse_rank_window(q) is not None] == []


# --- B1: a filter or a second noun is a named abstain, never a wrong L2 ---

_B1 = (
    ("excluding top 3, next 5 skus at WH-B", "rank_window_unhandled_terms:", ("wh",)),
    ("excluding top 3 in warehouse A, next 5", "rank_window_unhandled_terms:", ("in", "a")),
    ("excluding top 3, next 5 RAW skus", "rank_window_unhandled_terms:", ("raw",)),
    ("excluding top 3, next 5 chemical skus", "rank_window_unhandled_terms:", ("chemical",)),
    ("top 10 skus after the top 3 warehouses", "rank_window_entity_mismatch:warehouse!=sku", ()),
)


def _seed_multi(path: Path) -> None:
    """Two warehouses, ranks that disagree. A missing WHERE is a different answer."""
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        for table in ("transactions", "inventory", "shipments"):
            con.execute(f"DELETE FROM {table}")
        inv: list[tuple[Any, ...]] = []
        txn: list[tuple[Any, ...]] = []
        n = 0
        for i in range(1, 9):
            sku = f"SKU-{i:02d}"
            for loc, kg in (("WH-A", 1000 - i * 10), ("WH-B", 100 + i * 50)):
                inv.append((sku, loc, 10.0, 1.0, 2.0, "SUP-01", "RAW", None))
                n += 1
                txn.append((f"T{n:03d}", sku, loc, "outbound", float(kg), 2.0))
        con.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)", inv)
        con.executemany(
            "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, TIMESTAMP '2026-07-01')",
            txn,
        )
    finally:
        con.close()


@pytest.fixture()
def multi(tmp_path: Path) -> Path:
    path = tmp_path / "multi.duckdb"
    _seed_multi(path)
    return path


@pytest.mark.parametrize(("question", "reason", "tokens"), _B1)
def test_rank_window_does_not_drop_filters_or_rank_the_wrong_noun(
    multi: Path, question: str, reason: str, tokens: tuple[str, ...]
) -> None:
    h = _Harness(multi, payload={"query_plan": dict(_TOP5_PLAN), "plan_source": "ontology_plan"})
    env = h.ask(question)
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert env["rows"] == [] and env["values"] == []
    blob = _blob(env)
    assert reason in blob, blob
    if tokens:
        listed = blob.split("rank_window_unhandled_terms:", 1)[1].split()[0].rstrip(".,;")
        assert set(tokens) <= set(listed.split(",")), blob
    assert h.computes == [] and h.submits == []


def test_rank_window_needs_exactly_one_entity(lake: Path) -> None:
    h = _Harness(lake, payload={"query_plan": dict(_TOP5_PLAN), "plan_source": "ontology_plan"})
    env = h.ask(_NO_ENTITY_Q)
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert "rank_window_unhandled_terms:no_entity" in _blob(env)
    assert h.computes == [] and h.submits == []


# --- B2: "top 5 excluding top 3" is ranks 4-5, and no count does not answer ---


def test_top_n_excluding_top_m_keeps_the_difference(lake: Path) -> None:
    h = _Harness(lake)
    env = h.ask("top 5 skus excluding top 3 by revenue")
    assert env is not None and env["badge"] == "L2_VALIDATED", _blob(env or {})
    assert env["rows"] == [
        {"product_sku": "SKU-09", "outbound_value_myr": 2400.0},
        {"product_sku": "SKU-08", "outbound_value_myr": 2250.0},
    ]
    assert "LIMIT 2 OFFSET 3" in str(env["sql_used"])
    assert h.computes == []
    assert len(env["rows"]) != 9


def test_open_ended_window_abstains_instead_of_every_later_rank(lake: Path) -> None:
    q = "excluding top 3 skus by revenue"
    h = _Harness(lake, payload={"query_plan": {**_TOP5_PLAN, "limit": 50}})
    env = h.ask(q)
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert "rank_window_open_ended" in _blob(env)
    assert env["rows"] == []
    assert h.computes == [] and h.submits == []
    assert unhonored_qualifier_reason(q, plan={**_TOP5_PLAN, "limit": 50}) == (
        "rank_window_open_ended"
    )


def test_top_n_not_wider_than_the_exclusion_is_open_ended(lake: Path) -> None:
    env = _Harness(lake).ask("top 3 skus excluding top 5 by revenue")
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert "rank_window_open_ended" in _blob(env)
    assert env["rows"] == []


# --- B3: an offset the question did not ask for is not served --------------


def test_unrequested_plan_offset_is_named_abstain(lake: Path) -> None:
    q = "next 5 skus by revenue"
    h = _Harness(lake, payload={
        "query_plan": {**_TOP5_PLAN, "offset": 3}, "plan_source": "ontology_plan",
    })
    env = h.ask(q)
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert "unrequested_offset:3" in _blob(env)
    assert env["rows"] == []
    assert h.submits == []


def test_unrequested_sql_offset_is_named_abstain(lake: Path) -> None:
    q = "next 5 skus by revenue"
    sql = (
        f"SELECT sku, {_REV} AS revenue FROM transactions GROUP BY sku "
        "ORDER BY 2 DESC, sku ASC LIMIT 5 OFFSET 3"
    )
    h = _Harness(lake, payload={"query_sql": sql, "plan_source": "ontology_plan"})
    env = h.ask(q)
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert "unrequested_offset:3" in _blob(env)
    assert h.submits == []


# --- measure words: none abstains, units is not kg, selling stays revenue --


@pytest.mark.parametrize(
    "question",
    ["excluding top 3, next 5 skus", "after the top 3, what are the next 5 SKUs"],
)
def test_no_measure_word_abstains_by_name(lake: Path, question: str) -> None:
    h = _Harness(lake)
    env = h.ask(question)
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    assert "ambiguous_measure:none" in _blob(env)
    assert env["rows"] == []
    assert h.computes == [] and h.submits == []
    onto = load_verified_ontology(lake)
    assert "measure" not in intent_slots(question, onto)


def test_units_word_does_not_map_to_kilograms(lake: Path) -> None:
    h = _Harness(lake)
    env = h.ask("excluding top 3, next 5 skus by units sold")
    assert env is not None and env["badge"] == "ABSTAIN", _blob(env or {})
    blob = _blob(env)
    assert "unknown_measure:units" in blob, blob
    assert "outbound_kg" not in blob
    assert env["rows"] == []
    assert h.computes == [] and h.submits == []


def test_lane_does_not_call_ranking_or_the_intent_binder(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_executor.generative_ask as ga

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("rank-window lane called a generate fallback")

    monkeypatch.setattr(ga, "ontology_plan_from_ranking", _boom)
    monkeypatch.setattr(ga, "bind_plan", _boom)
    monkeypatch.setattr(ga, "intent_slots", _boom)
    env = _Harness(lake).ask(FOUNDER_Q)
    assert env is not None and env["badge"] == "L2_VALIDATED"
    assert 'ORDER BY "outbound_value_myr" DESC, d0."sku" ASC\nLIMIT 5 OFFSET 3' in str(
        env["sql_used"]
    )


# Tracked in RANK-WORDS-02. Not parsed here, and not turned into a window.
_RANK_WORDS_02 = (
    "skip the first 3",
    "bottom 5 excluding bottom 3",
    "ranks 8 to 4",
    "excluding SKU X, top 5",
)


def test_rank_words_02_phrases_are_not_windows() -> None:
    from cortex_client.qualifiers import parse_rank_window, rank_window_shape_reason

    assert [q for q in _RANK_WORDS_02 if parse_rank_window(q) is not None] == []
    assert [q for q in _RANK_WORDS_02 if rank_window_shape_reason(q) is not None] == []


# --- grammar only: no scored pack is opened or imported -------------------

_PACK_RE = re.compile(
    r"oracles\.ya?ml|questions\.ya?ml|curated|demo_pack|query_skill|golden|"
    r"verified_quer|hostile_score|playground|buyer_walk|bird_minidev",
    re.I,
)
_REPO = Path(__file__).resolve().parents[1]


def test_rank_window_parse_and_compile_touch_no_scored_pack(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cortex_client.qualifiers import parse_rank_window, rank_window_measure
    from dms_executor.generative_ask import rank_window_ask

    onto = load_verified_ontology(lake)
    assert onto is not None
    opened: list[str] = []

    def _spy(real: Any) -> Any:
        def _wrapped(target: Any, *a: Any, **k: Any) -> Any:
            opened.append(str(target))
            return real(target, *a, **k)

        return _wrapped

    monkeypatch.setattr(builtins, "open", _spy(builtins.open))
    monkeypatch.setattr(io, "open", _spy(io.open))
    monkeypatch.setattr(os, "open", _spy(os.open))
    for name in ("open", "read_text", "read_bytes"):
        monkeypatch.setattr(pathlib.Path, name, _spy(getattr(pathlib.Path, name)))
    specs = {n: m.description or "" for n, m in onto.measures.items()}
    h = _Harness(lake)
    for question, measure, *_rest in CASES:
        win = parse_rank_window(question)
        assert win is not None
        assert rank_window_measure(question, specs)[0] == measure
        compiled = onto.compile(measure, group_by=[("product", "sku")], limit=5, offset=3)
        assert "OFFSET 3" in getattr(compiled, "sql", "")
        env = rank_window_ask(
            question, onto=onto, allowed={"transactions", "inventory"}, lake=lake,
            space_id=None, session_id=None, submit=h.submit,
            ledger_append=lambda _p: SimpleNamespace(entry_id="led_rw", hash="hash_rw"),
            dialect="duckdb",
        )
        assert env is not None and env["badge"] == "L2_VALIDATED"
    hits = [p for p in opened if _PACK_RE.search(p)]
    assert hits == [], hits

    for rel in ("packages/cortex_client/cortex_client/qualifiers.py",
                "packages/executor/dms_executor/ontology.py"):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        mods = [
            *(a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names),
            *(n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)),
        ]
        assert not [m for m in mods if _PACK_RE.search(m)], (rel, mods)
    names = set(rank_window_ask.__code__.co_names)
    for fn in (parse_rank_window, rank_window_measure):
        names |= set(fn.__code__.co_names)
    assert not [n for n in names if _PACK_RE.search(n) or "paraphrase" in n], names


# --- fakes ------------------------------------------------------------------


@dataclass
class _LakeCortex:
    lake: Path
    asks: list[Any] = field(default_factory=list)
    insights: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        self.insights.append(question)
        return None

    def compute_query(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        raise AssertionError("ask lane called /dms/query")

    def submit(self, req: Any) -> QueryResult:
        if req.plan.get("kind") == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_bind", output={})
        sql = str(req.body.get("sql") or "")
        self.sql.append(sql)
        return QueryResult(
            ok=True, status="ok", run_id="run_rw", output={"rows": _execute(self.lake, sql)}
        )

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_rw", hash="hash_rw_not_entry")

    def ask(self, req: AskRequest) -> Any:
        self.asks.append(req)
        raise AssertionError("rank-window ask fell through to Cortex contract ask")


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m
