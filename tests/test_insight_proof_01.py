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


def test_proof_check_rejects_an_inconsistent_proof(
    run_a: tuple[ip.InsightRun, _Stub],
) -> None:
    got = _by_text(run_a[0], _A_HEADLINE)
    proof = got.proof
    ip.assert_proof_shape(proof, sql=got.sql, rows=got.rows)
    execs = proof["executions"]
    for bad in (
        {**proof, "raw_total": 1.0, "grouped_total": 999999.0},
        {**proof, "conserves": False},
        {**proof, "result_hash": "0" * 64},
        {**proof, "reexec_result_hash": "f" * 64},
        {**proof, "n_groups": 77},
        {**proof, "negative_parts": 1},
        {**proof, "negative_parts": 0.0},
        {**proof, "executions": [{**execs[0], "result_hash": "1" * 64}, execs[1]]},
    ):
        with pytest.raises(ip.ProofShapeError):
            ip.assert_proof_shape(bad)
    with pytest.raises(ip.ProofShapeError, match="before executed_at"):
        ip.assert_proof_shape({**proof, "executed_at": "2030-01-01T00:00:00Z"})
    with pytest.raises(ip.ProofShapeError):
        ip.assert_proof_shape({**proof, "sql_hash": "a" * 64}, sql=got.sql)
    swapped = [got.rows[0], got.rows[2], got.rows[1], *got.rows[3:]]
    with pytest.raises(ip.ProofShapeError, match="order_hash"):
        ip.assert_proof_shape(proof, rows=swapped)
    with pytest.raises(ip.ProofShapeError, match="result_hash"):
        ip.assert_proof_shape(proof, rows=got.rows[:-1])


def test_proof_check_binds_relation_item_and_watermarks(
    run_b: tuple[ip.InsightRun, _Stub],
) -> None:
    """Verify round 3: with ``onto`` the raw relation, item ids and watermarks are bound."""
    onto = Ontology.from_store(items=_items())
    got = _by_text(run_b[0], _B_CONCENTRATION)
    proof = got.proof
    ip.assert_proof_shape(proof, onto=onto, sql=got.sql, rows=got.rows)
    expr = onto.measures["order_amount"].expression
    other = f"SELECT {expr} AS raw_total FROM (SELECT 1 AS amount) f"
    forged_raw = {**proof, "raw_total_sql": other,
                  "raw_total_sql_hash": item_content_hash(other)}
    with pytest.raises(ip.ProofShapeError, match="own fact relation"):
        ip.assert_proof_shape(forged_raw, onto=onto, sql=got.sql, rows=got.rows)
    for item_id, version_id in (("item_obj_territory", _B_VERSION),
                                (_B_MEASURE_ITEM, "forged_v9")):
        forged = {**proof, "definition": {**proof["definition"], "item_id": item_id,
                                          "version_id": version_id}}
        with pytest.raises(ip.ProofShapeError, match="registry"):
            ip.assert_proof_shape(forged, onto=onto, sql=got.sql, rows=got.rows)
    future = {**proof, "watermarks": {t: "2099-01-01T00:00:00Z" for t in proof["watermarks"]}}
    with pytest.raises(ip.ProofShapeError, match="later than"):
        ip.assert_proof_shape(future, onto=onto, sql=got.sql, rows=got.rows)
    with pytest.raises(ip.ProofShapeError, match="exactly the tables"):
        ip.assert_proof_shape({**proof, "watermarks": {"orders": None}},
                              onto=onto, sql=got.sql, rows=got.rows)


def test_unknown_watermarks_are_said_in_the_assumptions(
    run_a: tuple[ip.InsightRun, _Stub], sales_wh: Path
) -> None:
    for p in run_a[0].proven:
        assert set(p.proof["watermarks"].values()) == {None}
        assert ip.WATERMARK_UNKNOWN_ASSUMPTION in p.envelope["assumptions"]

    def steady(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
        return {"orders": {"extracted_at": "2026-10-29 01:00:00"},
                "territory": {"extracted_at": "2026-10-29 01:00:00"}}

    run = _run(sales_wh, _Stub(sales_wh), set(_B_GRANTS),
               onto=Ontology.from_store(items=_items()), watermarks=steady)
    assert run.proven
    for p in run.proven:
        assert ip.WATERMARK_UNKNOWN_ASSUMPTION not in p.envelope["assumptions"]

    def partial(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
        return {"orders": {"extracted_at": "2026-10-29 01:00:00"}}

    run = _run(sales_wh, _Stub(sales_wh), set(_B_GRANTS),
               onto=Ontology.from_store(items=_items()), watermarks=partial)
    assert run.proven
    for p in run.proven:
        assert p.proof["watermarks"]["territory"] is None
        assert ip.WATERMARK_UNKNOWN_ASSUMPTION in p.envelope["assumptions"]


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
    assert entry["code"] == "signed_measure"
    _assert_refused_envelope(entry)
    raw_calls = [c for c in stub.calls if "AS raw_total" in c and "WRITE_OFF" in c]
    assert len(raw_calls) == 1
    assert not any("AS negative_parts" in c and "WRITE_OFF" in c for c in stub.calls)
    assert not any("WITH g AS" in c and "WRITE_OFF" in c for c in stub.calls)


def test_refusal_records_carry_no_figures(run_a: tuple[ip.InsightRun, _Stub],
                                          sales_wh: Path) -> None:
    """A refusal carries its code, its envelope and at most hashes: never a figure."""
    tampered = _run(sales_wh, _Stub(sales_wh, tamper_run2=True), set(_B_GRANTS),
                    onto=Ontology.from_store(items=_items()))
    for entry in list(run_a[0].refused) + list(tampered.refused):
        for value in (entry.get("evidence") or {}).values():
            assert isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), entry
    assert any(r.get("evidence") for r in tampered.refused)


def test_an_empty_fact_is_not_too_few_groups(sales_wh: Path) -> None:
    class _NullTotal(_Stub):
        def __call__(self, sql: str) -> Any:
            if "AS raw_total" in sql:
                self.calls.append(sql)
                return SimpleNamespace(ok=True, output={"rows": [{"raw_total": None}]})
            return super().__call__(sql)

    stub = _NullTotal(sales_wh)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    assert run.proven == ()
    assert _codes(run) == {"order_amount": "empty_fact"}
    for entry in run.refused:
        _assert_refused_envelope(entry)
        assert entry["envelope"]["text"].startswith("The measure has no figure to total")
    assert not any("WITH g AS" in c for c in stub.calls)


def _seed_mixed_sign(path: Path) -> Path:
    _seed_sales(path)
    con = connect_file(path, write=True)
    try:
        con.execute("DELETE FROM Sales.Orders")
        # North nets 1700 - 1000 = 700 and South 50 - 10 = 40: every group sum is
        # positive, and so is the total, but two orders are returns.
        con.execute("INSERT INTO Sales.Orders VALUES (1,1,1700),(2,1,-1000),(3,2,50),"
                    "(4,3,30),(5,4,20),(7,2,-10),(6,NULL,100)")
    finally:
        con.close()
    return path


def test_negative_parts_inside_positive_groups_are_signed(tmp_path: Path) -> None:
    wh = _seed_mixed_sign(tmp_path / "mixed.duckdb")
    stub = _Stub(wh)
    run = _run(wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    assert run.status == "refused"
    assert run.proven == ()
    assert _codes(run) == {"order_amount": "signed_measure"}
    for entry in run.refused:
        _assert_refused_envelope(entry)
    texts = json.dumps([r["envelope"] for r in run.refused])
    for leaked in ("North", "83.1", "11.2"):
        assert leaked not in texts
    parts = [c for c in stub.calls if "AS negative_parts" in c]
    assert len(parts) == 1
    assert 'GROUP BY f."order_id"' in parts[0]
    assert not any("WITH g AS" in c for c in stub.calls)


def test_a_positive_measure_records_zero_negative_parts(
    run_b: tuple[ip.InsightRun, _Stub],
) -> None:
    run, stub = run_b
    got = _by_text(run, _B_CONCENTRATION)
    assert got.proof["negative_parts"] == 0
    parts = [c for c in stub.calls if "AS negative_parts" in c]
    assert parts and got.proof["parts_sql_hash"] == item_content_hash(parts[0])


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


def test_a_refresh_under_a_mixed_case_registry_name_refuses(sales_wh: Path) -> None:
    """A registry keyed 'Orders' still matches the casefolded table the relation reads."""
    stamps = iter(["2026-10-29T01:00:00Z", "2026-10-29T01:05:00Z"] * 10)

    def moving(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
        stamp = next(stamps)
        return {"Orders": {"extracted_at": stamp}, "Territory": {"extracted_at": stamp}}

    stub = _Stub(sales_wh)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()),
               watermarks=moving)
    assert run.proven == ()
    assert {r["code"] for r in run.refused} == {"source_refreshed_during_proof"}
    for entry in run.refused:
        _assert_refused_envelope(entry)

    def steady(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
        return {"Orders": {"extracted_at": "2026-10-29 01:00:00"},
                "TERRITORY": {"extracted_at": "2026-10-29 01:00:00"}}

    run = _run(sales_wh, _Stub(sales_wh), set(_B_GRANTS),
               onto=Ontology.from_store(items=_items()), watermarks=steady)
    got = _by_text(run, _B_CONCENTRATION)
    assert got.proof["watermarks"] == {"orders": "2026-10-29T01:00:00Z",
                                       "territory": "2026-10-29T01:00:00Z"}
    assert ip.WATERMARK_UNKNOWN_ASSUMPTION not in got.envelope["assumptions"]


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


@pytest.mark.parametrize(
    ("relation", "column", "candidate"),
    [
        ("(SELECT territory_id, name FROM Sales.Territory)", "name",
         "order_amount by Territory.name"),
        ("(SELECT territory_id, name AS tname FROM Sales.Territory)", "tname",
         "order_amount by Territory.tname"),
        ("(SELECT territory_id, label FROM (SELECT territory_id, UPPER(name) AS label "
         "FROM Sales.Territory) t)", "label", "order_amount by Territory.label"),
        # Positional renames make no exp.Alias: they fail closed (verify round 2).
        ("(SELECT territory_id, region FROM Sales.Territory AS t(territory_id, region))",
         "region", "order_amount by Territory.region"),
        ("(SELECT * FROM Sales.Territory AS t(territory_id, region))",
         "region", "order_amount by Territory.region"),
        ("(SELECT territory_id, region FROM (SELECT 0 AS territory_id, 'x' AS region "
         "UNION ALL SELECT territory_id, name FROM Sales.Territory) u)",
         "region", "order_amount by Territory.region"),
    ],
)
def test_pii_on_a_subquery_relation_never_executes(
    tmp_path: Path, relation: str, column: str, candidate: str
) -> None:
    """A pii item on Sales.Territory binds an object that reads it through a subquery."""
    wh = _seed_sales(tmp_path / "pii_subq.duckdb")
    items = _items(pii_on_name=True)
    for it in items:
        if it["id"] == "item_obj_territory":
            it["payload"]["relation"] = relation
            it["payload"]["attributes"] = ["territory_id", column]
    stub = _Stub(wh)
    run = _run(wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=items))
    assert run.proven == ()
    assert _codes(run)[candidate] == "pii_dimension"
    for entry in run.refused:
        _assert_refused_envelope(entry)
    shown = json.dumps([r["envelope"] for r in run.refused])
    for name in ("North", "South", "East", "West", "86.4", "9.1"):
        assert name not in shown
    assert not any("WITH g AS" in c for c in stub.calls)


def test_a_renamed_relation_without_pii_still_proves(tmp_path: Path) -> None:
    """Control: the fail-closed rename rule binds only when a pii item touches the table."""
    wh = _seed_sales(tmp_path / "rename_ok.duckdb")
    items = _items()
    for it in items:
        if it["id"] == "item_obj_territory":
            it["payload"]["relation"] = (
                "(SELECT territory_id, region FROM Sales.Territory AS t(territory_id, region))")
            it["payload"]["attributes"] = ["territory_id", "region"]
    run = _run(wh, _Stub(wh), set(_B_GRANTS), onto=Ontology.from_store(items=items))
    got = _by_text(
        run, "2 of 4 Territory.region values (North, South) carry 86.4 pct of order_amount")
    assert_envelope_valid(got.envelope)
    assert _tuples(got.envelope["rows"])[0][:3] == ("North", 900.0, 81.8)


@pytest.mark.parametrize(
    "relation",
    [
        "(SELECT territory_id, region FROM Sales.Territory AS t(territory_id, region))",
        "(SELECT * FROM Sales.Territory)",
        "(SELECT t.* EXCLUDE (territory_id) FROM Sales.Territory t)",
        "(SELECT territory_id, name FROM Sales.Territory UNION SELECT 1, 'x')",
        "(SELECT territory_id, UPPER(name) FROM Sales.Territory)",
        "(SELECT * FROM (VALUES (1, 'x')) v(territory_id, region))",
        # A CTE name is not a granted table, so end to end this is 'ungranted' first.
        "(WITH c(territory_id, region) AS (SELECT territory_id, name FROM Sales.Territory) "
        "SELECT territory_id, region FROM c)",
    ],
)
def test_pii_taint_fails_closed_on_renames_it_does_not_model(relation: str) -> None:
    assert ip._subquery_taint(relation, {"name"}) is None


def test_pii_taint_keeps_explicit_aliases() -> None:
    got = ip._subquery_taint(
        "(SELECT territory_id, name AS tname FROM Sales.Territory)", {"name"})
    assert got is not None and {"name", "tname"} <= set(got)
    assert "territory_id" not in got


def test_pii_on_an_unresolvable_relation_is_all_pii() -> None:
    onto = Ontology.from_store(items=_items(pii_on_name=True))
    onto.objects["Territory"] = ip.replace(onto.objects["Territory"], relation="")
    assert "anything" in ip._pii_columns(onto, "Territory")
    assert "name" in ip._pii_columns(onto, "Territory")


# --------------------------------------------------------------------------
# verify round 3: name-free column references and comma joins
# --------------------------------------------------------------------------


def _seed_dummy(wh: Path) -> None:
    con = connect_file(wh, write=True)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS Sales.Dummy (x INTEGER)")
        con.execute("DELETE FROM Sales.Dummy")
        con.execute("INSERT INTO Sales.Dummy VALUES (1)")
    finally:
        con.close()


def _territory_items(relation: str, attr: str, *, pii: bool) -> list[dict[str, Any]]:
    items = _items(pii_on_name=pii)
    for it in items:
        if it["id"] == "item_obj_territory":
            it["payload"]["relation"] = relation
            it["payload"]["attributes"] = ["territory_id", attr]
    return items


_ROUND3_LEAKS = [
    # Each reaches the marked column with no exp.Column named 'name' (the verifier's 8a0b32c).
    ("(SELECT territory_id, #2 AS region FROM Sales.Territory)", "region", False),
    ("(SELECT territory_id, COLUMNS('name') AS region FROM Sales.Territory)", "region", False),
    ("(SELECT t.territory_id, t['name'] AS region FROM Sales.Territory t)", "region", False),
    ("(SELECT t.territory_id, struct_extract(t, 'name') AS region FROM Sales.Territory t)",
     "region", False),
    # A whole-row reference and a struct field of the marked column.
    ("(SELECT t.territory_id, CAST(t AS VARCHAR) AS region FROM Sales.Territory t)",
     "region", False),
    # A comma join hides Sales.Territory from relation_tables' FROM/JOIN regex.
    ("(SELECT t.territory_id, t.name FROM Sales.Dummy d, Sales.Territory t)", "name", True),
]


@pytest.mark.parametrize(("relation", "attr", "dummy"), _ROUND3_LEAKS)
def test_pii_read_without_its_name_never_executes(
    tmp_path: Path, relation: str, attr: str, dummy: bool
) -> None:
    wh = _seed_sales(tmp_path / "pii_r3.duckdb")
    granted = set(_B_GRANTS)
    if dummy:
        _seed_dummy(wh)
        granted.add("Sales.Dummy")
    stub = _Stub(wh)
    onto = Ontology.from_store(items=_territory_items(relation, attr, pii=True))
    run = _run(wh, stub, granted, onto=onto)
    texts = [p.envelope["text"] for p in run.proven]
    assert texts == []
    for leaked in ("2 of 4 Territory." + attr, "lands on rows with no Territory." + attr):
        assert not any(leaked in t for t in texts)
    assert _codes(run)[f"order_amount by Territory.{attr}"] == "pii_dimension"
    for entry in run.refused:
        _assert_refused_envelope(entry)
    shown = json.dumps([r["envelope"] for r in run.refused])
    for name in ("North", "South", "East", "West", "86.4", "9.1"):
        assert name not in shown
    assert not any("WITH g AS" in c for c in stub.calls)
    assert not any("territory" in c.casefold() for c in stub.calls)


@pytest.mark.parametrize(
    "relation",
    [
        "(SELECT territory_id, #2 AS region FROM Sales.Territory)",
        "(SELECT territory_id, COLUMNS('name') AS region FROM Sales.Territory)",
        "(SELECT t.territory_id, t['name'] AS region FROM Sales.Territory t)",
        "(SELECT t.territory_id, struct_extract(t, 'name') AS region FROM Sales.Territory t)",
        "(SELECT t.territory_id, t AS region FROM Sales.Territory t)",
        "(SELECT territory_id, Territory AS region FROM Sales.Territory)",
        "(SELECT territory_id, row_to_json(t) AS region FROM Sales.Territory t)",
        "(SELECT territory_id, FIRST(name) OVER () AS region FROM Sales.Territory)",
        "(SELECT territory_id, (SELECT MAX(name) FROM Sales.Territory) AS region "
        "FROM Sales.Orders)",
    ],
)
def test_pii_taint_is_an_allowlist(relation: str) -> None:
    assert ip._subquery_taint(relation, {"name"}) is None


def test_pii_taint_follows_struct_fields_of_a_marked_column() -> None:
    got = ip._subquery_taint(
        "(SELECT t.territory_id, t.name.first AS region FROM Sales.Territory t)", {"name"})
    assert got is not None and "region" in got


@pytest.mark.parametrize(
    ("relation", "want"),
    [
        ("(SELECT t.territory_id, t.name FROM Sales.Dummy d, Sales.Territory t)",
         {"dummy", "territory"}),
        ("Sales . Territory", {"sales", "territory"}),
        ("Sales.Territory", {"territory"}),
    ],
)
def test_read_tables_sees_comma_joins_and_spaced_dots(relation: str, want: set[str]) -> None:
    assert ip._read_tables(relation) == want


def test_comma_join_to_an_ungranted_table_never_submits(tmp_path: Path) -> None:
    """Sales.Territory is not granted; the object reads it through a comma join."""
    wh = _seed_sales(tmp_path / "comma_ungranted.duckdb")
    _seed_dummy(wh)
    stub = _Stub(wh)
    relation = "(SELECT t.territory_id, t.name FROM Sales.Dummy d, Sales.Territory t)"
    onto = Ontology.from_store(items=_territory_items(relation, "name", pii=False))
    run = _run(wh, stub, {"Sales.Orders", "Sales.Dummy"}, onto=onto)
    assert [p.envelope["text"] for p in run.proven] == []
    assert "ungranted" in set(_codes(run).values())
    for entry in run.refused:
        _assert_refused_envelope(entry)
    assert not any("territory" in c.casefold() for c in stub.calls)


def test_comma_join_without_pii_still_proves(tmp_path: Path) -> None:
    """Control: a granted comma join with no pii item proves as before."""
    wh = _seed_sales(tmp_path / "comma_ok.duckdb")
    _seed_dummy(wh)
    relation = "(SELECT t.territory_id, t.name FROM Sales.Dummy d, Sales.Territory t)"
    onto = Ontology.from_store(items=_territory_items(relation, "name", pii=False))
    run = _run(wh, _Stub(wh), set(_B_GRANTS) | {"Sales.Dummy"}, onto=onto)
    got = _by_text(run, _B_CONCENTRATION)
    assert got.envelope["badge"] == "L1_GOVERNED_METRIC"
    assert_envelope_valid(got.envelope)
    assert _tuples(got.envelope["rows"])[0][:3] == ("North", 900.0, 81.8)
    assert set(got.proof["watermarks"]) == {"orders", "territory", "dummy"}


# --------------------------------------------------------------------------
# verify round 4: views, implicit flow through predicates, the three-part pii
# name, proof figures bound to rows, top=0
# --------------------------------------------------------------------------


def _exec(wh: Path, *statements: str) -> None:
    con = connect_file(wh, write=True)
    try:
        for sql in statements:
            con.execute(sql)
    finally:
        con.close()


_LEAK_TOKENS = ("North", "South", "East", "West", "86.4", "9.1")


def _assert_nothing_leaked(run: ip.InsightRun, stub: _Stub, attr: str) -> None:
    """No sentence, no row, no refusal text and no submit carries the marked values."""
    assert [p.envelope["text"] for p in run.proven] == []
    assert run.proven == ()
    assert _codes(run)[f"order_amount by Territory.{attr}"] == "pii_dimension"
    for entry in run.refused:
        _assert_refused_envelope(entry)
    shown = json.dumps([r["envelope"] for r in run.refused])
    for token in _LEAK_TOKENS:
        assert token not in shown
    assert not any("WITH g AS" in c for c in stub.calls)
    assert not any("territory" in c.casefold() for c in stub.calls)


_VIEWS = [
    # (statements, view the object reads, attribute)
    (["CREATE VIEW Sales.TerritoryV AS SELECT territory_id, name AS region "
      "FROM Sales.Territory"], "Sales.TerritoryV", "region"),
    (["CREATE VIEW Sales.TerritoryV AS SELECT territory_id, name FROM Sales.Territory"],
     "Sales.TerritoryV", "name"),
    # A view over a view, renamed at each level.
    (["CREATE VIEW Sales.TerritoryV AS SELECT territory_id, name AS region "
      "FROM Sales.Territory",
      "CREATE VIEW Sales.TerritoryW AS SELECT territory_id, UPPER(region) AS area "
      "FROM Sales.TerritoryV"], "Sales.TerritoryW", "area"),
    # A view column list renames by position: fails closed.
    (["CREATE VIEW Sales.TerritoryV (territory_id, region) AS "
      "SELECT territory_id, name FROM Sales.Territory"], "Sales.TerritoryV", "region"),
]


@pytest.mark.parametrize(("statements", "view", "attr"), _VIEWS)
def test_a_view_over_a_marked_table_never_executes(
    tmp_path: Path, statements: list[str], view: str, attr: str
) -> None:
    """Verify round 4 (MEDIUM): a view hid the pii item; its values reached an L1 headline."""
    wh = _seed_sales(tmp_path / "pii_view.duckdb")
    _exec(wh, *statements)
    stub = _Stub(wh)
    onto = Ontology.from_store(items=_territory_items(view, attr, pii=True))
    granted = set(_B_GRANTS) | {"Sales.TerritoryV", "Sales.TerritoryW"}
    run = _run(wh, stub, granted, onto=onto)
    for leaked in (f"2 of 4 Territory.{attr} values (North, South) carry 86.4 pct of "
                   "order_amount", f"9.1 pct of order_amount lands on rows with no "
                   f"Territory.{attr} value"):
        assert leaked not in [p.envelope["text"] for p in run.proven]
    _assert_nothing_leaked(run, stub, attr)


def test_a_view_without_pii_still_proves(tmp_path: Path) -> None:
    """Control: a view over an unmarked table proves as before."""
    wh = _seed_sales(tmp_path / "view_ok.duckdb")
    _exec(wh, "CREATE VIEW Sales.TerritoryV AS SELECT territory_id, name AS region "
              "FROM Sales.Territory")
    onto = Ontology.from_store(items=_territory_items("Sales.TerritoryV", "region", pii=False))
    run = _run(wh, _Stub(wh), set(_B_GRANTS) | {"Sales.TerritoryV"}, onto=onto)
    got = _by_text(
        run, "2 of 4 Territory.region values (North, South) carry 86.4 pct of order_amount")
    assert got.envelope["badge"] == "L1_GOVERNED_METRIC"
    assert_envelope_valid(got.envelope)
    assert _tuples(got.envelope["rows"])[0][:3] == ("North", 900.0, 81.8)


def test_a_view_marked_on_its_own_column_never_executes(tmp_path: Path) -> None:
    """A pii item that names the view's own column binds the object that reads it."""
    wh = _seed_sales(tmp_path / "view_own.duckdb")
    _exec(wh, "CREATE VIEW Sales.TerritoryV AS SELECT territory_id, name AS region "
              "FROM Sales.Territory")
    items = _territory_items("Sales.TerritoryV", "region", pii=False)
    items.append({"id": "item_pii_view", "version_id": _B_VERSION, "kind": "pii",
                  "status": "approved",
                  "payload": {"name": "territory_region", "relation": "Sales.TerritoryV",
                              "column": "region"}})
    stub = _Stub(wh)
    run = _run(wh, stub, set(_B_GRANTS) | {"Sales.TerritoryV"},
               onto=Ontology.from_store(items=items))
    _assert_nothing_leaked(run, stub, "region")


# Verify round 6 (MEDIUM): a generated (virtual) column over the marked column
# read as a plain column of a base table, and NORTH/SOUTH reached an L1 headline.
_GENERATED = [
    # (generated column definition, relation the object reads, attribute)
    ("region VARCHAR GENERATED ALWAYS AS (upper(name)) VIRTUAL", "Sales.Territory", "region"),
    ("region VARCHAR AS (name || '')", "Sales.Territory", "region"),
    ("region VARCHAR AS (md5(name))", "Sales.Territory", "region"),
    # A subquery rename of the generated column carries its mark.
    ("region VARCHAR AS (upper(name))",
     "(SELECT territory_id, region AS area FROM Sales.Territory)", "area"),
]


def _seed_generated(wh: Path, column: str) -> None:
    _exec(wh, "DROP TABLE Sales.Territory",
          f"CREATE TABLE Sales.Territory (territory_id INTEGER, name VARCHAR, "
          f"zone VARCHAR, {column})",
          "INSERT INTO Sales.Territory (territory_id, name, zone) VALUES "
          "(1,'North','north'),(2,'South','south'),(3,'East','east'),(4,'West','west')")


@pytest.mark.parametrize(("column", "relation", "attr"), _GENERATED)
def test_a_generated_column_over_a_marked_column_never_executes(
    tmp_path: Path, column: str, relation: str, attr: str
) -> None:
    wh = _seed_sales(tmp_path / "pii_gen.duckdb")
    _seed_generated(wh, column)
    stub = _Stub(wh)
    onto = Ontology.from_store(items=_territory_items(relation, attr, pii=True))
    run = _run(wh, stub, set(_B_GRANTS), onto=onto)
    texts = [p.envelope["text"] for p in run.proven]
    assert (f"2 of 4 Territory.{attr} values (NORTH, SOUTH) carry 86.4 pct of "
            "order_amount") not in texts
    _assert_nothing_leaked(run, stub, attr)
    shown = json.dumps([r["envelope"] for r in run.refused])
    assert "NORTH" not in shown and "SOUTH" not in shown


def test_a_generated_column_over_an_unmarked_column_still_proves(tmp_path: Path) -> None:
    """Control: on a marked table, a generated column over an unmarked column proves."""
    wh = _seed_sales(tmp_path / "gen_ok.duckdb")
    _seed_generated(wh, "region VARCHAR AS (upper(zone))")
    onto = Ontology.from_store(items=_territory_items("Sales.Territory", "region", pii=True))
    run = _run(wh, _Stub(wh), set(_B_GRANTS), onto=onto)
    got = _by_text(
        run, "2 of 4 Territory.region values (NORTH, SOUTH) carry 86.4 pct of order_amount")
    assert got.envelope["badge"] == "L1_GOVERNED_METRIC"
    assert_envelope_valid(got.envelope)
    assert [t[:3] for t in _tuples(got.envelope["rows"])] == [
        ("NORTH", 900.0, 81.8), ("SOUTH", 50.0, 4.5), ("EAST", 30.0, 2.7),
        ("WEST", 20.0, 1.8), (None, 100.0, 9.1)]


def test_a_generated_column_without_pii_still_proves(tmp_path: Path) -> None:
    """Control: with no pii item, the generated column is an ordinary segment."""
    wh = _seed_sales(tmp_path / "gen_nopii.duckdb")
    _seed_generated(wh, "region VARCHAR AS (upper(name))")
    onto = Ontology.from_store(items=_territory_items("Sales.Territory", "region", pii=False))
    run = _run(wh, _Stub(wh), set(_B_GRANTS), onto=onto)
    got = _by_text(
        run, "2 of 4 Territory.region values (NORTH, SOUTH) carry 86.4 pct of order_amount")
    assert_envelope_valid(got.envelope)
    assert _tuples(got.envelope["rows"])[0][:3] == ("NORTH", 900.0, 81.8)


@pytest.mark.parametrize(("expression", "carries"), [
    ('CAST(upper("name") AS VARCHAR)', True),
    ('CAST(upper("zone") AS VARCHAR)', False),
    ('CAST(md5("zone") AS VARCHAR)', True),   # unlisted function: not modelled
    ("CAST(#2 AS VARCHAR)", True),            # positional: names no column
    (None, True),
])
def test_generated_expression_trace(expression: str | None, carries: bool) -> None:
    """In addition to the envelope tests above: the expression rule itself."""
    assert ip._generated_reads_marked(expression, {"name"}) is carries


def test_an_unreadable_generated_column_catalog_is_all_pii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: a catalog that cannot list generated columns makes the object all-PII."""
    wh = _seed_sales(tmp_path / "gen_cat.duckdb")
    monkeypatch.setattr(ip, "_generated_columns", lambda con: None)
    stub = _Stub(wh)
    onto = Ontology.from_store(items=_items(pii_on_name=True))
    run = _run(wh, stub, set(_B_GRANTS), onto=onto)
    _assert_nothing_leaked(run, stub, "name")


# Verify round 7 (MEDIUM): a generated column that reads another generated column
# (``region AS (lower(g1))`` over ``g1 AS (upper(name))``) was not marked, because
# the generated-column pass ran once against the pii items only, and north/south
# reached a proven L1 headline.
_GENERATED_CHAINS = [
    # (generated column definitions, relation the object reads, attribute)
    ("g1 VARCHAR GENERATED ALWAYS AS (upper(name)) VIRTUAL, "
     "region VARCHAR GENERATED ALWAYS AS (lower(g1)) VIRTUAL", "Sales.Territory", "region"),
    # Three levels deep.
    ("g1 VARCHAR AS (upper(name)), g2 VARCHAR AS (lower(g1)), region VARCHAR AS (upper(g2))",
     "Sales.Territory", "region"),
    # A subquery rename of the chained column carries its mark.
    ("g1 VARCHAR AS (upper(name)), region VARCHAR AS (lower(g1))",
     "(SELECT territory_id, region AS area FROM Sales.Territory)", "area"),
]


@pytest.mark.parametrize(("columns", "relation", "attr"), _GENERATED_CHAINS)
def test_a_chained_generated_column_over_a_marked_column_never_executes(
    tmp_path: Path, columns: str, relation: str, attr: str
) -> None:
    wh = _seed_sales(tmp_path / "pii_chain.duckdb")
    _seed_generated(wh, columns)
    stub = _Stub(wh)
    onto = Ontology.from_store(items=_territory_items(relation, attr, pii=True))
    run = _run(wh, stub, set(_B_GRANTS), onto=onto)
    texts = [p.envelope["text"] for p in run.proven]
    for values in ("(north, south)", "(NORTH, SOUTH)"):
        assert (f"2 of 4 Territory.{attr} values {values} carry 86.4 pct of "
                "order_amount") not in texts
    _assert_nothing_leaked(run, stub, attr)
    shown = json.dumps([r["envelope"] for r in run.refused]).casefold()
    assert "north" not in shown and "south" not in shown


def test_a_chained_generated_column_is_in_the_pii_columns(tmp_path: Path) -> None:
    """In addition to the envelope test above: every link of the chain is marked."""
    from dms_executor.demo_warehouse import connect_serving

    wh = _seed_sales(tmp_path / "pii_chain_cols.duckdb")
    _seed_generated(wh, "g1 VARCHAR AS (upper(name)), g2 VARCHAR AS (lower(g1)), "
                        "region VARCHAR AS (upper(g2)), other VARCHAR AS (upper(zone))")
    onto = Ontology.from_store(items=_territory_items("Sales.Territory", "region", pii=True))
    con = connect_serving(wh)
    try:
        cols = ip._pii_columns(onto, "Territory", con)
    finally:
        con.close()
    assert {"name", "g1", "g2", "region"} <= set(cols)
    assert "other" not in cols and "zone" not in cols


def test_a_chained_generated_column_over_an_unmarked_column_still_proves(
    tmp_path: Path,
) -> None:
    """Control: on a marked table, a chain that starts at an unmarked column proves."""
    wh = _seed_sales(tmp_path / "chain_ok.duckdb")
    _seed_generated(wh, "g1 VARCHAR AS (lower(zone)), region VARCHAR AS (upper(g1))")
    onto = Ontology.from_store(items=_territory_items("Sales.Territory", "region", pii=True))
    run = _run(wh, _Stub(wh), set(_B_GRANTS), onto=onto)
    got = _by_text(
        run, "2 of 4 Territory.region values (NORTH, SOUTH) carry 86.4 pct of order_amount")
    assert got.envelope["badge"] == "L1_GOVERNED_METRIC"
    assert_envelope_valid(got.envelope)
    assert [t[:3] for t in _tuples(got.envelope["rows"])] == [
        ("NORTH", 900.0, 81.8), ("SOUTH", 50.0, 4.5), ("EAST", 30.0, 2.7),
        ("WEST", 20.0, 1.8), (None, 100.0, 9.1)]


def _seed_codes(wh: Path) -> None:
    _exec(wh, "CREATE TABLE Sales.Codes (code VARCHAR)",
          "INSERT INTO Sales.Codes VALUES ('North'),('South'),('East'),('West')")


@pytest.mark.parametrize(
    "relation",
    [
        "(SELECT t.territory_id, c.code AS region FROM Sales.Territory t "
        "JOIN Sales.Codes c ON c.code = t.name)",
        "(SELECT t.territory_id, c.code AS region FROM Sales.Territory t, Sales.Codes c "
        "WHERE t.territory_id > 0 AND c.code = t.name)",
        "(SELECT t.territory_id, c.code AS region FROM Sales.Territory t "
        "JOIN Sales.Codes c ON c.code LIKE t.name || '%')",
    ],
)
def test_a_join_predicate_on_a_marked_column_never_executes(
    tmp_path: Path, relation: str
) -> None:
    """Verify round 4 (LOW): c.code = t.name served the marked values as c.code."""
    wh = _seed_sales(tmp_path / "pii_join_eq.duckdb")
    _seed_codes(wh)
    stub = _Stub(wh)
    onto = Ontology.from_store(items=_territory_items(relation, "region", pii=True))
    run = _run(wh, stub, set(_B_GRANTS) | {"Sales.Codes"}, onto=onto)
    _assert_nothing_leaked(run, stub, "region")


def test_predicate_taint_keeps_unrelated_columns_clean() -> None:
    """A join on the key does not mark the key; a filter on the marked column marks itself."""
    got = ip._subquery_taint(
        "(SELECT t.territory_id, c.code AS region FROM Sales.Territory t "
        "JOIN Sales.Codes c ON c.territory_id = t.territory_id WHERE t.name <> 'x')",
        {"name"})
    assert got is not None
    assert "region" not in got and "territory_id" not in got


def test_a_join_without_pii_still_proves(tmp_path: Path) -> None:
    """Control: the same join with no pii item proves as before."""
    wh = _seed_sales(tmp_path / "join_ok.duckdb")
    _seed_codes(wh)
    relation = ("(SELECT t.territory_id, c.code AS region FROM Sales.Territory t "
                "JOIN Sales.Codes c ON c.code = t.name)")
    onto = Ontology.from_store(items=_territory_items(relation, "region", pii=False))
    run = _run(wh, _Stub(wh), set(_B_GRANTS) | {"Sales.Codes"}, onto=onto)
    got = _by_text(
        run, "2 of 4 Territory.region values (North, South) carry 86.4 pct of order_amount")
    assert_envelope_valid(got.envelope)
    assert _tuples(got.envelope["rows"])[0][:3] == ("North", 900.0, 81.8)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Sales.Territory.name"},
        {"name": "Territory.name"},
        {"name": "territory_name", "table": "Sales.Territory", "column": "NAME "},
    ],
)
def test_every_pii_payload_form_binds(tmp_path: Path, payload: dict[str, str]) -> None:
    """Verify round 4 (LOW): the name-only three-part form parsed as table 'sales'."""
    wh = _seed_sales(tmp_path / "pii_forms.duckdb")
    items = _items()
    items.append({"id": "item_pii_form", "version_id": _B_VERSION, "kind": "pii",
                  "status": "approved", "payload": payload})
    stub = _Stub(wh)
    run = _run(wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=items))
    assert _B_CONCENTRATION not in [p.envelope["text"] for p in run.proven]
    _assert_nothing_leaked(run, stub, "name")


def test_proof_figures_are_bound_to_the_rows(run_b: tuple[ip.InsightRun, _Stub]) -> None:
    """Verify round 4 (LOW): totals and tolerance were not checked against the rows."""
    onto = Ontology.from_store(items=_items())
    got = _by_text(run_b[0], _B_CONCENTRATION)
    assert _tuples(got.rows)[0] == ("North", 900.0, 81.8, 81.8, 1100.0)
    assert got.proof["grouped_total"] == got.proof["raw_total"] == 1100.0
    assert got.proof["tolerance"] == ip.conservation_tolerance(5) == 0.03
    ip.assert_proof_shape(got.proof, onto=onto, sql=got.sql, rows=got.rows)
    for bad, why in (
        ({**got.proof, "raw_total": 5.0, "grouped_total": 5.0}, "grouped_total cell"),
        ({**got.proof, "raw_total": 1.0, "tolerance": 1e9}, "conservation tolerance"),
        ({**got.proof, "tolerance": 0.5}, "conservation tolerance"),
    ):
        with pytest.raises(ip.ProofShapeError, match=why):
            ip.assert_proof_shape(bad, onto=onto, sql=got.sql, rows=got.rows)
    # Rows whose values no longer add back to raw_total, with honest hashes over them.
    forged_rows = [dict(r) for r in got.rows]
    forged_rows[0]["value"] = 1.0
    forged = {**got.proof,
              "result_hash": item_result_hash(forged_rows),
              "reexec_result_hash": item_result_hash(forged_rows),
              "order_hash": ip.order_hash(forged_rows),
              "executions": [{**e, "result_hash": item_result_hash(forged_rows)}
                             for e in got.proof["executions"]]}
    with pytest.raises(ip.ProofShapeError, match="add back to raw_total"):
        ip.assert_proof_shape(forged, onto=onto, sql=got.sql, rows=forged_rows)


def test_top_zero_states_nothing(sales_wh: Path) -> None:
    """Verify round 4 (LOW): top=0 returned one proven insight."""
    stub = _Stub(sales_wh)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()),
               top=0)
    assert run.status == "ok"
    assert run.proven == ()
    assert [p.envelope["text"] for p in run.proven] == []
    assert stub.calls == []
    one = _run(sales_wh, _Stub(sales_wh), set(_B_GRANTS),
               onto=Ontology.from_store(items=_items()), top=1)
    assert len(one.proven) == 1
    assert_envelope_valid(one.proven[0].envelope)


class _Reorder(_Stub):
    """Honest cells, but rows 1 and 2 of the wrapper come back out of ORDER BY order."""

    def __init__(self, warehouse: Path, *, only_run2: bool) -> None:
        super().__init__(warehouse)
        self.only_run2 = only_run2
        self._n = 0

    def __call__(self, sql: str) -> Any:
        got = super().__call__(sql)
        rows = list(got.output["rows"])
        if sql.lstrip().upper().startswith("WITH G AS"):
            self._n += 1
            if (not self.only_run2 or self._n % 2 == 0) and len(rows) > 2:
                rows[1], rows[2] = rows[2], rows[1]
        return SimpleNamespace(ok=True, output={"rows": rows})


@pytest.mark.parametrize("only_run2", [True, False])
def test_rows_out_of_order_never_make_a_headline(demo_wh: Path, only_run2: bool) -> None:
    run = _run(demo_wh, _Reorder(demo_wh, only_run2=only_run2), set(DEMO_TABLES))
    texts = [p.envelope["text"] for p in run.proven]
    # The verifier's false sentences at 56d30ad, each made of real cells.
    for false in (
        "2 of 5 location_code values (WH-B, WH-A) carry 92.7 pct of stock_value_myr",
        "2 of 5 location_code values (WH-B, WH-E) carry 93.4 pct of outbound_kg",
        "2 of 4 lot.category values (PACKAGING, RAW) carry 96.3 pct of outbound_kg",
    ):
        assert false not in texts
    assert _A_HEADLINE not in texts
    codes = {r["code"] for r in run.refused}
    assert ("result_changed_between_runs" if only_run2 else "result_out_of_order") in codes
    for entry in run.refused:
        _assert_refused_envelope(entry)
    # Whatever is still proven had at most two rows to swap, and states the true share.
    for p in run.proven:
        rows = p.envelope["rows"]
        assert len(rows) <= 2
        assert_envelope_valid(p.envelope)


def test_honest_order_states_the_true_share(run_a: tuple[ip.InsightRun, _Stub]) -> None:
    """Oracle: every concentration states the true top-k share of its own rows."""
    run, _ = run_a
    for p in run.proven:
        if p.kind != "concentration":
            continue
        rows = p.envelope["rows"]
        labelled = [r for r in rows if r["label"] is not None]
        total = sum(float(r["value"]) for r in rows)
        true = round(100.0 * sum(float(r["value"]) for r in labelled[: p.k]) / total, 1)
        names = ", ".join(str(r["label"]) for r in labelled[: p.k])
        assert f"({names}) carry {true:.1f} pct" in p.envelope["text"]


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


def _seed_secret(wh: Path) -> None:
    """A second schema whose tables share the granted tables' bare names."""
    _exec(wh, "CREATE SCHEMA IF NOT EXISTS Secret",
          "CREATE TABLE Secret.Territory (territory_id INTEGER, name VARCHAR)",
          "INSERT INTO Secret.Territory VALUES (1,'Alice'),(2,'Bob'),(3,'Carol'),(4,'Dave')",
          "CREATE TABLE Secret.Orders AS SELECT * FROM Sales.Orders")


def _items_on(obj: str, relation: str) -> list[dict[str, Any]]:
    items = _items()
    for it in items:
        if it["kind"] == "object" and it["payload"]["name"] == obj:
            it["payload"]["relation"] = relation
    return items


_SECRET_SENTENCES = (
    "2 of 4 Territory.name values (Alice, Bob) carry 86.4 pct of order_amount",
    "9.1 pct of order_amount lands on rows with no Territory.name value",
)


def test_a_dimension_in_another_schema_with_a_granted_bare_name_never_submits(
    tmp_path: Path,
) -> None:
    """Verify round 5 (HIGH): Secret.Territory passed a grant of Sales.Territory by bare name.

    It was submitted (two wrapper runs reading ``LEFT JOIN Secret.Territory``) and
    came back as two proven L1 sentences over the ungranted schema's values.
    """
    wh = _seed_sales(tmp_path / "xschema_dim.duckdb")
    _seed_secret(wh)
    stub = _Stub(wh)
    run = _run(wh, stub, set(_B_GRANTS),
               onto=Ontology.from_store(items=_items_on("Territory", "Secret.Territory")))
    texts = [p.envelope["text"] for p in run.proven]
    for leaked in _SECRET_SENTENCES:
        assert leaked not in texts
    assert run.proven == ()
    assert _codes(run)["order_amount by Territory"] == "ungranted"
    assert stub.calls == []
    shown = json.dumps([r["envelope"] for r in run.refused])
    for token in ("Alice", "Bob", "86.4", "Secret"):
        assert token not in shown
    for entry in run.refused:
        _assert_refused_envelope(entry)


def test_a_fact_in_another_schema_with_a_granted_bare_name_never_submits(
    tmp_path: Path,
) -> None:
    """Verify round 5 (HIGH): all four submits read Secret.Orders, raw total included."""
    wh = _seed_sales(tmp_path / "xschema_fact.duckdb")
    _seed_secret(wh)
    stub = _Stub(wh)
    run = _run(wh, stub, set(_B_GRANTS),
               onto=Ontology.from_store(items=_items_on("Orders", "Secret.Orders")))
    assert [p.envelope["text"] for p in run.proven] == []
    assert run.proven == ()
    assert _codes(run)["order_amount"] == "ungranted"
    assert stub.calls == []


def test_a_qualified_grant_of_the_other_schema_still_proves(tmp_path: Path) -> None:
    """Control: granting Secret.Territory by its own qualified key proves its rows."""
    wh = _seed_sales(tmp_path / "xschema_ok.duckdb")
    _seed_secret(wh)
    run = _run(wh, _Stub(wh), {"Sales.Orders", "Secret.Territory"},
               onto=Ontology.from_store(items=_items_on("Territory", "Secret.Territory")))
    got = _by_text(run, _SECRET_SENTENCES[0])
    assert got.envelope["badge"] == "L1_GOVERNED_METRIC"
    assert_envelope_valid(got.envelope)
    assert _tuples(got.envelope["rows"])[0][:3] == ("Alice", 900.0, 81.8)


def test_the_statement_check_matches_main_for_a_same_bare_name() -> None:
    """The oracle: main's serve_gap refuses what the bare-name lookup grants."""
    from dms_executor.ontology import table_is_granted

    sql = ("SELECT ROUND(SUM(f.amount), 2) AS raw_total FROM Secret.Orders f")
    assert table_is_granted("Secret.Orders", set(_B_GRANTS)) is True
    assert ip._statement_gap(sql, set(_B_GRANTS)) == "sql_relation_not_granted"
    assert ip._statement_gap(sql.replace("Secret.", "Sales."), set(_B_GRANTS)) is None


def test_every_statement_is_checked_before_any_submit(
    sales_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw total, parts count and wrapper each pass the statement check first:
    a wrapper the check refuses means nothing of that candidate is submitted."""
    real = ip._statement_gap

    def gap(sql: str, granted: set[str]) -> str | None:
        if sql.lstrip().upper().startswith("WITH G AS"):
            return "sql_relation_not_granted"
        return real(sql, granted)

    monkeypatch.setattr(ip, "_statement_gap", gap)
    stub = _Stub(sales_wh)
    run = _run(sales_wh, stub, set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    texts = [p.envelope["text"] for p in run.proven]
    assert _B_CONCENTRATION not in texts and _B_UNKNOWN not in texts
    assert run.proven == ()
    assert _codes(run)["order_amount by Territory.name"] == "ungranted"
    assert stub.calls == []


def test_a_dict_result_that_is_not_ok_proves_nothing(sales_wh: Path) -> None:
    """Verify round 5 (LOW): {'ok': False, 'output': rows} was read as a success."""
    real = _Stub(sales_wh)

    def submit(sql: str) -> dict[str, Any]:
        return {"ok": False, "error": "denied", "output": real(sql).output}

    run = _run(sales_wh, submit, set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    texts = [p.envelope["text"] for p in run.proven]
    assert _B_CONCENTRATION not in texts and _B_UNKNOWN not in texts
    assert run.proven == ()
    assert set(_codes(run).values()) == {"cortex_submit_failed:not_ok"}
    for entry in run.refused:
        _assert_refused_envelope(entry)
    # A dict that is ok still proves.
    ok = _run(sales_wh, lambda sql: {"ok": True, "output": real(sql).output},
              set(_B_GRANTS), onto=Ontology.from_store(items=_items()))
    assert _B_CONCENTRATION in [p.envelope["text"] for p in ok.proven]


def test_insight_id_is_bound_to_measure_label_kind_and_sql(
    run_b: tuple[ip.InsightRun, _Stub],
) -> None:
    """Verify round 5 (LOW): the check only tested insight_id's regex."""
    onto = Ontology.from_store(items=_items())
    a = _by_text(run_b[0], _B_CONCENTRATION)
    b = _by_text(run_b[0], _B_UNKNOWN)
    assert (a.kind, b.kind) == ("concentration", "unknown_bucket")
    assert a.sql == b.sql and a.insight_id != b.insight_id
    for p in (a, b):
        ip.assert_proof_shape(p.proof, onto=onto, sql=p.sql, rows=p.rows, label=p.dimension)
        assert p.insight_id == ip.insight_id_for(p.measure, p.dimension, p.kind,
                                                 p.proof["sql_hash"])
    with pytest.raises(ip.ProofShapeError, match="insight_id is not the hash"):
        ip.assert_proof_shape({**a.proof, "insight_id": b.insight_id},
                              onto=onto, sql=a.sql, rows=a.rows, label=a.dimension)
    with pytest.raises(ip.ProofShapeError, match="insight_id is not the hash"):
        ip.assert_proof_shape(a.proof, onto=onto, sql=a.sql, rows=a.rows,
                              label="Territory.region")
    # Swapping both id and kind yields b's own proof, byte for byte: the two kinds
    # read one statement, so no other proof field tells them apart. Verify round 6
    # (LOW): bound to the insight it is attached to, the swapped proof fails.
    swapped = {**a.proof, "insight_id": b.insight_id, "kind": b.kind}
    assert swapped == b.proof
    with pytest.raises(ip.ProofShapeError, match="insight_id is not the insight"):
        ip.assert_proof_shape(swapped, onto=onto, sql=a.sql, rows=a.rows, label=a.dimension,
                              insight_id=a.insight_id, kind=a.kind)
    with pytest.raises(ip.ProofShapeError, match="kind is not the insight"):
        ip.assert_proof_shape({**a.proof, "kind": b.kind}, kind=a.kind)
    for p in (a, b):
        ip.assert_proof_shape(p.proof, onto=onto, sql=p.sql, rows=p.rows, label=p.dimension,
                              insight_id=p.insight_id, kind=p.kind)
        assert p.envelope["answer_id"] == p.proof["insight_id"] == p.insight_id


@pytest.mark.parametrize(
    "expression",
    [
        "SUM(CASE WHEN CURRENT_DATE > DATE '2020-01-01' THEN f.amount END)",
        "ROUND(SUM(f.amount * (CASE WHEN CURRENT_TIME IS NOT NULL THEN 1 ELSE 1 END)), 2)",
        "ROUND(SUM(f.amount * (CASE WHEN LOCALTIMESTAMP IS NOT NULL THEN 1 ELSE 1 END)), 2)",
        "ROUND(SUM(f.amount * (CASE WHEN get_current_time() IS NOT NULL THEN 1 ELSE 1 END)), 2)",
        "ROUND(SUM(f.amount * (CASE WHEN uuid() IS NOT NULL THEN 1 ELSE 1 END)), 2)",
    ],
)
def test_volatile_expression_refuses(sales_wh: Path, expression: str) -> None:
    stub = _Stub(sales_wh)
    onto = Ontology.from_store(items=_items(expression=expression))
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


def test_a_numeric_label_does_not_hide_an_unbacked_figure() -> None:
    rows = [
        {"label": "73", "value": 6.0, "share_pct": 60.0, "cum_share_pct": 60.0,
         "grouped_total": 10.0},
        {"label": "B", "value": 4.0, "share_pct": 40.0, "cum_share_pct": 100.0,
         "grouped_total": 10.0},
    ]
    assert ip.headline_numbers_backed(
        "73 alone is 73.9 pct of m across 2 x values", rows, {1, 2}) == ["73.9"]
    assert ip.headline_numbers_backed(
        "73 alone is 60.0 pct of m across 2 x values", rows, {1, 2}) == []
    assert ip.headline_numbers_backed(
        "1 of 2 x values (73) carry 60.0 pct of m", rows, {1, 2}) == []


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
    """INS-PROOF-02 wires exactly two importers, the route is mounted only with the flag.

    The executor package exports ``prove_for_space`` (``.importlinter`` lets only
    ``dms_api.wiring`` reach ``dms_executor``, by attribute), and ``app.py`` mounts
    the route only when ``DMS_INSIGHT_PROOF=1``. Nothing else may import it.
    """
    root = Path(__file__).resolve().parents[1]
    hits = []
    for base in ("packages", "apps"):
        for path in (root / base).rglob("*.py"):
            if path.name == "insight_proof.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "insight_proof" in text and re.search(r"^\s*(from|import)\s.*insight_proof",
                                                     text, re.M):
                hits.append(path.relative_to(root).as_posix())
    # INS-PROOF-03: the pin register is the one sibling that builds on this module,
    # and only the package __init__ imports the register (checked below).
    assert sorted(hits) == [
        "packages/executor/dms_executor/__init__.py",
        "packages/executor/dms_executor/insight_register.py",
    ]
    reg = []
    for base in ("packages", "apps"):
        for path in (root / base).rglob("*.py"):
            if path.name == "insight_register.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\s*(from|import)\s.*insight_register", text, re.M):
                reg.append(path.relative_to(root).as_posix())
    assert sorted(reg) == ["packages/executor/dms_executor/__init__.py"]
    app_text = (root / "apps/api/dms_api/app.py").read_text(encoding="utf-8")
    assert "    insight_proof,\n" in app_text
    assert (
        "    if insight_proof.enabled():\n        app.include_router(insight_proof.router)\n"
    ) in app_text


def test_route_stamp_keeps_the_new_stage_only_as_an_addition() -> None:
    assert "insight_proof" in ABSTAIN_STAGES
    assert kept_stamp("abstain:insight_proof") is True
    for stage in ("insights_budget", "served_sql", "envelope", "unspecified"):
        assert stage in ABSTAIN_STAGES
