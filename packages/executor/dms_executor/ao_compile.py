"""Compile a DuckDB source into an ontology proposal.

Gated by ``DMS_AO_COMPILE`` (default ``0``). Importing this module does not
import the vendored package, read a token, or touch the connection.
A compiled proposal has status ``proposed`` and ``served`` false. There is
no path that marks one served.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

_FLAG = "DMS_AO_COMPILE"
_FK_KEYS = ("source_table", "source_column", "target_table", "target_column")
_JOIN_KEYS = ("left_table", "left_column", "right_table", "right_column")
_TERM_KEYS = ("name", "table", "description")
_ATTR_KEYS = ("table", "term", "source_column", "display_name")
_TERM_ITEM: dict[str, Any] = {
    "type": "object",
    "required": ["name", "table", "description", "key"],
    "properties": {
        "key": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
        },
    },
}

#: Shape of ``OntologyProposal.to_json`` after a compile. ``served`` is false.
PROPOSAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "status",
        "vendor_sha",
        "served",
        "terms",
        "attributes",
        "inferred_fks",
        "declared_fks",
        "joins",
        "unkeyed",
    ],
    "properties": {
        "status": {"const": "proposed"},
        "served": {"const": False},
        "vendor_sha": {"type": "string", "minLength": 40},
        "terms": {"type": "array", "items": _TERM_ITEM},
        "attributes": {"type": "array"},
        "inferred_fks": {"type": "array"},
        "declared_fks": {"type": "array"},
        "joins": {"type": "array"},
        "unkeyed": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


def ao_compile_enabled() -> bool:
    """True only when the flag is the digit 1. Anything else, including unset, is off."""
    return os.environ.get(_FLAG, "0").strip() == "1"


@dataclass(frozen=True)
class OntologyProposal:
    """JSON-serialisable proposal. ``status`` is ``proposed`` or ``off``."""

    status: str
    vendor_sha: str
    terms: tuple[dict[str, Any], ...]
    attributes: tuple[dict[str, Any], ...]
    inferred_fks: tuple[dict[str, Any], ...]
    declared_fks: tuple[dict[str, Any], ...]
    joins: tuple[dict[str, Any], ...]
    served: bool = False
    unkeyed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.served:
            raise ValueError("an ontology proposal is never served")
        if self.status not in {"proposed", "off"}:
            raise ValueError("proposal status must be proposed or off")

    @classmethod
    def off(cls) -> OntologyProposal:
        empty: tuple[dict[str, Any], ...] = ()
        return cls(
            status="off",
            vendor_sha="",
            terms=empty,
            attributes=empty,
            inferred_fks=empty,
            declared_fks=empty,
            joins=empty,
            served=False,
            unkeyed=(),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OntologyProposal:
        if payload.get("served"):
            raise ValueError("an ontology proposal is never served")
        if payload.get("status") != "proposed":
            raise ValueError("compiled proposal status must be proposed")
        proposal = cls(
            status="proposed",
            vendor_sha=str(payload.get("vendor_sha") or ""),
            terms=_rows(payload.get("terms")),
            attributes=_rows(payload.get("attributes")),
            inferred_fks=_rows(payload.get("inferred_fks")),
            declared_fks=_rows(payload.get("declared_fks")),
            joins=_rows(payload.get("joins")),
            served=False,
            unkeyed=_names(payload.get("unkeyed")),
        )
        check_proposal_schema(proposal.to_json())
        return proposal

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "vendor_sha": self.vendor_sha,
            "served": self.served,
            "terms": [dict(row) for row in self.terms],
            "attributes": [dict(row) for row in self.attributes],
            "inferred_fks": [dict(row) for row in self.inferred_fks],
            "declared_fks": [dict(row) for row in self.declared_fks],
            "joins": [dict(row) for row in self.joins],
            "unkeyed": list(self.unkeyed),
        }


def compile_source(conn: Any, *, transport: Any = None) -> OntologyProposal:
    """Propose an ontology for ``conn``. Off: return without touching ``conn``."""
    if not ao_compile_enabled():
        return OntologyProposal.off()
    from ao_vendor.compile_run import compile_catalog

    payload: dict[str, Any] = compile_catalog(conn, transport=transport)
    return OntologyProposal.from_payload(payload)


def check_proposal_schema(data: dict[str, Any]) -> None:
    """Raise ``ValueError`` when ``data`` does not match ``PROPOSAL_JSON_SCHEMA``."""
    required = PROPOSAL_JSON_SCHEMA["required"]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("proposal missing " + ", ".join(missing))
    if data["status"] != "proposed":
        raise ValueError("proposal status must be proposed")
    if data["served"] is not False:
        raise ValueError("proposal served must be false")
    sha = data["vendor_sha"]
    if not isinstance(sha, str) or len(sha) < 40:
        raise ValueError("proposal vendor_sha")
    _objects(data["terms"], _TERM_KEYS)
    _objects(data["attributes"], _ATTR_KEYS)
    _objects(data["inferred_fks"], _FK_KEYS)
    _objects(data["declared_fks"], _FK_KEYS)
    _objects(data["joins"], _JOIN_KEYS)
    key_schema = PROPOSAL_JSON_SCHEMA["properties"]["terms"]["items"]["properties"]["key"]
    min_items = int(key_schema["minItems"])
    for term in data["terms"]:
        if not isinstance(term.get("attributes"), list):
            raise ValueError("term attributes")
        key = term.get("key")
        if (
            not isinstance(key, list)
            or len(key) < min_items
            or any(not isinstance(col, str) or not col for col in key)
        ):
            raise ValueError("term key")
    _names(data["unkeyed"])


def _names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("proposal unkeyed")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("proposal unkeyed")
        names.append(item)
    return tuple(names)


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError("proposal list")
    return tuple(dict(row) for row in value)


def _objects(rows: Any, keys: tuple[str, ...]) -> None:
    if not isinstance(rows, list):
        raise ValueError("proposal list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("proposal row")
        for key in keys:
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"proposal {key}")
