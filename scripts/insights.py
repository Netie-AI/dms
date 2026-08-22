"""Deterministic insights over a verified ontology: every figure compiled, every total conserved.

Why this exists
---------------
"Retrieve insights" is where most data products quietly hand the wheel to a
model: a prompt, a table, and a paragraph of prose with numbers in it that
nobody can point at. That is the F26 shape - a confident figure with no executed
query behind it - and E9 exists in this repo precisely to refuse it.

This does the other thing. An insight here is a TEMPLATE over a declared measure
and a verified dimension: concentration (the top few carry most of the total),
dominance (one group is most of it), an unknown bucket (a material share of the
measure lands on rows the dimension cannot label), or a refusal (the ontology
knows the question is ambiguous and says so instead of guessing). Every figure
comes out of the ontology compiler, which cannot fan out; every grouped total is
checked against the raw ungrouped total computed with no join at all; and every
insight carries the SQL that produced it. There is no model in the loop. A
model may later *phrase* these; it never *produces* them.

Measures are declared, not derived
----------------------------------
A sum over a numeric column is not a metric. Someone has to say what it means -
SubTotal excludes tax and freight, LineTotal is after discount, SalesAmount in
the warehouse is already net - and at what grain. The registry below is that
declaration for AdventureWorks, written by a person and labelled with what each
measure is NOT, because the caveat is the part a reader skips and the part that
makes a number wrong to quote.

Dimensions, by contrast, are discovered: any object reachable from the measure's
grain over verified many-to-one links, and any low-cardinality text attribute on
it. That is exactly the set the compiler can group by without inflating.

  python scripts/insights.py --database AdventureWorks2025
  python scripts/insights.py --database AdventureWorks2025 --json out.json --top 12

Exit 0 when every reported insight conserved. An insight whose grouped total
does not equal the raw total is not reported quietly - it is the defect this
whole layer exists to make unreachable, so its presence fails the run.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "lake" / "_reports" / "extract_manifest.json"
sys.path.insert(0, str(ROOT / "scripts"))

ABS_TOL = 0.02


# --------------------------------------------------------------------------
# the human-declared part
# --------------------------------------------------------------------------

# name -> (grain object, SQL expression over alias f, unit, what it is, what it is NOT)
MEASURES: dict[str, list[dict[str, str]]] = {
    "AdventureWorks2025": [
        {"name": "sales_subtotal", "grain": "Sales.SalesOrderHeader",
         "expr": 'ROUND(SUM(f."SubTotal"), 2)', "unit": "USD",
         "is": "order subtotal before tax and freight, one contribution per order",
         "is_not": "not revenue recognised, not net of returns, excludes tax and freight"},
        {"name": "freight_cost", "grain": "Sales.SalesOrderHeader",
         "expr": 'ROUND(SUM(f."Freight"), 2)', "unit": "USD",
         "is": "freight charged on orders",
         "is_not": "not the carrier's cost to us - this is what was billed"},
        {"name": "line_total", "grain": "Sales.SalesOrderDetail",
         "expr": 'ROUND(SUM(f."LineTotal"), 2)', "unit": "USD",
         "is": "extended line value after line discount, one contribution per order line",
         "is_not": "not net of order-level discounts, tax or freight"},
        {"name": "units_ordered", "grain": "Sales.SalesOrderDetail",
         "expr": 'SUM(f."OrderQty")', "unit": "units",
         "is": "quantity ordered, one contribution per order line",
         "is_not": "not shipped or delivered quantity"},
        {"name": "purchase_total_due", "grain": "Purchasing.PurchaseOrderHeader",
         "expr": 'ROUND(SUM(f."TotalDue"), 2)', "unit": "USD",
         "is": "purchase order total due to vendors",
         "is_not": "not paid, not received - ordered"},
    ],
    "AdventureWorksDW2025": [
        {"name": "internet_sales", "grain": "dbo.FactInternetSales",
         "expr": 'ROUND(SUM(f."SalesAmount"), 2)', "unit": "USD",
         "is": "internet channel sales amount, one contribution per fact row",
         "is_not": "excludes reseller channel; already net as loaded by the warehouse"},
        {"name": "reseller_sales", "grain": "dbo.FactResellerSales",
         "expr": 'ROUND(SUM(f."SalesAmount"), 2)', "unit": "USD",
         "is": "reseller channel sales amount",
         "is_not": "excludes internet channel"},
    ],
    "AdventureWorksLT2022": [
        {"name": "line_total", "grain": "SalesLT.SalesOrderDetail",
         "expr": 'ROUND(SUM(f."LineTotal"), 2)', "unit": "USD",
         "is": "extended line value after discount",
         "is_not": "not net of tax or freight"},
    ],
}


@dataclass
class Insight:
    id: str
    kind: str
    headline: str
    measure: str
    unit: str
    grain: str
    dimension: str
    rows: list[list[Any]]
    total: float
    n_groups: int
    top_share: float
    surprise: float
    sql: str
    notes: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    conserves: bool = True


def _label_attrs(con: Any, rel: str, *, lo: int = 2, hi: int = 60) -> list[str]:
    """Columns a business actually segments by - and nothing that merely looks like one.

    The first run of this miner reported that 95 pct of sales "lands on rows
    Person.Suffix cannot label" and grouped units by a 4 KB XML catalogue
    description. Both passed a distinct-count test; neither is a dimension. So
    a label column must be short (a segment name, not a document), and
    populated on more than half the rows (a field half the rows leave empty is an
    annotation, not a way the business is organised). The unknown-bucket
    insight then means what it says: a dimension people use is missing on a
    material share of the measure.
    """
    out = []
    for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall():
        if str(typ).upper() != "VARCHAR":
            continue
        d, nn, total, maxlen = con.execute(
            f'SELECT COUNT(DISTINCT "{name}"), COUNT("{name}"), COUNT(*), '
            f'MAX(LENGTH("{name}")) FROM {rel}'
        ).fetchone()
        if not (lo <= int(d) <= hi):
            continue
        if int(total) == 0 or int(nn) * 2 <= int(total):
            continue
        if maxlen is not None and int(maxlen) > 64:
            continue
        out.append(str(name))
    return out


def _reachable(onto: Any, grain: str, *, max_hops: int = 3) -> dict[str, list[str]]:
    """Objects reachable from the grain over verified many-to-one links only.

    Returns object -> list of link names forming the path. One path per object:
    if two distinct paths reach the same object at the same depth the object is
    dropped here, because the compiler will refuse it as ambiguous and an
    insight built on a guessed path is not an insight.
    """
    paths: dict[str, list[str]] = {grain: []}
    frontier = [grain]
    ambiguous: set[str] = set()
    for _ in range(max_hops):
        nxt: list[str] = []
        seen_this_level: dict[str, list[str]] = {}
        for obj in frontier:
            for link in onto.links.values():
                if link.from_object != obj or link.cardinality != "many_to_one":
                    continue
                if link.to_object in paths:
                    continue
                cand = paths[obj] + [link.name]
                if link.to_object in seen_this_level:
                    ambiguous.add(link.to_object)
                else:
                    seen_this_level[link.to_object] = cand
        for obj, path in seen_this_level.items():
            if obj in ambiguous:
                continue
            paths[obj] = path
            nxt.append(obj)
        frontier = nxt
        if not frontier:
            break
    paths.pop(grain, None)
    return paths


def mine(con: Any, entry: dict[str, Any], *, top: int) -> dict[str, Any]:
    from ontology import CompiledQuery, Refusal, from_manifest

    db = str(entry["database"])
    onto = from_manifest(entry, lake_root=ROOT)
    violations = onto.verify(con)
    if violations:
        return {"database": db, "error": f"{len(violations)} verify violations: "
                + "; ".join(f"{v.check} {v.subject}" for v in violations[:3])}
    declared = MEASURES.get(db, [])
    for m in declared:
        if m["grain"] in onto.objects:
            onto.add_measure(m["name"], m["grain"], m["expr"], description=m["is"])
    onto.verified = True  # add_measure does not invalidate link verdicts

    insights: list[Insight] = []
    refusals: list[dict[str, str]] = []
    broken: list[str] = []
    n_id = 0

    for m in declared:
        if m["grain"] not in onto.objects:
            continue
        fact_rel = onto.objects[m["grain"]].relation
        raw = con.execute(f"SELECT {m['expr']} FROM {fact_rel} f").fetchone()[0]
        if raw is None:
            continue
        raw = float(raw)
        reach = _reachable(onto, m["grain"])
        # also the grain's own attributes
        candidates: list[tuple[str, str, dict[str, str] | None]] = []
        for attr in _label_attrs(con, fact_rel):
            candidates.append((m["grain"], attr, None))
        for obj, path in reach.items():
            via = {}
            # via only needs naming where a pair has more than one link
            for ln in path:
                link = onto.links[ln]
                siblings = [
                    x for x in onto.links.values()
                    if x.from_object == link.from_object and x.to_object == link.to_object
                ]
                if len(siblings) > 1:
                    via[link.to_object] = ln
            for attr in _label_attrs(con, onto.objects[obj].relation):
                candidates.append((obj, attr, via or None))

        for obj, attr, via in candidates:
            got = onto.compile(m["name"], group_by=[(obj, attr)], via=via)
            if isinstance(got, Refusal):
                refusals.append({"question": f"{m['name']} by {obj}.{attr}",
                                 "reason": got.reason, "detail": got.detail})
                continue
            assert isinstance(got, CompiledQuery)
            rows = con.execute(got.sql).fetchall()
            vals = [(r[0], float(r[1])) for r in rows if r[1] is not None]
            if len(vals) < 2:
                continue
            total = sum(v for _, v in vals)
            n = len(vals)
            conserves = abs(total - raw) <= ABS_TOL * max(1, n)
            if not conserves:
                broken.append(f"{m['name']} by {obj}.{attr}: grouped {total:,.2f} "
                              f"!= raw {raw:,.2f}")
                continue
            if total <= 0:
                continue
            ordered = sorted(vals, key=lambda kv: -kv[1])
            k = max(1, min(3, n // 2))
            top_share = sum(v for _, v in ordered[:k]) / total
            top1 = ordered[0][1] / total
            null_share = sum(v for lbl, v in vals if lbl is None) / total
            dim_label = f"{obj}.{attr}" if obj != m["grain"] else f"{attr}"
            caveats = [f"{m['name']}: {m['is']}; {m['is_not']}"]
            notes = list(got.notes)

            def _mk(kind: str, headline: str, surprise: float) -> Insight:
                nonlocal n_id
                n_id += 1
                return Insight(
                    id=f"{db}-{n_id:03d}", kind=kind, headline=headline,
                    measure=m["name"], unit=m["unit"], grain=m["grain"],
                    dimension=dim_label,
                    rows=[[lbl, round(v, 2)] for lbl, v in ordered[:10]],
                    total=round(total, 2), n_groups=n, top_share=round(top_share, 4),
                    surprise=round(surprise, 4), sql=got.sql, notes=notes,
                    caveats=caveats, conserves=True,
                )

            uniform_k = k / n
            if n >= 4 and top_share >= 0.5 and top_share - uniform_k >= 0.2:
                lbls = ", ".join(str(lbl) for lbl, _ in ordered[:k])
                insights.append(_mk(
                    "concentration",
                    f"{k} of {n} {dim_label} values ({lbls}) carry "
                    f"{top_share * 100:.1f} pct of {m['name']}",
                    top_share - uniform_k,
                ))
            elif n >= 3 and top1 >= 0.4:
                insights.append(_mk(
                    "dominance",
                    f"{ordered[0][0]!s} alone is {top1 * 100:.1f} pct of {m['name']} "
                    f"across {n} {dim_label} values",
                    top1 - 1.0 / n,
                ))
            if null_share >= 0.05:
                insights.append(_mk(
                    "unknown_bucket",
                    f"{null_share * 100:.1f} pct of {m['name']} lands on rows "
                    f"{dim_label} cannot label (NULL) - a data gap, not a category",
                    min(null_share, 0.3),
                ))

    insights.sort(key=lambda i: -i.surprise)
    # One insight per (dimension, kind): the same territory split reported four
    # times over four measures is one story told four times. Measures then
    # rotate so the list is not all one metric.
    seen: set[tuple[str, str]] = set()
    # Two dimensions that split the measure into the SAME numbers are the same
    # story - EnglishProductSubcategoryName and its Spanish and French twins
    # produced three "insights" with identical figures. Identity is the value
    # vector, not the column name.
    seen_shape: set[tuple[str, str, tuple[float, ...]]] = set()
    per_measure: dict[str, int] = {}
    cap = max(2, top // max(1, len(declared)) + 1)
    picked: list[Insight] = []
    for i in insights:
        key = (i.dimension, i.kind)
        shape = (i.measure, i.kind, tuple(v for _, v in i.rows))
        if key in seen or shape in seen_shape or per_measure.get(i.measure, 0) >= cap:
            continue
        seen.add(key)
        seen_shape.add(shape)
        per_measure[i.measure] = per_measure.get(i.measure, 0) + 1
        picked.append(i)
        if len(picked) >= top:
            break

    return {
        "database": db,
        "ontology": {"objects": len(onto.objects), "links": len(onto.links),
                     "measures": len(declared)},
        "insights": [asdict(i) for i in picked],
        "candidates_examined": n_id,
        "refusals": refusals[:20],
        "broken": broken,
        "scope": ("every figure compiled by the ontology, every grouped total checked "
                  "against the raw total with no join; no model produced any number"),
    }


def main(argv: list[str] | None = None) -> int:
    # R-0012: a Windows console defaults to cp1252 and turns every accented
    # label into a replacement glyph. Say what the data says.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    import duckdb

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--database", required=True)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json", type=Path, help="write the report here")
    args = ap.parse_args(argv)

    if not MANIFEST.is_file():
        print(f"FAIL no manifest at {MANIFEST}")
        return 2
    entries = [e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))
               if e["database"] == args.database]
    if not entries:
        print(f"FAIL {args.database} not in the manifest")
        return 2

    con = duckdb.connect(":memory:")
    try:
        report = mine(con, entries[0], top=args.top)
    finally:
        con.close()
    if "error" in report:
        print(f"FAIL {report['error']}")
        return 1

    o = report["ontology"]
    print(f"=== INSIGHTS {report['database']} - {o['objects']} objects, "
          f"{o['links']} links, {o['measures']} declared measures ===")
    for i in report["insights"]:
        print(f"  [{i['kind']:<14}] {i['headline']}")
        print(f"  {'':<16} total {i['total']:,.2f} {i['unit']} over {i['n_groups']} groups; "
              f"top: " + ", ".join(f"{lbl}={v:,.0f}" for lbl, v in i['rows'][:3]))
    if report["refusals"]:
        print(f"  {len(report['refusals'])} questions refused as ambiguous or unreachable "
              f"(first: {report['refusals'][0]['question']} - {report['refusals'][0]['reason']})")
    print(f"  candidates examined: {report['candidates_examined']}; "
          f"scope: {report['scope']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"  wrote {args.json}")
    if report["broken"]:
        print(f"\nFAIL {len(report['broken'])} candidate groupings did not conserve:")
        for b in report["broken"][:10]:
            print(f"  - {b}")
        return 1
    if not report["insights"]:
        print("\nFAIL no insight cleared the thresholds - nothing was reported (R-0002)")
        return 1
    print("\nPASS every reported figure was compiled and conserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
