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
    canonical_decimal,
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
    assert validate_spec(_raw(reason="x" * 500)).reason == "x" * 500


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
