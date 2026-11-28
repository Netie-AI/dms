"""Value-inclusion foreign-key candidates.

Moved from ``scripts/fde/fde_pipeline.py`` so derive and the FDE profile share
one check. A child column is a candidate when every non-null value sits in one
unique parent column (a single-column integer primary key), the child covers
at least ``COVERAGE_MIN`` of that parent, and the child is not wider than the
parent. Per child column only the best-covered parent is kept. An exact tie
keeps every tied parent. The list is a proposal. Nothing here is a join.

Each row is ``[child, column, parent, parent_column, coverage, fanout]``.
``coverage`` is child distinct / parent rows. ``fanout`` is child non-null
rows / child distinct (mean rows per key).
"""

from __future__ import annotations

import re
from typing import Any

# Same families the FDE profile treated as integer keys.
INTEGER = re.compile(
    r"^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|UBIGINT|UINTEGER)",
    re.IGNORECASE,
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A 1..4 line number fits inside a wider id column. Below this it is not a link.
COVERAGE_MIN = 0.2


def quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _q(ident: str) -> str:
    return quote_ident(ident)


def relation_sql(name: str) -> str:
    """One or two identifier parts. A bare name stays one quoted identifier."""
    parts = name.split(".")
    if 1 <= len(parts) <= 2 and all(_IDENT.fullmatch(part) for part in parts):
        return ".".join(_q(part) for part in parts)
    return _q(name)


def infer_fk_candidates(
    con: Any,
    prof: dict[str, Any],
    pk: dict[str, list[str]],
    *,
    coverage_min: float = COVERAGE_MIN,
) -> list[list[Any]]:
    """Integer columns contained in another table's single-column integer PK.

    ``coverage_min`` 0 keeps hits the coverage gate would drop, so a caller can
    show them to a model and still refuse them. The default matches the FDE
    profile: below ``COVERAGE_MIN`` never leaves this function.
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
                if not pn or ci["distinct"] < coverage_min * pn or ci["distinct"] > pn:
                    continue
                if ci.get("min") is not None and ci["min"] < prof[parent]["columns"][pcol].get(
                    "min", 0
                ):
                    continue
                rel_c = relation_sql(child)
                rel_p = relation_sql(parent)
                qc, qp = _q(col), _q(pcol)
                missing = con.execute(
                    f"SELECT COUNT(*) FROM {rel_c} c WHERE c.{qc} IS NOT NULL "
                    "AND NOT EXISTS "
                    f"(SELECT 1 FROM {rel_p} p WHERE p.{qp} = c.{qc})"
                ).fetchone()[0]
                if missing == 0:
                    distinct = int(ci["distinct"])
                    present = int(info["rows"]) - int(ci["nulls"])
                    fanout = round(present / distinct, 3) if distinct else None
                    out.append([child, col, parent, pcol, round(distinct / pn, 3), fanout])
    # Surrogate ids overlap. Per child column keep the parent it covers best.
    # An exact tie stays ambiguous (all kept).
    best: dict[tuple[str, str], float] = {}
    for cand in out:
        best[(cand[0], cand[1])] = max(best.get((cand[0], cand[1]), 0.0), cand[4])
    return [cand for cand in out if cand[4] == best[(cand[0], cand[1])]]
