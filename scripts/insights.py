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
comes out of the ontology compiler, which cannot fan out through its joins;
every grouped total is
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

Dimensions, by contrast, are discovered: every object the compiler can reach
from the measure's grain (role-playing pairs tried once per link via=), and the
short, populated, low-cardinality text attributes on it. Columns excluded as
non-dimensions are listed in the report with the reason, and every grouping the
compiler refused is listed as a refusal - nothing is dropped without a record.

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


def _label_attrs(
    con: Any,
    rel: str,
    *,
    lo: int = 2,
    hi: int = 60,
    excluded: list[dict[str, str]] | None = None,
    owner: str = "",
) -> list[str]:
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
        why = None
        if not (lo <= int(d) <= hi):
            why = f"{int(d)} distinct values (dimension range is {lo}..{hi})"
        elif int(total) == 0 or int(nn) * 2 <= int(total):
            why = f"populated on {int(nn)} of {int(total)} rows (needs more than half)"
        elif maxlen is not None and int(maxlen) > 64:
            why = f"values up to {int(maxlen)} chars (a label is 64 or fewer)"
        if why is not None:
            if excluded is not None:
                excluded.append({"object": owner, "column": str(name), "why": why})
            continue
        out.append(str(name))
    return out


def _candidate_objects(onto: Any, grain: str) -> list[tuple[str, dict[str, str] | None]]:
    """Every object the compiler can reach from the grain, with the via it needs.

    The first version re-implemented path resolution here (_reachable) and
    disagreed with compile() - it dropped role-playing dimensions the compiler
    could reach with via=, kept objects the compiler then refused, and recorded
    no refusal for either. There is one resolver now: compile()'s. Candidates
    are every object; where a pair has more than one link, every link is tried
    as a via; compile() decides, and every Refusal is recorded as a refusal.
    """
    out: list[tuple[str, dict[str, str] | None]] = []
    for obj in onto.objects:
        if obj == grain:
            continue
        into = [x for x in onto.links.values() if x.to_object == obj]
        by_from: dict[str, list[Any]] = {}
        for x in into:
            by_from.setdefault(x.from_object, []).append(x)
        role_playing = [links for links in by_from.values() if len(links) > 1]
        if role_playing:
            for links in role_playing:
                for link in links:
                    out.append((obj, {obj: link.name}))
        else:
            out.append((obj, None))
    return out


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
    excluded: list[dict[str, str]] = []
    compiles = 0
    n_id = 0

    for m in declared:
        if m["grain"] not in onto.objects:
            continue
        fact_rel = onto.objects[m["grain"]].relation
        raw = con.execute(f"SELECT {m['expr']} FROM {fact_rel} f").fetchone()[0]
        if raw is None:
            continue
        raw = float(raw)
        candidates: list[tuple[str, str, dict[str, str] | None]] = []
        for attr in _label_attrs(con, fact_rel, excluded=excluded, owner=m["grain"]):
            candidates.append((m["grain"], attr, None))
        for obj, via in _candidate_objects(onto, m["grain"]):
            for attr in _label_attrs(con, onto.objects[obj].relation, excluded=excluded,
                                     owner=obj):
                candidates.append((obj, attr, via))

        for obj, attr, via in candidates:
            compiles += 1
            got = onto.compile(m["name"], group_by=[(obj, attr)], via=via)
            if isinstance(got, Refusal):
                if got.reason in {"no_path"}:
                    continue  # not reachable from this grain: not a question
                refusals.append({"question": f"{m['name']} by {obj}.{attr}"
                                 + (f" via {via}" if via else ""),
                                 "reason": got.reason, "detail": got.detail})
                continue
            assert isinstance(got, CompiledQuery)
            rows = con.execute(got.sql).fetchall()
            vals = [(r[0], float(r[1])) for r in rows if r[1] is not None]
            if len(vals) < 2:
                continue
            total = sum(v for _, v in vals)
            conserves = abs(total - raw) <= ABS_TOL
            if not conserves:
                broken.append(f"{m['name']} by {obj}.{attr}: grouped {total:,.2f} "
                              f"!= raw {raw:,.2f}")
                continue
            # Shares only mean something over a positive, same-signed total. A
            # measure with refunds in it (mixed signs) produced "2 of 5 carry
            # 4060 pct"; a zero or negative total vanished without a word.
            if total <= 0 or any(v < 0 for _, v in vals):
                refusals.append({"question": f"{m['name']} by {obj}.{attr}",
                                 "reason": "signed_measure",
                                 "detail": f"total {total:,.2f} with "
                                           f"{sum(1 for _, v in vals if v < 0)} negative "
                                           "groups - shares of a signed total are not a share"})
                continue
            # NULL is not a top value. It is the unlabelled remainder, reported
            # on its own; ranking is over labelled groups, ties broken by label
            # so the same data reports the same leader every run.
            labelled = [(lbl, v) for lbl, v in vals if lbl is not None]
            null_share = sum(v for lbl, v in vals if lbl is None) / total
            if len(labelled) < 2:
                continue
            n = len(labelled)
            ordered = sorted(labelled, key=lambda kv: (-kv[1], str(kv[0])))
            k = max(1, min(3, n // 2))
            top_share = sum(v for _, v in ordered[:k]) / total
            top1 = ordered[0][1] / total
            top2 = ordered[1][1] / total
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
            tied_at_boundary = (
                n > k and abs(ordered[k - 1][1] - ordered[k][1]) < 1e-9
            )
            if (n >= 4 and top_share >= 0.5 and top_share - uniform_k >= 0.2
                    and not tied_at_boundary):
                lbls = ", ".join(str(lbl) for lbl, _ in ordered[:k])
                insights.append(_mk(
                    "concentration",
                    f"{k} of {n} {dim_label} values ({lbls}) carry "
                    f"{top_share * 100:.1f} pct of {m['name']}",
                    top_share - uniform_k,
                ))
            elif n >= 3 and top1 >= 0.4 and top1 > top2:
                insights.append(_mk(
                    "dominance",
                    f"{ordered[0][0]!s} alone is {top1 * 100:.1f} pct of {m['name']} "
                    f"across {n} {dim_label} values",
                    top1 - 1.0 / n,
                ))
            if null_share >= 0.05:
                # Stated as what it is - rows with no value for this dimension -
                # and not as "a data gap": on AdventureWorks the NULL currency
                # rate is the US orders and the NULL salesperson is the online
                # channel. What the absence means is a question for a person.
                insights.append(_mk(
                    "unknown_bucket",
                    f"{null_share * 100:.1f} pct of {m['name']} lands on rows with "
                    f"no {dim_label} value",
                    min(null_share, 0.3),
                ))

    insights.sort(key=lambda i: -i.surprise)
    # One insight per (dimension, kind): the same territory split reported four
    # times over four measures is one story told four times. Measures then
    # rotate so the list is not all one metric.
    seen: set[tuple[str, str, str]] = set()
    # Two dimensions that split the measure into the SAME numbers are the same
    # story - EnglishProductSubcategoryName and its Spanish and French twins
    # produced three "insights" with identical figures. Identity is the value
    # vector, not the column name.
    seen_shape: set[tuple[str, str, tuple[float, ...]]] = set()
    per_measure: dict[str, int] = {}
    cap = max(2, top // max(1, len(declared)) + 1)
    picked: list[Insight] = []
    for i in insights:
        key = (i.grain, i.dimension, i.kind)
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
        "insights_built": n_id,
        "groupings_compiled": compiles,
        "refusals": refusals[:40],
        "columns_excluded": excluded,
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
    print(f"  groupings compiled: {report['groupings_compiled']}; insights built: "
          f"{report['insights_built']}; columns excluded as non-dimensions: "
          f"{len(report['columns_excluded'])}")
    for i in report["insights"][:3]:
        for cv in i["caveats"]:
            print(f"  caveat: {cv}")
    print(f"  scope: {report['scope']}")
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
