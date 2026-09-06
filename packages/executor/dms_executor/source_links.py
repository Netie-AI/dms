"""Measure a landed extract's declared links. Reads bronze, never the source.

Deliberately not in ``db_connector``. That module is extract-only by invariant
(DR-0005, ``tests/invariants/test_extract_only.py``): a DuckDB handle on the
connector path is the shape federation takes, and the Space boundary degrades to
advisory the moment the customer's query can run in the customer's engine. The
first draft of this function put ``duckdb.connect`` there and the invariant
caught it, which is what it is for.

The split is also the honest one. ``db_connector`` talks to the source database.
This talks to what landed in bronze, and answers one question about it: do the
foreign keys the source declared survive contact with the rows we actually have?
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from dms_executor.db_connector import SourceExtract
from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
from dms_executor.ontology import from_manifest


def verify_source_links(
    extract: SourceExtract,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Measure the source's declared links against the rows that actually landed.

    This is the second half of EPIC-020 acceptance clause 2. The first half -
    read the declared keys and compile them into an ontology - already happened
    in ``SourceKeys.manifest_entry``; until this function existed the result was
    put in the receipt and dropped, so ``verify()`` had never run against a
    customer extract (SQLSRC-08, Netie-AI/dms#156).

    A declared foreign key says a value *should* exist in the parent. It does not
    say the parent side is unique on those columns, and uniqueness is the only
    property that makes a join safe to group through - so every link starts
    ``unverified`` and only measurement moves it.

    The relations are the landed bronze tables, not parquet: ``manifest_entry``
    already carries the bronze table name in ``path``, and ``from_manifest``
    takes ``relation_for`` for exactly this reason.

    Never raises on a bad source. A link that cannot be measured is reported as a
    violation and left unverified, which is the same outcome as a link measured
    and found broken: the compiler refuses to join through either. The rows are
    landed and their provenance is real; only the join is in question, so this
    must not fail the extract.
    """
    entry = extract.manifest_entry
    declared = entry.get("foreign_keys") or []
    relations = {
        (str(t["schema"]), str(t["table"])): str(t["path"])
        for t in entry.get("tables", [])
    }

    def _bronze(schema: str, table: str) -> str:
        return relations[(schema, table)]

    if not declared:
        # Distinguished from "measured and clean" on purpose. A source that
        # declares no foreign keys has told us nothing, and reporting
        # ``verified: true`` over an empty claim set would be R-0011's silent
        # fallback wearing a green badge.
        return {
            "verified": False,
            "measured": False,
            "reason": "the source declares no foreign keys, so there is nothing to measure",
            "links": [],
            "violations": [],
        }

    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db))
    try:
        onto = from_manifest(entry, relation_for=_bronze)
        # The one fact verify() cannot derive for itself. It reads a DuckDB
        # connection, so a source that was always dirty and one we cut with
        # max_rows look identical to it - and they are opposite news: "your data
        # violates this key" versus "we did not land all of it". Not passing
        # this is a fail-open, because a capped parent then reads as a whole one
        # and its orphans get disclosed instead of refused.
        capped = {p.bronze_table for p in extract.pulls if p.truncated}
        violations = onto.verify(con, capped_relations=capped)
        links = [
            {
                "name": link.name,
                "from": link.from_object,
                "from_columns": list(link.from_columns),
                "to": link.to_object,
                "to_columns": list(link.to_columns),
                "cardinality": link.cardinality,
                "max_fanout": link.max_fanout,
            }
            for link in sorted(onto.links.values(), key=lambda x: x.name)
        ]
    except Exception as exc:  # noqa: BLE001 - reported, never raised past here
        # An unreadable relation or a malformed manifest must leave the extract
        # standing and the joins refused, not 500 a pull whose rows landed.
        return {
            "verified": False,
            "measured": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "links": [],
            "violations": [],
        }
    finally:
        con.close()

    return {
        "verified": onto.verified,
        "measured": True,
        "capped_tables": sorted(capped),
        "links": links,
        # Orphans are reported whether or not they were a refusal. A capped
        # parent refuses; a parent landed whole discloses, because those child
        # rows point at members that genuinely do not exist and no named group
        # is wrong. Both are news for a steward.
        "orphans": onto.orphans,
        "violations": [
            {"check": v.check, "subject": v.subject, "detail": v.detail} for v in violations
        ],
    }
