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
        object_key[k] = v.strip()

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
    """``mask_text`` over every string (keys too) in a JSON-shaped value."""
    if isinstance(value, Mapping):
        return {mask_text(str(k)): mask_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_json(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


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
