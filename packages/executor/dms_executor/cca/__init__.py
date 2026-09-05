"""EPIC-CCA — Constraint Cascade Ask.

An LLM may *propose* that "SEA" means eleven countries, that "commercial"
excludes housing, or that "agriculture" covers plantations and livestock. DMS
never states a proposal as fact. The pack is the proposal; a granted column's
landed values are the authority. A member reaches an answer only when a row a
this Space can read actually carries it, and the answer says which ones did not.

Stage modules (sense, asset_class, geo, segment) all certify through
``dms_executor.cca.binder`` so there is one matching rule, not four.
"""

from __future__ import annotations

from dms_executor.cca.binder import (
    BinderResult,
    TermPack,
    certify_pack,
    norm_value,
    scan_landed_columns,
)

__all__ = [
    "BinderResult",
    "TermPack",
    "certify_pack",
    "norm_value",
    "scan_landed_columns",
]
