"""The sentence a steward-confirmed answer carries, and the identifier shapes behind it (dms#284).

Pure: no imports from this package, no I/O. It lives in core because two layers need the same
spelling and neither may import the other: the executor builds the ``measure confirmed:`` line
(and validates a ``measure_basis`` against these shapes), and the PII scan (``dms_core.pii``)
must leave exactly that sentence alone. A digest prefix or a ledger id that happens to look
like a phone number is an identifier DMS minted, not a person's number; a masked digest is one
nobody can compare against the stored row.

The exemption is narrow by construction: ``sanctioned_confirmed_line`` returns the sentence
only when EVERY field it embeds has its identifier shape, and the scan exempts a string only
when it is equal to that sentence. Free text cannot ride it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CONFIRMED_PREFIX = "measure confirmed: "
CONFIRMED_MEMORY_ONLY_SUFFIX = " [confirmation held in memory only]"
#: How much of the definition hash the human-readable line shows.
CONFIRMED_LINE_HASH_CHARS = 12

#: The shape every identifier of a ``measure_basis`` must have (``fullmatch``). The key travels
#: through the PII scan unscanned, so what it may hold is pinned to these, never free text.
BASIS_SHAPES: dict[str, re.Pattern[str]] = {
    "measure_id": re.compile(r"[0-9A-Fa-f-]{8,64}"),
    "measure": re.compile(r"[a-z][a-z0-9_]{1,47}"),
    "definition_hash": re.compile(r"[0-9a-f]{64}"),
    "version_id": re.compile(r"[0-9A-Fa-f-]{8,64}"),
    "confirmed_at": re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.+\-Z]{5,40}"),
    "ledger_entry_id": re.compile(r"[A-Za-z0-9_.:\-]{1,128}"),
    "grain": re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"),
}
#: The fields the sentence embeds.
_LINE_FIELDS = ("measure", "definition_hash", "confirmed_at", "ledger_entry_id")


def confirmed_line(basis: Mapping[str, Any]) -> str:
    """The ``measure confirmed:`` sentence a customer reads, built from a ``measure_basis``.

    The only place the sentence is spelled. ``measure_use.confirmed_basis`` builds the answer's
    line with it, so the structured basis and the sentence can never disagree.
    """
    line = (
        f"{CONFIRMED_PREFIX}{basis['measure']} "
        f"(definition {str(basis['definition_hash'])[:CONFIRMED_LINE_HASH_CHARS]}, "
        f"confirmed {str(basis['confirmed_at'])[:10]} by this workspace's steward account, "
        f"ledger {basis['ledger_entry_id']})"
    )
    if not basis.get("persisted"):
        line += CONFIRMED_MEMORY_ONLY_SUFFIX
    return line


def sanctioned_confirmed_line(basis: Any) -> str | None:
    """``confirmed_line(basis)`` when every field it embeds has its identifier shape, else None.

    What the PII scan may leave alone. A basis that is not an object, or has a field off its
    shape, sanctions nothing and the sentence is scanned like any other text.
    """
    if not isinstance(basis, Mapping):
        return None
    for key in _LINE_FIELDS:
        value = basis.get(key)
        if not (isinstance(value, str) and BASIS_SHAPES[key].fullmatch(value)):
            return None
    return confirmed_line(basis)
