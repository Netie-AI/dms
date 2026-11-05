"""Governed action types (ACT-01): the registry and the spec validator.

An action type is declared in code. Its object type, source table, key fields
and the one field it may write are identifiers chosen here, never taken from a
request. A request supplies only a spec: which action, which object (by key),
the new value, ``set`` or ``clear``, and a reason.

Values are canonical Decimal strings (``'450.00'`` becomes ``'450'``). A float
is never stored. Key values are whitespace-stripped and never case-folded
(hard rule 12); whether a key matches the source encoding is ACT-02's check.

``spec_sha`` and ``state_hash`` are sha256 digests of sorted, compact JSON, the
``proposals._token_for`` convention. They are state digests, not a chain: the
one chain is in Cortex (hard rule 3).

``DMS_ACTIONS`` defaults off. Nothing on main calls this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Context, Decimal, InvalidOperation
from typing import Any

from dms_core.control_plane.ask_audit_scrub import mask_pii_counted, scrub_counted
from dms_core.pii import _luhn_ok, _valid_yymmdd


def actions_enabled() -> bool:
    """True only when ``DMS_ACTIONS`` is the digit 1. Unset is off."""
    return os.environ.get("DMS_ACTIONS", "0").strip() == "1"


@dataclass(frozen=True)
class ActionType:
    name: str
    object_type: str
    source_kind: str
    source_table: str
    key_fields: tuple[str, ...]
    field: str
    value_min: Decimal
    value_max: Decimal
    description: str


ACTION_TYPES: dict[str, ActionType] = {
    "inventory.set_reorder_level": ActionType(
        name="inventory.set_reorder_level",
        object_type="inventory_item",
        source_kind="demo_warehouse",
        source_table="inventory",
        key_fields=("sku", "location_id"),
        field="reorder_level_kg",
        value_min=Decimal("0"),
        value_max=Decimal("10000000"),
        description=(
            "Override the reorder level (kg) of one SKU at one location. "
            "Writes the DMS-owned writeback table only; the source is not edited."
        ),
    ),
}

ALLOWED_KEYS = frozenset({"action_type", "object_key", "value", "op", "reason"})
OPS = ("set", "clear")
REASON_MAX = 500
#: The longest key value (a SKU or location id) an action accepts.
KEY_VALUE_MAX = 200
_MAX_EXPONENT = 64
#: Significant digits a value may carry, and the longest text read as a number.
#: The registry's widest range is 8 integer digits; 40 leaves room for a fraction.
MAX_DIGITS = 40
_MAX_VALUE_TEXT = 100

# ASCII digits only: ``\d`` would also read fullwidth and Arabic-Indic digits.
_NUMBER = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_NON_FINITE = re.compile(r"^[+-]?(?:nan|snan|inf|infinity)$", re.IGNORECASE)


class ActionSpecError(ValueError):
    """A spec the action core refuses. ``code`` is a stable identifier."""

    status_code = 422

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    object_key: dict[str, str] = dc_field(hash=False)
    value: str | None
    op: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "object_key": dict(self.object_key),
            "value": self.value,
            "op": self.op,
            "reason": self.reason,
        }


def _digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _to_decimal(raw: Any) -> Decimal:
    if isinstance(raw, bool) or raw is None:
        raise ActionSpecError("value_not_number", "value must be a number")
    if isinstance(raw, Decimal):
        dec = raw
    elif isinstance(raw, int):
        dec = Decimal(raw)
    elif isinstance(raw, float):
        # repr is the shortest round-trip text; the stored form is a string.
        dec = Decimal(repr(raw))
    elif isinstance(raw, str):
        text = raw.strip()
        if len(text) > _MAX_VALUE_TEXT:
            raise ActionSpecError("value_out_of_range", "value has too many characters")
        if _NON_FINITE.match(text):
            raise ActionSpecError("value_not_finite", "value must be finite")
        if not _NUMBER.match(text):
            raise ActionSpecError("value_not_number", "value must be a number")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:  # pragma: no cover - regex already gates
            raise ActionSpecError("value_not_number", "value must be a number") from exc
    else:
        raise ActionSpecError("value_not_number", "value must be a number")
    if not dec.is_finite():
        raise ActionSpecError("value_not_finite", "value must be finite")
    if len(dec.as_tuple().digits) > MAX_DIGITS:
        raise ActionSpecError("value_out_of_range", f"value has more than {MAX_DIGITS} digits")
    return dec


def canonical_decimal(raw: Any) -> str:
    """The one stored form of a number: no exponent, no trailing zeros, no -0."""
    dec = _to_decimal(raw)
    if dec == 0:
        return "0"
    if abs(dec.adjusted()) > _MAX_EXPONENT:
        raise ActionSpecError("value_out_of_range", "value magnitude out of range")
    # A precision as wide as the input, so normalising never rounds.
    exact = Context(prec=max(28, len(dec.as_tuple().digits)))
    return format(dec.normalize(exact), "f")


def validate_spec(raw: Any) -> ActionSpec:
    """Validate a request spec against the code-declared registry."""
    if not isinstance(raw, Mapping):
        raise ActionSpecError("field_unknown", "spec must be an object")
    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        raise ActionSpecError("field_unknown", ", ".join(map(str, unknown)))

    name = raw.get("action_type")
    at = ACTION_TYPES.get(name) if isinstance(name, str) else None
    if at is None:
        raise ActionSpecError("action_type_unknown", str(name))

    key_raw = raw.get("object_key")
    if not isinstance(key_raw, Mapping):
        raise ActionSpecError("key_field_missing", "object_key must be an object")
    missing = [k for k in at.key_fields if k not in key_raw]
    if missing:
        raise ActionSpecError("key_field_missing", ", ".join(missing))
    extra = sorted(str(k) for k in key_raw if k not in at.key_fields)
    if extra:
        raise ActionSpecError("key_field_extra", ", ".join(extra))
    object_key: dict[str, str] = {}
    for k in at.key_fields:
        v = key_raw[k]
        if not isinstance(v, str) or not v.strip():
            raise ActionSpecError("key_value_blank", k)
        v = v.strip()
        if len(v) > KEY_VALUE_MAX:
            raise ActionSpecError("key_value_invalid", f"{k} is longer than {KEY_VALUE_MAX}")
        # A key is copied verbatim into the writeback row, the diff summary, the
        # append-only receipt and the Cortex ledger. It cannot be masked without
        # losing which object changed, so one that reads as PII or a secret is refused.
        if mask_text(v) != v:
            raise ActionSpecError("key_value_invalid", f"{k} reads as personal data or a secret")
        object_key[k] = v

    op = raw.get("op", "set")
    if op not in OPS:
        raise ActionSpecError("op_unknown", str(op))

    value: str | None
    if op == "clear":
        if raw.get("value") is not None:
            raise ActionSpecError("value_not_number", "clear takes no value")
        value = None
    else:
        value = canonical_decimal(raw.get("value"))
        dec = Decimal(value)
        if dec < at.value_min or dec > at.value_max:
            raise ActionSpecError(
                "value_out_of_range", f"{value} not in [{at.value_min}, {at.value_max}]"
            )

    reason = raw.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        raise ActionSpecError("field_unknown", "reason must be text")
    reason = reason.strip()
    if len(reason) > REASON_MAX:
        raise ActionSpecError("reason_too_long", f"{len(reason)} > {REASON_MAX}")
    # The reason lands in an append-only diff and receipt: no secret, no PII.
    reason = mask_text(reason)
    # Checked again once masked: apply re-validates the stored (masked) reason, so a
    # mask that lengthens it must be refused here, not at every later apply.
    if len(reason) > REASON_MAX:
        raise ActionSpecError("reason_too_long", f"{len(reason)} > {REASON_MAX} once masked")

    return ActionSpec(action_type=at.name, object_key=object_key, value=value, op=op, reason=reason)


def spec_sha(spec: ActionSpec) -> str:
    return _digest(spec.as_dict())


def diff_sha(diff: Mapping[str, Any]) -> str:
    """Digest of a whole stored action diff, pinned in ``dms.action_proposals``."""
    return _digest(dict(diff))


def mask_text(text: str) -> str:
    """Secrets scrubbed, then PII masked, as ``ask_audit`` stores a question.

    Idempotent: masked text masks to itself, so a stored reason re-validates to
    the same spec digest.
    """
    if not text:
        return text
    clean, _ = scrub_counted(text)
    masked, _ = mask_pii_counted(clean)
    return masked


def mask_json(value: Any) -> Any:
    """``mask_text`` over every string (keys too) and integer in a JSON-shaped value.

    An integer is masked by its decimal text: one the masker would change (an
    NRIC or a phone number written as a number) becomes the masked string, any
    other stays an integer. Masked text masks to itself, so this is idempotent.
    """
    if isinstance(value, Mapping):
        return {mask_text(str(k)): mask_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_json(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
        masked = mask_text(text)
        return value if masked == text else masked
    return value


#: Evidence keys that hold a structural identifier (a ledger pointer, a decision
#: id). The PII masker is never run over them: it matches digit runs inside ids
#: (about 3% of UUID4 ids, 7% of 64-hex ids) and a mangled pointer points nowhere.
#: They are validated by shape instead and kept verbatim.
EVIDENCE_ID_KEYS = frozenset({"audit_id", "reverts_decision_id"})
_ID_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def mask_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evidence as stored: identifiers verbatim (shape-checked), everything else masked.

    An identifier key whose value is not an ASCII id of at most 128 characters
    (letters, digits, ``_ . : -``) is refused (``evidence_id_invalid``) rather
    than masked: masking could only turn it into a pointer to nothing.
    Idempotent, like ``mask_json``.
    """
    out: dict[str, Any] = {}
    for key, value in evidence.items():
        name = str(key)
        if name in EVIDENCE_ID_KEYS:
            if not isinstance(value, str) or not _ID_SHAPE.match(value):
                raise ActionSpecError("evidence_id_invalid", f"evidence {name} is not an id")
            out[name] = value
        else:
            out[mask_text(name)] = mask_json(value)
    return out


APPROVAL_REF_MIN = 3
APPROVAL_REF_MAX = 200
_REF_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:#-]*\Z")
_HAS_LETTER = re.compile(r"[A-Za-z]")
#: An NRIC inside a ref, bounded by non-digits only (a letter prefix does not hide it).
_REF_NRIC = re.compile(r"(?<!\d)(\d{6})-?\d{2}-?\d{4}(?!\d)")
#: A card number inside a ref: 13-16 contiguous digits, or 4-4-4-(1-4) grouped by "-".
_REF_CARD = re.compile(r"(?<!\d)(?:\d{13,16}|\d{4}-\d{4}-\d{4}-\d{1,4})(?!\d)")


def approval_ref_problem(ref: str) -> str | None:
    """Why an (already stripped) approval reference is refused, or None.

    A ref names a change ticket (``CHG-2026-1030-4417``, ``CAB-1``): it is an
    identifier, kept verbatim, so it is validated by shape and never PII-masked
    (masking collapses distinct numeric tickets to one token). The shape: one
    ASCII token of letters, digits and ``_ . / : # -``, with at least one letter,
    so a bare NRIC, phone or card number is refused, and an email or a spaced
    narrative cannot pass. A ref the secret scrubber would change is refused too.

    Because the ref is kept verbatim, a ticket-shaped ref that carries an NRIC
    (``CHG-900101-14-5678``, ``NRIC900101145678``) or a Luhn-valid card number
    is refused as well: masking it would collapse it, storing it would put PII on
    the immutable ledger. Only the NRIC and card classes are checked; the
    masker's account and phone classes match ordinary numeric tickets
    (``CHG-2026-1030-4417``, ``INC-60123456789``).
    Returns ``approval_ref_too_long`` or ``approval_ref_invalid``.
    """
    if len(ref) > APPROVAL_REF_MAX:
        return "approval_ref_too_long"
    if not _REF_SHAPE.match(ref) or not _HAS_LETTER.search(ref):
        return "approval_ref_invalid"
    _clean, hits = scrub_counted(ref)
    if hits:
        return "approval_ref_invalid"
    masked, _ = mask_pii_counted(ref)
    if "DMSMASK_nric_" in masked or any(
        _valid_yymmdd(m.group(1)) for m in _REF_NRIC.finditer(ref)
    ):
        return "approval_ref_invalid"
    if any(_luhn_ok(m.group(0).replace("-", "")) for m in _REF_CARD.finditer(ref)):
        return "approval_ref_invalid"
    return None


def object_key_hash(object_key: Mapping[str, Any]) -> str:
    return _digest(dict(object_key))


def state_hash(state: Mapping[str, Any]) -> str:
    """Digest of one writeback cell. An absent row is value None, row_version 0."""
    return _digest(
        {
            "object_type": state["object_type"],
            "object_key": dict(state["object_key"]),
            "field": state["field"],
            "value": state["value"],
            "row_version": int(state["row_version"]),
        }
    )
