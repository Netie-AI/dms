"""Small Harborline rebuild. The gold SQL must still execute.

Gold numbers are the full-scale file. This test does not compare them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "demo" / "seed_company.py"
GOLD = ROOT / "tests" / "demo" / "gold.json"
CATALOG = ROOT / "docs" / "demo" / "harborline_catalog.json"


def test_small_scale_gold_sql_runs(tmp_path: Path) -> None:
    db = tmp_path / "harborline-small.duckdb"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scale",
            "small",
            "--seed",
            "20261128",
            "--duckdb",
            str(db),
            "--check-gold",
            str(GOLD),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[-4000:] + proc.stdout[-2000:]
    assert "tables=50" in proc.stdout
    assert "gold_sql_ok=12" in proc.stdout


def test_catalog_retrieval_names_the_business_tables() -> None:
    """Descriptions rank the right tables once the prompt is allowed to render.

    The ranker appends table descriptions after its fit check. At the 480
    token default that extra text is dropped and the prompt becomes an empty
    SCHEMA. 4000 is enough for this catalog.
    """
    from dms_executor.schema_context import build_schema_context

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    late = build_schema_context(
        "which supplier has a late delivery problem",
        catalog,
        max_tokens=4000,
    )
    stock = build_schema_context(
        "which SKU had a stockout",
        catalog,
        max_tokens=4000,
    )
    region = build_schema_context(
        "how did revenue break down by region",
        catalog,
        max_tokens=4000,
    )
    assert "inbound_receipts" in late.prompt
    assert "inventory_snapshots" in stock.prompt
    assert "regions" in region.prompt
    assert "order_lines" in region.prompt
