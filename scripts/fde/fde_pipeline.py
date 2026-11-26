"""FDE-PIPELINE-01: connect, extract, profile, ontology draft, question pack, eval, report.

One command takes a tenant from "here is a read-only login" to a scored report, and
the same command runs a fleet of tenants across engines in CI:

    # make 12 seeded tenants and load each into a scratch database on an engine
    python scripts/fde/fde_pipeline.py factory --n 12 --seed 7 --engine postgresql \\
        --host 127.0.0.1 --user fde_loader --grant-readonly fde_ro --out work/tenants

    # run every stage for every tenant, 4 at a time, and write per-tenant + fleet reports
    python scripts/fde/fde_pipeline.py run --tenants work/tenants --work work/run \\
        --workers 4 --expect-keys [--dms armed]

A tenant is a YAML file (``tenant``, ``source``, optional ``glossary``). Real
customers get one written by hand; the factory writes one per generated tenant plus
a ``generated`` block so the oracle copy can be rebuilt for the fidelity check.

Stages, and what each one proves:

* ``extract``  the product connector (``dms_executor.db_connector``) pulls every
  visible table into a bronze DuckDB warehouse; rows/s and key reads are recorded.
* ``profile``  row counts, types, nulls, distinct counts, min/max per column, plus
  foreign-key *candidates* found by value inclusion for sources that declare none.
* ``ontology`` a structural draft (facts, dimensions, line tables, measures, time
  columns, joins) from keys and profile alone. With ``--dms armed --compile`` each
  tenant's serving warehouse also goes through DMS's own AI compile, the one
  ``grade52`` uses (``scripts/verify/onto_compile.py``), and the proposal is stored
  in the Postgres the ``--onto-db-env`` variable names.
* ``steward``  approval stays human. A compiled version is approved only by an
  explicit ``--steward-approved-by <actor uuid> --approval-ref <text>`` or by an
  entry in ``--approval-file`` that names the tenant and the exact version id
  (``onto_steward.approve_version_cmd``). Without one the tenant is
  ``PENDING_STEWARD``: its proposal and diff are written, DMS is not asked, and the
  tenant is listed as a failure (never a pass). ``steward_pending.json`` in the
  work dir is the template a steward fills in for ``--approval-file``.
* ``pack``     human-style questions with gold SQL generated from the draft and run
  on the landed copy. A gold that is empty, all NULL, too long, or tied at its LIMIT
  is dropped with the reason. Two traps per tenant (refuse, clarify).
* ``eval``     fidelity (generated tenants: each gold re-run on the oracle copy must
  match the landed copy), key read vs declared keys, and with ``--dms armed|live``
  every question asked on the real ask path and judged CORRECT / WRONG / ABSTAIN /
  RECONFIRM. A stage that cannot run says SKIPPED and why; nothing is a PASS by default.
* ``report``   ``<work>/<tenant>/report.json`` and ``report.md``, and ``FLEET.md``.

The question templates are test-data generators for the eval harness, not part of
the answering path: DMS never sees them, only the question text.

Passwords come only from the environment variable the tenant file names
(``password_env``); they are never written to a tenant file or a report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "scripts" / "verify", ROOT / "scripts" / "fde", ROOT / "tests" / "fde_scenarios"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import fde_scenarios as fs  # noqa: E402
import tenant_factory as tf  # noqa: E402

EXIT_PASS, EXIT_FAIL, EXIT_CONFIG = 0, 1, 2
ENGINES = ("postgresql", "mysql", "sqlserver", "sqlite", "generated")
NUMERIC = re.compile(r"^(DECIMAL|NUMERIC|DOUBLE|FLOAT|REAL|HUGEINT)", re.IGNORECASE)
INTEGER = re.compile(r"^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|UBIGINT|UINTEGER)", re.IGNORECASE)
TEMPORAL = re.compile(r"^(DATE|TIMESTAMP)", re.IGNORECASE)
TEXT = re.compile(r"^(VARCHAR|TEXT|STRING)", re.IGNORECASE)
MAX_GOLD_ROWS = 60
ABSENT_MEASURES = (
    "carbon offset credits",
    "warranty claim payouts",
    "staff overtime hours",
    "customer NPS score",
)


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def space_id_for(tenant: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"netie-fde-tenant/{tenant}"))


# --------------------------------------------------------------------------- tenant files
@dataclass
class TenantSpec:
    tenant: str
    source: dict[str, Any]
    glossary: dict[str, str] = field(default_factory=dict)
    generated: dict[str, Any] | None = None
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> TenantSpec:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        src = dict(data.get("source") or {})
        if "password" in src:
            raise SystemExit(f"{path}: put the password in an env var and name it in password_env")
        if src.get("kind") not in ENGINES:
            raise SystemExit(f"{path}: source.kind must be one of {ENGINES}")
        return cls(
            tenant=str(data["tenant"]),
            source=src,
            glossary={str(k): str(v) for k, v in (data.get("glossary") or {}).items()},
            generated=data.get("generated"),
            path=path,
        )

    def dump(self, path: Path) -> None:
        import yaml

        body: dict[str, Any] = {"tenant": self.tenant, "source": self.source}
        if self.generated:
            body["generated"] = self.generated
        if self.glossary:
            body["glossary"] = self.glossary
        path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True))


def tenant_files(spec: str) -> list[Path]:
    p = Path(spec)
    return sorted(p.glob("*.yaml")) if p.is_dir() else [p]


def regenerate(spec: TenantSpec) -> tf.Tenant | None:
    g = spec.generated
    if not g:
        return None
    return tf.make_tenant(int(g["seed"]), int(g["index"]), rows_scale=float(g.get("rows_scale", 1)))


# --------------------------------------------------------------------------- extract
def stage_extract(spec: TenantSpec, work: Path, warehouse: Path | None = None) -> dict[str, Any]:
    """Land the tenant through the product connector; return paths, keys and timings."""
    views = work / "views.duckdb"
    if views.exists():
        views.unlink()  # scratch file this run made; never a source
    if spec.source["kind"] == "generated":
        t = regenerate(spec)
        if t is None:
            raise SystemExit(f"{spec.tenant}: kind generated needs a generated block")
        t0 = time.perf_counter()
        fs.build_duckdb_dataset(t.dataset, views)
        secs = time.perf_counter() - t0
        rows = sum(len(tb.rows) for tb in t.dataset.tables)
        return {
            "status": "OK",
            "engine": "generated",
            "note": "in-process oracle, no connector (unit-test path)",
            "views": str(views),
            "tables": len(t.dataset.tables),
            "rows": rows,
            "seconds": round(secs, 3),
            "rows_per_s": round(rows / secs) if secs else None,
            "pk": {tb.name: list(tb.pk) for tb in t.dataset.tables},
            "fk": [[tb.name, c, rt, rc] for tb in t.dataset.tables for c, rt, rc in tb.fks],
            "untyped": {},
        }
    from dms_executor.db_connector import SourceConfig, ingest_source_database

    src = spec.source
    pw_env = src.get("password_env") or "FDE_SOURCE_PASSWORD"
    cfg = SourceConfig(
        kind=src["kind"],
        host=str(src.get("host") or ""),
        port=src.get("port"),
        database=str(src["database"]),
        user=str(src.get("user") or ""),
        password=os.environ.get(pw_env, ""),
        encrypt=bool(src.get("encrypt", False)),
        trust_server_certificate=bool(src.get("trust_server_certificate", True)),
    )
    wh = warehouse or (
        work / "landed.duckdb"
    )  # not "bronze": DuckDB names the catalog after the file
    if warehouse is None and wh.exists():
        wh.unlink()  # scratch warehouse this pipeline owns
    t0 = time.perf_counter()
    extract = ingest_source_database(cfg, path=wh, space_id=space_id_for(spec.tenant))
    secs = time.perf_counter() - t0

    def bare(qualified: str) -> str:
        return qualified.split(".", 1)[1] if "." in qualified else qualified

    names = {p.source.split("#", 1)[1].split(".", 1)[1]: p.bronze_table for p in extract.pulls}
    fs._bronze_views(wh, views, names)
    rows = sum(int(getattr(p, "row_count", 0) or 0) for p in extract.pulls)
    if not rows:
        rows = _count_rows(views, list(names))
    return {
        "status": "OK",
        "engine": src["kind"],
        "source": cfg.describe(),
        "space_id": space_id_for(spec.tenant),
        "warehouse": str(wh),
        "views": str(views),
        "bronze": names,
        "tables": len(extract.pulls),
        "rows": rows,
        "seconds": round(secs, 3),
        "rows_per_s": round(rows / secs) if secs else None,
        "pk": {bare(k): list(v) for k, v in extract.keys.primary_keys.items()},
        "fk": [
            [bare(f.from_table), f.from_column, bare(f.to_table), f.to_column]
            for f in extract.keys.foreign_keys
        ],
        "untyped": {p.bronze_table: p.untyped_columns for p in extract.pulls if p.untyped_columns},
    }


def _count_rows(views: Path, tables: list[str]) -> int:
    import duckdb

    con = duckdb.connect(str(views), read_only=True)
    try:
        return sum(int(con.execute(f"SELECT COUNT(*) FROM {q(t)}").fetchone()[0]) for t in tables)
    finally:
        con.close()


# --------------------------------------------------------------------------- profile
def stage_profile(views: Path, pk: dict[str, list[str]]) -> dict[str, Any]:
    import duckdb

    con = duckdb.connect(str(views), read_only=True)
    try:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        prof: dict[str, Any] = {}
        for t in tables:
            n = int(con.execute(f"SELECT COUNT(*) FROM {q(t)}").fetchone()[0])
            cols = {}
            for name, ctype, *_ in con.execute(f"DESCRIBE {q(t)}").fetchall():
                dist_fn = "approx_count_distinct" if n > 1_000_000 else "COUNT(DISTINCT {})"
                dexpr = (
                    f"approx_count_distinct({q(name)})"
                    if dist_fn.startswith("approx")
                    else f"COUNT(DISTINCT {q(name)})"
                )
                nulls, distinct = con.execute(
                    f"SELECT COUNT(*) - COUNT({q(name)}), {dexpr} FROM {q(t)}"
                ).fetchone()
                info: dict[str, Any] = {
                    "type": str(ctype),
                    "nulls": int(nulls),
                    "distinct": int(distinct),
                }
                if NUMERIC.match(ctype) or INTEGER.match(ctype) or TEMPORAL.match(ctype):
                    lo, hi = con.execute(
                        f"SELECT MIN({q(name)}), MAX({q(name)}) FROM {q(t)}"
                    ).fetchone()
                    info["min"], info["max"] = fs._jsonable(lo), fs._jsonable(hi)
                cols[name] = info
            prof[t] = {
                "rows": n,
                "columns": cols,
                "key_candidates": _key_candidates(con, t, n, cols),
            }
        keys_source = "declared"
        if not any(pk.get(t) for t in prof):
            # Nothing declared or nothing visible to this login: fall back to unique
            # columns so the draft still has entities. Marked, so the steward knows.
            pk = {
                t: info["key_candidates"][0] for t, info in prof.items() if info["key_candidates"]
            }
            keys_source = "inferred"
        inferred = _infer_fks(con, prof, pk)
    finally:
        con.close()
    return {
        "status": "OK",
        "tables": prof,
        "fk_candidates": inferred,
        "keys_source": keys_source,
        "pk_used": pk,
    }


def _key_candidates(con: Any, t: str, n: int, cols: dict[str, Any]) -> list[list[str]]:
    """Integer columns (or pairs) that are non-null and unique: candidate primary keys."""
    if not n:
        return []
    ints = [c for c, ci in cols.items() if INTEGER.match(ci["type"]) and ci["nulls"] == 0]
    single = [[c] for c in ints if cols[c]["distinct"] == n]
    if single:
        return single
    pairs = []
    for i, a in enumerate(ints[:8]):
        for b in ints[i + 1 : 8]:
            got = con.execute(f"SELECT COUNT(DISTINCT ({q(a)}, {q(b)})) FROM {q(t)}").fetchone()[0]
            if got == n:
                pairs.append([a, b])
    return pairs


def _infer_fks(con: Any, prof: dict[str, Any], pk: dict[str, list[str]]) -> list[list[Any]]:
    """Integer columns whose non-null values all exist in another table's single-column PK.

    Value inclusion alone over-claims on small integer ranges (a 1..4 line number fits
    inside any id column), so a candidate must also cover at least 20% of the parent's
    keys, and only the best-covered parent per column is kept. These are proposals for
    the steward, never served as joins on their own.
    """
    parents = {
        t: cols[0]
        for t, cols in pk.items()
        if len(cols) == 1 and t in prof and INTEGER.match(prof[t]["columns"][cols[0]]["type"])
    }
    out: list[list[Any]] = []
    for child, info in prof.items():
        own_pk = pk.get(child, [])
        for col, ci in info["columns"].items():
            if not INTEGER.match(ci["type"]) or ci["distinct"] < 2:
                continue
            if own_pk == [col]:
                continue
            for parent, pcol in parents.items():
                if parent == child:
                    continue
                pn = prof[parent]["rows"]
                if not pn or ci["distinct"] < 0.2 * pn or ci["distinct"] > pn:
                    continue
                if ci.get("min") is not None and ci["min"] < prof[parent]["columns"][pcol].get(
                    "min", 0
                ):
                    continue
                missing = con.execute(
                    f"SELECT COUNT(*) FROM {q(child)} c WHERE c.{q(col)} IS NOT NULL "
                    "AND NOT EXISTS "
                    f"(SELECT 1 FROM {q(parent)} p WHERE p.{q(pcol)} = c.{q(col)})"
                ).fetchone()[0]
                if missing == 0:
                    out.append([child, col, parent, pcol, round(ci["distinct"] / pn, 3)])
    # Surrogate ids overlap: doctor ids 1..20 also fit inside branch ids 1..30. Per child
    # column keep the parent it covers best; an exact tie stays ambiguous (all kept).
    best: dict[tuple[str, str], float] = {}
    for c in out:
        best[(c[0], c[1])] = max(best.get((c[0], c[1]), 0.0), c[4])
    return [c for c in out if c[4] == best[(c[0], c[1])]]


# --------------------------------------------------------------------------- ontology
def humanize(name: str, glossary: dict[str, str], table: str | None = None) -> str:
    key = f"{table}.{name}" if table else name
    if key in glossary:
        return glossary[key]
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = re.sub(r"^(tbl|t)_", "", s, flags=re.IGNORECASE)
    return s.replace("_", " ").lower().strip()


def stage_ontology(
    profile: dict[str, Any],
    pk: dict[str, list[str]],
    fk: list[list[str]],
    glossary: dict[str, str],
) -> dict[str, Any]:
    """Structural draft from keys and value statistics only."""
    prof = profile["tables"]
    joins = [list(f) + ["declared"] for f in fk]
    if not joins:
        joins = [c[:4] + ["inferred"] for c in profile["fk_candidates"]]
    refs: dict[str, dict[str, tuple[str, str]]] = {}
    referenced: set[str] = set()
    for child, col, parent, pcol, _src in joins:
        refs.setdefault(child, {})[col] = (parent, pcol)
        referenced.add(parent)
    entities: dict[str, Any] = {}
    for t, info in prof.items():
        n = info["rows"] or 1
        keys = set(pk.get(t, []))
        r = refs.get(t, {})
        measures, times, cats, names = [], [], [], []
        for c, ci in info["columns"].items():
            if c in keys or c in r:
                continue
            ty = ci["type"]
            if TEMPORAL.match(ty):
                times.append(c)
            elif NUMERIC.match(ty):
                measures.append(c)
            elif TEXT.match(ty):
                if ci["distinct"] <= 12 and ci["distinct"] <= 0.5 * n:
                    cats.append(c)
                elif ci["distinct"] >= 0.9 * (n - ci["nulls"]):
                    names.append(c)
        composite_ref = len(keys) > 1 and any(k in r for k in keys)
        if composite_ref and measures and not times:
            role = "lines"
        elif times and measures and r:
            role = "fact"
        elif t in referenced and (names or cats):
            role = "dimension"
        else:
            role = "other"
        entities[t] = {
            "label": humanize(t, glossary),
            "role": role,
            "rows": info["rows"],
            "key": sorted(keys, key=list(info["columns"]).index),
            "refs": {c: list(v) for c, v in r.items()},
            "measures": measures,
            "time": times,
            "categories": cats,
            "names": names,
        }
    return {"status": "OK", "entities": entities, "joins": joins}


# --------------------------------------------------------------------------- pack
def _full_year(con: Any, t: str, col: str) -> int | None:
    rows = con.execute(
        f"SELECT EXTRACT(year FROM {q(col)}) y, COUNT(DISTINCT EXTRACT(month FROM {q(col)})) m "
        f"FROM {q(t)} WHERE {q(col)} IS NOT NULL GROUP BY 1 HAVING m = 12 ORDER BY 1 DESC LIMIT 1"
    ).fetchall()
    return int(rows[0][0]) if rows else None


def stage_pack(
    onto: dict[str, Any], views: Path, glossary: dict[str, str], *, max_questions: int = 25
) -> dict[str, Any]:
    import duckdb

    ents = onto["entities"]
    lab = lambda t, c=None: humanize(c, glossary, t) if c else ents[t]["label"]  # noqa: E731
    cands: list[dict[str, Any]] = []

    def add(template: str, question: str, sql: str, *, ordered: bool = False) -> None:
        cands.append(
            {
                "template": template,
                "question": question,
                "gold_sql": sql,
                "ordered": ordered,
                "expect": "answer",
            }
        )

    con = duckdb.connect(str(views), read_only=True)
    try:
        for f, fe in ents.items():
            if fe["role"] != "fact":
                continue
            tcol, m = fe["time"][0], fe["measures"][0]
            year = _full_year(con, f, tcol)
            if year is None:
                continue
            win = f"{q(tcol)} >= DATE '{year}-01-01' AND {q(tcol)} < DATE '{year + 1}-01-01'"
            fl, ml = lab(f), lab(f, m)
            add(
                "count_year",
                f"How many {fl} were there in {year}?",
                f"SELECT COUNT(*) FROM {q(f)} WHERE {win}",
            )
            add(
                "sum_year",
                f"What was the total {ml} across all {fl} in {year}?",
                f"SELECT ROUND(SUM({q(m)}), 2) FROM {q(f)} WHERE {win}",
            )
            add(
                "monthly",
                f"Show the total {ml} for each month of {year}, by month number.",
                f"SELECT EXTRACT(month FROM {q(tcol)})::INT AS month_no, ROUND(SUM({q(m)}), 2) "
                f"FROM {q(f)} WHERE {win} GROUP BY 1 ORDER BY 1",
                ordered=True,
            )
            if len(fe["measures"]) > 1:
                m2 = fe["measures"][1]
                add(
                    "avg_year",
                    f"What was the average {lab(f, m2)} per {fl.rstrip('s')} in {year}?",
                    f"SELECT ROUND(AVG({q(m2)}), 2) FROM {q(f)} WHERE {win}",
                )
            if fe["categories"]:
                s = fe["categories"][0]
                top = con.execute(
                    f"SELECT {q(s)} FROM {q(f)} WHERE {q(s)} IS NOT NULL GROUP BY 1 "
                    f"ORDER BY COUNT(*) DESC, 1 LIMIT 1"
                ).fetchone()
                if top:
                    v = str(top[0]).replace("'", "''")
                    add(
                        "avg_filtered",
                        f"What was the average {ml} of {fl} with {lab(f, s)} '{top[0]}' in {year}?",
                        f"SELECT ROUND(AVG({q(m)}), 2) FROM {q(f)} WHERE {win} AND {q(s)} = '{v}'",
                    )
                    add(
                        "share_by_status",
                        f"How many {fl} were there in {year} for each {lab(f, s)}?",
                        f"SELECT {q(s)}, COUNT(*) FROM {q(f)} WHERE {win} GROUP BY 1",
                    )
            for rc, (d, dk) in list(fe["refs"].items())[:2]:
                de = ents.get(d)
                if not de or de["role"] != "dimension":
                    continue
                dl = lab(d)
                j = f"{q(f)} f JOIN {q(d)} d ON f.{q(rc)} = d.{q(dk)}"
                fwin = win.replace(q(tcol), f"f.{q(tcol)}")
                if de["categories"]:
                    c = de["categories"][0]
                    add(
                        "by_dim_category",
                        f"Break down the total {ml} in {year} by {dl} {lab(d, c)}.",
                        f"SELECT d.{q(c)}, ROUND(SUM(f.{q(m)}), 2) FROM {j} "
                        f"WHERE {fwin} GROUP BY 1",
                    )
                if de["names"]:
                    nm = de["names"][0]
                    add(
                        "top5_dim",
                        f"Which 5 {dl}s had the highest total {ml} in {year}?",
                        f"SELECT d.{q(nm)}, ROUND(SUM(f.{q(m)}), 2) AS total FROM {j} "
                        f"WHERE {fwin} GROUP BY 1 ORDER BY total DESC LIMIT 5",
                        ordered=True,
                    )
                add(
                    "dims_without_fact",
                    f"How many {dl}s had no {fl} at all in {year}?",
                    f"SELECT COUNT(*) FROM {q(d)} d WHERE NOT EXISTS (SELECT 1 FROM {q(f)} f "
                    f"WHERE f.{q(rc)} = d.{q(dk)} AND {fwin})",
                )
                add(
                    "distinct_dims_q2",
                    f"How many different {dl}s had at least one of the {fl} between April and "
                    f"June {year}?",
                    f"SELECT COUNT(DISTINCT {q(rc)}) FROM {q(f)} WHERE {q(tcol)} >= DATE "
                    f"'{year}-04-01' AND {q(tcol)} < DATE '{year}-07-01'",
                )
            for ln, le in ents.items():
                if le["role"] != "lines" or not le["measures"]:
                    continue
                hdr = [(c, p) for c, p in le["refs"].items() if p[0] == f]
                if not hdr:
                    continue
                hc, (_, hk) = hdr[0]
                lm = le["measures"][-1]
                lj = f"{q(ln)} l JOIN {q(f)} f ON l.{q(hc)} = f.{q(hk)}"
                fwin = win.replace(q(tcol), f"f.{q(tcol)}")
                add(
                    "lines_sum",
                    f"What was the total {lab(ln, lm)} on {lab(ln)} for {fl} in {year}?",
                    f"SELECT ROUND(SUM(l.{q(lm)}), 2) FROM {lj} WHERE {fwin}",
                )
                add(
                    "lines_avg_count",
                    f"On average, how many {lab(ln)} lines did each of the {fl} in {year} have?",
                    f"SELECT ROUND(COUNT(*) * 1.0 / COUNT(DISTINCT l.{q(hc)}), 2) FROM {lj} "
                    f"WHERE {fwin}",
                )
        kept, dropped = [], []
        for c in cands:
            why = _gold_problem(views, c)
            (dropped if why else kept).append({**c, "drop": why} if why else c)
    finally:
        con.close()
    # Round-robin across templates so a capped pack still covers every shape.
    by_t: dict[str, list[dict[str, Any]]] = {}
    for c in kept:
        by_t.setdefault(c["template"], []).append(c)
    ordered: list[dict[str, Any]] = []
    while any(by_t.values()) and len(ordered) < max_questions - 2:
        for t in list(by_t):
            if by_t[t] and len(ordered) < max_questions - 2:
                ordered.append(by_t[t].pop(0))
    labels = {e["label"] for e in ents.values()} | set(glossary.values())
    absent = next((a for a in ABSENT_MEASURES if a not in labels), ABSENT_MEASURES[0])
    facts = [t for t, e in ents.items() if e["role"] == "fact"]
    dims = [t for t, e in ents.items() if e["role"] == "dimension"]
    if facts:
        ordered.append(
            {
                "template": "trap_refuse",
                "expect": "refuse",
                "question": f"What were the total {absent} for {lab(facts[0])} last year?",
            }
        )
    if dims:
        ordered.append(
            {
                "template": "trap_clarify",
                "expect": "clarify",
                "question": f"Which {lab(dims[0])} is the best one?",
            }
        )
    for i, item in enumerate(ordered, 1):
        item["id"] = f"g{i:02d}"
    gold: dict[str, Any] = {}
    for item in ordered:
        if item["expect"] == "answer":
            cols, rows = fs.run_select(views, item["gold_sql"])
            gold[item["id"]] = {"columns": cols, "rows": rows}
    return {
        "status": "OK" if any(i["expect"] == "answer" for i in ordered) else "EMPTY",
        "questions": ordered,
        "gold": gold,
        "dropped": [{"template": d["template"], "why": d["drop"]} for d in dropped],
        "candidates": len(cands),
    }


def _gold_problem(views: Path, c: dict[str, Any]) -> str | None:
    try:
        _, rows = fs.run_select(views, c["gold_sql"])
    except Exception as exc:  # noqa: BLE001 - recorded as the drop reason
        return f"error:{type(exc).__name__}"
    if not rows:
        return "empty"
    if all(v is None for r in rows for v in r):
        return "all_null"
    if len(rows) > MAX_GOLD_ROWS:
        return "too_many_rows"
    if c["ordered"] and fs._tied_at_limit(views, c["gold_sql"]):
        return "tie_at_limit"
    return None


# --------------------------------------------------------------------------- eval
def stage_eval_offline(
    spec: TenantSpec,
    extract: dict[str, Any],
    onto: dict[str, Any],
    pack: dict[str, Any],
    profile: dict[str, Any],
    work: Path,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    t = regenerate(spec)
    if t is not None:
        want_pk = {tb.name: list(tb.pk) for tb in t.dataset.tables}
        want_fk = sorted([tb.name, c, rt, rc] for tb in t.dataset.tables for c, rt, rc in tb.fks)
        got_fk = sorted(list(f) for f in extract["fk"])
        out["keys"] = {
            "pk_read": sum(1 for k, v in want_pk.items() if extract["pk"].get(k) == v),
            "pk_want": len(want_pk),
            "fk_read": len(set(map(tuple, got_fk)) & set(map(tuple, want_fk))),
            "fk_want": len(want_fk),
        }
        cand = {tuple(c[:4]) for c in profile["fk_candidates"]}
        truth = set(map(tuple, want_fk))
        out["fk_inference"] = {
            "candidates": len(cand),
            "true": len(cand & truth),
            "precision": round(len(cand & truth) / len(cand), 3) if cand else None,
            "recall": round(len(cand & truth) / len(truth), 3) if truth else None,
        }
        roles_ok = sum(
            1
            for name, r in t.roles.items()
            if onto["entities"].get(name, {}).get("role") == r["role"]
        )
        meas_ok = sum(
            1
            for name, r in t.roles.items()
            if r["role"] in ("fact", "lines")
            and sorted(onto["entities"].get(name, {}).get("measures", [])) == sorted(r["measures"])
        )
        out["ontology"] = {
            "roles_ok": roles_ok,
            "tables": len(t.roles),
            "measures_ok": meas_ok,
            "measure_tables": sum(1 for r in t.roles.values() if r["role"] in ("fact", "lines")),
        }
        oracle = work / "oracle.duckdb"
        fs.build_duckdb_dataset(t.dataset, oracle)
        ok = bad = 0
        miss = []
        for item in pack["questions"]:
            if item["expect"] != "answer":
                continue
            _, rows = fs.run_select(oracle, item["gold_sql"])
            if fs.rows_match(rows, pack["gold"][item["id"]]["rows"], ordered=item["ordered"]):
                ok += 1
            else:
                bad += 1
                miss.append(item["id"])
        oracle.unlink()
        out["fidelity"] = {"gold_ok": ok, "mismatch": bad, "ids": miss}
    else:
        out["fidelity"] = {"status": "SKIPPED", "why": "no oracle for a real tenant"}
    return out


def eval_failures(ev: dict[str, Any], *, expect_keys: bool) -> list[str]:
    fails = []
    fid = ev.get("fidelity", {})
    if fid.get("mismatch"):
        fails.append(f"fidelity mismatch {fid['ids']}")
    k = ev.get("keys")
    if expect_keys and k and (k["pk_read"] != k["pk_want"] or k["fk_read"] != k["fk_want"]):
        fails.append(f"keys PK {k['pk_read']}/{k['pk_want']} FK {k['fk_read']}/{k['fk_want']}")
    if ev.get("pack_status") == "EMPTY":
        fails.append("pack empty: no answerable question could be generated")
    dms = ev.get("dms") or {}
    if dms.get("tally", {}).get("WRONG"):
        fails.append(f"DMS WRONG={dms['tally']['WRONG']}")
    if dms.get("tally", {}).get("ERROR"):
        # No answer envelope came back, so the question was never graded.
        fails.append(f"DMS ERROR={dms['tally']['ERROR']} (not graded)")
    return fails


# --------------------------------------------------------------------------- run one tenant
def run_tenant(path: str, work_root: str, opts: dict[str, Any]) -> dict[str, Any]:
    spec = TenantSpec.load(Path(path))
    work = Path(work_root) / spec.tenant
    work.mkdir(parents=True, exist_ok=True)
    rep: dict[str, Any] = {"tenant": spec.tenant, "engine": spec.source["kind"], "stages": {}}
    timings: dict[str, float] = {}

    def timed(name: str, fn: Any, *a: Any, **k: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            timings[name] = round(time.perf_counter() - t0, 3)

    try:
        wh = Path(opts["warehouse"]) if opts.get("warehouse") else None
        if opts.get("tenant_warehouse"):
            # Compile reads the whole serving warehouse, so each tenant gets its own.
            from dms_executor.demo_warehouse import ensure_demo_warehouse

            wh = work / "serve.duckdb"
            if wh.exists():
                wh.unlink()  # scratch warehouse this pipeline owns
            ensure_demo_warehouse(wh)
        ext = timed("extract", stage_extract, spec, work, wh)
        rep["stages"]["extract"] = {k: v for k, v in ext.items() if k not in ("pk", "fk")}
        rep["keys_read"] = {"pk_tables": len(ext["pk"]), "fk_columns": len(ext["fk"])}
        views = Path(ext["views"])
        prof = timed("profile", stage_profile, views, ext["pk"])
        rep["stages"]["profile"] = {"status": "OK", "fk_candidates": len(prof["fk_candidates"])}
        rep["stages"]["profile"]["keys_source"] = prof["keys_source"]
        onto = timed("ontology", stage_ontology, prof, prof["pk_used"], ext["fk"], spec.glossary)
        roles: dict[str, int] = {}
        for e in onto["entities"].values():
            roles[e["role"]] = roles.get(e["role"], 0) + 1
        rep["stages"]["ontology"] = {"status": "OK", "roles": roles, "joins": len(onto["joins"])}
        pack = timed(
            "pack",
            stage_pack,
            onto,
            views,
            spec.glossary,
            max_questions=int(opts.get("max_questions", 25)),
        )
        n_ans = sum(1 for i in pack["questions"] if i["expect"] == "answer")
        rep["stages"]["pack"] = {
            "status": pack["status"],
            "questions": len(pack["questions"]),
            "answerable": n_ans,
            "traps": len(pack["questions"]) - n_ans,
            "candidates": pack["candidates"],
            "dropped": pack["dropped"],
        }
        ev = timed("eval", stage_eval_offline, spec, ext, onto, pack, prof, work)
        ev["pack_status"] = pack["status"]
        rep["eval"] = ev
        for name, obj in (("profile", prof), ("ontology_draft", onto), ("pack", pack)):
            (work / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str) + "\n")
        rep["space_id"] = ext.get("space_id")
        rep["warehouse"] = ext.get("warehouse")
        rep["status"] = "OK"
    except Exception as exc:  # noqa: BLE001 - one tenant's failure is its own report
        rep["status"] = "ERROR"
        rep["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    rep["timings"] = timings
    rep["failures"] = (
        [rep["error"]]
        if rep["status"] == "ERROR"
        else eval_failures(rep.get("eval", {}), expect_keys=bool(opts.get("expect_keys")))
    )
    (work / "report.json").write_text(json.dumps(rep, indent=1, default=str) + "\n")
    return rep


# --------------------------------------------------------------------------- DMS eval
def _armed_packs(
    reports: list[dict[str, Any]], work_root: Path
) -> tuple[dict[str, tuple[list[Any], dict[str, Any]]], dict[str, str]]:
    packs, spaces = {}, {}
    for r in reports:
        if r["status"] != "OK" or not r.get("space_id"):
            continue
        p = json.loads((work_root / r["tenant"] / "pack.json").read_text())
        packs[r["tenant"]] = (p["questions"], p["gold"])
        spaces[r["tenant"]] = r["space_id"]
    return packs, spaces


def _merge_armed(reports: list[dict[str, Any]], out: Path, args: argparse.Namespace) -> None:
    data = json.loads(out.read_text())
    for r in reports:
        got = data["companies"].get(r["tenant"])
        if got:
            r.setdefault("eval", {})["dms"] = {
                "mode": "armed",
                "tally": got["tally"],
                "answered": got["answered"],
                "reason_codes": reason_code_counts(got.get("cases", [])),
            }
            r["failures"] = eval_failures(r["eval"], expect_keys=bool(args.expect_keys))


def reason_code_counts(cases: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Per-question reason codes from the answer envelope, counted (layer when none)."""
    out: dict[str, int] = {}
    for c in cases:
        rc = c.get("reconfirm") if isinstance(c.get("reconfirm"), dict) else {}
        code = (
            (rc or {}).get("reason_code")
            or c.get("abstain_reason")
            or (f"verdict:{c.get('verdict')}" if c.get("verdict") else "none")
        )
        out[str(code)] = out.get(str(code), 0) + 1
    return dict(sorted(out.items()))


def dms_armed(reports: list[dict[str, Any]], work_root: Path, args: argparse.Namespace) -> None:
    """Ask every generated question on the real in-process ask path (grade52 armed)."""
    if getattr(args, "compile", False):
        dms_armed_compiled(reports, work_root, args)
        return
    packs, spaces = _armed_packs(reports, work_root)
    if not packs:
        return
    out = work_root / "dms_armed.json"
    ns = argparse.Namespace(
        source="fleet", all_flags=args.all_flags, timeout=args.timeout, json=str(out)
    )
    fs.armed(list(packs), ns, packs=packs, landed=(Path(args.warehouse), spaces))
    _merge_armed(reports, out, args)


# --------------------------------------------------------------------------- compile + steward
ONTO_TENANT_NS = "netie-fde-onto"


def onto_tenant_id(tenant: str) -> str:
    """Ontology tenant uuid for an FDE tenant (stable, never the box default tenant)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ONTO_TENANT_NS}/{tenant}"))


@contextmanager
def scoped_env(values: dict[str, str]) -> Iterator[None]:
    """Set ``values`` for the block, then restore the process env exactly."""
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def onto_env(args: argparse.Namespace, tenant: str) -> dict[str, str]:
    """Env the compile, approve and ontology-grounded asks need for one tenant."""
    name = args.onto_db_env
    url = os.environ.get(name, "").strip()
    if not url:
        raise SystemExit(f"[CONFIG] --compile needs {name} (scratch ontology Postgres URL)")
    return {
        "DATABASE_URL": url,
        "DMS_AO_COMPILE": "1",
        "DMS_ONTO_PG": "1",
        "DMS_ONTO_FROM_DATA": "1",
        "DMS_TENANT_ID": onto_tenant_id(tenant),
    }


def load_approval_file(path: str | None) -> dict[str, dict[str, str]]:
    """``{tenant: {version_id, actor, approval_ref}}`` from a steward's recorded file.

    The file is JSON ``{"approvals": [{"tenant", "version_id", "actor",
    "approval_ref"}]}``. An entry missing any field is refused by name; it is
    never filled in.
    """
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for i, row in enumerate(data.get("approvals") or []):
        missing = [
            k
            for k in ("tenant", "version_id", "actor", "approval_ref")
            if not str(row.get(k) or "").strip()
        ]
        if missing:
            raise SystemExit(f"[CONFIG] approval file entry {i} missing {','.join(missing)}")
        out[str(row["tenant"])] = {
            k: str(row[k]).strip() for k in ("version_id", "actor", "approval_ref")
        }
    return out


def steward_decision(
    tenant: str,
    version_id: str | None,
    args: argparse.Namespace,
    recorded: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    """Who approves ``version_id`` for ``tenant``, or None (stays PENDING_STEWARD).

    Only two sources count: the explicit ``--steward-approved-by`` flag with an
    ``--approval-ref``, or a recorded file entry for this tenant naming this exact
    version. Nothing else approves.
    """
    if not version_id:
        return None
    entry = recorded.get(tenant)
    if entry and entry["version_id"] == version_id:
        return {"actor": entry["actor"], "approval_ref": entry["approval_ref"], "source": "file"}
    actor = (getattr(args, "steward_approved_by", None) or "").strip()
    if actor:
        return {
            "actor": actor,
            "approval_ref": (getattr(args, "approval_ref", None) or "").strip(),
            "source": "flag",
        }
    return None


def stage_steward(
    r: dict[str, Any],
    work_root: Path,
    args: argparse.Namespace,
    recorded: dict[str, dict[str, str]],
    *,
    transport: Any = None,
    store: Any = None,
) -> dict[str, Any]:
    """Compile one tenant's serving warehouse, then approve only on a human's say."""
    import onto_compile
    import onto_steward

    tenant = r["tenant"]
    tdir = work_root / tenant
    entry = recorded.get(tenant)
    res: dict[str, Any] = {"tenant_id": onto_tenant_id(tenant)}
    if entry:
        # A steward approved a version an earlier run compiled: approve that one.
        res.update(version_id=entry["version_id"], compiled="reused")
    else:
        try:
            got = onto_compile.compile_warehouse(
                Path(r["warehouse"]),
                res["tenant_id"],
                transport=transport,
                store=store,
                dest=tdir / "onto_proposal.json",
            )
        except SystemExit as exc:
            res.update(status="COMPILE_REFUSED", detail=str(exc))
            return res
        res.update(
            version_id=got["version_id"],
            compiled="new",
            counts=got["counts"],
            model_calls=got["model_calls"],
        )
    who = steward_decision(tenant, res.get("version_id"), args, recorded)
    if who is None:
        res["status"] = "PENDING_STEWARD"
        return res
    try:
        done = onto_steward.approve_version_cmd(
            version_id=str(res["version_id"]),
            actor=who["actor"],
            approval_ref=who["approval_ref"],
            store=store,
        )
    except SystemExit as exc:
        res.update(status="APPROVAL_REFUSED", detail=str(exc), approval_source=who["source"])
        return res
    res.update(
        status="APPROVED",
        approval_source=who["source"],
        approved_by=done["actor"],
        approval_ref=done["approval_ref"],
        approved_items=done["n"],
    )
    return res


def steward_failures(onto: dict[str, Any]) -> list[str]:
    st = onto.get("status")
    if st == "APPROVED":
        return []
    if st == "PENDING_STEWARD":
        return [
            f"ontology PENDING_STEWARD version={onto.get('version_id')} "
            "(DMS not asked; approve with --steward-approved-by or --approval-file)"
        ]
    return [f"ontology {st}: {onto.get('detail', '')}".strip()]


def dms_armed_compiled(
    reports: list[dict[str, Any]], work_root: Path, args: argparse.Namespace
) -> None:
    """Per tenant: compile, steward approval, then ontology-grounded armed asks."""
    import grade52
    from dms_core.control_plane.onto_data import reset_ontology_data_store

    packs, spaces = _armed_packs(reports, work_root)
    recorded = load_approval_file(args.approval_file)
    pending = []
    for r in reports:
        if r["tenant"] not in packs:
            continue
        t = r["tenant"]
        with scoped_env(onto_env(args, t)):
            reset_ontology_data_store()
            onto = stage_steward(r, work_root, args, recorded)
            (work_root / t / "steward.json").write_text(json.dumps(onto, indent=1) + "\n")
            r.setdefault("eval", {})["onto"] = onto
            print(f"[STEWARD] {t}: {onto['status']} version={onto.get('version_id')}", flush=True)
            if onto["status"] == "PENDING_STEWARD":
                pending.append(
                    {"tenant": t, "version_id": onto["version_id"], "actor": "", "approval_ref": ""}
                )
            if onto["status"] != "APPROVED":
                r["failures"] = eval_failures(r["eval"], expect_keys=bool(args.expect_keys))
                r["failures"] += steward_failures(onto)
                continue
            onto["precheck"] = grade52.approved_ontology_precheck()
            out = work_root / t / "dms_armed.json"
            ns = argparse.Namespace(
                source="fleet", all_flags=args.all_flags, timeout=args.timeout, json=str(out)
            )
            fs.armed([t], ns, packs={t: packs[t]}, landed=(Path(r["warehouse"]), {t: spaces[t]}))
            reset_ontology_data_store()
            _merge_armed([r], out, args)
    if pending:
        (work_root / "steward_pending.json").write_text(
            json.dumps({"approvals": pending}, indent=1) + "\n"
        )


# --------------------------------------------------------------------------- fleet report
def fleet_markdown(reports: list[dict[str, Any]]) -> str:
    head = (
        "| tenant | engine | status | tables | rows | extract rows/s | PK/FK read | "
        "FK infer P/R | roles ok | measures ok | questions (ans+trap) | dropped | "
        "gold fidelity | DMS ans/correct/WRONG/abstain/reconfirm |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for r in reports:
        ex = r.get("stages", {}).get("extract", {})
        ev = r.get("eval", {})
        k, fi, on, fid = (
            ev.get("keys"),
            ev.get("fk_inference"),
            ev.get("ontology"),
            ev.get("fidelity", {}),
        )
        pk_fk = (
            f"{k['pk_read']}/{k['pk_want']} . {k['fk_read']}/{k['fk_want']}"
            if k
            else "{} . {}".format(
                r.get("keys_read", {}).get("pk_tables", "-"),
                r.get("keys_read", {}).get("fk_columns", "-"),
            )
        )
        pa = r.get("stages", {}).get("pack", {})
        gold_s = (
            f"{fid['gold_ok']}/{fid['gold_ok'] + fid['mismatch']}" if "gold_ok" in fid else "n/a"
        )
        dms = ev.get("dms")
        dms_s = (
            "{answered}/{c}/{w}/{a}/{rc}".format(
                answered=dms["answered"],
                c=dms["tally"]["CORRECT"],
                w=dms["tally"]["WRONG"],
                a=dms["tally"]["ABSTAIN"],
                rc=dms["tally"]["RECONFIRM"],
            )
            + (f" (+{dms['tally']['ERROR']} error)" if dms["tally"].get("ERROR") else "")
            if dms
            else "SKIPPED"
        )
        lines.append(
            f"| {r['tenant']} | {r['engine']} | {'FAIL' if r['failures'] else r['status']} | "
            f"{ex.get('tables', '-')} | {ex.get('rows', '-')} | {ex.get('rows_per_s', '-')} | "
            f"{pk_fk} | "
            f"{(str(fi['precision']) + '/' + str(fi['recall'])) if fi else '-'} | "
            f"{(str(on['roles_ok']) + '/' + str(on['tables'])) if on else '-'} | "
            f"{(str(on['measures_ok']) + '/' + str(on['measure_tables'])) if on else '-'} | "
            f"{pa.get('answerable', 0)}+{pa.get('traps', 0)} | {len(pa.get('dropped', []))} | "
            f"{gold_s} | "
            f"{dms_s} |"
        )
    fails = [f"- {r['tenant']}: {'; '.join(r['failures'])}" for r in reports if r["failures"]]
    tot_q = sum(r.get("stages", {}).get("pack", {}).get("answerable", 0) for r in reports)
    return (
        f"# FDE fleet report\n\n{len(reports)} tenants, {tot_q} answerable generated questions.\n\n"
        + head
        + "\n".join(lines)
        + "\n\n"
        + ("## Failures\n\n" + "\n".join(fails) + "\n" if fails else "No failures.\n")
    )


# --------------------------------------------------------------------------- CLI
def cmd_factory(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scale = f"{args.rows_scale:g}".replace(".", "p")
    for t in tf.make_fleet(args.n, args.seed, rows_scale=args.rows_scale):
        # Seed and scale are in the name: the loader never overwrites, so an existing
        # database with this name holds exactly this tenant.
        dbname = f"fdet_s{args.seed}x{scale}_{t.key}"
        src: dict[str, Any] = {"kind": args.engine}
        if args.engine == "generated":
            pass
        elif args.engine == "sqlite":
            fs.load_dataset("sqlite", t.dataset, dbname, args)
            src["database"] = f"{dbname}.sqlite"
        else:
            fs.load_dataset(args.engine, t.dataset, dbname, args)
            src.update(
                host=args.host,
                database=dbname,
                user=args.read_user or args.user,
                password_env=args.read_password_env or args.password_env,
            )
            if args.port:
                src["port"] = args.port
        spec = TenantSpec(
            tenant=f"s{args.seed}x{scale}_{t.key}_{args.engine}",
            source=src,
            glossary=t.labels,
            generated={
                "seed": args.seed,
                "index": int(t.key[1:4]),
                "rows_scale": args.rows_scale,
                "domain": t.domain,
                "naming": t.naming,
            },
        )
        spec.dump(out / f"{spec.tenant}.yaml")
        print(
            f"[TENANT] {spec.tenant}: {t.domain}/{t.naming}, {len(t.dataset.tables)} tables, "
            f"{sum(len(tb.rows) for tb in t.dataset.tables)} rows"
        )
    return EXIT_PASS


def compile_config_error(args: argparse.Namespace) -> str | None:
    """Named refusal for a compile/steward flag set that cannot be honoured."""
    if getattr(args, "compile", False) and args.dms != "armed":
        return "--compile needs --dms armed"
    if (args.steward_approved_by or args.approval_file) and not args.compile:
        return "steward approval needs --compile"
    if args.steward_approved_by and not (args.approval_ref or "").strip():
        return "--steward-approved-by needs --approval-ref"
    if args.compile and not os.environ.get(args.onto_db_env, "").strip():
        return f"--compile needs {args.onto_db_env} (scratch ontology Postgres URL)"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    files = [str(p) for s in args.tenants for p in tenant_files(s)]
    if not files:
        print("[CONFIG] no tenant files")
        return EXIT_CONFIG
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    opts = {"max_questions": args.max_questions, "expect_keys": args.expect_keys}
    bad = compile_config_error(args)
    if bad:
        print(f"[CONFIG] {bad}")
        return EXIT_CONFIG
    if args.dms == "armed":
        # One in-process app at a time, so landing is serial.
        import grade52
        from dms_executor.demo_warehouse import ensure_demo_warehouse

        grade52.armed_preflight(grade52.armed_config())
        if args.compile:
            opts["tenant_warehouse"] = True
        else:
            args.warehouse = str(work / "fleet_warehouse.duckdb")
            ensure_demo_warehouse(Path(args.warehouse))
            opts["warehouse"] = args.warehouse
        args.workers = 1
    t0 = time.perf_counter()
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            reports = list(
                pool.map(run_tenant, files, [str(work)] * len(files), [opts] * len(files))
            )
    else:
        reports = [run_tenant(f, str(work), opts) for f in files]
    if args.dms == "armed":
        dms_armed(reports, work, args)
    wall = round(time.perf_counter() - t0, 1)
    for r in reports:
        (work / r["tenant"] / "report.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
        st = "FAIL" if r["failures"] else r["status"]
        print(
            f"[{st}] {r['tenant']}: " + (", ".join(r["failures"]) or json.dumps(r.get("timings")))
        )
    md = fleet_markdown(reports) + f"\nWall time {wall}s with {args.workers} worker(s).\n"
    (work / "FLEET.md").write_text(md)
    (work / "fleet.json").write_text(json.dumps(reports, indent=1, default=str) + "\n")
    print(md)
    return EXIT_FAIL if any(r["failures"] for r in reports) else EXIT_PASS


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("factory", help="generate seeded tenants and load them into an engine")
    f.add_argument("--n", type=int, default=8)
    f.add_argument("--seed", type=int, default=7)
    f.add_argument("--rows-scale", type=float, default=1.0)
    f.add_argument("--engine", choices=ENGINES, default="generated")
    f.add_argument("--host", default="127.0.0.1")
    f.add_argument("--port", type=int)
    f.add_argument("--user", default="")
    f.add_argument("--read-user", default="", help="login the tenant file uses to extract")
    f.add_argument(
        "--password-env",
        default="FDE_SOURCE_PASSWORD",
        help="env var holding the loader password (the scenario loaders read this one)",
    )
    f.add_argument(
        "--read-password-env",
        default="",
        help="env var the tenant file names for the read-only login (default: --password-env)",
    )
    f.add_argument("--grant-readonly", default="")
    f.add_argument("--out-dir", default=".", help="sqlite files go here")
    f.add_argument("--out", required=True, help="directory for tenant YAML files")
    r = sub.add_parser("run", help="run every stage for tenant files or directories")
    r.add_argument("--tenants", nargs="+", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--max-questions", type=int, default=25)
    r.add_argument("--expect-keys", action="store_true")
    r.add_argument("--dms", choices=["none", "armed"], default="none")
    r.add_argument("--all-flags", action="store_true")
    r.add_argument("--timeout", type=float, default=120.0)
    r.add_argument(
        "--compile",
        action="store_true",
        help="--dms armed: AI ontology compile per tenant (grade52's), then steward step",
    )
    r.add_argument(
        "--onto-db-env",
        default="FDE_ONTO_DATABASE_URL",
        help="env var holding the Postgres URL the ontology versions are stored in",
    )
    r.add_argument(
        "--steward-approved-by",
        default="",
        metavar="ACTOR_UUID",
        help="explicit human approval of every version this run compiles",
    )
    r.add_argument("--approval-ref", default="", help="steward reference recorded on approval")
    r.add_argument(
        "--approval-file",
        default="",
        help="recorded approvals: {approvals:[{tenant,version_id,actor,approval_ref}]}",
    )
    args = ap.parse_args(argv)
    if args.cmd == "factory":
        if args.engine not in ("generated", "sqlite") and not os.environ.get(args.password_env):
            print(f"[CONFIG] {args.password_env} must hold the loader password")
            return EXIT_CONFIG
        return cmd_factory(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
