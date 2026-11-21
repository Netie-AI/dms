"""FDE-ONBOARD-01 (dms#541) stages 2 to 4: measure a file Space, propose types
and value groups, write a masked pack and prove nothing leaked.

Privacy rule: the pack directory is what may leave the machine. Every file in it
is built from names, landed types, counts, shapes and PII tokens. No cell value
is selected into it. Raw values (value groups, samples before masking) exist
only in process memory and, for value groups, in the local item store the caller
owns. A failed measurement is reported by exception class only, because a
DuckDB message can quote a value.

No model, no network, no word list (rule 15): value groups come from structural
folds of the values themselves. The serving cursor is the only reader
(``connect_serving``); ``duckdb`` stays in this package (rule 7).

Swap: none.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import duckdb
from dms_core.pii import classify_column, is_mask_token, mask_payload

from dms_executor.bronze import list_bronze_tables, quote_ident
from dms_executor.demo_grants import canonical_space_id
from dms_executor.demo_warehouse import connect_serving, warehouse_path
from dms_executor.onto_profile import _column_counts, _columns, _relation

PACK_FORMAT = "fde_pack/1"
SAMPLE_ROWS = 20
MIN_LEAK_LEN = 4
SHORT_INT_MAX_DIGITS = 6
MAX_GROUP_DISTINCT = 200
SHAPE_MAX = 64
#: Distinct values read per column to build shapes. A bound, not a sample of values returned.
SHAPE_DISTINCT_CAP = 50_000
PII_SAMPLE_VALUES = 50
SAMPLES_MASK = "pii_token_then_shape/1"

_PUNCT = frozenset("-_./:,@+()#&%'\"")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
)
_NUMERIC_BASES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
    }
)
_FLOAT_BASES = frozenset({"FLOAT", "REAL", "DOUBLE"})
_EXACT_BASES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "DECIMAL",
        "DATE",
        "BOOLEAN",
        "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE",
    }
)
_PLAIN_DECIMAL = r"-?[0-9]+(\.[0-9]+)?"
_EXPONENT = r"-?[0-9]+(\.[0-9]*)?[eE][+-]?[0-9]+"

#: Every numeric key the pack, the #514 profile output and the manifest use. A
#: number under any other key is refused by ``LeakChecker.check_pack``.
ALLOWED_NUMBER_KEYS = frozenset(
    {
        # column stats
        "row_count",
        "null_count",
        "distinct_count",
        "null_rate",
        "min_exp",
        "max_exp",
        "observed_scale",
        "whole_digits",
        "rows",
        "count",
        "stage",
        "bytes",
        "max_rows",
        # onto_profile output
        "file_tables",
        "child_rows",
        "child_null_rows",
        "child_distinct",
        "parent_distinct",
        "matched_parent_keys",
        "orphan_rows",
        "orphan_keys",
        "max_fanout",
        "max_columns_per_table",
        "max_columns",
        "max_pairs",
        "pairs_measured",
        "pairs_type_incompatible",
        "pairs_child_all_null",
        "pairs_below_orphan_threshold",
        "pairs_over_cap_not_measured",
        "pair_keys_not_searched_as_parents",
        "orphan_observation_min_match_ratio",
        "pairs_searched",
        "single_columns",
        "pairs",
        # manifest
        "min_length",
        "short_integer_max_digits",
        "columns_checked",
        "distinct_values_checked",
        "proposed",
        "approved",
    }
)
#: ``min`` and ``max`` are numbers only as the length range of a text column.
_ALLOWED_PATH_KEYS = frozenset({("length", "min"), ("length", "max")})
#: A dict under one of these keys is a table of counts; any key under it is a count.
_COUNT_CONTAINERS = frozenset({"counts"})

_EXACT_DECIMAL_MAX_SCALE = 18
_EXACT_DECIMAL_MAX_PRECISION = 38
_BIGINT_MAX_DIGITS = 18


def _int(row: tuple[Any, ...] | None, index: int = 0) -> int:
    return int(row[index] or 0) if row else 0


def _base(landed: str) -> str:
    return landed.split("(")[0].strip().upper()


# --- tables ------------------------------------------------------------------------------


def space_file_tables(space_id: str, *, path: Path | None = None) -> list[str]:
    """``bronze.<t>`` labels of the Space's file-landed tables (SQL sources skipped)."""
    space = canonical_space_id(space_id)
    db = path or warehouse_path()
    return [
        str(row["table"])
        for row in list_bronze_tables(path=db, space_id=space)
        if row.get("source_kind") != "sql"
    ]


# --- shapes ------------------------------------------------------------------------------


def shape(value: object) -> str:
    """``shape:`` plus run-length classes. Never contains an alphanumeric run of the input."""
    runs: list[tuple[str, int]] = []
    for ch in str(value):
        if ch in _PUNCT:
            cls = ch
        elif ch in "0123456789":
            cls = "D"
        elif ch.isspace():
            cls = "S"
        elif ch.isalpha() and ch.isupper():
            cls = "U"
        elif ch.isalpha() and ch.islower():
            cls = "L"
        else:
            cls = "?"
        if runs and runs[-1][0] == cls:
            runs[-1] = (cls, runs[-1][1] + 1)
        else:
            runs.append((cls, 1))
    out = ""
    truncated = False
    for cls, n in runs:
        piece = cls if (cls in _PUNCT and n == 1) else f"{cls}{n}"
        if len(out) + len(piece) > SHAPE_MAX:
            truncated = True
            break
        out += piece
    return "shape:" + out + ("+" if truncated else "")


# --- names -------------------------------------------------------------------------------


class NameMask:
    """Replaces a table or column name the PII scanner changes with ``fde_name_NN``."""

    def __init__(self, tables: Sequence[tuple[str, Sequence[str]]] = ()) -> None:
        self._token_of: dict[str, str] = {}
        self._raw_of: dict[str, str] = {}
        for table, columns in tables:
            self.out(table.rpartition(".")[2])
            for column in columns:
                self.out(column)

    def out(self, name: str) -> str:
        known = self._token_of.get(name)
        if known is not None:
            return known
        if mask_payload(text=name)["text"] == name:
            return name
        token = f"fde_name_{len(self._token_of) + 1:02d}"
        self._token_of[name] = token
        self._raw_of[token] = name
        return token

    def out_table(self, label: str) -> str:
        schema, dot, bare = label.rpartition(".")
        return f"{schema}{dot}{self.out(bare)}"

    def text(self, text: str) -> str:
        """``text`` with every masked raw name replaced by its token (longest first)."""
        for raw in sorted(self._token_of, key=len, reverse=True):
            if raw in text:
                text = text.replace(raw, self._token_of[raw])
        return text

    def back(self, text: str) -> str:
        for token in sorted(self._raw_of, key=len, reverse=True):
            text = text.replace(token, self._raw_of[token])
        return text

    def tokens(self) -> dict[str, str]:
        return dict(self._raw_of)


# --- column stats ------------------------------------------------------------------------


def _error(exc: Exception) -> dict[str, str]:
    return {"error": type(exc).__name__}


def _magnitude(con: duckdb.DuckDBPyConnection, rel: str, q: str) -> dict[str, Any]:
    row = con.execute(
        f"SELECT MIN(abs({q})) FILTER (WHERE {q} <> 0), MAX(abs({q})), BOOL_OR({q} < 0) "
        f"FROM {rel} WHERE {q} IS NOT NULL"
    ).fetchone()

    def _exp(v: Any) -> int | None:
        if v is None:
            return None
        f = float(v)
        return math.floor(math.log10(f)) if math.isfinite(f) and f > 0 else None

    return {
        "min_exp": _exp(row[0]) if row else None,
        "max_exp": _exp(row[1]) if row else None,
        "negative": bool(row[2]) if row else False,
    }


def _decimal(con: duckdb.DuckDBPyConnection, rel: str, q: str, *, double: bool) -> dict[str, Any]:
    text = f"trim(CAST({q} AS VARCHAR))" if double else f"trim({q})"
    row = con.execute(
        f"SELECT COUNT(*) FILTER (WHERE {q} IS NOT NULL), "
        f"COUNT(*) FILTER (WHERE {q} IS NOT NULL "
        f"AND NOT regexp_full_match({text}, '{_PLAIN_DECIMAL}')), "
        f"COUNT(*) FILTER (WHERE {q} IS NOT NULL AND regexp_full_match({text}, '{_EXPONENT}')), "
        f"MAX(CASE WHEN regexp_full_match({text}, '{_PLAIN_DECIMAL}') "
        f"THEN length(split_part({text}, '.', 2)) END), "
        f"MAX(CASE WHEN regexp_full_match({text}, '{_PLAIN_DECIMAL}') "
        f"THEN length(ltrim(split_part({text}, '.', 1), '-')) END) "
        f"FROM {rel}"
    ).fetchone()
    present, bad, exponent = _int(row, 0), _int(row, 1), _int(row, 2)
    scale, whole = _int(row, 3), _int(row, 4)
    out: dict[str, Any] = {
        "lossless": False,
        "observed_scale": None,
        "whole_digits": None,
        "basis": "double_text" if double else "text",
        "reason": None,
    }
    if present == 0:
        out["reason"] = "no_values"
    elif exponent:
        out["reason"] = "exponent_notation"
    elif bad:
        out["reason"] = "not_plain_decimal"
    else:
        out["observed_scale"], out["whole_digits"] = scale, whole
        if scale > _EXACT_DECIMAL_MAX_SCALE:
            out["reason"] = "scale_over_18"
        elif whole + scale > _EXACT_DECIMAL_MAX_PRECISION:
            out["reason"] = "precision_over_38"
        else:
            out["lossless"] = True
    return out


def _date(con: duckdb.DuckDBPyConnection, rel: str, q: str) -> dict[str, Any]:
    parts = ["COUNT(*) FILTER (WHERE " + q + " IS NOT NULL)"]
    parts += [
        f"COUNT(*) FILTER (WHERE {q} IS NOT NULL AND try_strptime(trim({q}), ?) IS NULL)"
        for _ in _DATE_FORMATS
    ]
    row = con.execute(f"SELECT {', '.join(parts)} FROM {rel}", list(_DATE_FORMATS)).fetchone()
    present = _int(row, 0)
    formats = (
        [fmt for i, fmt in enumerate(_DATE_FORMATS) if _int(row, 1 + i) == 0] if present else []
    )
    return {"formats": formats, "lossless": bool(formats)}


def _shapes(con: duckdb.DuckDBPyConnection, rel: str, q: str) -> list[dict[str, Any]]:
    rows = con.execute(
        f"SELECT CAST({q} AS VARCHAR), COUNT(*) FROM {rel} WHERE {q} IS NOT NULL "
        f"GROUP BY 1 LIMIT {SHAPE_DISTINCT_CAP}"
    ).fetchall()
    counts: dict[str, int] = {}
    for value, n in rows:
        key = shape(value)
        counts[key] = counts.get(key, 0) + int(n)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return [{"shape": s, "rows": n} for s, n in top]


def _pii_kind(
    con: duckdb.DuckDBPyConnection, rel: str, q: str, column: str, table: str
) -> str | None:
    sample = [
        r[0]
        for r in con.execute(
            f"SELECT CAST({q} AS VARCHAR) FROM {rel} "
            f"WHERE {q} IS NOT NULL LIMIT {PII_SAMPLE_VALUES}"
        ).fetchall()
    ]
    return classify_column(column, sample, table=table)


def _measure(
    con: duckdb.DuckDBPyConnection,
    rel: str,
    table: str,
    column: str,
    landed: str,
    counts: tuple[int, int],
    total: int,
) -> dict[str, Any]:
    q = quote_ident(column)
    nulls, distinct = counts
    base = _base(landed)
    stats: dict[str, Any] = {
        "type": landed,
        "row_count": total,
        "null_count": nulls,
        "distinct_count": distinct,
        "null_rate": round(nulls / total, 4) if total else 0.0,
    }

    def attempt(key: str, fn: Any) -> None:
        try:
            stats[key] = fn()
        except Exception as exc:  # noqa: BLE001 - class name only, never the message
            stats[key] = _error(exc)

    if base == "VARCHAR":
        attempt(
            "length",
            lambda: dict(
                zip(
                    ("min", "max"),
                    (
                        int(v or 0)
                        for v in con.execute(
                            f"SELECT MIN(length({q})), MAX(length({q})) FROM {rel}"
                        ).fetchone()  # type: ignore[union-attr]
                    ),
                    strict=True,
                )
            ),
        )
        attempt("decimal", lambda: _decimal(con, rel, q, double=False))
        attempt(
            "leading_zero",
            lambda: (
                _int(
                    con.execute(
                        f"SELECT COUNT(*) FROM {rel} WHERE regexp_full_match(trim({q}), '0[0-9]+')"
                    ).fetchone()
                )
                > 0
            ),
        )
        attempt("date", lambda: _date(con, rel, q))
    elif base in _NUMERIC_BASES:
        attempt("magnitude", lambda: _magnitude(con, rel, q))
        if base in _FLOAT_BASES:
            attempt("decimal", lambda: _decimal(con, rel, q, double=True))
    attempt("shapes", lambda: _shapes(con, rel, q))
    attempt("pii_kind", lambda: _pii_kind(con, rel, q, column, table.rpartition(".")[2]))
    return stats


def column_stats(
    space_id: str, *, path: Path | None = None
) -> dict[str, dict[str, dict[str, Any]]]:
    """table -> column -> measured stats. Counts, lengths, magnitudes, shapes. No value."""
    db = path or warehouse_path()
    labels = space_file_tables(space_id, path=db)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not labels:
        return out
    con = connect_serving(db)
    try:
        for label in labels:
            rel = _relation(label)
            columns = _columns(con, label)
            table_stats: dict[str, dict[str, Any]] = {}
            out[label] = table_stats
            try:
                total, counts = _column_counts(con, rel, [c for c, _ in columns])
            except Exception as exc:  # noqa: BLE001
                for column, landed in columns:
                    table_stats[column] = {"type": landed, **_error(exc)}
                continue
            for column, landed in columns:
                try:
                    table_stats[column] = _measure(
                        con, rel, label, column, landed, counts[column], total
                    )
                except Exception as exc:  # noqa: BLE001
                    table_stats[column] = {"type": landed, **_error(exc)}
    finally:
        con.close()
    return out


# --- stage 3: type proposals --------------------------------------------------------------


def _usable(block: Any) -> dict[str, Any] | None:
    return block if isinstance(block, dict) and "error" not in block else None


def _type_choice(st: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """``(proposed_type, reason, extra payload)`` or None when nothing is proposed."""
    landed = str(st.get("type", ""))
    base = _base(landed)
    if "error" in st or base in _EXACT_BASES or base.startswith("TIMESTAMP"):
        return None
    is_text = base == "VARCHAR"
    if not is_text and base not in _FLOAT_BASES:
        return None
    dec = _usable(st.get("decimal"))
    if dec is not None:
        if dec.get("lossless"):
            scale, whole = int(dec["observed_scale"]), int(dec["whole_digits"])
            if is_text and st.get("leading_zero") is True:
                return "code", "leading_zero", {}
            if is_text and scale == 0 and whole <= _BIGINT_MAX_DIGITS:
                return "BIGINT", "lossless_integer", {}
            return f"DECIMAL(38,{scale})", "lossless_decimal", {}
        reason = str(dec.get("reason"))
        if not is_text or reason in {"exponent_notation", "scale_over_18", "precision_over_38"}:
            return landed, reason, {}
    if not is_text:
        return None
    date = _usable(st.get("date"))
    if date is not None and date.get("formats"):
        formats = list(date["formats"])
        if len(formats) == 1:
            return "DATE", "single_format", {"format": formats[0]}
        return "DATE", "ambiguous_date_format", {"formats": formats}
    if st.get("pii_kind") is not None:
        return None
    rows, nulls, distinct = (
        int(st.get(k) or 0) for k in ("row_count", "null_count", "distinct_count")
    )
    if distinct and distinct <= 50 and distinct * 2 <= rows:
        return "category", "low_cardinality", {}
    shapes = st.get("shapes")
    present = rows - nulls
    if (
        isinstance(shapes, list)
        and shapes
        and present > 0
        and distinct > 1
        and int(shapes[0]["rows"]) == present
    ):
        return "code", "single_shape", {}
    return None


def type_items(
    stats: dict[str, dict[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """``("column_type", payload, evidence)`` per column with a measured proposal."""
    items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for table in sorted(stats):
        for column in sorted(stats[table]):
            st = stats[table][column]
            choice = _type_choice(st)
            if choice is None:
                continue
            proposed, reason, extra = choice
            payload = {
                "name": f"column_type:{table}.{column}",
                "table": table,
                "column": column,
                "landed_type": st.get("type"),
                "proposed_type": proposed,
                "reason": reason,
                **extra,
            }
            dec = _usable(st.get("decimal")) or {}
            evidence = {
                "row_count": st.get("row_count"),
                "null_count": st.get("null_count"),
                "distinct_count": st.get("distinct_count"),
                "observed_scale": dec.get("observed_scale"),
                "whole_digits": dec.get("whole_digits"),
            }
            items.append(("column_type", payload, evidence))
    return items


# --- stage 3: value groups (LOCAL, raw values) ---------------------------------------------


def _case_space(v: str) -> str:
    return " ".join(v.split()).casefold()


def _punct(v: str) -> str:
    return "".join(ch for ch in _case_space(v) if ch.isalnum())


_SEPARATORS = "-_ /."
_WORD_SPLIT = re.compile(r"[\s\-_/.]+")


def _initials(v: str) -> str:
    words = [w for w in _WORD_SPLIT.split(_case_space(v)) if w]
    if len(words) < 2:
        return ""
    return "".join(w[0] for w in words if w[0].isalnum())


def _link_values(values: list[str]) -> tuple[dict[int, int], list[tuple[str, int, int]]]:
    """Union-find over value indexes. Returns parent map and ``(fold, a, b)`` edges."""
    parent = {i: i for i in range(len(values))}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges: list[tuple[str, int, int]] = []

    def link(fold: str, a: int, b: int) -> None:
        if a != b:
            edges.append((fold, a, b))
            parent[find(a)] = find(b)

    def bucket(fold: str, keys: list[str]) -> None:
        seen: dict[str, int] = {}
        for i, key in enumerate(keys):
            if not key:
                continue
            if key in seen:
                link(fold, seen[key], i)
            else:
                seen[key] = i

    bucket("case_space", [_case_space(v) for v in values])
    punct_keys = [_punct(v) for v in values]
    bucket("punct", punct_keys)

    # affix: a leading or trailing segment, ending or starting at a separator, that
    # is the same segment on at least two distinct values.
    folded = [_case_space(v) for v in values]
    prefix_count: dict[str, int] = {}
    suffix_count: dict[str, int] = {}
    for f in set(folded):
        for i, ch in enumerate(f):
            if ch in _SEPARATORS:
                if i + 1 < len(f):
                    prefix_count[f[: i + 1]] = prefix_count.get(f[: i + 1], 0) + 1
                if i > 0:
                    suffix_count[f[i:]] = suffix_count.get(f[i:], 0) + 1
    by_punct: dict[str, int] = {}
    for i, key in enumerate(punct_keys):
        by_punct.setdefault(key, i)
    for i, f in enumerate(folded):
        stripped: set[str] = set()
        for k, ch in enumerate(f):
            if ch not in _SEPARATORS:
                continue
            if k + 1 < len(f) and prefix_count.get(f[: k + 1], 0) >= 2:
                stripped.add(_punct(f[k + 1 :]))
            if k > 0 and suffix_count.get(f[k:], 0) >= 2:
                stripped.add(_punct(f[:k]))
        for key in sorted(stripped):
            j = by_punct.get(key)
            if key and j is not None and j != i and punct_keys[j] != punct_keys[i]:
                link("affix", i, j)

    # initials: a 2-5 letter value that is the initials of a multi-word value.
    initials_of: dict[str, int] = {}
    for i, v in enumerate(values):
        ini = _initials(v)
        if 2 <= len(ini) <= 5 and ini.isalpha():
            initials_of.setdefault(ini, i)
    for i, key in enumerate(punct_keys):
        j = initials_of.get(key)
        if j is not None and 2 <= len(key) <= 5 and key.isalpha() and j != i:
            link("initials", i, j)

    return {i: find(i) for i in range(len(values))}, edges


def value_groups(
    space_id: str,
    stats: dict[str, dict[str, dict[str, Any]]],
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Groups of distinct values that are one value written two ways. RAW values: local only."""
    db = path or warehouse_path()
    candidates: list[tuple[str, str]] = []
    for table in sorted(stats):
        for column in sorted(stats[table]):
            st = stats[table][column]
            distinct = int(st.get("distinct_count") or 0)
            if (
                "error" not in st
                and _base(str(st.get("type", ""))) == "VARCHAR"
                and st.get("pii_kind") is None
                and 2 <= distinct <= MAX_GROUP_DISTINCT
            ):
                candidates.append((table, column))
    groups: list[dict[str, Any]] = []
    if not candidates:
        return groups
    con = connect_serving(db)
    try:
        for table, column in candidates:
            q = quote_ident(column)
            fetched = con.execute(
                f"SELECT {q}, COUNT(*) FROM {_relation(table)} WHERE {q} IS NOT NULL GROUP BY {q}"
            ).fetchall()
            counted = sorted(((str(v), int(n)) for v, n in fetched), key=lambda x: x[0])
            values = [v for v, _ in counted]
            roots, edges = _link_values(values)
            members_of: dict[int, list[int]] = {}
            for i, root in roots.items():
                members_of.setdefault(root, []).append(i)
            folds_of: dict[int, set[str]] = {}
            for fold, a, _b in edges:
                folds_of.setdefault(roots[a], set()).add(fold)
            ordered = sorted(
                (m for m in members_of.values() if len(m) >= 2),
                key=lambda m: min(values[i] for i in m),
            )
            for member_idx in ordered:
                rows_sorted = sorted(
                    ((values[i], counted[i][1]) for i in member_idx), key=lambda x: (-x[1], x[0])
                )
                groups.append(
                    {
                        "group_id": f"g{len(groups) + 1:03d}",
                        "table": table,
                        "column": column,
                        "folds": sorted(folds_of.get(roots[member_idx[0]], set())),
                        "members": [
                            {"value": v, "rows": n, "mask": f"V{k:02d}", "shape": shape(v)}
                            for k, (v, n) in enumerate(rows_sorted, start=1)
                        ],
                        "target": rows_sorted[0][0],
                    }
                )
    finally:
        con.close()
    return groups


def value_items(
    groups: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for g in groups:
        target = g["target"]
        payload = {
            "name": f"value_map:{g['table']}.{g['column']}:{g['group_id']}",
            "table": g["table"],
            "column": g["column"],
            "group_id": g["group_id"],
            "folds": list(g["folds"]),
            "map": {m["value"]: target for m in g["members"] if m["value"] != target},
            "origin": "measured",
        }
        evidence = {"rows": {m["mask"]: m["rows"] for m in g["members"]}}
        items.append(("value_map", payload, evidence))
    return items


# --- stage 4: masked samples and the pack --------------------------------------------------


def masked_samples(
    space_id: str,
    names: NameMask,
    *,
    rows: int = SAMPLE_ROWS,
    path: Path | None = None,
) -> dict[str, Any]:
    """Up to ``rows`` rows per table. Every cell is None, a PII token, or a shape."""
    db = path or warehouse_path()
    labels = space_file_tables(space_id, path=db)
    tables: dict[str, Any] = {}
    if labels:
        con = connect_serving(db)
        try:
            for label in labels:
                columns = [c for c, _ in _columns(con, label)]
                if not columns:
                    continue
                select = ", ".join(quote_ident(c) for c in columns)
                fetched = con.execute(
                    f"SELECT {select} FROM {_relation(label)} LIMIT {int(rows)}"
                ).fetchall()
                dict_rows = [dict(zip(columns, r, strict=True)) for r in fetched]
                masked = mask_payload(rows=dict_rows)["rows"]
                cells = [
                    [
                        None
                        if row[c] is None
                        else (row[c] if is_mask_token(row[c]) else shape(row[c]))
                        for c in columns
                    ]
                    for row in masked
                ]
                tables[names.out_table(label)] = {
                    "columns": [names.out(c) for c in columns],
                    "rows": cells,
                }
        finally:
            con.close()
    return {"max_rows": int(rows), "mask": SAMPLES_MASK, "tables": tables}


def write_pack_file(out_dir: Path, rel: str, obj: Any) -> None:
    target = Path(out_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def pack_files(out_dir: Path) -> dict[str, dict[str, Any]]:
    root = Path(out_dir)
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel != "manifest.json":
            data = p.read_bytes()
            out[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return out


# --- leak check ----------------------------------------------------------------------------


def _atoms(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _atoms(v)
    elif isinstance(node, list):
        for v in node:
            yield from _atoms(v)
    elif isinstance(node, str):
        yield node


def _numbers(node: Any, key: str, chain: tuple[str, ...]) -> Iterator[tuple[str, tuple[str, ...]]]:
    """``(nearest dict key, ancestor chain)`` of every JSON number."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _numbers(v, str(k), (*chain, str(k)))
    elif isinstance(node, list):
        for v in node:
            yield from _numbers(v, key, chain)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield key, chain


def _number_allowed(key: str, chain: tuple[str, ...]) -> bool:
    if key in ALLOWED_NUMBER_KEYS:
        return True
    parent = chain[-2] if len(chain) >= 2 else ""
    if (parent, key) in _ALLOWED_PATH_KEYS:
        return True
    return any(c in _COUNT_CONTAINERS for c in chain[:-1])


class LeakChecker:
    """Counts source values that appear in text. Returns counts only, never a value."""

    def __init__(self, space_id: str, *, path: Path | None = None) -> None:
        self._db = path or warehouse_path()
        self._tables = space_file_tables(space_id, path=self._db)

    def _values_sql(self, con: duckdb.DuckDBPyConnection) -> tuple[str, int]:
        selects: list[str] = []
        columns = 0
        for label in self._tables:
            for column, _t in _columns(con, label):
                columns += 1
                q = quote_ident(column)
                selects.append(
                    f"SELECT DISTINCT lower(trim(CAST({q} AS VARCHAR))) AS v "
                    f"FROM {_relation(label)} WHERE {q} IS NOT NULL"
                )
        if not selects:
            return "SELECT CAST(NULL AS VARCHAR) AS v WHERE false", 0
        union = " UNION ".join(selects)
        sql = (
            f"SELECT v FROM ({union}) WHERE length(v) >= {MIN_LEAK_LEN} "
            f"AND NOT regexp_full_match(v, '[0-9]{{1,{SHORT_INT_MAX_DIGITS}}}')"
        )
        return sql, columns

    def hits(self, text: str) -> int:
        con = connect_serving(self._db)
        try:
            sql, _ = self._values_sql(con)
            row = con.execute(
                f"SELECT COUNT(*) FROM ({sql}) WHERE contains(?, v)", [text.lower()]
            ).fetchone()
            return _int(row)
        finally:
            con.close()

    def check_pack(self, out_dir: Path) -> dict[str, Any]:
        root = Path(out_dir)
        hits: dict[str, int] = {}
        unlisted: dict[str, list[str]] = {}
        con = connect_serving(self._db)
        try:
            sql, columns = self._values_sql(con)
            distinct = _int(con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone())
            for p in sorted(root.rglob("*.json")):
                rel = p.relative_to(root).as_posix()
                raw = p.read_text(encoding="utf-8")
                try:
                    doc = json.loads(raw)
                except ValueError:
                    doc = None
                haystack = raw
                if doc is not None:
                    haystack = "\n".join(_atoms(doc)) + "\n" + raw
                n = _int(
                    con.execute(
                        f"SELECT COUNT(*) FROM ({sql}) WHERE contains(?, v)", [haystack.lower()]
                    ).fetchone()
                )
                if n:
                    hits[rel] = n
                if doc is not None:
                    bad = sorted(
                        {k for k, chain in _numbers(doc, "", ()) if not _number_allowed(k, chain)}
                    )
                    if bad:
                        # A key can itself be a source value: name it only when it is not.
                        unlisted[rel] = sorted(
                            {
                                k
                                if _int(
                                    con.execute(
                                        f"SELECT COUNT(*) FROM ({sql}) WHERE contains(?, v)",
                                        [k.lower()],
                                    ).fetchone()
                                )
                                == 0
                                else "<key_holds_source_value>"
                                for k in bad
                            }
                        )
                else:
                    unlisted[rel] = ["<unparseable>"]
        finally:
            con.close()
        return {
            "status": "failed" if hits or unlisted else "passed",
            "min_length": MIN_LEAK_LEN,
            "short_integer_max_digits": SHORT_INT_MAX_DIGITS,
            "columns_checked": columns,
            "distinct_values_checked": distinct,
            "hits": hits,
            "unlisted_numbers": unlisted,
        }


# --- build ---------------------------------------------------------------------------------


def _mask_node(node: Any, names: NameMask) -> Any:
    if isinstance(node, dict):
        return {names.text(str(k)): _mask_node(v, names) for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_node(v, names) for v in node]
    if isinstance(node, str):
        return names.text(node)
    return node


def _masked_group(g: dict[str, Any], names: NameMask) -> dict[str, Any]:
    mask_of = {m["value"]: m["mask"] for m in g["members"]}
    out: dict[str, Any] = {
        "group_id": g["group_id"],
        "table": names.out_table(str(g["table"])),
        "column": names.out(str(g["column"])),
        "folds": list(g["folds"]),
        "members": [
            {"mask": m["mask"], "shape": m["shape"], "rows": m["rows"]} for m in g["members"]
        ],
        "target_mask": mask_of.get(g["target"]),
    }
    for key in ("id", "status"):
        if key in g:
            out[key] = g[key]
    return out


def build_pack(
    out_dir: Path,
    *,
    space_id: str,
    space_name: str,
    profile: dict[str, Any],
    stats: dict[str, dict[str, dict[str, Any]]],
    names: NameMask,
    type_rows: list[dict[str, Any]],
    group_rows: list[dict[str, Any]],
    path: Path | None = None,
) -> dict[str, Any]:
    """Write schema, profile, masked samples and the stage 3 proposals. Never a raw value."""
    out = Path(out_dir)
    masked_stats = {
        names.out_table(t): {names.out(c): s for c, s in cols.items()} for t, cols in stats.items()
    }
    schema = {
        "format": PACK_FORMAT,
        "tables": [
            {
                "table": names.out_table(t),
                "row_count": next(iter(cols.values()), {}).get("row_count", 0),
                "columns": [
                    {"name": names.out(c), "type": s.get("type"), "pii_kind": s.get("pii_kind")}
                    for c, s in cols.items()
                ],
            }
            for t, cols in stats.items()
        ],
    }
    write_pack_file(out, "schema.json", schema)
    write_pack_file(out, "profile.json", {**_mask_node(profile, names), "columns": masked_stats})
    write_pack_file(out, "samples.masked.json", masked_samples(space_id, names, path=path))
    write_pack_file(
        out, "proposals/column_types.json", {"stage": 3, "items": _mask_node(type_rows, names)}
    )
    write_pack_file(
        out,
        "proposals/value_groups.json",
        {"stage": 3, "items": [_masked_group(g, names) for g in group_rows]},
    )
    return {
        "files": [
            "schema.json",
            "profile.json",
            "samples.masked.json",
            "proposals/column_types.json",
            "proposals/value_groups.json",
        ]
    }
