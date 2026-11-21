"""ONTO-SCOPE-01 (dms#523): the approved-item ontology of one Space.

``Ontology.from_store()`` with no argument reads every approved item of the
install. Rule 16 says a Space answers only through its own verified ontology,
so the readers on the ask path build theirs here instead, for the Space that
is asking:

* an item whose version names the asking Space is used;
* an item whose version names another Space is never used;
* an item whose version names no Space: see ``unassigned_items_govern`` (open
  founder question, today's behaviour held);
* a ``pii`` item is kept from every version. It is a restriction, not a grant,
  and scoping it away would unmask a column somewhere;
* a ``profile`` item is not read here (dms#515);
* a demo Space, and any Space while ``DMS_ONTO_FROM_DATA`` is unset, read as
  they did before this ticket.

A SQL-source Space has an ontology its source declared. That one answers alone
(``ask_ontology``): the two are never merged, because a merge is a new join
origin.

Approval is not measurement. Nothing here marks an item verified: the ask path
still runs ``Ontology.verify`` against the lake before a link may be used.
"""

from __future__ import annotations

from typing import Any

from dms_core.control_plane.onto_data import ontology_data_store

from dms_executor.demo_grants import is_demo_space
from dms_executor.ontology import Ontology, onto_from_data_enabled
from dms_executor.space_ontology import load_space_ontology


def unassigned_items_govern(space_id: str | None) -> bool:
    """FOUNDER QUESTION, not answered (dms#523 clause 8).

    An approved item whose version names no Space: what does it govern?

    1. Demo Spaces only (the ticket's recommendation).
    2. Nothing, anywhere, until it is assigned to a Space.
    3. Every Space that is granted the tables it names (today's behaviour).

    Until the founder answers this returns True for every Space, which is the
    behaviour of the commit before this ticket. This function is the only
    place that decision is made. Do not answer it here without the founder.
    """
    return True


def space_items(space_id: str | None) -> list[dict[str, Any]]:
    """Approved items the asking Space may read, oldest first.

    Raises when the item store cannot be read; the caller names that abstain.
    """
    store = ontology_data_store()
    if not onto_from_data_enabled() or is_demo_space(space_id):
        # Clauses 9 and 10: a demo Space and a flag-off install are unchanged.
        return store.approved_items()
    rows = list(store.approved_items_of_space(space_id))
    if unassigned_items_govern(space_id):
        rows.extend(store.approved_items_of_space(None))
    held = {row.get("id") for row in rows}
    # A protection is install-wide: every approved pii item, whatever Space
    # its version names.
    rows.extend(
        row
        for row in store.approved_items()
        if str(row.get("kind") or "") == "pii" and row.get("id") not in held
    )
    rows.sort(key=lambda row: (str(row.get("ts") or ""), str(row.get("id") or "")))
    return rows


def space_item_ontology(space_id: str | None) -> Ontology:
    """The item ontology of one Space. Never ``Ontology.from_store()`` bare."""
    return Ontology.from_store(space_items(space_id))


def ask_ontology(space_id: str | None) -> Ontology:
    """The one ontology an ask in ``space_id`` is checked against under the flag.

    A Space with an ontology derived from its SQL source answers through that
    alone. Any other Space answers through its approved items. Never both.
    """
    if not is_demo_space(space_id):
        derived = load_space_ontology(space_id)
        if derived is not None:
            return derived
    return space_item_ontology(space_id)
