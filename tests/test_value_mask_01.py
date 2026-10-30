"""VALUE-INDEX-01 (A): descriptions are masked against values as whole tokens.

The description masker replaced every loaded text value as a bare
case-insensitive substring, so a Y/N column turned "Net revenue in ringgit"
into "DMSVAL_01et reveDMSVAL_01ue ...", and a short digit value cut a year
into a number run. Synthetic data only. Pair file: test_value_index_01.py.
"""

from __future__ import annotations

from typing import Any

from dms_executor.schema_context import build_schema_context

_NET_DESC = "Net revenue in ringgit"


def _yn_schema(values: list[str], description: str = _NET_DESC) -> dict[str, Any]:
    return {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "orders",
                "description": f"Order lines. {description}",
                "columns": [
                    {
                        "name": "is_refunded",
                        "type": "varchar",
                        "description": "Y if refunded, N otherwise",
                        "values": values,
                    },
                    {"name": "net_revenue", "type": "double", "description": description},
                ],
            }
        ],
    }


def test_yn_values_do_not_shred_descriptions() -> None:
    built = build_schema_context(
        "net revenue by refund flag",
        _yn_schema(["Y", "N", "ringg"]),
        grantable={"orders"},
    )
    for text in (built.prompt, built.envelope_prompt):
        assert f"description={_NET_DESC}" in text
        assert "Y if refunded, N otherwise" in text
        assert "DMSVAL_" not in text


def test_substring_of_a_longer_word_is_not_masked_but_a_whole_token_is() -> None:
    schema = {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "ledger",
                "columns": [
                    {
                        "name": "payee",
                        "type": "varchar",
                        "description": "Billed to A&B Corp. Not A&B Corporate or Aliyah.",
                        "values": ["A&B Corp", "Ali"],
                    }
                ],
            }
        ],
    }
    prompt = build_schema_context("payee", schema, grantable={"ledger"}).prompt
    # whole token, trailing punctuation of the sentence is not part of the value
    assert "Billed to A&B Corp" not in prompt
    assert "Billed to DMSVAL_" in prompt
    # the same text as a prefix of a longer word stays intact
    assert "Not A&B Corporate or Aliyah." in prompt


def test_short_digit_values_do_not_shred_digits_in_a_description() -> None:
    # "Net revenue 2024 2025" shredded by a short digit value reads as a number run.
    built = build_schema_context(
        "net revenue",
        _yn_schema(["1", "0", "20"], "Net revenue 2024 2025 in ringgit"),
        grantable={"orders"},
    )
    assert "description=Net revenue 2024 2025 in ringgit" in built.prompt
    assert "DMSVAL_" not in built.prompt
