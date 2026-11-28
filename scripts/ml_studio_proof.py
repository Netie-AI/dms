"""Four modelling runs on the Harborline demo company.

Planner and the insight paragraph need OpenVault. This script does not call
it. Those two steps are not_run here. The fits use aggregated SQL only.

    DMS_ML_STUDIO=1 python scripts/ml_studio_proof.py --duckdb /tmp/harborline-small.duckdb

Exit 0 when each task finished or is an honest refusal. Exit 2 when the
warehouse is missing. Not PASS.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = (
    {
        "name": "forecast",
        "task": "forecast",
        "target": "revenue",
        "features": [],
        "time_column": "month_start",
        "season": 4,
        "sql": """
            SELECT CAST(date_trunc('month', o.order_date) AS DATE) AS month_start,
                   SUM(ol.line_amount) AS revenue
            FROM harborline.order_lines ol
            JOIN harborline.orders o ON o.order_id = ol.order_id
            GROUP BY 1
            ORDER BY 1
        """,
    },
    {
        "name": "classification",
        "task": "classification",
        "target": "big_ticket",
        "features": ["line_count", "qty", "unit_price"],
        "time_column": None,
        "season": 12,
        "sql": """
            SELECT o.order_id,
                   COUNT(*) AS line_count,
                   SUM(ol.fulfilled_qty) AS qty,
                   AVG(ol.unit_price) AS unit_price,
                   CASE WHEN SUM(ol.line_amount) >= 500 THEN 1 ELSE 0 END AS big_ticket
            FROM harborline.orders o
            JOIN harborline.order_lines ol ON ol.order_id = o.order_id
            GROUP BY o.order_id
        """,
    },
    {
        "name": "regression",
        "task": "regression",
        "target": "revenue",
        "features": ["line_count", "qty", "unit_price"],
        "time_column": None,
        "season": 12,
        "sql": """
            SELECT o.order_id,
                   COUNT(*) AS line_count,
                   SUM(ol.fulfilled_qty) AS qty,
                   AVG(ol.unit_price) AS unit_price,
                   SUM(ol.line_amount) AS revenue
            FROM harborline.orders o
            JOIN harborline.order_lines ol ON ol.order_id = o.order_id
            GROUP BY o.order_id
        """,
    },
    {
        "name": "clustering",
        "task": "clustering",
        "target": None,
        "features": ["line_count", "qty", "revenue"],
        "time_column": None,
        "season": 12,
        "sql": """
            SELECT o.order_id,
                   COUNT(*) AS line_count,
                   SUM(ol.fulfilled_qty) AS qty,
                   SUM(ol.line_amount) AS revenue
            FROM harborline.orders o
            JOIN harborline.order_lines ol ON ol.order_id = o.order_id
            GROUP BY o.order_id
        """,
    },
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harborline modelling proof")
    parser.add_argument("--duckdb", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("DMS_ML_STUDIO") != "1":
        print("not_run flag_off", flush=True)
        return 2
    db = Path(args.duckdb) if args.duckdb else Path("/tmp/harborline-small.duckdb")
    out = Path(args.out) if args.out else Path("/tmp/ml-studio-proof")
    out.mkdir(parents=True, exist_ok=True)
    if args.seed and not db.is_file():
        db.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/demo/seed_company.py"),
                "--scale",
                "small",
                "--duckdb",
                str(db),
            ],
            check=True,
        )
    if not db.is_file():
        report = {
            "planner": "not_run",
            "insights": "not_run",
            "reason": "warehouse_missing",
            "tasks": {job["name"]: "not_run" for job in JOBS},
        }
        (out / "proof.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("not_run warehouse_missing", flush=True)
        return 2
    os.environ["DMS_WAREHOUSE_DB"] = str(db)
    from dms_executor.ml_fit import fit_frame
    from dms_executor.ml_studio import check_model_sql
    from dms_executor.sql_loop import run_readonly

    summary: dict[str, object] = {
        "planner": "not_run",
        "insights": "not_run",
        "warehouse": str(db),
        "tasks": {},
    }
    tasks: dict[str, object] = {}
    for job in JOBS:
        name = str(job["name"])
        why = check_model_sql(str(job["sql"]), {"harborline.orders", "harborline.order_lines"})
        if why:
            tasks[name] = {"status": "refused", "reason": why}
            continue
        rows, err = run_readonly(str(job["sql"]), db)
        if err or not rows:
            tasks[name] = {"status": "failed" if err else "refused", "reason": err or "too_small"}
            continue
        columns = list(rows[0].keys())
        frame = [[row.get(column) for column in columns] for row in rows]
        card = fit_frame(
            task=str(job["task"]),
            columns=columns,
            rows=frame,
            target=job["target"],  # type: ignore[arg-type]
            features=list(job["features"]),  # type: ignore[arg-type]
            time_column=job["time_column"],  # type: ignore[arg-type]
            season=int(job["season"]),
        )
        card["insights_status"] = "not_run"
        card["insights"] = None
        _write_charts(out, name, card.get("charts") or [])
        card.pop("charts", None)
        tasks[name] = {
            "status": card.get("status"),
            "reason": card.get("reason"),
            "proven_solution": card.get("proven_solution"),
            "winner": card.get("winner"),
            "result_metric": card.get("result_metric"),
            "baseline_metric": card.get("baseline_metric"),
            "row_count": card.get("row_count"),
            "models": card.get("models"),
            "split": card.get("split"),
        }
        payload = json.dumps(card, indent=2, default=str) + "\n"
        (out / f"{name}.json").write_text(payload, encoding="utf-8")
        print(
            f"{name} status={card.get('status')} proven={card.get('proven_solution')} "
            f"winner={card.get('winner')} rows={card.get('row_count')}",
            flush=True,
        )
    summary["tasks"] = tasks
    payload = json.dumps(summary, indent=2, default=str) + "\n"
    (out / "proof.json").write_text(payload, encoding="utf-8")
    print("planner=not_run insights=not_run", flush=True)
    return 0


def _write_charts(out: Path, name: str, charts: list[dict[str, str]]) -> None:
    for chart in charts:
        raw = chart.get("png_b64") or ""
        if not raw:
            continue
        (out / f"{name}-{chart.get('name') or 'chart'}.png").write_bytes(base64.b64decode(raw))


if __name__ == "__main__":
    raise SystemExit(main())
