"""The ``dms_payload`` a contract ask may carry (cortex-contract 1.5.0, WIRE-01b).

Cortex runs its plan-then-SQL ladder when an ask carries a payload: the
tables, columns and joins the question may use. This module builds that
payload from what the Space may read. It reads the serving catalog (names,
types, keys), a bounded window of rows per table to classify column values,
and the ontology, and returns a plain dict shaped as ``AskPayload``, or None.
Nothing here puts it on the wire; the ask site decides that.

- Tables: only names the signed grant lists (``readable``, the manifest's
  ``row_predicates`` keys) that the serving catalog holds, compared with
  ASCII-only case folding as Cortex compares them. A payload can only narrow
  the grant; Cortex refuses a table outside it before any model call. Tables
  are ordered by trigram similarity to the question, catalog order on ties.
- Columns: Cortex samples distinct values of payload columns and sends them
  to its model unmasked. Contract 1.5.0 has no per-column mask flag, so a
  column goes in only when it is positively cleared, and every other column
  is left out. Cleared means: a boolean (its values are true and false), or a
  name ``dms_core.pii`` itself treats as non-personal (the metric-skip
  pattern, a typed date name for a date column, or an ontology
  ``non_personal`` tag with source and field), and then no read value is
  classified as personal data. A text column also passes the value-hint
  probe. A column that is unknown, unread or in doubt is not sent (once the
  contract carries a per-column mask flag, it can go as masked instead). Text
  and boolean columns come first, because Cortex samples only the first columns
  in payload order (ASSUMPTION: this helps value grounding; measure it).
- Types: a type that carries a quoted literal (an ENUM's labels) or runs past
  ``MAX_TYPE_CHARS`` is sent as its head word only (``ENUM``, ``STRUCT``).
- Joins: only when the ontology passed ``verify()``, only many-to-one links,
  only between two relations that are each one physical table row for row,
  and only on key columns that are that table's own columns and are in the
  payload. Row for row is an allowlist: a bare table name, or one plain
  ``SELECT`` over one bare table whose projection is ``*``, bare columns and
  aliased casts. Anything else maps to no table, so a derived relation is
  never offered as a join on its base table.
- Size: at most ``MAX_TABLES`` tables and ``MAX_COLUMNS`` columns per table,
  and at most ``MAX_PAYLOAD_BYTES`` of JSON. Over the byte cap the least
  similar tables are dropped first.

Any error returns None and logs its class, so the ask goes out as it does
without a payload.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from dms_core.pii import _typed_date_column, classify_column, fail_closed_mask_payload
from sqlglot import exp, parse_one

from dms_executor.demo_warehouse import connect_serving
from dms_executor.ontology import Ontology
from dms_executor.schema_context import (
    _cleared_value_blocked,
    _column_positively_cleared,
    _cosine,
    _ident,
    _ontology_tags,
    _quote,
    _trigrams,
    _type_family,
    read_catalog,
)

_LOG = logging.getLogger(__name__)

MAX_TABLES = 24
MAX_COLUMNS = 60
#: Cortex caps ``schema_context`` at 8192 UTF-8 bytes. The payload keeps to the same bound.
MAX_PAYLOAD_BYTES = 8192
MAX_TYPE_CHARS = 64
_DESCRIPTION_CHARS = 400
#: Rows read per table to classify column values. Bounded, never a full scan.
_VALUE_ROWS = 200
#: Distinct values per column the classifier sees.
_VALUES_PER_COLUMN = 64

#: The only SELECT clauses a row-for-row relation may carry.
_ROWWISE_SELECT_ARGS = frozenset({"expressions", "from"})
#: The only nodes a derived column of a row-for-row relation may be built from.
_ROWWISE_NODES = frozenset(
    {
        exp.Alias,
        exp.Cast,
        exp.Column,
        exp.DataType,
        exp.DataTypeParam,
        exp.Identifier,
        exp.Literal,
        exp.Paren,
        exp.TryCast,
    }
)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _fold(name: str) -> str:
    """ASCII A-Z to a-z only, as Cortex folds identifiers. No Unicode lowering."""
    return str(name or "").translate(_ASCII_LOWER)


def _first(type_name: str) -> int:
    """0 for text and boolean columns, 1 for the rest."""
    text = str(type_name or "").upper()
    return 0 if text.startswith(("VARCHAR", "TEXT", "STRING", "CHAR", "BOOL", "ENUM")) else 1


def _type_text(raw: object) -> str | None:
    """The type the model reads. A quoted literal or an over-long type keeps its head word."""
    text = " ".join(str(raw or "").split())
    if "'" in text or '"' in text or len(text) > MAX_TYPE_CHARS:
        text = text.split("(", 1)[0].strip()[:MAX_TYPE_CHARS]
    if "'" in text or '"' in text:
        return None
    return text or None


class _Rowwise(NamedTuple):
    """A relation that is one physical table row for row."""

    table: str
    star: bool
    plain: frozenset[str]
    derived: frozenset[str]

    def keeps(self, column: str) -> bool:
        """True when ``column`` is the base table's own column under its own name."""
        name = _fold(column)
        return name not in self.derived and (self.star or name in self.plain)


def _bare_column(node: exp.Expression) -> bool:
    return (
        type(node) is exp.Column
        and isinstance(node.this, exp.Identifier)
        and not any(v for k, v in node.args.items() if k != "this")
    )


def _rowwise(relation: str) -> _Rowwise | None:
    """The table a relation is row for row, by allowlist. Anything else is None."""
    rel = str(relation or "").strip()
    if _ident(rel):
        return _Rowwise(rel, True, frozenset(), frozenset())
    try:
        node: Any = parse_one(rel, read="duckdb")
    except Exception:  # noqa: BLE001 - unreadable is not row for row
        return None
    while type(node) is exp.Subquery and not any(
        v for k, v in node.args.items() if k != "this"
    ):
        node = node.this
    if type(node) is not exp.Select:
        return None
    if any(v for k, v in node.args.items() if k not in _ROWWISE_SELECT_ARGS):
        return None
    source = node.args.get("from")
    if type(source) is not exp.From or any(v for k, v in source.args.items() if k != "this"):
        return None
    table = source.this
    if type(table) is not exp.Table or not isinstance(table.this, exp.Identifier):
        return None
    for key, value in table.args.items():
        if not value or key == "this":
            continue
        if key != "alias" or any(v for k, v in value.args.items() if k != "this"):
            return None
    if not _ident(table.name):
        return None
    star = False
    plain: set[str] = set()
    derived: set[str] = set()
    for item in node.expressions:
        if type(item) is exp.Star and not any(item.args.values()):
            star = True
        elif _bare_column(item):
            plain.add(_fold(item.name))
        elif type(item) is exp.Alias and isinstance(item.args.get("alias"), exp.Identifier):
            for part in item.walk():
                if type(part) not in _ROWWISE_NODES:
                    return None
                if type(part) is exp.Column and not _bare_column(part):
                    return None
            derived.add(_fold(item.alias))
        else:
            return None
    if not star and not plain and not derived:
        return None
    return _Rowwise(str(table.name), star, frozenset(plain), frozenset(derived))


def physical_table(relation: str) -> str | None:
    """The one table a relation is row for row, else None.

    ``inventory`` and ``(SELECT *, CAST(ts AS DATE) AS day FROM t)`` are
    their table. ``(SELECT sku, ANY_VALUE(category) ... GROUP BY sku)`` is
    not: joining on ``inventory`` there would repeat each SKU once per lot.
    Only a plain SELECT over one bare table maps to it; PIVOT, a sample, a
    set-returning or unknown function, or any other clause maps to None.
    """
    found = _rowwise(relation)
    return found.table if found is not None else None


def _column_cleared(
    table: str,
    column: str,
    family: str,
    values: Sequence[str] | None,
    *,
    tagged: bool,
) -> bool:
    """True only when this column is positively cleared to leave DMS with its values.

    ``values`` is None when they could not be read. Doubt is False.
    """
    try:
        if family == "boolean":
            return classify_column(column, (), table=table) is None
        if values is None or not _name_cleared(table, column, family, tagged=tagged):
            return False
        if classify_column(column, list(values), table=table) is not None:
            return False
        if family == "text":
            return not any(_cleared_value_blocked(value) for value in values)
        return True
    except Exception:  # noqa: BLE001 - doubt: the column is not sent
        return False


def _name_cleared(table: str, column: str, family: str, *, tagged: bool) -> bool:
    """The name half of the clearance; values are read only for these columns."""
    if family == "boolean":
        return True
    if family == "date" and _typed_date_column(column):
        return True
    return _column_positively_cleared(table, column, tagged=tagged)


def _read_values(con: Any, table: str, columns: Sequence[str]) -> dict[str, list[str]] | None:
    """Distinct non-empty values per column from the first rows. None when unread."""
    if not columns:
        return {}
    try:
        rows = con.execute(
            "SELECT "
            + ", ".join(f"CAST({_quote(c)} AS VARCHAR)" for c in columns)
            + f" FROM {_quote(table)} LIMIT {_VALUE_ROWS}"
        ).fetchall()
    except Exception:  # noqa: BLE001 - unread values clear nothing
        return None
    out: dict[str, list[str]] = {c: [] for c in columns}
    for row in rows:
        for column, cell in zip(columns, row, strict=False):
            if cell is None:
                continue
            text = " ".join(str(cell).split())
            seen = out[column]
            if text and text not in seen and len(seen) < _VALUES_PER_COLUMN:
                seen.append(text)
    return out


def _measure_notes(ontology: Ontology, table: str) -> str:
    """Measures defined at this table's grain, by name and description.

    Sent only when the mask leaves the text as it is.
    """
    notes = []
    for measure in ontology.measures.values():
        obj = ontology.objects.get(measure.grain)
        if obj is None or physical_table(obj.relation) != table:
            continue
        text = measure.description or measure.expression
        notes.append(f"{measure.name}: {' '.join(str(text).split())}")
    out = "; ".join(notes)[:_DESCRIPTION_CHARS]
    if not out:
        return ""
    masked = fail_closed_mask_payload(text=out)
    return out if isinstance(masked, dict) and masked.get("text") == out else ""


def _joins(ontology: Ontology, columns: Mapping[str, set[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in ontology.links.values():
        if link.cardinality != "many_to_one":
            continue
        left = ontology.objects.get(link.from_object)
        right = ontology.objects.get(link.to_object)
        if left is None or right is None:
            continue
        lrel = _rowwise(left.relation)
        rrel = _rowwise(right.relation)
        if lrel is None or rrel is None:
            continue
        a, b = lrel.table, rrel.table
        if a == b or a not in columns or b not in columns:
            continue
        pairs = list(zip(link.from_columns, link.to_columns, strict=False))
        if not pairs or len(link.from_columns) != len(link.to_columns):
            continue
        if not all(lrel.keeps(f) and rrel.keeps(t) for f, t in pairs):
            continue
        if not all(_fold(f) in columns[a] and _fold(t) in columns[b] for f, t in pairs):
            continue
        for f, t in pairs:
            out.append(
                {
                    "left_table": a,
                    "left_column": f,
                    "right_table": b,
                    "right_column": t,
                    "relation": f"{link.name} {link.cardinality}",
                }
            )
    return out


def payload_bytes(payload: Mapping[str, Any]) -> int:
    """JSON size with ASCII escapes and spaced separators: never below a compact UTF-8 body."""
    return len(json.dumps(payload).encode("utf-8"))


def _fit(tables: list[dict[str, Any]], joins: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Drop the least similar tables, then a lone table's tail columns, to fit the byte cap."""
    tables = list(tables)
    while tables:
        names = {t["name"] for t in tables}
        kept = [j for j in joins if j["left_table"] in names and j["right_table"] in names]
        payload: dict[str, Any] = {"tables": tables}
        if kept:
            payload["joins"] = kept
        if payload_bytes(payload) <= MAX_PAYLOAD_BYTES:
            return payload
        if len(tables) > 1:
            tables.pop()
            continue
        only = {k: v for k, v in tables[0].items() if k != "description"}
        cols = list(only["columns"])
        while cols:
            only["columns"] = cols
            if payload_bytes({"tables": [only]}) <= MAX_PAYLOAD_BYTES:
                return {"tables": [only]}
            cols = cols[:-1]
        return None
    return None


def build_dms_payload(
    question: str,
    *,
    serving: Path | None,
    readable: Iterable[str],
    ontology: Ontology | None = None,
) -> dict[str, Any] | None:
    """``AskPayload`` for this question, or None when no readable table survives."""
    try:
        return _build(question, serving=serving, readable=readable, ontology=ontology)
    except Exception as exc:  # noqa: BLE001 - the ask must not fail on its payload
        _LOG.warning("dms_payload_unbuilt %s", type(exc).__name__)
        return None


def _build(
    question: str,
    *,
    serving: Path | None,
    readable: Iterable[str],
    ontology: Ontology | None,
) -> dict[str, Any] | None:
    granted = {str(name) for name in readable if str(name or "").strip()}
    exact = {_fold(name) for name in granted}
    if not exact or serving is None:
        return None
    _fingerprint, catalog = read_catalog(serving, granted, "duckdb")
    tags = _ontology_tags(ontology)
    candidates: list[tuple[str, list[tuple[int, Mapping[str, Any], str]]]] = []
    for dataset in catalog.get("datasets") or []:
        name = str(dataset.get("name") or "")
        if _fold(name) not in exact:
            continue
        named = []
        for index, col in enumerate(dataset.get("columns") or []):
            col_name = str(col.get("name") or "")
            family = _type_family(str(col.get("type") or ""))
            if col_name and _name_cleared(
                name, col_name, family, tagged=(name, col_name) in tags
            ):
                named.append((index, col, family))
        if named:
            candidates.append((name, named))
    if not candidates:
        return None
    asked = _trigrams(question or "")

    def score(item: tuple[str, list[tuple[int, Mapping[str, Any], str]]]) -> float:
        words = " ".join([item[0], *(str(col["name"]) for _, col, _ in item[1])])
        return _cosine(asked, _trigrams(words))

    candidates = sorted(candidates, key=score, reverse=True)[:MAX_TABLES]
    tables: list[dict[str, Any]] = []
    con = connect_serving(Path(serving))
    try:
        for name, named in candidates:
            needs = [str(col["name"]) for _, col, family in named if family != "boolean"]
            values = _read_values(con, name, needs)
            kept = [
                (index, col)
                for index, col, family in named
                if _column_cleared(
                    name,
                    str(col["name"]),
                    family,
                    None if values is None else values.get(str(col["name"]), []),
                    tagged=(name, str(col["name"])) in tags,
                )
            ]
            if not kept:
                continue
            kept.sort(key=lambda item: (_first(str(item[1].get("type") or "")), item[0]))
            tables.append(
                {
                    "name": name,
                    "columns": [
                        {"name": str(col["name"]), "type": _type_text(col.get("type"))}
                        for _, col in kept[:MAX_COLUMNS]
                    ],
                }
            )
    finally:
        con.close()
    if not tables:
        return None
    joins: list[dict[str, Any]] = []
    if ontology is not None:
        for table in tables:
            notes = _measure_notes(ontology, table["name"])
            if notes:
                table["description"] = notes
        if ontology.verified:
            columns = {t["name"]: {_fold(c["name"]) for c in t["columns"]} for t in tables}
            joins = _joins(ontology, columns)
    return _fit(tables, joins)


__all__ = [
    "MAX_COLUMNS",
    "MAX_PAYLOAD_BYTES",
    "MAX_TABLES",
    "MAX_TYPE_CHARS",
    "build_dms_payload",
    "payload_bytes",
    "physical_table",
]
