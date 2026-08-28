"""Grain-guarded Space insights on the ask path.

Genie-class "what stands out" without a model. Numbers come from
``scripts/ontology.py`` compile + a conservation check against the ungrouped
total. duckdb stays in this package. Constructor must not import this.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.envelope import build_answer_envelope
from dms_executor.warehouse_identity import serving_warehouse_path

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ontology import CompiledQuery, Ontology, Refusal  # noqa: E402

_INSIGHTS_ASK = re.compile(
    r"\b(?:insights?|brief|what stands out|key findings|overview)\b",
    re.I,
)

def _tol(n: int) -> float:
    # Grouped SUM is rounded per group; raw is rounded once.
    return 0.005 * n + 0.005 + 1e-6


def is_insights_ask(question: str) -> bool:
    return bool(_INSIGHTS_ASK.search(question or ""))


def maybe_grain_insights(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any] | None:
    if not is_insights_ask(question):
        return None
    return grain_insights(space_id=space_id, session_id=session_id, warehouse=warehouse)


def grain_insights(
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any]:
    db = Path(warehouse) if warehouse is not None else serving_warehouse_path()
    if not db.is_file():
        return _abstain(
            "No serving warehouse is on disk, so I cannot compile insights.",
            space_id=space_id,
            session_id=session_id,
        )

    con = duckdb.connect(str(db), read_only=True)
    try:
        onto, dim = _ontology_for_inventory(con)
        if onto is None or dim is None:
            return _abstain(
                "inventory is missing a usable grain or grouping column, "
                "so I cannot compile insights.",
                space_id=space_id,
                session_id=session_id,
            )
        violations = onto.verify(con)
        if violations:
            named = "; ".join(f"{v.check} {v.subject}" for v in violations[:3])
            return _abstain(
                f"The ontology did not verify against this warehouse ({named}). "
                "I will not report a figure from an unverified grain.",
                space_id=space_id,
                session_id=session_id,
            )
        got = onto.compile("stock_value_myr", group_by=[("lot", dim)])
        if isinstance(got, Refusal):
            return _abstain(
                f"Cannot compile stock value by {dim}: {got.reason} - {got.detail}",
                space_id=space_id,
                session_id=session_id,
            )
        assert isinstance(got, CompiledQuery)
        raw_row = con.execute(
            "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) FROM inventory"
        ).fetchone()
        if raw_row is None:
            # No ungrouped total means nothing to conserve the roll-up against,
            # and an unconserved roll-up is exactly what this path exists to refuse.
            return _abstain(
                "The ungrouped stock-value total came back empty, so a grouped "
                "roll-up has nothing to conserve against.",
                space_id=space_id,
                session_id=session_id,
            )
        raw = float(raw_row[0] or 0)
        fetched = con.execute(got.sql).fetchall()
        rows: list[dict[str, Any]] = [
            {dim: str(r[0]), "stock_value_myr": float(r[1])}
            for r in fetched
            if r[1] is not None
        ]
        total = sum(float(r["stock_value_myr"]) for r in rows)
        if not rows or abs(total - raw) > _tol(len(rows)):
            return _abstain(
                "A grouped stock-value roll-up did not conserve to the ungrouped "
                f"total (grouped {total:.2f} vs raw {raw:.2f}). Nothing is reported.",
                space_id=space_id,
                session_id=session_id,
            )
        rows.sort(key=lambda r: float(r["stock_value_myr"]), reverse=True)
        top = rows[0]
        share = 100.0 * top["stock_value_myr"] / raw if raw else 0.0
        top_label = str(top[dim])
        n_groups = float(len(rows))
        bullets = [
            (
                f"{top_label} is {share:.1f}% of carrying value "
                f"({top['stock_value_myr']:,.2f} of {raw:,.2f} MYR)"
            ),
            f"Grouped by {dim}; total conserved to the lot-level sum "
            f"({int(n_groups)} groups).",
        ]
        text = (
            "Grain-guarded insights over this Space warehouse. Every figure was "
            "compiled at declared grain (stock lot) and conserved to the ungrouped "
            "total. No model produced a number.\n\nInsights:\n"
            + "\n".join(f"- {b}" for b in bullets)
        )
        values = [
            {"id": "v0", "value": float(top["stock_value_myr"]), "label": "top_group_myr"},
            {"id": "v1", "value": float(raw), "label": "stock_value_myr"},
            {"id": "v2", "value": float(round(share, 1)), "label": "top_share_pct"},
            {"id": "v3", "value": n_groups, "label": "n_groups"},
        ]
        return build_answer_envelope(
            answer_id="ans_insights_stock",
            text=text,
            badge="L1_GOVERNED_METRIC",
            abstained=False,
            values=values,
            sql_used=got.sql,
            assumptions=[
                f"grain compiler: stock_value_myr by lot.{dim}",
                "conservation vs ungrouped SUM(quantity_kg * unit_cost_myr)",
            ],
            space_id=space_id,
            session_id=session_id,
            rows=rows,
            chart={
                "kind": "hbar",
                "x": dim,
                "y": "stock_value_myr",
                "title": f"Carrying value by {dim}",
            },
            route="governed_metric",
        )
    finally:
        con.close()


def _ontology_for_inventory(con: Any) -> tuple[Any, str | None]:
    """Lot grain from columns that exist.

    Cortex demo has category+storage_bin; the thin seed does not.
    """
    try:
        cols = {str(r[0]) for r in con.execute("DESCRIBE inventory").fetchall()}
    except Exception:  # noqa: BLE001
        return None, None
    if "quantity_kg" not in cols or "unit_cost_myr" not in cols:
        return None, None
    key = [c for c in ("sku", "location_id", "storage_bin", "supplier_id") if c in cols]
    if not key:
        return None, None
    dim = "category" if "category" in cols else ("location_id" if "location_id" in cols else None)
    if dim is None:
        return None, None
    onto = Ontology()
    onto.add_object("lot", "inventory", key)
    onto.add_measure(
        "stock_value_myr",
        "lot",
        "ROUND(SUM(f.quantity_kg * f.unit_cost_myr), 2)",
        description="carrying value, one contribution per stock lot",
    )
    return onto, dim


def _abstain(
    text: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    return build_answer_envelope(
        answer_id="ans_insights_abstain",
        text=text,
        badge="ABSTAIN",
        abstained=True,
        values=[],
        sql_used=None,
        assumptions=["grain insights refused"],
        space_id=space_id,
        session_id=session_id,
        rows=[],
        route="abstain",
    )
