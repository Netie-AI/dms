"""Render what actually happened between bronze, silver and gold.

Why this and not an arrow diagram
---------------------------------
dbt and Airflow draw bronze -> silver -> gold for free, and a diagram that only
shows the arrows tells you a promote ran, not whether it was honest. What nobody
else renders is the row arithmetic:

    source_rows  ==  passed + quarantined      (nothing vanished)
    unmatched    ==  0                         (no join fan-out)

``PromoteReceipt.reconciled`` already asserts exactly that
(``packages/core/dms_core/pipelines.py``), and ``unmatched`` is documented as
"negative means the join produced more rows than it consumed" - fan-out
detection, computed on every promote since it was written.

None of it has ever reached a human: ``apps/ui/src/lib/api.ts`` has zero pipeline
functions. This script is the smallest honest way to see it, and it is
deliberately not a UI - the product direction may be moving to customers whose
data arrives from SQL Server rather than a file upload, in which case "bronze"
means something different and a committed UI surface would be built to throw away.

    python scripts/show_lineage.py --pipeline silver_sales
    python scripts/show_lineage.py --receipt path/to/receipt.json

Exit 1 when a receipt does not reconcile. A promote that lost rows is not a
cosmetic problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "executor"))
sys.path.insert(0, str(ROOT / "packages" / "core"))

G, R, Y, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m"
if sys.platform == "win32" and "WT_SESSION" not in __import__("os").environ:
    G = R = Y = D = B = X = ""


def _bar(passed: int, quarantined: int, width: int = 44) -> str:
    total = passed + quarantined
    if total <= 0:
        return D + "-" * width + X
    p = round(width * passed / total)
    return G + "#" * p + X + R + "-" * (width - p) + X


def render(rc: dict[str, Any]) -> int:
    src = rc.get("source_rows")
    passed = int(rc.get("passed") or 0)
    quar = int(rc.get("quarantined") or 0)
    unmatched = int(rc.get("unmatched") or 0)
    reconciled = bool(rc.get("reconciled"))

    print(f"\n{B}{' -> '.join(rc.get('sources') or ['?'])}  ->  {rc.get('target')}{X}")
    print(f"{D}run {rc.get('run_id')}  lineage={rc.get('lineage')}{X}\n")

    print(f"  bronze  {src if src is not None else '?':>7} rows read")
    print(f"          {_bar(passed, quar)}")
    print(f"  silver  {passed:>7} passed     {quar:>5} quarantined"
          f" -> {rc.get('quarantine_table') or '-'}")

    if rc.get("dedup_key"):
        print(f"{D}          dedup key: {', '.join(rc['dedup_key'])}{X}")

    reasons = rc.get("counts_by_reason") or {}
    if reasons:
        print(f"\n  {B}why rows were held back{X}")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {v:>5}  {k}")
        # This column does not have to sum to `quarantined`, and pretending it
        # does would be its own small lie. Three different kinds of entry live
        # here: per-row contract failures, pipeline-level expectations, and
        # `join_cardinality_change`, which is a row-count delta rather than a
        # verdict on any row at all.
        tally = sum(reasons.values())
        if tally != quar:
            kinds = []
            if "join_cardinality_change" in reasons:
                kinds.append("a join row-count delta, not a per-row verdict")
            if any(k.startswith("expectation_fail") for k in reasons):
                kinds.append("pipeline-level expectations")
            if not kinds:
                kinds.append("a row can fail more than one check")
            print(f"{D}    reasons total {tally}, quarantined rows {quar} - includes "
                  f"{'; '.join(kinds)}{X}")

    print(f"\n  {B}row conservation{X}")
    if src is None:
        print(f"    {Y}UNKNOWN{X}  this receipt predates source_rows;"
              " conservation cannot be checked")
        return 0
    lhs = f"{passed} + {quar}"
    verdict = "OK  " if passed + quar == src else "LOST"
    print(f"    {verdict}  {lhs} = {passed + quar}  vs  {src} read")
    if unmatched == 0:
        print("    OK    unmatched = 0 (no rows dropped or duplicated by a join)")
    elif unmatched > 0:
        print(f"    {R}LOST{X}  unmatched = {unmatched} - an INNER JOIN dropped rows")
    else:
        print(f"    {R}FAN-OUT{X}  unmatched = {unmatched} - the join produced "
              f"{abs(unmatched)} MORE rows than it consumed")
        print(f"{D}          this is the defect class that inflates a total while every{X}")
        print(f"{D}          individual figure still looks plausible{X}")

    print()
    if reconciled:
        print(f"  {G}RECONCILED{X} - every row read is accounted for.\n")
        return 0
    print(f"  {R}NOT RECONCILED{X} - do not promote this to gold.\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pipeline", help="pipeline name under pipelines/ to run and render")
    g.add_argument("--receipt", type=Path, help="a receipt JSON already produced")
    args = ap.parse_args(argv)

    if args.receipt:
        return render(json.loads(args.receipt.read_text(encoding="utf-8")))

    from dms_executor import load_pipeline_by_name, run_promote

    receipt = run_promote(load_pipeline_by_name(args.pipeline))
    return render(receipt.to_dict())


if __name__ == "__main__":
    raise SystemExit(main())
