"""INS-PROOF-01 proof demo: proven insights over the demo warehouse, and their refusals.

  DMS_INSIGHT_PROOF=1 DMS_DEMO_FALLBACK=0 python scripts/demo_insight_proof.py \
      --warehouse "$TMP/insproof" --top 6
  ... --tamper     # the second run returns a changed row; the insight must refuse

``--warehouse DIR`` seeds the thin demo warehouse at ``DIR/warehouse.duckdb``.
The submit here is a LOCAL stand-in for Cortex submit: each statement runs on a
fresh read-only serving connection to that file. Nothing in this demo is a
Cortex run, a ledger entry, or a hosted result. Figures are local claims at the
commit you run it on.

Exit 0 only when every proven envelope passes ``assert_envelope_valid`` and its
proof passes ``assert_proof_shape``; with ``--tamper`` also only when the change
was caught as ``result_changed_between_runs`` and nothing was proven. With the
flag unset it prints ``status: off`` and exits 0: nothing was claimed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("packages/executor", "packages/core", "packages/cortex_client"):
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
    args = ap.parse_args(argv)

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


if __name__ == "__main__":
    raise SystemExit(main())
