"""ONTO-PROFILE-01 (dms#514): measure a file Space, propose keys and links.

An uploaded file declares no key, so a key here comes from measurement alone.
This module counts. It calls no model, ranks nothing by a column name, and
proposes no measure, grain or aggregate. What it returns is a proposal for a
steward: nothing in it is read by the ask path.

Privacy: the result carries counts, table names, column names and landed
types. No cell value is selected, returned or logged. A failed measurement is
reported by exception class only, because a DuckDB message can quote a value.

It counts what ``Ontology.verify`` counts: a NULL key is not a key, a NULL
child value is an optional reference and not an orphan, and values are
compared as landed (no cast, trim or case fold, rule 12).

Swap: none. Reads the serving cursor (``connect_serving``), never a write lease.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.bronze import list_bronze_tables, quote_ident
from dms_executor.demo_grants import canonical_space_id
from dms_executor.demo_warehouse import connect_serving, warehouse_path

PROFILER = "onto_profile/1"

#: Columns the bronze writer adds to every landed table. Not source data.
_WRITER_COLUMNS = frozenset({"_src", "_ingest_id"})

#: Bounds. Each one is stated in the result under ``bounds``.
MAX_COLUMNS_PER_TABLE = 60
MAX_PAIR_COLUMNS = 20
MAX_LINK_PAIRS = 2000
ORPHAN_OBSERVATION_MIN_MATCH = 0.5


def _int(row: tuple[Any, ...] | None, index: int = 0) -> int:
    return int(row[index] or 0) if row else 0


def _relation(label: str) -> str:
    """``bronze.orders`` or a bare ``main`` name as a quoted relation."""
    schema, _, name = label.rpartition(".")
    return f"{quote_ident(schema or 'main')}.{quote_ident(name)}"


def _columns(con: duckdb.DuckDBPyConnection, label: str) -> list[tuple[str, str]]:
    schema, _, name = label.rpartition(".")
    rows = con.execute(
        """
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_schema = ? AND table_name = ?
         ORDER BY ordinal_position
        """,
        [schema or "main", name],
    ).fetchall()
    return [(str(c), str(t)) for c, t in rows if str(c) not in _WRITER_COLUMNS]


def _column_counts(
    con: duckdb.DuckDBPyConnection, rel: str, columns: list[str]
) -> tuple[int, dict[str, tuple[int, int]]]:
    """Row count, then ``(null_count, distinct_count)`` per column. One scan."""
    parts = ["COUNT(*)"]
    for col in columns:
        q = quote_ident(col)
        parts.append(f"COUNT({q})")
        parts.append(f"COUNT(DISTINCT {q})")
    row = con.execute(f"SELECT {', '.join(parts)} FROM {rel}").fetchone()
    total = _int(row)
    out: dict[str, tuple[int, int]] = {}
    for i, col in enumerate(columns):
        out[col] = (total - _int(row, 1 + 2 * i), _int(row, 2 + 2 * i))
    return total, out


def _pair_distinct(con: duckdb.DuckDBPyConnection, rel: str, a: str, b: str) -> int:
    row = con.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {quote_ident(a)}, {quote_ident(b)} FROM {rel})"
    ).fetchone()
    return _int(row)


def _max_fanout(con: duckdb.DuckDBPyConnection, rel: str, col: str) -> int:
    """Most child rows that share one non-NULL value. A count of rows, not a value."""
    q = quote_ident(col)
    row = con.execute(
        f"SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM {rel} WHERE {q} IS NOT NULL GROUP BY {q})"
    ).fetchone()
    return _int(row)


def _containment(
    con: duckdb.DuckDBPyConnection, child_rel: str, child_col: str, parent_rel: str, key_col: str
) -> tuple[int, int]:
    """``(orphan_rows, matched_parent_keys)``. Values compared as landed.

    The parent column is a measured key, so the join cannot fan out. NULL
    child values are left out: an optional reference is not an orphan.
    """
    c, k = quote_ident(child_col), quote_ident(key_col)
    row = con.execute(
        f"""
        SELECT COUNT(*) FILTER (WHERE p.{k} IS NULL),
               COUNT(DISTINCT c.{c}) FILTER (WHERE p.{k} IS NOT NULL)
          FROM {child_rel} AS c
          LEFT JOIN {parent_rel} AS p ON c.{c} = p.{k}
         WHERE c.{c} IS NOT NULL
        """
    ).fetchone()
    return _int(row), _int(row, 1)


def _measure_table(
    con: duckdb.DuckDBPyConnection, entry: dict[str, Any], bounds: dict[str, Any]
) -> dict[str, Any]:
    label = str(entry["table"])
    rel = _relation(label)
    all_columns = _columns(con, label)
    measured = all_columns[:MAX_COLUMNS_PER_TABLE]
    if len(all_columns) > len(measured):
        bounds["columns_not_measured"][label] = [c for c, _ in all_columns[len(measured) :]]
    types = dict(measured)
    names = [c for c, _ in measured]
    total, counts = _column_counts(con, rel, names)
    table: dict[str, Any] = {
        "table": label,
        "rel": rel,
        "row_count": total,
        "types": types,
        "counts": counts,
        "truncated": bool(entry.get("truncated")),
        "keys": [],
        "searched": {"single_columns": len(names), "pairs": 0},
    }
    if table["truncated"]:
        bounds["truncated_tables"].append(label)
    if total == 0:
        table["unkeyed_reason"] = "empty_table"
        return table
    for col in names:
        nulls, distinct = counts[col]
        if nulls == 0 and distinct == total:
            table["keys"].append({"columns": [col], "distinct_count": distinct, "null_count": 0})
    if table["keys"]:
        return table
    # No single column identifies a row: try pairs of NULL-free columns.
    eligible = [c for c in names if counts[c][0] == 0]
    pair_columns = eligible[:MAX_PAIR_COLUMNS]
    searched = 0
    for a, b in combinations(pair_columns, 2):
        searched += 1
        distinct = _pair_distinct(con, rel, a, b)
        if distinct == total:
            table["keys"].append({"columns": [a, b], "distinct_count": distinct, "null_count": 0})
    table["searched"]["pairs"] = searched
    bounds["pair_search"]["tables"][label] = {
        "pairs_searched": searched,
        "columns_with_nulls_not_paired": [c for c in names if counts[c][0] > 0],
        "columns_over_pair_cap_not_paired": eligible[MAX_PAIR_COLUMNS:],
    }
    if not table["keys"]:
        table["unkeyed_reason"] = "no_unique_column_or_pair"
    return table


def _link_search(
    con: duckdb.DuckDBPyConnection, tables: list[dict[str, Any]], bounds: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    links: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    stats = bounds["link_search"]
    fanout: dict[tuple[str, str], int] = {}
    for parent in tables:
        for key in parent["keys"]:
            if len(key["columns"]) != 1:
                stats["pair_keys_not_searched_as_parents"] += 1
                continue
            key_col = key["columns"][0]
            key_type = parent["types"][key_col]
            for child in tables:
                if child is parent:
                    continue
                own_keys = {tuple(k["columns"]) for k in child["keys"]}
                for col, col_type in child["types"].items():
                    if col_type != key_type:
                        stats["pairs_type_incompatible"] += 1
                        continue
                    nulls, distinct = child["counts"][col]
                    if distinct == 0:
                        stats["pairs_child_all_null"] += 1
                        continue
                    if stats["pairs_measured"] >= MAX_LINK_PAIRS:
                        stats["pairs_over_cap_not_measured"] += 1
                        continue
                    stats["pairs_measured"] += 1
                    try:
                        orphan_rows, matched = _containment(
                            con, child["rel"], col, parent["rel"], key_col
                        )
                    except duckdb.Error as exc:
                        bounds["unmeasured"].append(
                            {
                                "child_table": child["table"],
                                "child_columns": [col],
                                "parent_table": parent["table"],
                                "parent_columns": [key_col],
                                "error": type(exc).__name__,
                            }
                        )
                        continue
                    if orphan_rows > 0 and matched / distinct < ORPHAN_OBSERVATION_MIN_MATCH:
                        stats["pairs_below_orphan_threshold"] += 1
                        continue
                    if (child["table"], col) not in fanout:
                        fanout[(child["table"], col)] = _max_fanout(con, child["rel"], col)
                    evidence = {
                        "child_rows": child["row_count"],
                        "child_null_rows": nulls,
                        "child_distinct": distinct,
                        "parent_distinct": key["distinct_count"],
                        "matched_parent_keys": matched,
                        "orphan_rows": orphan_rows,
                        "orphan_keys": distinct - matched,
                        "max_fanout": fanout[(child["table"], col)],
                        "child_is_own_key": (col,) in own_keys,
                        "child_has_key": bool(child["keys"]),
                        "column_types": {"child": col_type, "parent": key_type},
                        "truncated": child["truncated"] or parent["truncated"],
                    }
                    row = {
                        "child_table": child["table"],
                        "child_columns": [col],
                        "parent_table": parent["table"],
                        "parent_columns": [key_col],
                        "evidence": evidence,
                    }
                    if orphan_rows > 0:
                        result = "orphans"
                        observations.append({**row, "reason": result})
                    elif not child["keys"]:
                        result = "child_has_no_key"
                        observations.append({**row, "reason": result})
                    else:
                        result = "link"
                        links.append(row)
                    key.setdefault("referenced_by", []).append(
                        {
                            "table": child["table"],
                            "columns": [col],
                            "result": result,
                            "orphan_rows": orphan_rows,
                            "matched_parent_keys": matched,
                        }
                    )
    return links, observations


def profile_space(space_id: str, *, path: Path | None = None) -> dict[str, Any]:
    """Measure the file-landed tables of one Space. Counts, names and types only.

    ``keys``: a column, or a pair when no single column qualifies, with zero
    NULLs and as many distinct values as rows. ``links``: a column of a keyed
    table whose every non-NULL value exists in a single-column key of another
    table. ``observations``: the same search where it failed only on orphans
    or because the child table has no key. ``bounds`` states every limit.
    """
    space = canonical_space_id(space_id)
    db = path or warehouse_path()
    file_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in list_bronze_tables(path=db, space_id=space):
        if row.get("source_kind") == "sql":
            skipped.append({"table": str(row["table"]), "reason": "sql_source"})
        else:
            file_rows.append(row)
    bounds: dict[str, Any] = {
        "max_columns_per_table": MAX_COLUMNS_PER_TABLE,
        "columns_not_measured": {},
        "pair_search": {
            "when": "only for a table where no single column is a key",
            "max_columns": MAX_PAIR_COLUMNS,
            "wider_than_pairs": "not searched",
            "tables": {},
        },
        "link_search": {
            "shape": "one child column to one single-column key of another table",
            "type_rule": "identical landed type, compared as landed, no cast or trim",
            "self_references": "not searched",
            "max_pairs": MAX_LINK_PAIRS,
            "pairs_measured": 0,
            "pairs_type_incompatible": 0,
            "pairs_child_all_null": 0,
            "pairs_below_orphan_threshold": 0,
            "pairs_over_cap_not_measured": 0,
            "pair_keys_not_searched_as_parents": 0,
        },
        "orphan_observation_min_match_ratio": ORPHAN_OBSERVATION_MIN_MATCH,
        "truncated_tables": [],
        "unmeasured": [],
    }
    out: dict[str, Any] = {
        "space_id": space,
        "profiler": PROFILER,
        "file_tables": len(file_rows),
        "tables": [],
        "skipped": skipped,
        "keys": [],
        "links": [],
        "observations": [],
        "unkeyed_tables": [],
        "bounds": bounds,
    }
    if not file_rows:
        return out
    con = connect_serving(db)
    try:
        tables: list[dict[str, Any]] = []
        for entry in file_rows:
            try:
                tables.append(_measure_table(con, entry, bounds))
            except duckdb.Error as exc:
                bounds["unmeasured"].append(
                    {"table": str(entry["table"]), "error": type(exc).__name__}
                )
        links, observations = _link_search(con, tables, bounds)
    finally:
        con.close()
    for table in tables:
        out["tables"].append(
            {
                "table": table["table"],
                "row_count": table["row_count"],
                "columns": [{"name": c, "type": t} for c, t in table["types"].items()],
                "truncated": table["truncated"],
            }
        )
        for key in table["keys"]:
            out["keys"].append(
                {
                    "table": table["table"],
                    "columns": list(key["columns"]),
                    "evidence": {
                        "row_count": table["row_count"],
                        "distinct_count": key["distinct_count"],
                        "null_count": key["null_count"],
                        "column_types": {c: table["types"][c] for c in key["columns"]},
                        "referenced_by": list(key.get("referenced_by", [])),
                        "truncated": table["truncated"],
                    },
                }
            )
        if not table["keys"]:
            out["unkeyed_tables"].append(
                {
                    "table": table["table"],
                    "row_count": table["row_count"],
                    "reason": table["unkeyed_reason"],
                    "searched": dict(table["searched"]),
                }
            )
    out["links"] = links
    out["observations"] = observations
    return out
