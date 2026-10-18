"""Shared constants for measure authority and badge wording. Constants only: no imports.

Envelope, generative ask and measure use all read these; tests import them
rather than retyping the strings.
"""

BADGE_UNCONFIRMED = "L2_UNCONFIRMED"
#: Badge a confirmed-measure answer wears. Staged at L2_VALIDATED; a later,
#: founder-gated step flips it to L1_GOVERNED_METRIC.
CONFIRMED_MEASURE_BADGE = "L2_VALIDATED"

UNCONFIRMED_PREFIX = "measure unconfirmed: "
UNCONFIRMED_LINE = (
    "measure unconfirmed: this figure comes from generated SQL, not from a measure a "
    "steward has confirmed. It passed the scope, join and fan-out checks but is not certified."
)
UNCONFIRMED_TEXT = "This figure comes from generated SQL, not a steward-confirmed measure."

CONFIRMED_PREFIX = "measure confirmed: "
CONFIRMED_MEMORY_ONLY_SUFFIX = " [confirmation held in memory only]"

MEASURE_STATUS_CONFIRMED = "confirmed"
MEASURE_STATUS_UNCONFIRMED = "unconfirmed"

#: Caps (Cortex wire budget is the reason for the confirmed cap).
MAX_CONFIRMED_MEASURES = 80
MAX_PROPOSED_PER_RUN = 60
MAX_DESCRIPTION_CHARS = 200

#: The keys a ``measure_basis`` object carries, in the order the envelope shows them. The
#: envelope builder and ``assert_envelope_valid`` accept exactly these and nothing else:
#: the key travels through the PII masker unscanned (see ``dms_core.pii.SAFE_ENVELOPE_KEYS``),
#: so what it may hold is pinned to identifier shapes, never free text.
CONFIRMED_BASIS_KEYS = (
    "status",
    "measure_id",
    "measure",
    "definition_hash",
    "version_id",
    "confirmed_at",
    "ledger_entry_id",
    "grain",
    "columns_checked",
    "persisted",
)
#: How much of the definition hash the human-readable assumption line shows.
CONFIRMED_LINE_HASH_CHARS = 12
