"""Prove the lake is what its metadata says it is, before anything builds on it.

Why this exists
---------------
A semantic layer is a set of promises about data: this column is a key, this
relationship is many-to-one, this measure is additive at this grain. Every one
of those promises is load-bearing, and every one of them is inherited from
somebody else's DDL. A promise that is trusted and false is not a small problem
- it is the ~15x inflation bug, arriving through the front door with a schema
diagram to vouch for it.

So each promise is executed rather than believed. This is also the one place
where the approach goes further than the vendors it copies: Databricks lets a
metric view declare ``rely.at_most_one_match`` and documents plainly that it is
"not validated at runtime. If the join produces a fan-out, measures return
incorrect results." Power BI does better - it fails the whole refresh when a
one-side column receives duplicates - and that is the behaviour copied here.

Checks
------
  row_count       every row the source claimed arrived in the lake
  pk_null         no declared key column is NULL - a NULL key silently drops
                  rows from an inner join and passes a uniqueness check
  pk_unique       a declared primary key is actually unique in the extracted
                  data. This is the "one" side of every join that follows
  fk_intact       every child key value exists in the parent. Orphans mean an
                  inner join drops rows and a total quietly shrinks
  fk_cardinality  DERIVED, not declared: is the parent side of this link really
                  unique? A link whose parent side is not unique is a fan-out
                  hazard and every measure crossing it must name an aggregation

``fk_cardinality`` is the ontology's raw material. It is the difference between
"the schema says this is many-to-one" and "we ran it and it is many-to-one".

Runs entirely over Parquet in DuckDB. No SQL Server, no live stack - the lake is
the artifact everything downstream reads, so the lake is what gets asserted
(R-0001).

  python scripts/validate_lake.py
  python scripts/validate_lake.py --database AdventureWorks2025
  python scripts/validate_lake.py --json

Exit 0 only when every check held.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAKE = ROOT / "data" / "lake"
REPORTS = LAKE / "_reports"
MANIFEST = REPORTS / "extract_manifest.json"


class LakeNotBuilt(Exception):
    """The lake is missing, so there is nothing to validate. Never a silent pass."""


def _q(path: Path) -> str:
    return "read_parquet('" + str(path).replace("\\", "/").replace("'", "''") + "')"


def _col(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def load_manifest() -> list[dict[str, Any]]:
    if not MANIFEST.is_file():
        raise LakeNotBuilt(
            f"no manifest at {MANIFEST} - run "
            "python scripts/load_adventureworks.py --restore --extract first"
        )
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not data:
        raise LakeNotBuilt("manifest is empty; the extract produced nothing")
    return list(data)


def validate_database(con: Any, db: dict[str, Any]) -> dict[str, Any]:
    name = str(db["database"])
    tables = {f"{t['schema']}.{t['table']}": t for t in db.get("tables", [])}
    pks: dict[str, list[str]] = dict(db.get("primary_keys") or {})
    fks: list[dict[str, Any]] = list(db.get("foreign_keys") or [])

    findings: list[dict[str, Any]] = []

    def fail(check: str, subject: str, detail: str) -> None:
        findings.append({"check": check, "subject": subject, "detail": detail})

    # -- row_count -------------------------------------------------------
    for key, t in tables.items():
        path = ROOT / str(t["path"])
        if not path.is_file():
            fail("row_count", key, f"parquet missing at {path}")
            continue
        got = con.execute(f"SELECT COUNT(*) FROM {_q(path)}").fetchone()[0]
        if int(got) != int(t["extracted_rows"]):
            fail("row_count", key,
                 f"parquet holds {got:,} rows, manifest recorded "
                 f"{int(t['extracted_rows']):,}")
        elif int(t["extracted_rows"]) != int(t["declared_rows"]):
            fail("row_count", key,
                 f"extracted {int(t['extracted_rows']):,} rows, source declared "
                 f"{int(t['declared_rows']):,} - the copy is incomplete")

    # -- pk_null and pk_unique -------------------------------------------
    cardinality: dict[str, bool] = {}
    for key, cols in pks.items():
        t = tables.get(key)
        if t is None:
            fail("pk_unique", key, "declared a primary key but was never extracted")
            continue
        path = ROOT / str(t["path"])
        if not path.is_file():
            continue
        col_list = ", ".join(_col(c) for c in cols)
        null_pred = " OR ".join(f"{_col(c)} IS NULL" for c in cols)
        n, distinct, nulls = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT ({col_list})), "
            f"SUM(CASE WHEN {null_pred} THEN 1 ELSE 0 END) FROM {_q(path)}"
        ).fetchone()
        if nulls:
            fail("pk_null", key, f"{int(nulls):,} rows have NULL in key ({', '.join(cols)})")
        if int(n) != int(distinct):
            fail("pk_unique", key,
                 f"{int(n):,} rows but only {int(distinct):,} distinct keys "
                 f"({', '.join(cols)}) - {int(n) - int(distinct):,} duplicates")
        cardinality[key] = int(n) == int(distinct) and not nulls

    # -- fk_intact and fk_cardinality ------------------------------------
    links: list[dict[str, Any]] = []
    by_constraint: dict[str, list[dict[str, Any]]] = {}
    for fk in fks:
        by_constraint.setdefault(str(fk["name"]), []).append(fk)

    for cname, cols in by_constraint.items():
        child_key = str(cols[0]["from_table"])
        parent_key = str(cols[0]["to_table"])
        child, parent = tables.get(child_key), tables.get(parent_key)
        if child is None or parent is None:
            fail("fk_intact", cname,
                 f"{child_key} -> {parent_key}: an end of this link was not extracted")
            continue
        cpath, ppath = ROOT / str(child["path"]), ROOT / str(parent["path"])
        if not (cpath.is_file() and ppath.is_file()):
            continue

        on = " AND ".join(
            f"c.{_col(c['from_column'])} = p.{_col(c['to_column'])}" for c in cols
        )
        child_not_null = " AND ".join(
            f"c.{_col(c['from_column'])} IS NOT NULL" for c in cols
        )
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {_q(cpath)} c WHERE {child_not_null} "
            f"AND NOT EXISTS (SELECT 1 FROM {_q(ppath)} p WHERE {on})"
        ).fetchone()[0]
        if int(orphans):
            fail("fk_intact", cname,
                 f"{child_key} -> {parent_key}: {int(orphans):,} rows reference a "
                 "parent that does not exist; an inner join would drop them")

        # The derived half. A link is only safe to join through if the PARENT
        # side is unique on the referenced columns. Declared or not, we measure.
        pcols = ", ".join(_col(c["to_column"]) for c in cols)
        pn, pdistinct = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT ({pcols})) FROM {_q(ppath)}"
        ).fetchone()
        many_to_one = int(pn) == int(pdistinct)
        max_mult = 0
        if not many_to_one:
            max_mult = int(
                con.execute(
                    f"SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM {_q(ppath)} "
                    f"GROUP BY {pcols})"
                ).fetchone()[0]
                or 0
            )
        links.append(
            {
                "constraint": cname,
                "from_table": child_key,
                "from_columns": [str(c["from_column"]) for c in cols],
                "to_table": parent_key,
                "to_columns": [str(c["to_column"]) for c in cols],
                "cardinality": "many_to_one" if many_to_one else "many_to_many",
                "parent_rows": int(pn),
                "parent_distinct_keys": int(pdistinct),
                "max_fanout": 1 if many_to_one else max_mult,
                "orphans": int(orphans),
            }
        )

    hazards = [link for link in links if link["cardinality"] != "many_to_one"]
    return {
        "database": name,
        "tables": len(tables),
        "primary_keys": len(pks),
        "links": links,
        "fanout_hazards": len(hazards),
        "findings": findings,
        "unique_keys": cardinality,
    }


def stage_validate(database: str | None = None, as_json: bool = False) -> int:
    import duckdb

    print("=== VALIDATE (executed against the lake, not against the DDL) ===")
    manifest = load_manifest()
    if database:
        manifest = [m for m in manifest if m["database"] == database]
        if not manifest:
            raise LakeNotBuilt(f"{database} is not in the manifest")

    con = duckdb.connect(":memory:")
    reports = []
    try:
        for db in manifest:
            report = validate_database(con, db)
            reports.append(report)
            bad = len(report["findings"])
            print(
                f"  {report['database']:<22} {report['tables']:>3} tables  "
                f"{report['primary_keys']:>3} PKs  {len(report['links']):>3} links  "
                f"{report['fanout_hazards']:>3} fan-out hazards  "
                + ("all checks held" if not bad else f"{bad} FINDINGS")
            )
            for link in report["links"]:
                if link["cardinality"] != "many_to_one":
                    print(
                        f"       hazard: {link['from_table']} -> {link['to_table']} "
                        f"parent side is not unique, up to {link['max_fanout']}x fan-out"
                    )
    finally:
        con.close()

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "validation.json"
    out.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"  report: {out.relative_to(ROOT)}")
    if as_json:
        print(json.dumps(reports, indent=2))

    findings = [f for r in reports for f in r["findings"]]
    if findings:
        print(f"\nFAIL {len(findings)} validity findings:")
        for f in findings[:40]:
            print(f"  - [{f['check']}] {f['subject']}: {f['detail']}")
        if len(findings) > 40:
            print(f"  ... and {len(findings) - 40} more (see {out.name})")
        print("\n  Nothing should build a metric on this until these are explained.")
        return 1

    total_links = sum(len(r["links"]) for r in reports)
    hazards = sum(r["fanout_hazards"] for r in reports)
    print("\nPASS every declared key is unique and every declared link resolves.")
    print(f"     {total_links} links measured, {hazards} carry a real fan-out hazard.")
    print("     A hazard is not a defect - it is a relationship a measure must")
    print("     aggregate across rather than join through.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--database", help="validate one database instead of all")
    ap.add_argument("--json", action="store_true", help="also dump the full report")
    args = ap.parse_args(argv)
    try:
        return stage_validate(args.database, args.json)
    except LakeNotBuilt as exc:
        print(f"FAIL {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
