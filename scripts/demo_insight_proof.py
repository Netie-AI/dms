"""INS-PROOF-01 proof demo: proven insights over the demo warehouse, and their refusals.

  DMS_INSIGHT_PROOF=1 DMS_DEMO_FALLBACK=0 python scripts/demo_insight_proof.py \
      --warehouse "$TMP/insproof" --top 6
  ... --tamper     # the second run returns a changed row; the insight must refuse
  ... --via-api    # INS-PROOF-02: POST /v1/insight-proof/run through create_app()
  ... --via-api --ledger-down   # the ledger append raises; nothing is served

``--warehouse DIR`` seeds the thin demo warehouse at ``DIR/warehouse.duckdb``.
The submit here is a LOCAL stand-in for Cortex submit: each statement runs on a
fresh read-only serving connection to that file. Nothing in this demo is a
Cortex run, a ledger entry, or a hosted result. Figures are local claims at the
commit you run it on.

Exit 0 only when every proven envelope passes ``assert_envelope_valid`` and its
proof passes ``assert_proof_shape``; with ``--tamper`` also only when the change
was caught as ``result_changed_between_runs`` and nothing was proven. With the
flag unset it prints ``status: off`` and exits 0: nothing was claimed.

``--via-api`` (INS-PROOF-02) drives ``create_app()`` through ``TestClient``. The
Executor's own submit path (serve_gap, a test-signed manifest, ``Cortex.submit``)
reaches an IN-PROCESS Cortex stub that executes on the same tmp warehouse and
records every statement and every ledger append; the F5 gate is stubbed to
allow. It prints each envelope's badge, text, rows and audit_id, both execution
hashes, the ledger payload keys and the audit-store row. Still no Cortex run, no
real ledger entry and no hosted result is claimed. With the flag unset it
prints the 404 and the relative OpenAPI check instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("packages/executor", "packages/core", "packages/cortex_client",
             "packages/ledger", "apps/api"):
    sys.path.insert(0, str(ROOT / _pkg))


class LocalSubmit:
    """Runs each statement on its own serving connection. ``tamper`` alters run 2."""

    def __init__(self, warehouse: Path, *, tamper: bool = False) -> None:
        self.warehouse = warehouse
        self.tamper = tamper
        self.calls = 0
        self._wrapper_runs = 0

    def __call__(self, sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_serving

        self.calls += 1
        con = connect_serving(self.warehouse)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        if sql.lstrip().upper().startswith("WITH G AS"):
            self._wrapper_runs += 1
            if self.tamper and self._wrapper_runs % 2 == 0 and rows:
                rows[0] = {**rows[0], "value": float(rows[0]["value"]) + 1.0}
        return SimpleNamespace(ok=True, output={"rows": rows})


def _no_watermarks(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warehouse", type=Path, required=True,
                    help="directory to seed the demo warehouse in")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--tamper", action="store_true",
                    help="the second run returns a changed row")
    ap.add_argument("--via-api", action="store_true",
                    help="POST /v1/insight-proof/run through create_app() (INS-PROOF-02)")
    ap.add_argument("--ledger-down", action="store_true",
                    help="with --via-api: the ledger append raises")
    args = ap.parse_args(argv)
    if args.via_api:
        return _via_api(args)

    from dms_executor import bronze
    from dms_executor import insight_proof as ip
    from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid

    if not ip.insight_proof_enabled():
        print("status: off")
        return 0

    args.warehouse.mkdir(parents=True, exist_ok=True)
    db = ensure_demo_warehouse(args.warehouse / "warehouse.duckdb")
    submit = LocalSubmit(db, tamper=args.tamper)
    run = ip.prove_insights(
        db,
        submit=submit,
        granted=set(DEMO_TABLES),
        space_id="space_demo_insight_proof",
        session_id="sess_demo_insight_proof",
        top=args.top,
        watermarks=bronze.lookup_ingest_watermarks,
    )
    print(f"status: {run.status}  proven: {len(run.proven)}  refused: {len(run.refused)}  "
          f"submits: {submit.calls}  (local stand-in for Cortex submit)")
    print(f"scope: {run.scope}")
    ok = True
    for p in run.proven:
        env, proof = p.envelope, p.proof
        print(f"\n[{p.kind}] {env['text']}")
        print(f"  badge {env['badge']}  route {env['served_route']}  "
              f"source {proof['definition']['source']}")
        print("  " + " | ".join(ip.PROOF_COLUMNS))
        for r in env["rows"]:
            print("  " + " | ".join(str(r[c]) for c in ip.PROOF_COLUMNS))
        print(f"  conserves: grouped {proof['grouped_total']:,.2f} == raw "
              f"{proof['raw_total']:,.2f} (tol {proof['tolerance']:.2f})")
        print(f"  sql_hash {proof['sql_hash']}")
        same = "==" if proof["result_hash"] == proof["reexec_result_hash"] else "!="
        print(f"  result_hash {proof['result_hash']} {same} reexec_result_hash "
              f"{proof['reexec_result_hash']}")
        print(f"  order_hash {proof['order_hash']}  negative_parts {proof['negative_parts']}")
        print(f"  watermarks {proof['watermarks']}  ledger {proof['ledger']}")
        try:
            assert_envelope_valid(env)
            ip.assert_proof_shape(proof, sql=p.sql, rows=p.rows)
        except (AssertionError, ip.ProofShapeError) as exc:
            ok = False
            print(f"  FAIL {type(exc).__name__}: {exc}")

    print("\nrefusals:")
    for r in run.refused:
        ev = r.get("evidence") or {}
        if r["code"] == "result_changed_between_runs":
            print(f"  REFUSED result_changed_between_runs {r['candidate']}: "
                  f"result_hash {ev.get('result_hash')} != reexec_result_hash "
                  f"{ev.get('reexec_result_hash')}")
            continue
        print(f"  refused {r['candidate']} {r['code']}")
        if r["envelope"].get("abstained") is not True:
            ok = False
            print("  FAIL refusal envelope is not abstained")

    if args.tamper:
        caught = any(r["code"] == "result_changed_between_runs" for r in run.refused)
        if not caught or run.proven:
            print("\nFAIL --tamper was not caught as result_changed_between_runs")
            return 1
        print("\nPASS the changed second run was refused; nothing was proven.")
        return 0 if ok else 1
    if not run.proven:
        print("\nFAIL nothing was proven")
        return 1
    if not ok:
        print("\nFAIL a proven envelope or proof did not pass its check")
        return 1
    print("\nPASS every proven envelope passed assert_envelope_valid and assert_proof_shape.")
    return 0


# --------------------------------------------------------------------------
# INS-PROOF-02: through the API
# --------------------------------------------------------------------------

_DEMO_KEY = "ov_demo_cortex_stub_key_000"


class _StubCortex:
    """In-process Cortex stand-in: submit runs on the tmp warehouse; ledger records."""

    base_url = "http://cortex.stub"
    api_key = _DEMO_KEY

    def __init__(self, warehouse: Path, *, ledger_down: bool) -> None:
        self.warehouse = warehouse
        self.ledger_down = ledger_down
        self.submits: list[str] = []
        self.ledger: list[Any] = []

    def submit(self, req: Any) -> Any:
        import json
        from decimal import Decimal

        from cortex_contract.execution import QueryResult
        from dms_executor.demo_warehouse import connect_serving

        if (req.plan or {}).get("kind") == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run-bind")
        sql = str(req.body["sql"])
        self.submits.append(sql)
        con = connect_serving(self.warehouse)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        rows = json.loads(json.dumps(
            rows, default=lambda o: float(o) if isinstance(o, Decimal) else str(o)
        ))
        return QueryResult(ok=True, status="ok", run_id="run-sql", output={"rows": rows})

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        self.ledger.append(req)
        if self.ledger_down:
            raise RuntimeError("ledger unreachable (demo --ledger-down)")
        n = len(self.ledger)
        return LedgerAppendResponse(entry_id=f"led_demo_{n:04d}", hash=f"{n:064x}", seq=n)


def _test_minter() -> Any:
    from cortex_contract.execution import Manifest
    from dms_executor.manifest import ManifestMinter

    m = ManifestMinter()
    m.mint_manifest = lambda acl: Manifest(  # type: ignore[method-assign]
        session_id=acl.session_id, org_id=acl.org_id, space_id=acl.space_id,
        pool_id=acl.pool_id, issuer_key_id="demo-kid", allowed_paths=list(acl.allowed_paths),
        row_predicates=dict(acl.row_predicates), issued_at="2026-10-30T00:00:00+00:00",
        expires_at="2026-10-30T01:00:00+00:00", signature="ZGVtbw",
    )
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    return m


def _openapi_relative() -> bool:
    import copy
    import json
    import os

    from dms_api.app import create_app

    prior = os.environ.get("DMS_INSIGHT_PROOF")
    os.environ["DMS_INSIGHT_PROOF"] = "1"
    try:
        on = create_app().openapi()
    finally:
        if prior is None:
            os.environ.pop("DMS_INSIGHT_PROOF", None)
        else:
            os.environ["DMS_INSIGHT_PROOF"] = prior
    off = create_app().openapi()
    stripped = copy.deepcopy(on)
    for path in [p for p in stripped["paths"] if p.startswith("/v1/insight-proof")]:
        del stripped["paths"][path]
    stripped["components"]["schemas"].pop("InsightProofRunIn", None)
    return json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def _via_api(args: argparse.Namespace) -> int:
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from dms_api.routes import insight_proof as route
    from dms_core.control_plane.ask_audit import InMemoryAskAuditStore
    from dms_executor import Executor, bronze
    from dms_executor import insight_proof as ip
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid
    from fastapi.testclient import TestClient

    if not ip.insight_proof_enabled():
        res = TestClient(create_app()).post("/v1/insight-proof/run", json={})
        print(f"POST /v1/insight-proof/run -> {res.status_code} {res.json()}")
        same = _openapi_relative()
        print(f"openapi identical minus new paths: {same}")
        return 0 if res.status_code == 404 and same else 1

    args.warehouse.mkdir(parents=True, exist_ok=True)
    db = ensure_demo_warehouse(args.warehouse / "warehouse.duckdb")
    cortex = _StubCortex(db, ledger_down=args.ledger_down)
    route.compliance_gate = lambda **kw: ComplianceDecision(  # type: ignore[assignment]
        allowed=True, reason="ok", action=str(kw.get("action"))
    )
    bronze.lookup_ingest_watermarks = _no_watermarks  # type: ignore[assignment]
    app = create_app()
    app.state.cortex = cortex
    app.state.ask_service = Executor(cortex=cortex, minter=_test_minter(),  # type: ignore[arg-type]
                                     warehouse_path=db)
    audit = InMemoryAskAuditStore()
    app.state.ask_audit_store = audit
    res = TestClient(app).post("/v1/insight-proof/run", json={"top": args.top})
    print(f"POST /v1/insight-proof/run -> {res.status_code}  (in-process Cortex stub, "
          f"F5 stubbed to allow; no Cortex run is claimed)")
    if res.status_code != 200:
        print(res.text[:400])
        return 1
    body = res.json()
    recs = audit.list_between(since=None, until=None, limit=10_000)
    by_q = {r.question: r for r in recs}
    print(f"counts {body['counts']}  submits {len(cortex.submits)}  "
          f"ledger appends {len(cortex.ledger)}  audit rows {len(recs)}  "
          f"orphan_ledger_entries {body['orphan_ledger_entries']}")
    ok = _DEMO_KEY not in res.text
    for item in body["insights"]:
        env, proof = item["envelope"], item["proof"]
        print(f"\n[{proof['kind']}] {env['text']}")
        print(f"  badge {env['badge']}  audit_id {env['audit_id']}  "
              f"source {proof['definition']['source']}")
        print("  " + " | ".join(ip.PROOF_COLUMNS))
        for r in env["rows"]:
            print("  " + " | ".join(str(r[c]) for c in ip.PROOF_COLUMNS))
        e1, e2 = proof["executions"]
        match = "MATCH" if e1["result_hash"] == e2["result_hash"] else "DIFFER"
        print(f"  execution 1 {e1['result_hash']}")
        print(f"  execution 2 {e2['result_hash']}  {match}  "
              f"(wrapper submitted {cortex.submits.count(env['sql_used'])}x)")
        print(f"  proof.ledger {proof['ledger']}")
        led = next(q for q in cortex.ledger if q.payload["insight_id"] == proof["insight_id"])
        keys = sorted(led.payload)
        print(f"  ledger payload keys {keys}")
        print(f"  ledger payload has sql/rows: {'sql' in keys or 'rows' in keys}")
        rec = by_q.get("insight:" + proof["insight_id"])
        if rec is None:
            ok = False
            print("  FAIL no audit row")
        else:
            print(f"  audit executed_sql == sql_used: {rec.executed_sql == env['sql_used']}  "
                  f"tables_read {list(rec.tables_read)}  row_count {rec.row_count}  "
                  f"cortex_entry_id {rec.cortex_entry_id}  seq {rec.ledger_seq}")
        try:
            assert_envelope_valid(env)
            ip.assert_proof_shape(proof)
        except (AssertionError, ip.ProofShapeError) as exc:
            ok = False
            print(f"  FAIL {type(exc).__name__}: {exc}")
    print("\nrefused:")
    for r in body["refused"]:
        print(f"  {r['candidate']}  {r['code']}")
    if args.ledger_down:
        failed = [r for r in body["refused"] if r["code"] == "ledger_append_failed"]
        all_abstain = all(r.badge == "abstain" for r in recs)
        print(f"\nproven {len(body['insights'])}, every proven candidate REFUSED "
              f"ledger_append_failed ({len(failed)}); audit rows all abstain: {all_abstain}")
        return 0 if not body["insights"] and failed and all_abstain and ok else 1
    if not body["insights"] or not ok:
        print("\nFAIL nothing was served, or a check failed")
        return 1
    print("\nPASS every served envelope passed assert_envelope_valid and assert_proof_shape; "
          "the stub key is not in the response.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
