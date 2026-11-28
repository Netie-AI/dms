"""Optional live source drivers.

Each kind stays off unless its DMS_* flag is on. The modules import their
database drivers lazily, so this package imports on a machine that has neither
psycopg nor oracledb.
"""

from __future__ import annotations

import os
from typing import Any


def flag_on(name: str) -> bool:
    """On only for the digit 1. ``true``, ``yes``, and ``on`` stay off."""
    return os.environ.get(name, "").strip() == "1"


class DriverError(Exception):
    """Typed driver failure. The message is the code only. No secret, no row."""

    def __init__(self, code: str, *, doc: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.doc = doc


def keys_from_rows(
    pk_rows: list[tuple[Any, ...]],
    fk_rows: list[tuple[Any, ...]],
    skip: frozenset[str],
) -> dict[str, Any]:
    """Primary and foreign keys in the catalog column order both drivers return."""
    pks: dict[str, list[str]] = {}
    for schema, table, column, _ordinal in pk_rows:
        if str(schema).lower() in skip:
            continue
        pks.setdefault(f"{schema}.{table}", []).append(str(column))
    fks: list[dict[str, Any]] = []
    for name, cs, ct, cc, ps, pt, pc, ordinal in fk_rows:
        if str(cs).lower() in skip or str(ps).lower() in skip:
            continue
        fks.append(
            {
                "name": str(name),
                "from_table": f"{cs}.{ct}",
                "from_column": str(cc),
                "to_table": f"{ps}.{pt}",
                "to_column": str(pc),
                "ordinal": int(ordinal),
            }
        )
    return {"primary_keys": pks, "foreign_keys": fks}
