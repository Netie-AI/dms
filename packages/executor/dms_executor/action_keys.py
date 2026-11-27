"""Governed action keys (ACT-02): does an object key exist in its source, as encoded?

Hard rule 12. A key that parses, validates and matches nothing is the most
dangerous single failure, so an action proposal is refused unless its object key
matches exactly one source row, byte for byte after whitespace strip. ``BETA`` is
not ``SKU-BETA`` and ``wh-b`` is not ``WH-B``: nothing here folds case. A miss
returns up to five candidate keys matched case-insensitively by substring on any key
field (keys matching more fields first), so the
steward can see the source encoding and resend it; a candidate is never used in
place of the key that was sent.

A candidate is a raw source value on its way out of DMS in an HTTP body (and onto
the steward's screen), so it passes the same PII line the input side applies to a
sent key: a key column the classifier names as PII returns no candidates at all,
and a candidate with any field that ``dms_core.pii.column_is_pii`` flags or that
``mask_text`` would change is dropped. A key value DMS refuses to accept is never
offered as a suggestion.

Identifiers (table, key columns, field) come only from the code registry in
``dms_core.control_plane.action_types``; key values travel as ``$k0``/``$k1``
parameters. Only the ``demo_warehouse`` source kind is supported. Read-only:
this goes through ``demo_warehouse.execute_sql``, which opens a serving cursor.
duckdb stays in executor (hard rule 7).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
from dms_core.control_plane.action_types import mask_text
from dms_core.pii import column_is_pii

from dms_executor import demo_warehouse

SUPPORTED_SOURCE_KINDS = frozenset({"demo_warehouse"})
CANDIDATE_LIMIT = 5
# Rows read before the PII filter, so a few withheld keys do not empty the list.
# The response is still capped at CANDIDATE_LIMIT.
_CANDIDATE_SCAN = CANDIDATE_LIMIT * 5

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"not a registry identifier: {name!r}")
    return name


def _text(value: Any) -> str | None:
    """Source values leave executor as text. A float becomes its shortest repr."""
    if value is None:
        return None
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _candidate_is_clean(table: str, row: Mapping[str, Any], cols: Sequence[str]) -> bool:
    """True when no field of a candidate key reads as PII or a secret. Fails closed."""
    for c in cols:
        v = row.get(c)
        if v is None:
            continue
        text = str(v)
        try:
            if column_is_pii(c, [text], table=table) or mask_text(text) != text:
                return False
        except Exception:  # noqa: BLE001 - a classifier error withholds the value
            return False
    return True


def resolve_key(
    source_kind: str,
    source_table: str,
    key_fields: Sequence[str],
    field: str,
    key: Mapping[str, str],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Look one object key up in its source.

    Returns ``{"status": "found", "source_value": <text or None>}``,
    ``{"status": "not_found", "candidates": [...]}``,
    ``{"status": "ambiguous", "matches": n}`` or
    ``{"status": "unsupported_source", "source_kind": ...}``.
    A source without the table is ``not_found`` with no candidates (fail closed).
    """
    if source_kind not in SUPPORTED_SOURCE_KINDS:
        return {"status": "unsupported_source", "source_kind": source_kind}
    table = _ident(source_table)
    cols = [_ident(k) for k in key_fields]
    value_col = _ident(field)
    params = {f"k{i}": str(key[k]) for i, k in enumerate(key_fields)}

    exact_where = " AND ".join(f"CAST({c} AS VARCHAR) = $k{i}" for i, c in enumerate(cols))
    try:
        rows = demo_warehouse.execute_sql(
            f"SELECT {value_col} AS v FROM {table} WHERE {exact_where} LIMIT 2",
            path=path,
            params=params,
        )
    except (duckdb.CatalogException, duckdb.BinderException):
        # The source has no such table or column: a miss, not a 500.
        return {"status": "not_found", "candidates": []}
    if len(rows) == 1:
        return {"status": "found", "source_value": _text(rows[0]["v"])}
    if len(rows) > 1:
        return {"status": "ambiguous", "matches": len(rows)}

    # A key column the classifier names as PII by name alone offers nothing back.
    if any(column_is_pii(c, [], table=table) for c in cols):
        return {"status": "not_found", "candidates": []}

    # A candidate matches at least one key field (case-insensitive substring); the
    # keys that match more fields come first. Requiring every field to match would
    # hide SKU-ALPHA@WH-B from a steward who sent SKU-ALPHA@WH-A.
    score = " + ".join(
        f"(CASE WHEN contains(lower({c}), lower($k{i})) THEN 1 ELSE 0 END)"
        for i, c in enumerate(cols)
    )
    select_cols = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" for c in cols)
    order = ", ".join(cols)
    near = demo_warehouse.execute_sql(
        f"SELECT {order} FROM ("
        f"SELECT {order}, ({score}) AS match_score "
        f"FROM (SELECT DISTINCT {select_cols} FROM {table}) AS keys"
        f") AS scored WHERE match_score > 0 "
        f"ORDER BY match_score DESC, {order} LIMIT {_CANDIDATE_SCAN}",
        path=path,
        params=params,
    )
    clean = [{c: r[c] for c in cols} for r in near if _candidate_is_clean(table, r, cols)]
    return {"status": "not_found", "candidates": clean[:CANDIDATE_LIMIT]}


__all__ = ["CANDIDATE_LIMIT", "SUPPORTED_SOURCE_KINDS", "resolve_key"]
