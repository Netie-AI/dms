"""ACT-01. No skip, no xfail. CI runs this file inside pytest tests/.

Pure checks on the governed action type registry and the spec validator. No
database. The Postgres half is ``tests/control_plane/test_act_01_writeback.py``.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import pytest
from dms_core.control_plane.action_types import (
    ACTION_TYPES,
    ActionSpecError,
    actions_enabled,
    approval_ref_problem,
    canonical_decimal,
    mask_evidence,
    mask_json,
    spec_sha,
    state_hash,
    validate_spec,
)


def _raw(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "action_type": "inventory.set_reorder_level",
        "object_key": {"sku": "SKU-BETA", "location_id": "WH-B"},
        "value": "450.00",
        "op": "set",
        "reason": "lead time doubled",
    }
    raw.update(over)
    return raw


def _code(raw: Any) -> str:
    with pytest.raises(ActionSpecError) as info:
        validate_spec(raw)
    return info.value.code


def test_flag_defaults_off_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ACTIONS", raising=False)
    assert actions_enabled() is False
    for off in ("true", "yes", " 0", "0", "", "on", "TRUE", "2"):
        monkeypatch.setenv("DMS_ACTIONS", off)
        assert actions_enabled() is False, off
    for on in ("1", " 1 ", "1\n"):
        monkeypatch.setenv("DMS_ACTIONS", on)
        assert actions_enabled() is True, on


def test_registry_holds_exactly_one_code_declared_type() -> None:
    assert list(ACTION_TYPES) == ["inventory.set_reorder_level"]
    at = ACTION_TYPES["inventory.set_reorder_level"]
    assert at.object_type == "inventory_item"
    assert at.source_kind == "demo_warehouse"
    assert at.source_table == "inventory"
    assert at.key_fields == ("sku", "location_id")
    assert at.field == "reorder_level_kg"
    assert at.value_min == Decimal("0")
    assert at.value_max == Decimal("10000000")
    with pytest.raises(AttributeError):
        at.field = "qty_on_hand_kg"  # type: ignore[misc]


def test_spec_accepts_and_canonicalises() -> None:
    spec = validate_spec(_raw())
    assert spec.action_type == "inventory.set_reorder_level"
    assert spec.object_key == {"sku": "SKU-BETA", "location_id": "WH-B"}
    assert spec.value == "450"
    assert spec.op == "set"
    assert spec.reason == "lead time doubled"
    assert validate_spec(_raw(value="1e3")).value == "1000"
    assert validate_spec(_raw(value=450)).value == "450"
    assert validate_spec(_raw(value="0.50")).value == "0.5"
    assert validate_spec(_raw(value="-0")).value == "0"
    assert validate_spec(_raw(value="10000000")).value == "10000000"
    # op and reason are optional; op defaults to set.
    bare = validate_spec(
        {
            "action_type": "inventory.set_reorder_level",
            "object_key": {"sku": "SKU-BETA", "location_id": "WH-B"},
            "value": "450",
        }
    )
    assert (bare.op, bare.reason) == ("set", "")


def test_spec_refusals() -> None:
    assert _code(_raw(value="-1")) == "value_out_of_range"
    assert _code(_raw(value="10000000.01")) == "value_out_of_range"
    assert _code(_raw(value="NaN")) == "value_not_finite"
    assert _code(_raw(value="Infinity")) == "value_not_finite"
    assert _code(_raw(value="-inf")) == "value_not_finite"
    assert _code(_raw(value=float("nan"))) == "value_not_finite"
    assert _code(_raw(value="abc")) == "value_not_number"
    assert _code(_raw(value="4_50")) == "value_not_number"
    assert _code(_raw(value=True)) == "value_not_number"
    assert _code(_raw(value=None)) == "value_not_number"
    assert _code(_raw(action_type="inventory.delete")) == "action_type_unknown"
    assert _code(_raw(object_key={"sku": "SKU-BETA"})) == "key_field_missing"
    assert (
        _code(_raw(object_key={"sku": "SKU-BETA", "location_id": "WH-B", "bin": "3"}))
        == "key_field_extra"
    )
    assert _code(_raw(object_key={"sku": "  ", "location_id": "WH-B"})) == "key_value_blank"
    assert _code(_raw(actor="someone")) == "field_unknown"
    assert _code(_raw(op="delete")) == "op_unknown"
    assert _code(_raw(reason="x" * 501)) == "reason_too_long"
    assert _code("not a mapping") == "field_unknown"
    # 500 characters is the limit, not past it.
    words = ("lead time doubled " * 28)[:500]
    assert validate_spec(_raw(reason=words)).reason == words.strip()


def test_reason_is_masked_before_it_is_pinned() -> None:
    """The reason lands in an append-only diff and receipt: PII and secrets never do."""
    spec = validate_spec(
        _raw(reason="asked by 900101-14-5678 (jian@example.com), password=hunter2xyz")
    )
    assert "900101-14-5678" not in spec.reason
    assert "jian@example.com" not in spec.reason
    assert "hunter2xyz" not in spec.reason
    assert spec.reason == "asked by DMSMASK_nric_01 (DMSMASK_email_01), password=[redacted]"
    # Masking is idempotent, so the stored reason re-validates to the same digest.
    again = validate_spec({**spec.as_dict()})
    assert again.reason == spec.reason
    assert spec_sha(again) == spec_sha(spec)


def test_values_are_ascii_and_bounded() -> None:
    """Fullwidth and Arabic-Indic digits are not numbers; a value has at most 40 digits."""
    assert _code(_raw(value="\uff14\uff15\uff10")) == "value_not_number"
    assert _code(_raw(value="\u0664\u0665\u0660")) == "value_not_number"
    assert _code(_raw(value="1" + "0" * 100_000)) == "value_out_of_range"
    assert _code(_raw(value="4." + "1" * 45)) == "value_out_of_range"
    assert _code(_raw(value=Decimal("4." + "1" * 45))) == "value_out_of_range"
    assert validate_spec(_raw(value="4." + "1" * 39)).value == "4." + "1" * 39


def test_key_values_are_stripped_never_case_folded() -> None:
    spec = validate_spec(_raw(object_key={"sku": "sku-beta ", "location_id": " WH-B"}))
    assert spec.object_key == {"sku": "sku-beta", "location_id": "WH-B"}


def test_value_is_never_a_float() -> None:
    spec = validate_spec(_raw(value=450.5))
    assert spec.value == "450.5"
    assert isinstance(spec.value, str)
    assert canonical_decimal("450.00") == "450"
    assert canonical_decimal("1E+3") == "1000"


def test_clear_takes_no_value() -> None:
    spec = validate_spec(_raw(op="clear", value=None))
    assert (spec.op, spec.value) == ("clear", None)
    assert _code(_raw(op="clear", value="450")) == "value_not_number"


def test_spec_sha_and_state_hash_follow_the_token_convention() -> None:
    spec = validate_spec(_raw())
    expected = hashlib.sha256(
        json.dumps(
            {
                "action_type": "inventory.set_reorder_level",
                "object_key": {"sku": "SKU-BETA", "location_id": "WH-B"},
                "value": "450",
                "op": "set",
                "reason": "lead time doubled",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert spec_sha(spec) == expected
    # Same spec written two ways hashes the same.
    assert spec_sha(validate_spec(_raw(value="4.5e2"))) == expected

    absent = state_hash(
        {
            "object_type": "inventory_item",
            "object_key": {"sku": "SKU-BETA", "location_id": "WH-B"},
            "field": "reorder_level_kg",
            "value": None,
            "row_version": 0,
        }
    )
    cleared = state_hash(
        {
            "object_type": "inventory_item",
            "object_key": {"sku": "SKU-BETA", "location_id": "WH-B"},
            "field": "reorder_level_kg",
            "value": None,
            "row_version": 2,
        }
    )
    # A row that was set and then cleared is not the absent row (ABA-safe).
    assert absent != cleared
    assert len(absent) == 64


def test_single_alembic_head() -> None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0009_action_writeback"]


def test_key_values_that_read_as_pii_or_run_long_are_refused() -> None:
    """A key is copied verbatim into the receipt and the ledger, so PII is refused, not kept."""
    for key in (
        {"sku": "900101-14-5678", "location_id": "WH-B"},
        {"sku": "SKU-BETA", "location_id": "ali@example.com"},
        {"sku": "S" * 201, "location_id": "WH-B"},
    ):
        assert _code(_raw(object_key=key)) == "key_value_invalid", key
    with pytest.raises(ActionSpecError) as info:
        validate_spec(_raw(object_key={"sku": "900101-14-5678", "location_id": "WH-B"}))
    assert str(info.value) == "key_value_invalid: sku reads as personal data or a secret"
    # Real SKU shapes, an EAN-13 among them, pass through unchanged.
    for sku in ("SKU-BETA", "9556001234567", "ITEM-2026-0042", "S" * 200):
        spec = validate_spec(_raw(object_key={"sku": sku, "location_id": "WH-B"}))
        assert spec.object_key == {"sku": sku, "location_id": "WH-B"}


def test_reason_that_masks_past_the_limit_is_refused_at_propose() -> None:
    """Before the fix this validated at propose and failed every later apply as tampering."""
    reason = ("a@b.co " * 80)[:500]
    assert len(reason.strip()) <= 500
    assert _code(_raw(reason=reason)) == "reason_too_long"


def test_mask_json_masks_integers_like_their_text() -> None:
    masked = mask_json({"nric_int": 900101145678, "rows": 3, "flag": True, "ok": "SKU-BETA"})
    assert masked == {"nric_int": "DMSMASK_nric_01", "rows": 3, "flag": True, "ok": "SKU-BETA"}
    assert mask_json(masked) == masked


def test_mask_evidence_keeps_ids_verbatim_and_masks_free_text() -> None:
    """ACT-02 MEDIUM 1: about 3% of UUID4 and 7% of 64-hex ids were rewritten."""
    uuid4_id = "ce4c9504-8966-4851-8653-567a9ade70f6"
    hex_id = "f5ca38f748a1d6eaf726b8a42fb575c3c71f1864a8143301782de13da2d9202b"
    assert mask_json(uuid4_id) != uuid4_id, "the masker alone does mangle this id"
    ev = mask_evidence(
        {"audit_id": uuid4_id, "reverts_decision_id": hex_id, "question": "for 900101-14-5678"}
    )
    assert ev == {
        "audit_id": uuid4_id,
        "reverts_decision_id": hex_id,
        "question": "for DMSMASK_nric_01",
    }
    assert mask_evidence(ev) == ev
    for bad in ("ali@example.com", "id with space", "", 12345, "x" * 129):
        with pytest.raises(ActionSpecError) as info:
            mask_evidence({"audit_id": bad})
        assert info.value.code == "evidence_id_invalid", bad


def test_approval_ref_is_a_ticket_shape_kept_verbatim() -> None:
    """ACT-02 MEDIUM 2: numeric change refs are kept; non-ticket refs are refused."""
    for ok in (
        "CHG-2026-1030-4417",
        "CHG-2026-1031-9999",
        "INC-60123456789",
        "CAB-1",
        "INC0012345678",
        "RFC-2026/10#77",
        "X" * 200,
    ):
        assert approval_ref_problem(ok) is None, ok
    for bad in (
        "CAB jian@example.com",
        "900101-14-5678",
        "+60123456789",
        "4111111111111111",
        "CAB-1​",
        "ＣAB-1",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
    ):
        assert approval_ref_problem(bad) == "approval_ref_invalid", bad
    assert approval_ref_problem("X" * 201) == "approval_ref_too_long"


def test_core_actor_is_canonical_and_default_refused_in_any_spelling() -> None:
    """ACT-02 MEDIUM 3: compared as a parsed UUID, returned in canonical form."""
    from dms_core.control_plane.actions import ActorRequired, _require_actor

    for spelling in (
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}",
        "urn:uuid:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
    ):
        with pytest.raises(ActorRequired):
            _require_actor(spelling)
    canonical = "ad4b40d3-12ba-4c3e-9d6f-0123456789ab"
    for spelling in (canonical.upper(), canonical.replace("-", ""), f"{{{canonical}}}"):
        assert _require_actor(spelling) == canonical
    assert _require_actor("steward-1") == "steward-1"
