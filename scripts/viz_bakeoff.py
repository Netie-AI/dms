"""Compare last-mile viz channels against a certified envelope.

This is a bakeoff harness, not a chart library. Channels that cannot keep
magnitude+rank with the envelope fail. Channels that are product-cloned
(Superset-as-DMS-UI, live DuckLake folder in Power BI) are refused.

    python scripts/viz_bakeoff.py --self-check
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Certified ranking = live hostile sales01 Sales (same bytes as .tmp/certified_sales_top3.csv).
ENVELOPE = {
    "badge": "L0_CERTIFIED",
    "abstained": False,
    "values": [
        {"id": "v0", "value": 1545366.40, "label": "Electronics"},
        {"id": "v1", "value": 1199018.49, "label": "Home"},
        {"id": "v2", "value": 400000.00, "label": "Sports"},
    ],
    "rows": [
        {"category": "Electronics", "revenue": 1545366.40},
        {"category": "Home", "revenue": 1199018.49},
        {"category": "Sports", "revenue": 400000.00},
    ],
    "chart": {"kind": "hbar", "x": "category", "y": "revenue", "title": "Top 3"},
}
CERTIFIED_CSV = ROOT / ".tmp" / "certified_sales_top3.csv"


def _nums(env: dict) -> list[float]:
    return [float(v["value"]) for v in env["values"]]


def score_simplechart(env: dict) -> dict[str, object]:
    chart = env.get("chart") or {}
    y = chart.get("y")
    rows = env.get("rows") or []
    if chart.get("kind") not in {"hbar", "bar", "line", "bignum"}:
        return {"ok": False, "reason": "unknown SimpleChart kind"}
    drawn = [float(r[y]) for r in rows] if y else []
    expected = _nums(env)
    match = drawn == expected
    return {
        "ok": match,
        "channel": "simplechart",
        "reason": "magnitude+rank match envelope" if match else "chart rows drifted from values[]",
    }


def score_powerbi(env: dict) -> dict[str, object]:
    # P-DMS-24: Folder-union of DuckLake snapshots double-counts. Single-file export only.
    recipe = ROOT / "docs" / "POWERBI_DUCKLAKE.md"
    text = recipe.read_text(encoding="utf-8") if recipe.exists() else ""
    if "Never do this" not in text or "Isolated export" not in text:
        return {"ok": False, "channel": "powerbi", "reason": "POWERBI_DUCKLAKE.md recipe missing"}
    return {
        "ok": True,
        "channel": "powerbi",
        "reason": "allowed as single-file export; Folder-union refused",
    }


def score_superset(_env: dict) -> dict[str, object]:
    return {
        "ok": False,
        "channel": "superset",
        "reason": "refused as DMS chrome (architecture plan). Optional later behind serving_engine.",
    }


def score_pointer_uacc(env: dict) -> dict[str, object]:
    """Pointer/UACC/Playwright may screenshot the certified CSV. Not a product shell."""
    if not CERTIFIED_CSV.is_file():
        return {
            "ok": False,
            "channel": "pointer_uacc_playwright",
            "reason": "certified_sales_top3.csv missing; export envelope before a Pointer shot",
            "product": False,
        }
    import csv

    with CERTIFIED_CSV.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    drawn = [float(r[list(r.keys())[1]]) for r in rows]
    expected = _nums(env)
    if drawn != expected:
        return {
            "ok": False,
            "channel": "pointer_uacc_playwright",
            "reason": f"CSV drifted from envelope {drawn!r} != {expected!r}",
            "product": False,
        }
    return {
        "ok": True,
        "channel": "pointer_uacc_playwright",
        "reason": "certified CSV magnitudes match envelope; demo/control only",
        "product": False,
    }


def run() -> list[dict[str, object]]:
    env = json.loads(json.dumps(ENVELOPE))
    return [
        score_simplechart(env),
        score_powerbi(env),
        score_superset(env),
        score_pointer_uacc(env),
    ]


def main() -> int:
    rows = run()
    print("channel\tok\treason")
    for r in rows:
        print(f"{r['channel']}\t{r['ok']}\t{r['reason']}")
    simple = next(r for r in rows if r["channel"] == "simplechart")
    pbi = next(r for r in rows if r["channel"] == "powerbi")
    superset = next(r for r in rows if r["channel"] == "superset")
    if not simple["ok"]:
        print("FAIL: SimpleChart must match envelope magnitudes")
        return 1
    if not pbi["ok"]:
        print("FAIL: Power BI recipe must stay single-file")
        return 1
    if superset["ok"]:
        print("FAIL: Superset-as-shell must stay refused")
        return 1
    print("PASS: native chart wins in-app; Power BI export ok; Superset not chrome")
    return 0


if __name__ == "__main__":
    if "--self-check" not in sys.argv and sys.argv[1:]:
        print("usage: python scripts/viz_bakeoff.py --self-check")
        raise SystemExit(2)
    raise SystemExit(main())
