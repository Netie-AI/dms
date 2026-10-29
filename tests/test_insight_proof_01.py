"""INS-PROOF-01 - insights that carry their proof, behind ``DMS_INSIGHT_PROOF``.

Every figure a proven insight states is a cell of SQL that ran twice through
an injected submit and returned the same rows both times; the grouped total is
conserved against a no-join raw total; the proof is a sibling of the envelope,
never a key in it. Assertions are on the rendered text and the returned rows
(hard rule 10) and on ``assert_envelope_valid`` (rule 10a).

The submit stub executes on a fresh serving connection to a tmp warehouse. It
stands in for Cortex; nothing here claims a Cortex run.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from dms_executor import insight_proof as ip
from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    connect_file,
    connect_serving,
    ensure_demo_warehouse,
)
from dms_executor.envelope import (
    assert_envelope_valid,
    invented_totals,
    orphan_money_figures,
)
from dms_executor.ontology import Ontology
from dms_executor.route_stamp import ABSTAIN_STAGES, kept_stamp
from dms_executor.skills_quarantine import item_content_hash, item_result_hash

_FLAG = "DMS_INSIGHT_PROOF"

_A_HEADLINE = "2 of 5 location_code values (WH-B, WH-E) carry 73.2 pct of stock_value_myr"
_A_ROWS = [
    ("WH-B", 15015.0, 50.3, 50.3, 29840.0),
    ("WH-E", 6825.0, 22.9, 73.2, 29840.0),
    ("WH-A", 5816.0, 19.5, 92.7, 29840.0),
    ("WH-C", 1800.0, 6.0, 98.7, 29840.0),
    ("WH-D", 384.0, 1.3, 100.0, 29840.0),
]
_B_CONCENTRATION = "2 of 4 Territory.name values (North, South) carry 86.4 pct of order_amount"
_B_UNKNOWN = "9.1 pct of order_amount lands on rows with no Territory.name value"
_B_MEASURE_ITEM = "item_measure_order_amount"
_B_VERSION = "ver_sales_b1"


# --------------------------------------------------------------------------
# submit stub
# --------------------------------------------------------------------------


class _Stub:
    """Executes each statement on its own serving connection. Records every call."""

    def __init__(
        self,
        warehouse: Path,
        *,
        tamper_run2: bool = False,
        drop_last_group: bool = False,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.warehouse = warehouse
        self.calls: list[str] = []
        self.tamper_run2 = tamper_run2
        self.drop_last_group = drop_last_group
        self.raise_exc = raise_exc
        self._wrapper_runs = 0

    def __call__(self, sql: str) -> Any:
        self.calls.append(sql)
        if self.raise_exc is not None:
            raise self.raise_exc
        con = connect_serving(self.warehouse)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        if sql.lstrip().upper().startswith("WITH G AS"):
            self._wrapper_runs += 1
            if self.drop_last_group and len(rows) > 1:
                rows = rows[:-1]
            if self.tamper_run2 and self._wrapper_runs % 2 == 0 and rows:
                rows[0] = {**rows[0], "value": float(rows[0]["value"]) + 1.0}
        return SimpleNamespace(ok=True, output={"rows": rows})


def _no_watermarks(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {}


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")


# --------------------------------------------------------------------------
# fixture A: the demo warehouse with the demo ontology
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_wh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return ensure_demo_warehouse(tmp_path_factory.mktemp("insproof_a") / "demo.duckdb")


def _run(
    warehouse: Path,
    stub: Callable[[str], Any],
    granted: set[str],
    *,
    onto: Ontology | None = None,
    watermarks: Callable[..., dict[str, dict[str, Any]]] = _no_watermarks,
    top: int = 12,
) -> ip.InsightRun:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(_FLAG, "1")
        mp.setenv("DMS_DEMO_FALLBACK", "0")
        return ip.prove_insights(
            warehouse,
            submit=stub,
            granted=granted,
            space_id="space_insproof",
            session_id="sess_insproof",
            top=top,
            onto=onto,
            watermarks=watermarks,
        )


@pytest.fixture(scope="module")
def run_a(demo_wh: Path) -> tuple[ip.InsightRun, _Stub]:
    stub = _Stub(demo_wh)
    return _run(demo_wh, stub, set(DEMO_TABLES)), stub


def _tuples(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [tuple(r[c] for c in ip.PROOF_COLUMNS) for r in rows]


def _by_text(run: ip.InsightRun, needle: str) -> ip.ProvenInsight:
    hits = [p for p in run.proven if needle in p.envelope["text"]]
    assert hits, [p.envelope["text"] for p in run.proven]
    return hits[0]


def _codes(run: ip.InsightRun) -> dict[str, str]:
    return {r["candidate"]: r["code"] for r in run.refused}


def test_fixture_a_proves_the_stock_concentration(run_a: tuple[ip.InsightRun, _Stub],
                                                   demo_wh: Path) -> None:
    run, _stub = run_a
    assert run.status == "ok"
    got = _by_text(run, _A_HEADLINE)
    env = got.envelope
    assert _tuples(env["rows"]) == _A_ROWS
    assert env["text"] == got.headline
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["served_route"] == "ontology_compile"
    assert got.proof["definition"]["source"] == "demo_ontology"
    assert any("not a steward-approved item" in a for a in env["assumptions"])

    # Independent oracle: no compiler, no wrapper, one plain GROUP BY.
    con = connect_serving(demo_wh)
    try:
        oracle = con.execute(
            "SELECT l.location_code, ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) AS v "
            "FROM inventory i LEFT JOIN locations l ON i.location_id = l.location_id "
            "GROUP BY l.location_code ORDER BY v DESC, l.location_code"
        ).fetchall()
    finally:
        con.close()
    total = sum(float(v) for _, v in oracle)
    cum = 0.0
    expect = []
    for label, v in oracle:
        cum += float(v)
        expect.append((label, float(v), round(100.0 * float(v) / total, 1),
                       round(100.0 * cum / total, 1), round(total, 2)))
    assert expect == _A_ROWS
    assert _tuples(env["rows"]) == expect


# --------------------------------------------------------------------------
# fixture B: an approved store over Sales.Orders / Sales.Territory
# --------------------------------------------------------------------------


def _seed_sales(path: Path) -> Path:
    con = connect_file(path, write=True)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS Sales")
        con.execute("DROP TABLE IF EXISTS Sales.Orders")
        con.execute("DROP TABLE IF EXISTS Sales.Territory")
        con.execute(
            "CREATE TABLE Sales.Orders (order_id INTEGER, territory_id INTEGER, "
            "amount DECIMAL(12,2))"
        )
        con.execute(
            "INSERT INTO Sales.Orders VALUES (1,1,700),(2,1,200),(3,2,50),(4,3,30),"
            "(5,4,20),(6,NULL,100)"
        )
        con.execute("CREATE TABLE Sales.Territory (territory_id INTEGER, name VARCHAR)")
        con.execute(
            "INSERT INTO Sales.Territory VALUES (1,'North'),(2,'South'),(3,'East'),(4,'West')"
        )
    finally:
        con.close()
    return path


def _items(*, pii_on_name: bool = False, orders_key: str = "order_id",
           expression: str = "ROUND(SUM(f.amount), 2)") -> list[dict[str, Any]]:
    def item(item_id: str, kind: str, payload: dict[str, Any],
             status: str = "approved") -> dict[str, Any]:
        return {"id": item_id, "version_id": _B_VERSION, "kind": kind,
                "status": status, "payload": payload}

    rows = [
        item("item_obj_orders", "object",
             {"name": "Orders", "relation": "Sales.Orders", "key": [orders_key]}),
        item("item_obj_territory", "object",
             {"name": "Territory", "relation": "Sales.Territory", "key": ["territory_id"],
              "attributes": ["territory_id", "name"]}),
        item("item_link_order_territory", "link",
             {"name": "order_in_territory", "from_object": "Orders",
              "from_columns": ["territory_id"], "to_object": "Territory",
              "to_columns": ["territory_id"]}),
        item(_B_MEASURE_ITEM, "measure",
             {"name": "order_amount", "grain": "Orders", "expression": expression,
              "additive": True, "description": "order amount, one contribution per order"}),
        item("item_measure_proposed", "measure",
             {"name": "order_count_proposed", "grain": "Orders", "expression": "COUNT(*)",
              "additive": True, "description": "proposed, never approved"},
             status="proposed"),
    ]
    if pii_on_name:
        rows.append(item("item_pii_territory_name", "pii",
                         {"name": "territory_name", "relation": "Sales.Territory",
                          "column": "name"}))
    return rows


_B_GRANTS = {"Sales.Orders", "Sales.Territory"}


@pytest.fixture(scope="module")
def sales_wh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _seed_sales(tmp_path_factory.mktemp("insproof_b") / "lake_b.duckdb")


@pytest.fixture(scope="module")
def run_b(sales_wh: Path) -> tuple[ip.InsightRun, _Stub]:
    stub = _Stub(sales_wh)
    return _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items())), stub


def test_fixture_b_approved_store(run_b: tuple[ip.InsightRun, _Stub]) -> None:
    run, _stub = run_b
    assert run.status == "ok"
    texts = [p.envelope["text"] for p in run.proven]
    assert _B_CONCENTRATION in texts
    assert _B_UNKNOWN in texts
    for p in run.proven:
        env = p.envelope
        assert env["badge"] == "L1_GOVERNED_METRIC"
        assert_envelope_valid(env)
        rows = _tuples(env["rows"])
        assert ("North", 900.0, 81.8) == rows[0][:3]
        assert (None, 100.0, 9.1) == rows[-1][:3]
        assert p.proof["definition"]["item_id"] == _B_MEASURE_ITEM
        assert p.proof["definition"]["version_id"] == _B_VERSION
        assert p.proof["definition"]["source"] == "approved_store"
        assert not any("demo ontology" in a for a in env["assumptions"])
    every = json.dumps([p.envelope for p in run.proven] + list(run.refused), default=str)
    assert "order_count_proposed" not in every
    assert all("order_count_proposed" not in r["candidate"] for r in run.refused)
    assert all(p.measure != "order_count_proposed" for p in run.proven)


# --------------------------------------------------------------------------
# the proof
# --------------------------------------------------------------------------


def test_proof_conserves_hashes_and_stays_out_of_the_envelope(
    run_a: tuple[ip.InsightRun, _Stub], demo_wh: Path
) -> None:
    run, _stub = run_a
    got = _by_text(run, _A_HEADLINE)
    proof, env = got.proof, got.envelope
    assert proof["raw_total"] == proof["grouped_total"] == 29840.0
    assert proof["tolerance"] == 0.03
    assert proof["n_groups"] == 5
    assert proof["conserves"] is True
    assert proof["sql_hash"] == item_content_hash(env["sql_used"])
    assert proof["raw_total_sql_hash"] == item_content_hash(proof["raw_total_sql"])

    con = connect_serving(demo_wh)
    try:
        cur = con.execute(env["sql_used"])
        cols = [d[0] for d in cur.description]
        fresh = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()
    projected = [{c: r[c] for c in ip.PROOF_COLUMNS} for r in fresh]
    assert proof["result_hash"] == proof["reexec_result_hash"] == item_result_hash(projected)
    assert [e["result_hash"] for e in proof["executions"]] == [proof["result_hash"]] * 2
    assert all(e["engine"] == "submit" and e["row_count"] == 5 for e in proof["executions"])
    assert proof["ledger"] is None
    assert proof["scope"] == ip.PROOF_SCOPE
    assert proof["v"] == 1 and proof["insight_id"] == got.insight_id
    assert re.fullmatch(r"ins_[0-9a-f]{16}", got.insight_id)
    ip.assert_proof_shape(proof)

    assert "insight_proof" not in env
    assert "proof" not in env
    blob = json.dumps(env, default=str)
    for h in (proof["sql_hash"], proof["result_hash"], proof["raw_total_sql_hash"]):
        assert h not in blob
    # The trace is the record of what ran for this candidate, kept off the envelope.
    assert len(got.trace.statements) >= 1


def test_proof_shape_rejects_free_text_and_extra_keys(
    run_a: tuple[ip.InsightRun, _Stub],
) -> None:
    run, _ = run_a
    proof = _by_text(run, _A_HEADLINE).proof
    for bad in (
        {**proof, "note": "hello"},
        {**proof, "kind": "Concentration!"},
        {**proof, "executed_at": "yesterday"},
        {**proof, "raw_total": float("nan")},
        {**proof, "ledger": {"entry": "x"}},
        {**proof, "scope": "anything else"},
        {**proof, "watermarks": {"inventory": "last tuesday"}},
        {**proof, "raw_total_sql": "SELECT 1"},
    ):
        with pytest.raises(ip.ProofShapeError):
            ip.assert_proof_shape(bad)


# --------------------------------------------------------------------------
# grounding
# --------------------------------------------------------------------------


def test_every_proven_figure_is_a_row_cell(
    run_a: tuple[ip.InsightRun, _Stub], run_b: tuple[ip.InsightRun, _Stub]
) -> None:
    for run in (run_a[0], run_b[0]):
        assert run.proven
        for p in run.proven:
            env = p.envelope
            assert orphan_money_figures(env["text"], env["values"]) == []
            assert invented_totals(env["values"], env["rows"]) == []
            cells = {float(v) for r in env["rows"] for v in r.values()
                     if isinstance(v, (int, float)) and not isinstance(v, bool)}
            for v in env["values"]:
                assert float(v["value"]) in cells
            assert ip.headline_numbers_backed(env["text"], env["rows"], {p.k, p.n}) == []


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def _assert_refused_envelope(entry: dict[str, Any]) -> None:
    env = entry["envelope"]
    code = entry["code"].split(":", 1)[0]
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == []
    assert env["rows"] == []
    assert env["text"] == ip.REFUSALS[code]
    assert env["served_route"] == "abstain:insight_proof"
    assert_envelope_valid(env)


def test_demo_refusals_name_the_reason(run_a: tuple[ip.InsightRun, _Stub]) -> None:
    run, _ = run_a
    codes = _codes(run)
    assert codes["utilisation_pct"] == "non_additive"
    assert codes["supplier_rank_score"] == "non_additive"
    assert codes["net_movement_kg"] == "signed_measure"
    assert codes["audit_overdue"] == "volatile_measure"
    for entry in run.refused:
        _assert_refused_envelope(entry)
    for p in run.proven:
        for name in ("utilisation_pct", "supplier_rank_score", "net_movement_kg",
                     "audit_overdue"):
            assert name not in p.envelope["text"]


def test_net_movement_is_refused_after_its_raw_total(run_a: tuple[ip.InsightRun, _Stub]) -> None:
    run, stub = run_a
    entry = next(r for r in run.refused if r["candidate"] == "net_movement_kg")
    assert entry["evidence"]["raw_total"] == -3520.0
    raw_calls = [c for c in stub.calls if "AS raw_total" in c and "WRITE_OFF" in c]
    assert len(raw_calls) == 1
    assert not any("WITH g AS" in c and "WRITE_OFF" in c for c in stub.calls)


def test_run2_differs_from_run1(sales_wh: Path) -> None:
    stub = _Stub(sales_wh, tamper_run2=True)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    assert run.proven == ()
    entry = next(r for r in run.refused if r["code"] == "result_changed_between_runs")
    ev = entry["evidence"]
    assert re.fullmatch(r"[0-9a-f]{64}", ev["result_hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", ev["reexec_result_hash"])
    assert ev["result_hash"] != ev["reexec_result_hash"]
    _assert_refused_envelope(entry)


def test_a_silent_row_cap_does_not_conserve(sales_wh: Path) -> None:
    stub = _Stub(sales_wh, drop_last_group=True)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    assert run.proven == ()
    assert {r["code"] for r in run.refused} == {"not_conserved"}
    for entry in run.refused:
        _assert_refused_envelope(entry)


def test_a_refresh_during_the_proof_refuses(sales_wh: Path) -> None:
    stamps = iter(["2026-10-29T01:00:00Z", "2026-10-29T01:05:00Z"] * 10)

    def moving(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
        return {"orders": {"extracted_at": next(stamps)}}

    stub = _Stub(sales_wh)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()),
               watermarks=moving)
    assert run.proven == ()
    assert {r["code"] for r in run.refused} == {"source_refreshed_during_proof"}


def test_a_steady_watermark_is_recorded(sales_wh: Path) -> None:
    def steady(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
        return {"orders": {"extracted_at": "2026-10-29 01:00:00"}}

    run = _run(sales_wh, _Stub(sales_wh), set(_B_GRANTS),
               onto=Ontology.from_store(items=_items()), watermarks=steady)
    assert run.proven
    marks = run.proven[0].proof["watermarks"]
    assert marks["orders"] == "2026-10-29T01:00:00Z"
    assert marks["territory"] is None


def test_cortex_unreachable_refuses_every_candidate(demo_wh: Path) -> None:
    stub = _Stub(demo_wh, raise_exc=httpx.ConnectError("refused"))
    run = _run(demo_wh, stub, set(DEMO_TABLES))
    assert run.proven == ()
    executed = [r for r in run.refused
                if r["code"] not in {"non_additive", "volatile_measure"}]
    assert executed
    assert {r["code"] for r in executed} == {"cortex_unavailable"}
    for entry in run.refused:
        _assert_refused_envelope(entry)


def test_pii_dimension_never_executes(sales_wh: Path) -> None:
    stub = _Stub(sales_wh)
    onto = Ontology.from_store(items=_items(pii_on_name=True))
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=onto)
    assert run.proven == ()
    assert _codes(run)["order_amount by Territory.name"] == "pii_dimension"
    assert not any('"name"' in c or ".name" in c or "_name" in c for c in stub.calls)


def test_ungranted_fact_never_submits(sales_wh: Path) -> None:
    stub = _Stub(sales_wh)
    run = _run(sales_wh, stub, {"Sales.Territory"}, onto=Ontology.from_store(items=_items()))
    assert run.proven == ()
    assert _codes(run)["order_amount"] == "ungranted"
    assert stub.calls == []


def test_ungranted_dimension_never_submits(sales_wh: Path) -> None:
    stub = _Stub(sales_wh)
    run = _run(sales_wh, stub, {"Sales.Orders"}, onto=Ontology.from_store(items=_items()))
    assert run.proven == ()
    assert "ungranted" in set(_codes(run).values())
    assert stub.calls == []


def test_volatile_expression_refuses(sales_wh: Path) -> None:
    stub = _Stub(sales_wh)
    onto = Ontology.from_store(items=_items(
        expression="SUM(CASE WHEN CURRENT_DATE > DATE '2020-01-01' THEN f.amount END)"))
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=onto)
    assert _codes(run)["order_amount"] == "volatile_measure"
    assert stub.calls == []


# --------------------------------------------------------------------------
# E4 bare-integer gate
# --------------------------------------------------------------------------


def test_bare_integer_percent_is_unbacked(run_a: tuple[ip.InsightRun, _Stub]) -> None:
    run, _ = run_a
    rows = _by_text(run, _A_HEADLINE).rows
    text = "2 of 5 location_code values (WH-B, WH-E) carry 73 pct of stock_value_myr"
    assert ip.headline_numbers_backed(text, rows, {2, 5}) == ["73"]
    assert ip.headline_numbers_backed(_A_HEADLINE, rows, {2, 5}) == []
    assert ip.headline_numbers_backed(_A_HEADLINE, rows, {3, 5}) == ["2"]


def test_labels_with_digits_are_not_flagged() -> None:
    rows = [
        {"label": "SH-100", "value": 820.0, "share_pct": 61.2, "cum_share_pct": 61.2,
         "grouped_total": 1340.0},
        {"label": "CAM-B-01", "value": 520.0, "share_pct": 38.8, "cum_share_pct": 100.0,
         "grouped_total": 1340.0},
    ]
    text = "SH-100 alone is 61.2 pct of shipping_cost_myr across 2 shipment_id values"
    assert ip.headline_numbers_backed(text, rows, {1, 2}) == []
    text2 = "1 of 2 cctv_camera_id values (CAM-B-01) carry 38.8 pct of stock_value_myr"
    assert ip.headline_numbers_backed(text2, rows, {1, 2}) == []


def test_integer_formatter_proves_nothing(
    sales_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ip, "format_share", lambda cell: f"{float(cell):.0f}")
    run = _run(sales_wh, _Stub(sales_wh), set(_B_GRANTS),
               onto=Ontology.from_store(items=_items()))
    assert run.proven == ()
    assert run.refused
    assert {r["code"] for r in run.refused} == {"headline_unbacked"}
    for entry in run.refused:
        _assert_refused_envelope(entry)


# --------------------------------------------------------------------------
# whole-run refusal, flag off
# --------------------------------------------------------------------------


def test_unverified_ontology_refuses_the_run(sales_wh: Path) -> None:
    stub = _Stub(sales_wh)
    onto = Ontology.from_store(items=_items(orders_key="territory_id"))
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=onto)
    assert run.status == "refused"
    assert run.proven == ()
    assert [r["code"] for r in run.refused] == ["ontology_unverified"]
    for entry in run.refused:
        _assert_refused_envelope(entry)
        assert not re.search(r"\d", entry["envelope"]["text"])
    assert stub.calls == []


@pytest.fixture
def _clean_flag() -> Iterator[None]:
    saved = os.environ.pop(_FLAG, None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ[_FLAG] = saved


@pytest.mark.usefixtures("_clean_flag")
@pytest.mark.parametrize("value", [None, "0", "true", "", "yes"])
def test_flag_off_is_main(value: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    if value is not None:
        monkeypatch.setenv(_FLAG, value)
    calls: list[str] = []

    def spy(sql: str) -> Any:
        calls.append(sql)
        raise AssertionError("submit must not be called with the flag off")

    assert ip.insight_proof_enabled() is False
    run = ip.prove_insights(Path("does/not/exist"), submit=spy, granted=set())
    assert run.status == "off"
    assert run.proven == () and run.refused == ()
    assert calls == []


def test_nothing_on_main_imports_the_module() -> None:
    root = Path(__file__).resolve().parents[1]
    hits = []
    for base in ("packages", "apps"):
        for path in (root / base).rglob("*.py"):
            if path.name == "insight_proof.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "insight_proof" in text and re.search(r"^\s*(from|import)\s.*insight_proof",
                                                     text, re.M):
                hits.append(str(path))
    assert hits == []


def test_route_stamp_keeps_the_new_stage_only_as_an_addition() -> None:
    assert "insight_proof" in ABSTAIN_STAGES
    assert kept_stamp("abstain:insight_proof") is True
    for stage in ("insights_budget", "served_sql", "envelope", "unspecified"):
        assert stage in ABSTAIN_STAGES
