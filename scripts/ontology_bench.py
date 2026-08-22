"""A generated benchmark: every verified link becomes test cases, at real n.

Why this exists
---------------
The free-form gate measures 24 curated questions. By R-0010, zero errors there
bounds the true error rate at ~12 percent - honest, and not a number to sell
accuracy on. Databricks' answer is customer-authored benchmarks (500 questions,
hand-written); Palantir's is generating test cases from the data itself. This
does the second, because generated cases scale to the n where the rule of three
starts saying something: n >= 300 before "<1 percent" is a claim at all.

Cases are generated from the ontology the lake itself declared and verify()
measured: for each verified many-to-one link and each summable fact column,
a grouped case (SUM by dimension attribute) and a filtered case (SUM where
attribute = a value taken from the data, hard rule 12 style). For each pair of
objects joined by MORE than one link - the role-playing shape AdventureWorks
really ships - a must-refuse case asserts the compiler refuses until ``via=``
names the path, and that each named path then executes.

What a pass here does and does not prove
----------------------------------------
Each case is executed twice: once through the ontology compiler, once through a
plain SQL template that shares no compiler code. Both trust the same measured
link metadata, so agreement validates the COMPILER, not the metadata - which is
why every grouped case also carries the metadata-independent check: the grouped
total must equal the raw ungrouped total of the fact table, computed with no
join at all. A fan-out or row loss breaks that identity no matter what both
sides believed about the link.

Stated plainly: this bounds the error rate of the deterministic engine layer -
typed request in, correct rows out. It says nothing about natural-language
understanding, which is measured (at small n) by the free-form gate. The two
bounds multiply into the product's honest claim; neither substitutes for the
other.

The first run of this bench found a real compiler defect on real data: eleven
self-join cases (an account's parent account, an employee's manager) where the
compiler silently answered "by the object's OWN attribute" when ``via=`` had
named the parent link - the quietest wrong number, a different question
answered confidently. It had been reported by adversarial review and half
fixed; the bench caught the other half within minutes of existing.

  python scripts/ontology_bench.py                # all extracted databases
  python scripts/ontology_bench.py --database AdventureWorks2025
  python scripts/ontology_bench.py --max-cases 120

Exit 0 only when nothing disagreed and every must-refuse case refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "lake" / "_reports" / "extract_manifest.json"

sys.path.insert(0, str(ROOT / "scripts"))

NUMERIC = {"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT",
           "DOUBLE", "FLOAT", "REAL"}
NUMERIC_PREFIX = ("DECIMAL",)

ABS_TOL = 0.02  # both sides round to 2dp; this absorbs one last-ulp rounding flip


def _numeric_cols(con: Any, rel: str) -> list[str]:
    out = []
    for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall():
        t = str(typ).upper()
        if t in NUMERIC or t.startswith(NUMERIC_PREFIX):
            out.append(str(name))
    return out


def _label_cols(con: Any, rel: str, *, max_distinct: int = 200) -> list[str]:
    """Low-cardinality VARCHAR columns - the attributes a person groups by."""
    out = []
    for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall():
        if str(typ).upper() != "VARCHAR":
            continue
        d, nn = con.execute(
            f'SELECT COUNT(DISTINCT "{name}"), COUNT("{name}") FROM {rel}'
        ).fetchone()
        if 2 <= int(d) <= max_distinct and int(nn) > 0:
            out.append(str(name))
    return out


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= ABS_TOL


def _rows_agree(compiled: list[tuple], oracle: list[tuple]) -> str | None:
    """Unordered label -> value comparison. Ties make ORDER BY nondeterministic
    across two different plans, so order is not part of engine-layer truth here."""
    ca = {tuple(r[:-1]): r[-1] for r in compiled}
    ob = {tuple(r[:-1]): r[-1] for r in oracle}
    if set(ca) != set(ob):
        only_c = list(set(ca) - set(ob))[:3]
        only_o = list(set(ob) - set(ca))[:3]
        return f"group sets differ: compiled-only {only_c}, oracle-only {only_o}"
    for k, v in ca.items():
        if (v is None) != (ob[k] is None):
            return f"{k}: one side NULL ({v!r} vs {ob[k]!r})"
        if v is not None and not _close(v, ob[k]):
            return f"{k}: compiled {v} vs oracle {ob[k]}"
    return None


def generate_and_run(
    con: Any,
    entry: dict[str, Any],
    *,
    max_cases: int,
    max_measures_per_fact: int = 2,
    max_attrs_per_dim: int = 2,
) -> dict[str, Any]:
    from ontology import CompiledQuery, Refusal, from_manifest

    db = str(entry["database"])
    onto = from_manifest(entry)
    violations = onto.verify(con)
    if violations:
        return {"database": db, "error": f"{len(violations)} verify violations", "cases": []}

    paths = {f"{t['schema']}.{t['table']}": (ROOT / str(t["path"])).as_posix()
             for t in entry["tables"]}

    # fact -> dim links, grouped by (fact, dim) so role-playing pairs are found
    pairs: dict[tuple[str, str], list[Any]] = {}
    for link in onto.links.values():
        if link.cardinality == "many_to_one":
            pairs.setdefault((link.from_object, link.to_object), []).append(link)

    passed = failed = refused_ok = refusal_failures = 0
    failures: list[dict[str, Any]] = []
    cases = 0
    facts_seen: dict[str, list[str]] = {}
    key_cols: dict[str, set[str]] = {}
    for name, obj in onto.objects.items():
        key_cols[name] = set(obj.key)
    for link in onto.links.values():
        key_cols.setdefault(link.from_object, set()).update(link.from_columns)

    measure_n = 0

    for (fact, dim), links in sorted(pairs.items()):
        if cases >= max_cases:
            break
        fact_rel = f"read_parquet('{paths[fact]}')"
        dim_rel = f"read_parquet('{paths[dim]}')"

        if fact not in facts_seen:
            nums = [c for c in _numeric_cols(con, fact_rel) if c not in key_cols[fact]]
            facts_seen[fact] = nums[:max_measures_per_fact]
        measures = facts_seen[fact]
        if not measures:
            continue
        attrs = _label_cols(con, dim_rel)[:max_attrs_per_dim]
        if not attrs:
            continue

        # A self-link (an account's parent, an employee's manager) has two
        # readings of "by attr" - the object's own and its parent's. The bench
        # asks the parent's, so via= always names the link for fact == dim;
        # without it the compiler correctly answers the OTHER reading and the
        # oracle template here would grade it against the wrong question.
        self_linked = fact == dim
        ambiguous = len(links) > 1
        if ambiguous:
            # must-refuse: the compiler may not pick a path silently
            mname = f"m_{measure_n}"
            measure_n += 1
            onto.add_measure(mname, fact, f'ROUND(SUM(f."{measures[0]}"), 2)')
            onto.verified = True  # add_measure does not invalidate; be explicit
            got = onto.compile(mname, group_by=[(dim, attrs[0])])
            cases += 1
            if isinstance(got, Refusal) and got.reason == "ambiguous_path":
                refused_ok += 1
            else:
                refusal_failures += 1
                failures.append({"case": f"{fact}->{dim} ambiguous", "detail":
                                 f"expected ambiguous_path refusal, got {type(got).__name__}"})

        for link in links[: (len(links) if ambiguous else 1)]:
            if cases >= max_cases:
                break
            on = " AND ".join(
                f'f."{a}" = d."{b}"' for a, b in zip(link.from_columns, link.to_columns)
            )
            via = {dim: link.name} if (ambiguous or self_linked) else None
            for mcol in measures:
                if cases >= max_cases:
                    break
                mname = f"m_{measure_n}"
                measure_n += 1
                onto.add_measure(mname, fact, f'ROUND(SUM(f."{mcol}"), 2)')
                onto.verified = True
                for attr in attrs:
                    if cases >= max_cases:
                        break
                    # -- grouped case --------------------------------------
                    cases += 1
                    got = onto.compile(mname, group_by=[(dim, attr)], via=via)
                    if not isinstance(got, CompiledQuery):
                        failed += 1
                        failures.append({"case": f"SUM({fact}.{mcol}) by {dim}.{attr}",
                                         "detail": f"refused: {got.reason}: {got.detail[:120]}"})
                        continue
                    compiled_rows = con.execute(got.sql).fetchall()
                    oracle_rows = con.execute(
                        f'SELECT d."{attr}", ROUND(SUM(f."{mcol}"), 2) '
                        f"FROM {fact_rel} f LEFT JOIN {dim_rel} d ON {on} "
                        f'GROUP BY d."{attr}"'
                    ).fetchall()
                    err = _rows_agree(compiled_rows, oracle_rows)
                    if err is None:
                        # metadata-independent leg: no join at all
                        raw = con.execute(
                            f'SELECT ROUND(SUM("{mcol}"), 2) FROM {fact_rel}'
                        ).fetchone()[0]
                        grouped = sum(r[-1] for r in compiled_rows if r[-1] is not None)
                        n_groups = sum(1 for r in compiled_rows if r[-1] is not None)
                        if raw is not None and not (
                            abs(grouped - float(raw)) <= ABS_TOL * max(1, n_groups)
                        ):
                            err = (f"conservation: grouped {grouped:,.2f} != "
                                   f"raw total {float(raw):,.2f}")
                    if err is None:
                        passed += 1
                    else:
                        failed += 1
                        failures.append({"case": f"SUM({fact}.{mcol}) by {dim}.{attr}"
                                         + (f" via {link.name}" if via else ""),
                                         "detail": err})

                # -- filtered scalar case (one per measure/link) -----------
                if cases >= max_cases:
                    break
                attr = attrs[0]
                pick = con.execute(
                    f'SELECT "{attr}" FROM {dim_rel} WHERE "{attr}" IS NOT NULL '
                    f'GROUP BY "{attr}" ORDER BY COUNT(*) DESC LIMIT 1'
                ).fetchone()
                if pick is None:
                    continue
                value = pick[0]
                cases += 1
                got = onto.compile(mname, filters=[(dim, attr, "=", value)], via=via)
                if not isinstance(got, CompiledQuery):
                    failed += 1
                    failures.append({"case": f"SUM({fact}.{mcol}) where {dim}.{attr}={value!r}",
                                     "detail": f"refused: {got.reason}"})
                    continue
                compiled_v = con.execute(got.sql).fetchone()[0]
                keycols = ", ".join(f'"{b}"' for b in link.to_columns)
                factcols = ", ".join(f'f."{a}"' for a in link.from_columns)
                lit = "'" + str(value).replace("'", "''") + "'"
                oracle_v = con.execute(
                    f'SELECT ROUND(SUM(f."{mcol}"), 2) FROM {fact_rel} f '
                    f"WHERE ({factcols}) IN (SELECT {keycols} FROM {dim_rel} "
                    f'WHERE "{attr}" = {lit})'
                ).fetchone()[0]
                both_null = compiled_v is None and oracle_v is None
                if both_null or (
                    compiled_v is not None and oracle_v is not None
                    and _close(compiled_v, oracle_v)
                ):
                    passed += 1
                else:
                    failed += 1
                    failures.append({"case": f"SUM({fact}.{mcol}) where {dim}.{attr}={value!r}",
                                     "detail": f"compiled {compiled_v} vs oracle {oracle_v}"})

    return {
        "database": db,
        "cases": cases,
        "passed": passed,
        "failed": failed,
        "refused_ok": refused_ok,
        "refusal_failures": refusal_failures,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    import duckdb

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--database", help="limit to one database")
    ap.add_argument("--max-cases", type=int, default=400, help="cap per database")
    ap.add_argument("--json", type=Path, help="also write the full report here")
    args = ap.parse_args(argv)

    if not MANIFEST.is_file():
        print(f"FAIL no manifest at {MANIFEST}; run load_adventureworks.py first")
        return 2
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.database:
        entries = [e for e in entries if e["database"] == args.database]
        if not entries:
            print(f"FAIL {args.database} not in the manifest")
            return 2

    con = duckdb.connect(":memory:")
    reports = []
    try:
        for entry in entries:
            r = generate_and_run(con, entry, max_cases=args.max_cases)
            reports.append(r)
            if "error" in r:
                print(f"  {r['database']:<22} ERROR {r['error']}")
                continue
            print(f"  {r['database']:<22} {r['cases']:>4} cases  "
                  f"{r['passed']:>4} passed  {r['failed']:>3} failed  "
                  f"{r['refused_ok']:>2} correct refusals  "
                  f"{r['refusal_failures']:>2} refusal failures")
    finally:
        con.close()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")

    total = sum(r.get("cases", 0) for r in reports)
    passed = sum(r.get("passed", 0) for r in reports)
    failed = sum(r.get("failed", 0) for r in reports)
    ref_ok = sum(r.get("refused_ok", 0) for r in reports)
    ref_bad = sum(r.get("refusal_failures", 0) for r in reports)
    answered = passed + failed

    print()
    print(f"  total          {total} generated cases ({answered} answerable, "
          f"{ref_ok + ref_bad} must-refuse)")
    if answered:
        print(f"  precision      {100.0 * passed / answered:.2f} pct ({passed}/{answered})")
    if not failed and not ref_bad and answered:
        bound = 3.0 / answered * 100.0
        print(f"  error bound    0 wrong in {answered} bounds the true engine-layer error"
              f" rate at ~{bound:.2f} pct (R-0010, 95 pct)")
        if answered < 300:
            print(f"  n caveat       {answered} < 300, so '<1 pct' is not yet claimable")
    print("  scope          this bounds the deterministic engine layer only -")
    print("                 typed request in, correct rows out. It says nothing about")
    print("                 natural-language understanding; the free-form gate owns that.")

    bad = failed + ref_bad
    if bad:
        print(f"\nFAIL {bad} cases wrong:")
        for r in reports:
            for f in r.get("failures", [])[:10]:
                print(f"  - [{r['database']}] {f['case']}: {f['detail']}")
        return 1
    if not answered:
        print("\nFAIL nothing was generated - the bench measured nothing (R-0002)")
        return 1
    print("\nPASS every generated case agreed with its oracle and conserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
