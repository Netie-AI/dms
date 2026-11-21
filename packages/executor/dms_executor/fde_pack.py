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
import unicodedata
from collections.abc import Collection, Iterator, Mapping, Sequence
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
        "held_from_earlier_runs",
        "agrees",
        "disagrees",
        "invalid_envelope",
        "http_error",
        "kept",
        "dropped",
        # stage 5 proposals: a position in the model's list, a count of variants
        "index",
        "variants",
        # stage 6 measure
        "asked",
        "correct",
        "abstained",
        "wrong",
        "http_status",
    }
)
#: ``min`` and ``max`` are numbers only as the length range of a text column.
_ALLOWED_PATH_KEYS = frozenset({("length", "min"), ("length", "max")})
#: The manifest's per-stage ``counts`` dict: any key directly under it is a count. Only
#: at that path, so a source column named ``counts`` opens nothing.
_COUNT_PATH = ("stages", "counts")

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
        # A punctuation run always carries its count, so no cell made only of punctuation
        # is its own shape (``-`` is ``shape:-1``, never ``shape:-``).
        piece = f"{cls}{n}"
        if len(out) + len(piece) > SHAPE_MAX:
            truncated = True
            break
        out += piece
    return "shape:" + out + ("+" if truncated else "")


# --- names -------------------------------------------------------------------------------


_RESERVED_NAME = re.compile(r"fde_name_\d+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\bfde_name_\d+\b")


def _reserved(name: str) -> bool:
    """A name inside the namespace the pack itself writes: it must be tokenised."""
    folded = name.casefold()
    return (
        _RESERVED_NAME.search(name) is not None
        or folded.startswith("dmsmask_")
        or folded.startswith("shape:")
    )


def _scanner_changes(name: str) -> bool:
    """The PII masker run over a name as it runs over a cell: the text scan, and the value
    classifier on the name as a value (a landline such as ``+60 3-8070 1234`` is found by
    the second only). Fails closed."""
    try:
        return (
            mask_payload(text=name)["text"] != name or classify_column("_name", [name]) is not None
        )
    except Exception:  # noqa: BLE001 - a name the masker cannot read is masked
        return True


def name_is_pii(name: str) -> bool:
    """Public for the leak gate: a column or table name the PII masker flags."""
    return _scanner_changes(name)


def csv_header_detected(path: Path) -> bool | None:
    """DuckDB's own CSV sniffer on the file as uploaded: does it read a header row?

    Bronze reads every CSV with ``header := true``. When the sniffer says the first row is
    data, the first row would leave as column names. None when the sniffer cannot read the
    file (bronze ingest then reports it). A fresh in-memory connection: no lake is touched.
    """
    con = duckdb.connect(":memory:")
    try:
        row = con.execute("SELECT HasHeader FROM sniff_csv(?)", [str(path)]).fetchone()
    except duckdb.Error:
        return None
    finally:
        con.close()
    return None if row is None else bool(row[0])


def _source_variants(source: str) -> list[str]:
    """The original upload name and its stem. pii.py has no person-name detector for a
    bare header: the scanner sees emails, phones, ids and dates, not a name like ``alice``."""
    base = source.replace("\\", "/").rpartition("/")[2]
    stem = base.rpartition(".")[0] if "." in base else base
    return [v for v in dict.fromkeys((source, base, stem)) if v]


class NameMask:
    """Replaces a table or column name the PII scanner changes with ``fde_name_NN``.

    A name in the reserved namespace (``fde_name_N``, ``DMSMASK_``, ``shape:``) is
    tokenised too, so the model never sees two columns with one name. ``sources`` maps a
    table label to its original upload file name: the table is tokenised when the scanner
    changes that name or its stem, even if the derived table name is clean."""

    def __init__(
        self,
        tables: Sequence[tuple[str, Sequence[str]]] = (),
        *,
        sources: Mapping[str, str] | None = None,
        force: Collection[str] = (),
    ) -> None:
        self._token_of: dict[str, str] = {}
        self._raw_of: dict[str, str] = {}
        self._source_text: dict[str, str] = {}
        self._pattern: tuple[int, re.Pattern[str] | None] = (-1, None)
        #: Names tokenised because their table's header row reads as data
        #: (``data_header_tables``). Replaced as whole atoms or whole words only.
        self._forced = frozenset(force)
        self._forced_re: re.Pattern[str] | None = None
        for table, columns in tables:
            bare = table.rpartition(".")[2]
            origin = (sources or {}).get(table)
            variants = _source_variants(origin) if origin else []
            if variants and any(_scanner_changes(v) for v in variants):
                token = self._assign(bare)
                for v in variants:
                    self._source_text[v] = token
            else:
                self.out(bare)
            for column in columns:
                self.out(column)

    def _assign(self, name: str) -> str:
        known = self._token_of.get(name)
        if known is not None:
            return known
        token = f"fde_name_{len(self._token_of) + 1:02d}"
        self._token_of[name] = token
        self._raw_of[token] = name
        return token

    def out(self, name: str) -> str:
        known = self._token_of.get(name)
        if known is not None:
            return known
        if name not in self._forced and not _reserved(name) and not _scanner_changes(name):
            return name
        return self._assign(name)

    def out_table(self, label: str) -> str:
        schema, dot, bare = label.rpartition(".")
        return f"{schema}{dot}{self.out(bare)}"

    def text(self, text: str) -> str:
        """``text`` with every masked raw name replaced by its token, in one pass.

        A forced name (a header that reads as data, which can be a short common word)
        is replaced only as a whole atom or a whole word, never inside another word.
        """
        exact = self._token_of.get(text)
        if exact is not None:
            return exact
        if self._forced:
            if self._forced_re is None:
                alts = "|".join(re.escape(r) for r in sorted(self._forced, key=len, reverse=True))
                self._forced_re = re.compile(rf"(?<![A-Za-z0-9_])(?:{alts})(?![A-Za-z0-9_])")
            text = self._forced_re.sub(lambda m: self._assign(m.group(0)), text)
        size = len(self._token_of) + len(self._source_text)
        if self._pattern[0] != size:
            raws = sorted(
                {*(r for r in self._token_of if r not in self._forced), *self._source_text},
                key=len,
                reverse=True,
            )
            pat = re.compile("|".join(re.escape(r) for r in raws)) if raws else None
            self._pattern = (size, pat)
        pat = self._pattern[1]
        if pat is None:
            return text
        return pat.sub(
            lambda m: self._token_of.get(m.group(0)) or self._source_text[m.group(0)], text
        )

    def back(self, text: str) -> str:
        """Whole tokens only. An unknown token (``fde_name_99``) stays as it is."""
        return _TOKEN_RE.sub(lambda m: self._raw_of.get(m.group(0), m.group(0)), text)

    def tokens(self) -> dict[str, str]:
        return dict(self._raw_of)


def _coarse(shape_text: str) -> str:
    """``shape:U1L2S1U1L2`` -> ``ULSUL``: the character classes, counts dropped."""
    return re.sub(r"\d+", "", shape_text.removeprefix("shape:")).rstrip("+")


def _reads_as_value(name: str) -> bool:
    """A header that parses as a number or an ISO-like date is a value, not a name."""
    text = name.strip()
    return bool(
        re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*", text)
        or re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", text)
    )


def _classes_of(text: str) -> str:
    """The letter, digit and space classes a text uses (``U``, ``L``, ``D``, ``S``), sorted.

    Punctuation is left out, so ``No. 12, Jalan Ampang`` and ``Jalan Sultan Ismail 88``
    share ``DLSU``. A name made only of punctuation and underscores has no class.
    """
    out = set()
    for ch in text:
        if ch.isupper():
            out.add("U")
        elif ch.islower():
            out.add("L")
        elif ch.isdigit():
            out.add("D")
        elif ch.isspace():
            out.add("S")
    return "".join(sorted(out))


def data_header_columns(
    stats: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, set[str]]:
    """Column names that read as data, per table (a CSV with no header line).

    Bronze reads every CSV with a header, so a headerless file lands its first data row
    as column names, and names leave the machine as schema. Measured, no word list: a
    column hits when its name parses as a value, or when the classes of its name equal
    the classes of a value the column holds (all distinct values, ``_classes``). Each hit
    column is tokenised; when a third or more of a table's columns hit, the header row
    reads as data and every name of that table is tokenised. A name made of lower-case
    letters only also hits a lower-case column (``note``): tokenising it costs the model a
    word, never a value.
    """
    out: dict[str, set[str]] = {}
    for table, cols in stats.items():
        hits: set[str] = set()
        for name, st in cols.items():
            classes = set(st.get("_classes") or [])
            own = _classes_of(name)
            if _reads_as_value(name) or (own and own in classes):
                hits.add(name)
        if hits and len(hits) * 3 >= len(cols):
            hits = set(cols)
        if hits:
            out[table] = hits
    return out


def data_header_tables(stats: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> set[str]:
    """Tables whose every name is tokenised by ``data_header_columns``."""
    return {t for t, hit in data_header_columns(stats).items() if hit == set(stats[t])}


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


def _class_sets(con: duckdb.DuckDBPyConnection, rel: str, q: str) -> list[str]:
    """Every distinct ``_classes_of`` of the column's values (at most 15 class strings,
    read over at most ``SHAPE_DISTINCT_CAP`` distinct values). Local only: ``build_pack``
    drops every stats key that starts with ``_``."""
    rows = con.execute(
        f"SELECT DISTINCT CAST({q} AS VARCHAR) FROM {rel} WHERE {q} IS NOT NULL "
        f"LIMIT {SHAPE_DISTINCT_CAP}"
    ).fetchall()
    return sorted({_classes_of(str(v)) for (v,) in rows} - {""})


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
    attempt("_classes", lambda: _class_sets(con, rel, q))
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

#: The only files a pack may hold. Any other file under the pack directory is refused.
PACK_FILES = frozenset(
    {
        "manifest.json",
        "send_manifest.json",
        "schema.json",
        "profile.json",
        "samples.masked.json",
        "measure.json",
        "proposals/column_types.json",
        "proposals/value_groups.json",
        "proposals/ontology_draft.json",
        "proposals/verified_questions.json",
        "proposals/value_mappings.json",
    }
)

#: Fields this tool generates: an item or run id, a sha256, a timestamp. Hex and digits in
#: them can spell a short code (``c012``). Such an atom is left out of the leak check only
#: when it is exactly one of the ``generated`` values the caller passes (or, for ``sha256``,
#: the digest of a pack file or of a listed value). Every other atom is checked.
_GENERATED_KEYS = frozenset({"id", "run_id", "space_id", "version_id", "sha256", "created_at"})

#: Words the tool itself writes. An atom that is exactly one of these is not a source value
#: leak. A closed constant: no cell value is ever added to it. The landed types of this
#: lake come from information_schema (a per-check set), never from a cell.
TOOL_VOCAB = frozenset(
    {
        # stage statuses and item statuses
        "done", "refused", "failed", "not_requested", "not_reached", "running", "passed",
        "proposed", "approved", "revoked",
        # stage names and item kinds
        "measure", "profile", "propose", "pack", "leak_check", "stage", "stages",
        "column_type", "column_types", "value_map", "value_maps", "value_group",
        "value_groups", "value_mapping", "value_mappings", "ontology_draft",
        "verified_question", "verified_questions",
        # fold names
        "case_space", "punct", "affix", "initials",
        # mask and pack format strings
        PACK_FORMAT, SAMPLES_MASK, "measured",
        # type proposals
        "code", "category", "DATE", "BIGINT", "VARCHAR",
        *(f"DECIMAL(38,{n})" for n in range(_EXACT_DECIMAL_MAX_SCALE + 1)),
        *_DATE_FORMATS,
        # reason codes this module writes
        "leading_zero", "lossless_integer", "lossless_decimal", "single_format",
        "ambiguous_date_format", "low_cardinality", "single_shape", "exponent_notation",
        "scale_over_18", "precision_over_38", "not_plain_decimal", "no_values",
        "double_text", "text",
        # the pack's own structure: every key, stage name, outcome and code this tool and
        # the #514 profiler write (an exact atom only; inside another atom it still hits)
        "01_ontology_draft.md",
        "02_verified_questions.md",
        "03_value_mappings.md",
        "DOUBLE",
        "abstained",
        "agrees",
        "asked",
        "basis",
        "bounds",
        "bytes",
        "calls",
        "child",
        "child_columns",
        "child_distinct",
        "child_has_key",
        "child_has_no_key",
        "child_is_own_key",
        "child_null_rows",
        "child_rows",
        "child_table",
        "column",
        "column_types",
        "columns",
        "columns_checked",
        "columns_not_measured",
        "correct",
        "counts",
        "created_at",
        "date",
        "decimal",
        "description",
        "disagrees",
        "distinct_count",
        "distinct_values_checked",
        "dropped",
        "duplicate",
        "empty_table",
        "error",
        "evidence",
        "fde_onboard/1",
        "fde_onboard/v1",
        "fde_send/1",
        "file_tables",
        "files",
        "files_seen",
        "fixed_prompts",
        "folds",
        "format",
        "formats",
        "from_columns",
        "from_object",
        "group_id",
        "held_from_earlier_runs",
        "hint",
        "hits",
        "http_error",
        "imported",
        "index",
        "ingest",
        "ingested",
        "invalid_envelope",
        "item_store_in_memory",
        "items",
        "kept",
        "keys",
        "kind",
        "landed_type",
        "length",
        "link",
        "link_search",
        "links",
        "lossless",
        "magnitude",
        "manifest.json",
        "mask",
        "masked_pack",
        "matched_parent_keys",
        "max_columns",
        "max_columns_per_table",
        "max_exp",
        "max_fanout",
        "max_pairs",
        "max_rows",
        "measure.json",
        "members",
        "min_exp",
        "min_length",
        "model",
        "model_calls",
        "model_says_distinct",
        "name",
        "names_read_as_data",
        "need_attention",
        "negative",
        "no_unique_column_or_pair",
        "none_until_a_steward_approves",
        "null_count",
        "null_rate",
        "object",
        "observations",
        "observed_scale",
        "one_to_one",
        "onto_profile/1",
        "ontology_dropped",
        "ontology_items",
        "origin",
        "orphan_keys",
        "orphan_observation_min_match_ratio",
        "orphan_rows",
        "orphans",
        "pair_keys_not_searched_as_parents",
        "pair_search",
        "pairs_below_orphan_threshold",
        "pairs_child_all_null",
        "pairs_measured",
        "pairs_over_cap_not_measured",
        "pairs_type_incompatible",
        "parent",
        "parent_columns",
        "parent_distinct",
        "parent_table",
        "persisted",
        "pii_kind",
        "profile.json",
        "profile_items",
        "profile_persisted",
        "profiler",
        "prompt",
        "prompts",
        "proposals/column_types.json",
        "proposals/ontology_draft.json",
        "proposals/value_groups.json",
        "proposals/value_mappings.json",
        "proposals/verified_questions.json",
        "proposed_sql_unreviewed",
        "proposed_type",
        "questions_dropped",
        "questions_kept",
        "reason",
        "reference",
        "referenced_by",
        "relation",
        "result",
        "row_count",
        "rows",
        "run_id",
        "samples",
        "samples.masked.json",
        "schema",
        "schema.json",
        "searched",
        "send_manifest.json",
        "sha256",
        "shape",
        "shapes",
        "short_integer_max_digits",
        "single_columns",
        "skipped",
        "space",
        "space_id",
        "space_persisted",
        "sql_source",
        "status",
        "store",
        "table",
        "tables",
        "tables_with_data_headers",
        "target_mask",
        "to_columns",
        "to_object",
        "tool",
        "truncated",
        "truncated_tables",
        "truth",
        "type",
        "type_and_clean_proposals",
        "unexpected_files",
        "unkeyed_reason",
        "unkeyed_tables",
        "unknown_column",
        "unknown_group",
        "unknown_table",
        "unknown_target",
        "unlisted_numbers",
        "unmeasured",
        "user_message",
        "value_mappings_dropped",
        "variants",
        "version",
        "whole_digits",
        "wrong",
        # exception class names a failed measurement reports
        "ConversionException", "BinderException", "ParserException", "CatalogException",
        "InvalidInputException", "OutOfRangeException", "ConstraintException",
        "InternalException", "SyntaxException", "IOException",
    }
)  # fmt: skip

_RATIO_KEYS = frozenset({"null_rate", "orphan_observation_min_match_ratio"})
_SIGNED_INT_KEYS = frozenset({"min_exp", "max_exp"})


def normalise(text: str) -> str:
    """NFKC, no format characters (zero-width and friends), casefolded. Both sides of the
    leak comparison go through this."""
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return unicodedata.normalize("NFKC", folded.casefold())


def _atoms(
    node: Any,
    key: str = "",
    generated: Collection[str] = frozenset(),
    shas: Collection[str] = frozenset(),
    vocab: Collection[str] = TOOL_VOCAB,
) -> Iterator[str]:
    """Every key and string value, decoded. A generated value the caller listed, or a
    closed tool word, is skipped; it must be that whole atom."""
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k) not in vocab:
                yield str(k)
            yield from _atoms(v, str(k), generated, shas, vocab)
    elif isinstance(node, list):
        for v in node:
            yield from _atoms(v, key, generated, shas, vocab)
    elif isinstance(node, str):
        if key == "sha256" and (node in shas or node in generated):
            return
        if key in ("group_id", "target_mask", "mask") and re.fullmatch(r"g\d{3,}|V\d{2,}", node):
            return
        if key in _GENERATED_KEYS and node in generated:
            return
        if node in vocab:
            return
        yield node


def _numbers(
    node: Any, key: str, chain: tuple[str, ...]
) -> Iterator[tuple[str, tuple[str, ...], Any]]:
    """``(nearest dict key, ancestor chain, value)`` of every JSON number."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _numbers(v, str(k), (*chain, str(k)))
    elif isinstance(node, list):
        for v in node:
            yield from _numbers(v, key, chain)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield key, chain, node


def _number_allowed(key: str, chain: tuple[str, ...]) -> bool:
    if key in ALLOWED_NUMBER_KEYS:
        return True
    parent = chain[-2] if len(chain) >= 2 else ""
    if (parent, key) in _ALLOWED_PATH_KEYS:
        return True
    return len(chain) == 3 and chain[:2] == _COUNT_PATH


def _number_problem(key: str, value: Any) -> str | None:
    """Why an allowed key holds a number that is not a count or a ratio, else None."""
    if key in _RATIO_KEYS:
        ok = isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1
        return None if ok else "not_a_ratio"
    if not isinstance(value, int):
        return "not_an_int"
    if key in _SIGNED_INT_KEYS:
        return None
    return None if value >= 0 else "not_a_count"


class LeakChecker:
    """Counts source values that appear in text. Returns counts only, never a value."""

    def __init__(self, space_id: str, *, path: Path | None = None) -> None:
        self._db = path or warehouse_path()
        self._tables = space_file_tables(space_id, path=self._db)
        self._loaded: tuple[frozenset[str], int, frozenset[str]] | None = None

    def _load(self) -> tuple[frozenset[str], int, frozenset[str]]:
        """Normalised distinct source values, the column count and the landed type names.
        Read once and kept in memory only."""
        if self._loaded is not None:
            return self._loaded
        values: set[str] = set()
        types: set[str] = set()
        columns = 0
        con = connect_serving(self._db)
        try:
            for label in self._tables:
                bare = label.rpartition(".")[2]
                if name_is_pii(bare) and len(normalise(bare)) >= MIN_LEAK_LEN:
                    values.add(normalise(bare).strip())
                for column, landed in _columns(con, label):
                    columns += 1
                    types.add(landed)
                    types.add(_base(landed))
                    # A name the PII masker flags is data: it must not leave either.
                    if name_is_pii(column) and len(normalise(column)) >= MIN_LEAK_LEN:
                        values.add(normalise(column).strip())
                    q = quote_ident(column)
                    cur = con.execute(
                        f"SELECT DISTINCT CAST({q} AS VARCHAR) FROM {_relation(label)} "
                        f"WHERE {q} IS NOT NULL"
                    )
                    while True:
                        batch = cur.fetchmany(10_000)
                        if not batch:
                            break
                        for (raw,) in batch:
                            v = normalise(str(raw)).strip()
                            if len(v) >= MIN_LEAK_LEN and not re.fullmatch(
                                f"[0-9]{{1,{SHORT_INT_MAX_DIGITS}}}", v
                            ):
                                values.add(v)
        finally:
            con.close()
        self._loaded = (frozenset(values), columns, frozenset(types))
        return self._loaded

    def _count(self, text: str) -> int:
        haystack = normalise(text)
        return sum(1 for v in self._load()[0] if v in haystack)

    def hits(self, text: str) -> int:
        return self._count(text)

    def check_pack(self, out_dir: Path, *, generated: Collection[str] = ()) -> dict[str, Any]:
        root = Path(out_dir)
        gen = frozenset(generated)
        values, columns, landed_types = self._load()
        vocab = TOOL_VOCAB | landed_types
        files = [p for p in sorted(root.rglob("*")) if p.is_file()]
        blobs = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
        shas = {hashlib.sha256(b).hexdigest() for b in blobs.values()}
        shas |= {hashlib.sha256(g.encode("utf-8")).hexdigest() for g in gen}

        def safe(name: str, what: str = "path") -> str:
            # A key or a path can itself be a source value: name it only when it is not.
            return name if self._count(name) == 0 else f"<{what}_holds_source_value>"

        hits: dict[str, int] = {}
        unlisted: dict[str, list[str]] = {}
        unexpected: list[str] = []
        for rel, data in blobs.items():
            if rel not in PACK_FILES:
                unexpected.append(safe(rel))
            raw = data.decode("utf-8", errors="replace")
            doc: Any = None
            if rel.endswith(".json"):
                try:
                    doc = json.loads(raw)
                except ValueError:
                    doc = None
            # Decoded atoms, not the raw text: a JSON escape cannot hide a value.
            haystack = raw if doc is None else "\n".join(_atoms(doc, "", gen, shas, vocab))
            n = self._count(haystack)
            if n:
                hits[safe(rel)] = n
            if doc is not None:
                bad: set[str] = set()
                for k, chain, value in _numbers(doc, "", ()):
                    if not _number_allowed(k, chain):
                        bad.add(safe(k, "key"))
                        continue
                    why = _number_problem(k, value)
                    if why is not None:
                        bad.add(f"{safe(k, 'key')}:{why}")
                if bad:
                    unlisted[safe(rel)] = sorted(bad)
            elif rel.endswith(".json"):
                unlisted[safe(rel)] = ["<unparseable>"]
        return {
            "status": "failed" if hits or unlisted or unexpected else "passed",
            "min_length": MIN_LEAK_LEN,
            "short_integer_max_digits": SHORT_INT_MAX_DIGITS,
            "columns_checked": columns,
            "distinct_values_checked": len(values),
            "hits": hits,
            "unlisted_numbers": unlisted,
            "unexpected_files": sorted(unexpected),
        }


# --- build ---------------------------------------------------------------------------------


def _without_prose(node: Any) -> Any:
    """The #514 bounds with their explanatory sentences left out (numbers and names kept).

    Every word in the pack is checked against the data, so a sentence such as "only for a
    table where no single column is a key" would refuse a lake holding the cell "single".
    """
    if isinstance(node, dict):
        return {k: _without_prose(v) for k, v in node.items() if not isinstance(v, str)}
    return node


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
        names.out_table(t): {
            names.out(c): {k: v for k, v in s.items() if not str(k).startswith("_")}
            for c, s in cols.items()
        }
        for t, cols in stats.items()
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
    profile = {**profile, "bounds": _without_prose(profile.get("bounds") or {})}
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
