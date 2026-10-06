"""STUDIO-SELECT-01 (dms#364): a Studio table/column pick, packed for Cortex ask.

Schema only. Column names and declared types come from information_schema and
joins from the declared ontology; no row is read and none reaches the payload.
Nothing widens: an empty pick, an unknown table or an unknown column refuses by
name instead of falling back to the whole Space.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from cortex_client.models import AskRequest
from dms_core.ask import SelectionRefused

from dms_executor.demo_warehouse import connect_file
from dms_executor.ontology import Ontology, relation_tables

#: Clause words that make a derived relation something other than "the table's rows".
_NOT_ROW_PRESERVING = (" GROUP BY ", " DISTINCT ", " WHERE ", " JOIN ", " LIMIT ", " UNION ")


class SelectionAskRequest(AskRequest):
    """AskRequest plus ``studio_selection``. Additive on the 1.2.0 wire.

    The facade dumps the model and the generated AskRequest keeps unknown keys
    in ``additional_properties``, so this rides to Cortex without a client or
    contract change. Cortex AskRequest (main and pin 279cbd85) is a plain
    pydantic model: the key is accepted and ignored until Cortex reads it.
    """

    studio_selection: dict[str, Any]


def _split(label: str) -> tuple[str, str]:
    schema, _, name = label.rpartition(".")
    return schema or "main", name


def read_selection_schema(warehouse: Path, tables: Iterable[str]) -> dict[str, dict[str, str]]:
    """``{label: {column: data_type}}`` for the named tables that exist, ordinal order."""
    labels = sorted({t for t in tables if t})
    if not labels or not Path(warehouse).is_file():
        return {}
    pairs = [_split(t) for t in labels]
    where = " OR ".join("(table_schema = ? AND table_name = ?)" for _ in pairs)
    con = connect_file(Path(warehouse))
    try:
        rows = con.execute(
            "SELECT table_schema, table_name, column_name, data_type "
            f"FROM information_schema.columns WHERE {where} "
            "ORDER BY table_schema, table_name, ordinal_position",
            [part for pair in pairs for part in pair],
        ).fetchall()
    finally:
        con.close()
    out: dict[str, dict[str, str]] = {}
    for schema, name, column, dtype in rows:
        label = str(name) if schema == "main" else f"{schema}.{name}"
        out.setdefault(label, {})[str(column)] = str(dtype)
    return out


def _physical_table(relation: str) -> str | None:
    """The one base table a relation is row-for-row, else None.

    ``product`` is ``SELECT sku, ANY_VALUE(category) ... GROUP BY sku`` over
    inventory. Handing that link over as a join on physical ``inventory`` is
    the ~15x lot fan-out the grain guard exists to refuse.
    """
    rel = relation.strip()
    tables = relation_tables(rel)
    if len(tables) != 1:
        return None
    if rel.startswith("("):
        flat = " ".join(rel.upper().split())
        if not flat.startswith("(SELECT *") or any(k in flat for k in _NOT_ROW_PRESERVING):
            return None
    return next(iter(tables))


def build_studio_selection(
    selection: Sequence[Mapping[str, Any]],
    *,
    schema: Mapping[str, Mapping[str, str]],
    ontology: Ontology | None,
) -> dict[str, Any]:
    """Selection -> the ``studio_selection`` payload. Raises SelectionRefused.

    ``schema`` is what the caller may read (granted tables that exist), so an
    ungranted table and a missing one refuse with the same code (A-0007).
    A join is listed only when both tables and its key columns are selected
    and the verified ontology does not measure it many-to-many; any other
    link between selected tables is named in ``joins_omitted``.
    """
    if not selection:
        raise SelectionRefused(
            "selection_empty",
            "Nothing is selected. Tick at least one table and column, or clear "
            "the selection to ask the whole Space.",
        )
    picked: dict[str, list[str]] = {}
    for item in selection:
        cols = picked.setdefault(str(item.get("table") or ""), [])
        cols.extend(str(c) for c in item.get("columns") or [] if str(c) not in cols)

    unknown_tables = [t for t in picked if t not in schema]
    if unknown_tables:
        raise SelectionRefused(
            "selection_unknown_table",
            f"Not a readable table in this Space: {', '.join(unknown_tables)}. "
            "Pick from the Studio list.",
            unknown_tables,
        )
    unknown_columns = [
        f"{t}.{c}" for t, cols in picked.items() for c in cols if c not in schema[t]
    ]
    if unknown_columns:
        raise SelectionRefused(
            "selection_unknown_column",
            f"No such column: {', '.join(unknown_columns)}.",
            unknown_columns,
        )
    no_columns = [t for t, cols in picked.items() if not cols]
    if no_columns:
        raise SelectionRefused(
            "selection_no_columns",
            f"Tick at least one column of {', '.join(no_columns)}, or untick the table.",
            no_columns,
        )

    joins: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    if ontology is not None:
        for link in ontology.links.values():
            a = _physical_table(ontology.objects[link.from_object].relation)
            b = _physical_table(ontology.objects[link.to_object].relation)
            if a is None or b is None or a == b or a not in picked or b not in picked:
                continue
            keys_picked = set(link.from_columns) <= set(picked[a]) and set(
                link.to_columns
            ) <= set(picked[b])
            if link.cardinality == "many_to_many":
                omitted.append({"name": link.name, "reason": "many_to_many"})
            elif not keys_picked:
                omitted.append({"name": link.name, "reason": "key_columns_not_selected"})
            else:
                joins.append(
                    {
                        "name": link.name,
                        "from_table": a,
                        "from_columns": list(link.from_columns),
                        "to_table": b,
                        "to_columns": list(link.to_columns),
                        "cardinality": link.cardinality,
                    }
                )
    return {
        "tables": [
            {"table": t, "columns": [{"name": c, "type": schema[t][c]} for c in cols]}
            for t, cols in picked.items()
        ],
        "joins": joins,
        "joins_omitted": omitted,
        "ontology_verified": bool(ontology is not None and ontology.verified),
    }
