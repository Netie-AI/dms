"""FEAT-VALUE-MATCH-01. Synthetic values only. Flag default off."""

from __future__ import annotations

from typing import Any

from dms_executor.schema_context import build_schema_context, prepare_generate_context
from dms_executor.value_match import (
    ColumnInput,
    embed_body,
    match_prompt,
    role_embedder,
    take_clarify_envelope,
    value_not_found_reconfirm,
)

_ASK = "show Flordia electronic sales"


def _sales() -> dict[str, Any]:
    return {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "sales",
                "columns": [
                    {
                        "name": "region",
                        "type": "varchar",
                        "values": ["Florida", "Georgia"],
                    },
                    {
                        "name": "category",
                        "type": "varchar",
                        "values": ["Electronics", "Furniture"],
                    },
                    {"name": "amount", "type": "double", "samples": ["10"]},
                ],
            }
        ],
    }


def _with_person() -> dict[str, Any]:
    body = _sales()
    body["datasets"].append(
        {
            "name": "customers",
            "columns": [
                {
                    "name": "customer_name",
                    "type": "varchar",
                    "values": ["Georgia Hale", "Sam Lee"],
                },
                {
                    "name": "region",
                    "type": "varchar",
                    "values": ["Florida", "Georgia"],
                },
            ],
        }
    )
    return body


def _ambiguous() -> dict[str, Any]:
    return {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "sales",
                "columns": [
                    {
                        "name": "region",
                        "type": "varchar",
                        "values": ["Florida", "Georgia"],
                    }
                ],
            },
            {
                "name": "accounts",
                "columns": [
                    {
                        "name": "region",
                        "type": "varchar",
                        "values": ["Georgia", "Texas"],
                    }
                ],
            },
        ],
    }


def _line_value(prompt: str, column: str) -> str:
    prefix = f"- {column}: "
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError(column)


def _fake_model(prompt: str) -> str:
    """Stand-in for the SQL writer. It copies stored candidates, not the span."""
    region = _line_value(prompt, "sales.region")
    category = _line_value(prompt, "sales.category")
    return f"SELECT SUM(amount) FROM sales WHERE region = '{region}' AND category = '{category}'"


def test_flag_off_prompt_bytes_and_check_match(monkeypatch) -> None:
    monkeypatch.delenv("DMS_VALUE_MATCH", raising=False)
    off = build_schema_context(_ASK, _sales(), grantable={"sales"})
    monkeypatch.setenv("DMS_VALUE_MATCH", "0")
    zero = build_schema_context(_ASK, _sales(), grantable={"sales"})
    assert off.prompt == zero.prompt
    assert off.envelope_prompt == zero.envelope_prompt
    assert off.value_match_stamp == ""
    assert off.value_match_readings == ()
    assert "CANDIDATE VALUES" not in off.prompt
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.delenv("DMS_VALUE_MATCH", raising=False)
    ctx_off = prepare_generate_context(
        {"marker": "same"}, question=_ASK, schema=_sales(), grantable={"sales"}
    )
    monkeypatch.setenv("DMS_VALUE_MATCH", "0")
    ctx_zero = prepare_generate_context(
        {"marker": "same"}, question=_ASK, schema=_sales(), grantable={"sales"}
    )
    assert ctx_off == ctx_zero
    granted = {"sales.region": ("Florida", "Georgia")}
    bad = "SELECT SUM(amount) FROM sales WHERE region = 'Flordia'"
    assert value_not_found_reconfirm(bad, asked=_ASK, granted=granted) is None


def test_misspelling_candidates_and_stored_filter(monkeypatch) -> None:
    monkeypatch.delenv("DMS_VALUE_MATCH", raising=False)
    off = build_schema_context(_ASK, _sales(), grantable={"sales"})
    monkeypatch.setenv("DMS_VALUE_MATCH", "1")
    built = build_schema_context(_ASK, _sales(), grantable={"sales"})
    assert built.prompt.startswith(off.prompt)
    assert built.envelope_prompt.startswith(off.envelope_prompt)
    assert "sales.region: Florida" in built.prompt
    assert "sales.category: Electronics" in built.prompt
    assert "Flordia" not in built.prompt.split("CANDIDATE VALUES", 1)[-1]
    sql = _fake_model(built.prompt)
    assert "Florida" in sql
    assert "Electronics" in sql
    granted = dict(built.value_match_granted)
    assert value_not_found_reconfirm(sql, asked=_ASK, granted=granted) is None
    bad = "SELECT SUM(amount) FROM sales WHERE region = 'Flordia' AND category = 'Electronics'"
    env = value_not_found_reconfirm(bad, asked=_ASK, granted=granted)
    assert env is not None
    assert env["reconfirm"]["reason_code"] == "value_not_found"
    assert env["rows"] == []
    assert env["values"] == []
    assert env["sql_used"] is None
    assert env["abstained"] is True


def test_person_column_is_not_indexed_or_sent(monkeypatch) -> None:
    monkeypatch.setenv("DMS_VALUE_MATCH", "1")
    built = build_schema_context(
        "orders for Georgia Halee",
        _with_person(),
        grantable={"sales", "customers"},
    )
    assert "customers.customer_name" not in built.value_match_columns
    assert "Georgia Hale" not in built.prompt
    assert "Sam Lee" not in built.prompt
    assert "- customers.customer_name:" not in built.prompt


def test_ambiguous_columns_are_clarify_readings(monkeypatch) -> None:
    monkeypatch.setenv("DMS_VALUE_MATCH", "1")
    asked = "total for Georgia"
    built = build_schema_context(asked, _ambiguous(), grantable={"sales", "accounts"})
    columns = {item[2] for item in built.value_match_readings}
    assert columns == {"sales.region", "accounts.region"}
    assert {item[3] for item in built.value_match_readings} == {"Georgia"}
    env = take_clarify_envelope(asked)
    assert env is not None
    assert env["clarify"]["blocks"] is True
    texts = {item["text"] for item in env["clarify"]["readings"]}
    assert "sales.region is Georgia" in texts
    assert "accounts.region is Georgia" in texts
    assert env["rows"] == []
    assert env["sql_used"] is None


def test_masked_and_row_filtered_columns_are_skipped(monkeypatch) -> None:
    monkeypatch.setenv("DMS_VALUE_MATCH", "1")
    masked = {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "sales",
                "row_filter": "tenant = 1",
                "columns": [
                    {
                        "name": "region",
                        "type": "varchar",
                        "values": ["Florida", "Georgia"],
                        "masked": True,
                    }
                ],
            }
        ],
    }
    built = build_schema_context(_ASK, masked, grantable={"sales"})
    assert built.value_match_columns == ()
    assert "CANDIDATE VALUES" not in built.prompt
    filtered = {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "sales",
                "columns": [
                    {
                        "name": "region",
                        "type": "varchar",
                        "values": ["Florida", "Georgia"],
                        "row_filter": "region <> ''",
                    }
                ],
            }
        ],
    }
    held = build_schema_context(_ASK, filtered, grantable={"sales"})
    assert "sales.region" not in held.value_match_columns
    assert "CANDIDATE VALUES" not in held.prompt


def test_caps_are_stamped_when_hit(monkeypatch) -> None:
    monkeypatch.setenv("DMS_VALUE_MATCH", "1")
    monkeypatch.setenv("DMS_VALUE_MATCH_PER_COLUMN", "1")
    monkeypatch.setenv("DMS_VALUE_MATCH_TOKEN_BUDGET", "1")
    built = build_schema_context("Flordia Georga", _sales(), grantable={"sales"})
    assert "value_match_cap:per_column" in built.value_match_stamp
    assert "value_match_cap:token_budget" in built.value_match_stamp


def test_embed_role_has_no_model_and_a_failure_keeps_bm25(monkeypatch) -> None:
    body = embed_body(["Florida", "Flordia"])
    assert body["role"] == "embed"
    assert "model" not in body
    assert "provider" not in body
    seen: dict[str, Any] = {}

    def transport(payload: dict[str, Any]) -> list[list[float]]:
        seen["body"] = payload
        return [[1.0, 0.0] for _ in payload["input"]]

    monkeypatch.setenv("DMS_VALUE_MATCH", "1")
    column = ColumnInput(
        "sales",
        "region",
        ("Florida", "Georgia"),
    )
    ranked = match_prompt([column], "Flordia", embedder=role_embedder(transport))
    assert seen["body"]["role"] == "embed"
    assert "model" not in seen["body"]
    assert "provider" not in seen["body"]
    assert any(item.value == "Florida" for item in ranked.candidates)

    def down(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed_down")

    kept = match_prompt([column], "Flordia", embedder=down)
    assert any(item.value == "Florida" for item in kept.candidates)
