"""SCHEMA-RETRIEVE: connector schema prompt, masked samples, token cap.

Two synthetic sources: a CRM (mysql) with an empty ontology, and a finance
ledger (sqlserver) with a model-shaped ontology. No scored-pack questions.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import pathlib
import threading
from pathlib import Path
from typing import Any

import pytest
from cortex_client.client import CortexClient
from cortex_client.compute import _insights_body
from cortex_client.insights import insights_post
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology
from dms_executor.schema_context import (
    HINT_CANDIDATE_CAP,
    INDEX_COLUMN_CAP,
    MAX_PROMPT_TOKENS,
    build_schema_context,
    build_space_index,
    cached_value_index,
    estimate_tokens,
    ontology_payload,
    prepare_generate_context,
    schema_from_serving,
)

_NAME = "Zelda Qwerty"
_EMAIL = "zelda.secret@example.com"
_PHONE = "+60123456789"
_DOB = "1990-01-15"
_PASSPORT = "A12345678"
_SEGMENT = "MIDMARKET"
_EXPR = "SUM(f.balance_amt)"
_ALIAS = "amount owed"
_MEASURE_DESC = "unpaid invoice balance"
_PACK_QUESTION = "What is our total spend by supplier country?"
_PACK_ID = "cq_low_stock_wh_a"
_BANNED_SOURCE = (
    "curated_ceo",
    "oracles.yaml",
    "questions.yaml",
    "certified_queries",
    "demo_pack",
    "score_curated",
)
_PII = (_NAME, _EMAIL, _PHONE, _DOB, _PASSPORT)


def _crm_schema() -> dict[str, Any]:
    return {
        "dialect": "mysql",
        "datasets": [
            {
                "name": "account",
                "description": "customer organisation",
                "columns": [
                    {
                        "name": "account_id",
                        "type": "bigint",
                        "primary_key": True,
                        "description": "account key",
                        "samples": ["1"],
                    },
                    {
                        "name": "segment",
                        "type": "varchar",
                        "description": "market segment",
                        "distinct": 1,
                        "values": [_SEGMENT],
                    },
                ],
            },
            {
                "name": "contact",
                "description": "person at an account",
                "columns": [
                    {
                        "name": "contact_id",
                        "type": "bigint",
                        "primary_key": True,
                        "samples": ["9"],
                    },
                    {
                        "name": "account_id",
                        "type": "bigint",
                        "samples": ["1"],
                    },
                    {
                        "name": "customer_name",
                        "type": "varchar",
                        "description": "person name",
                        "distinct": 40,
                        "values": [_NAME],
                    },
                    {
                        "name": "email",
                        "type": "varchar",
                        "distinct": 40,
                        "values": [_EMAIL],
                    },
                    {
                        "name": "phone",
                        "type": "varchar",
                        "distinct": 40,
                        "values": [_PHONE],
                    },
                    {
                        "name": "date_of_birth",
                        "type": "date",
                        "description": "birth date",
                        "samples": [_DOB],
                    },
                    {
                        "name": "passport_no",
                        "type": "varchar",
                        "description": "travel document",
                        "distinct": 40,
                        "values": [_PASSPORT],
                    },
                    {
                        "name": "memo",
                        "type": "varchar",
                        "description": f"reach {_EMAIL}",
                        "distinct": 1,
                        "values": ["noted"],
                    },
                ],
            },
            {
                "name": "restricted_note",
                "description": "not granted",
                "columns": [
                    {
                        "name": "note_body",
                        "type": "varchar",
                        "samples": ["RESTRICTEDTOKEN"],
                    }
                ],
            },
        ],
        "relationships": [
            {
                "name": "contact_account",
                "from": "contact",
                "from_column": "account_id",
                "to": "account",
                "to_column": "account_id",
            },
            {
                "name": "account_self",
                "from": "account",
                "from_column": "account_id",
                "to": "account",
                "to_column": "account_id",
            },
            {
                "name": "missing_edge",
                "from": "contact",
                "from_column": "missing_col",
                "to": "account",
                "to_column": "account_id",
            },
        ],
    }


def _finance_schema() -> dict[str, Any]:
    return {
        "dialect": "sqlserver",
        "datasets": [
            {
                "name": "invoice",
                "description": "customer invoice",
                "columns": [
                    {
                        "name": "invoice_id",
                        "type": "bigint",
                        "primary_key": True,
                        "samples": ["100"],
                    },
                    {
                        "name": "balance_amt",
                        "type": "decimal",
                        "description": "open balance",
                        "samples": ["40.00"],
                    },
                    {
                        "name": "status",
                        "type": "varchar",
                        "description": "invoice status",
                        "distinct": 1,
                        "values": ["OPEN"],
                    },
                ],
            },
            {
                "name": "payment",
                "description": "cash applied to an invoice",
                "columns": [
                    {
                        "name": "payment_id",
                        "type": "bigint",
                        "primary_key": True,
                        "samples": ["7"],
                    },
                    {
                        "name": "invoice_id",
                        "type": "bigint",
                        "samples": ["100"],
                    },
                ],
            },
        ],
        "relationships": [
            {
                "name": "payment_invoice",
                "from": "payment",
                "from_column": "invoice_id",
                "to": "invoice",
                "to_column": "invoice_id",
            }
        ],
    }


def _finance_model() -> dict[str, Any]:
    return {
        "measures": [
            {
                "name": "open_receivable",
                "grain": "invoice",
                "expression": _EXPR,
                "description": _MEASURE_DESC,
                "aliases": [_ALIAS],
            }
        ],
        "relationships": [],
    }


def _finance_ontology() -> Ontology:
    onto = Ontology()
    onto.add_object("invoice_head", "invoice", ["invoice_id"])
    onto.add_object("payment_row", "payment", ["payment_id"])
    onto.add_link(
        "payment_of_invoice",
        "payment_row",
        ["invoice_id"],
        "invoice_head",
        ["invoice_id"],
    )
    onto.add_measure(
        "open_receivable",
        "invoice_head",
        _EXPR,
        description=_MEASURE_DESC,
    )
    return onto


def _crm_prompt(ontology: Any = None) -> str:
    return build_schema_context(
        "which accounts are midmarket",
        _crm_schema(),
        ontology=ontology,
        grantable={"account", "contact"},
    ).prompt


def test_crm_empty_ontology_masks_pii_and_keeps_cleared_samples() -> None:
    for empty in (None, {}):
        prompt = _crm_prompt(empty)
        for raw in _PII:
            assert raw not in prompt
        assert "RESTRICTEDTOKEN" not in prompt
        assert "restricted_note" not in prompt
        assert "account.segment = midmarket" in prompt
        assert _SEGMENT not in prompt
        assert f"samples={_SEGMENT}" not in prompt
        assert "account_self" not in prompt
        assert "missing_col" not in prompt
        assert "samples=" in prompt
        assert "date_of_birth" in prompt
        assert "passport_no" in prompt
        assert "customer_name" in prompt
        assert "bigint" in prompt
        assert "varchar" in prompt
        assert "primary_key" in prompt
        assert "foreign_key=account.account_id" in prompt
        assert "reason=score:" in prompt
        assert "reason=path:contact.account_id>account.account_id" in prompt
        assert "contact.account_id = account.account_id (contact_account)" in prompt
        assert "DIALECT: mysql" in prompt
        assert "postgres" not in prompt.lower()
        assert "MEASURES" not in prompt
        assert estimate_tokens(prompt) <= MAX_PROMPT_TOKENS
        assert ontology_payload(empty) == {"measures": [], "relationships": []}


def test_finance_model_ontology_and_curated_object_share_one_shape() -> None:
    model = _finance_model()
    prompt = build_schema_context(
        "open receivable amount owed",
        _finance_schema(),
        ontology=model,
    ).prompt
    assert "DIALECT: sqlserver" in prompt
    assert "postgres" not in prompt.lower()
    assert _EXPR in prompt
    assert _ALIAS in prompt
    assert _MEASURE_DESC in prompt
    assert "invoice.status = open" in prompt
    assert "OPEN" not in prompt
    assert "samples=OPEN" not in prompt
    assert "samples=40.00" in prompt
    assert "payment.invoice_id = invoice.invoice_id (payment_invoice)" in prompt
    assert "MEASURES" in prompt

    onto = _finance_ontology()
    payload = ontology_payload(onto)
    assert payload["measures"][0]["name"] == "open_receivable"
    assert payload["measures"][0]["expression"] == _EXPR
    assert payload["measures"][0]["grain"] == "invoice_head"
    assert payload["measures"][0]["aliases"] == ()
    assert payload["relationships"][0]["from"] == "payment"
    assert payload["relationships"][0]["to"] == "invoice"
    object_prompt = build_schema_context(
        "open receivable",
        _finance_schema(),
        ontology=onto,
    ).prompt
    assert _EXPR in object_prompt
    assert "grain=invoice_head" in object_prompt
    assert _ALIAS not in object_prompt
    assert "payment.invoice_id = invoice.invoice_id" in object_prompt


def test_same_builder_covers_both_schemas() -> None:
    crm = build_schema_context("midmarket accounts", _crm_schema(), ontology={})
    finance = build_schema_context(
        "open receivable", _finance_schema(), ontology=_finance_model()
    )
    assert crm.dialect == "mysql"
    assert finance.dialect == "sqlserver"
    assert "account" in crm.prompt and "invoice" in finance.prompt
    assert "MEASURES" not in crm.prompt
    assert _EXPR in finance.prompt


def test_mask_exception_sends_no_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mask down")

    monkeypatch.setattr("dms_executor.schema_context.fail_closed_mask_payload", _boom)
    prompt = _crm_prompt(None)
    assert "samples=" not in prompt
    for raw in (*_PII, _SEGMENT, "RESTRICTEDTOKEN"):
        assert raw not in prompt
    assert "date_of_birth" in prompt
    assert "date" in prompt
    assert "DIALECT: mysql" in prompt
    assert "contact.account_id = account.account_id" in prompt


def test_token_cap_drops_unrelated_datasets() -> None:
    datasets: list[dict[str, Any]] = [
        {
            "name": "keep_me_table",
            "columns": [
                {
                    "name": "keep_me_col",
                    "type": "varchar",
                    "samples": ["KEEPTOKEN"],
                    "values": ["KEEPTOKEN"],
                    "distinct": 1,
                }
            ],
        }
    ]
    for i in range(25):
        datasets.append(
            {
                "name": f"batch_{i:02d}",
                "columns": [
                    {
                        "name": "filler_col",
                        "type": "varchar",
                        "description": "x" * 200,
                        "samples": [f"NOISETOKEN{i:02d}" + ("y" * 40)],
                    }
                ],
            }
        )
    prompt = build_schema_context(
        "keep_me_table keep_me_col",
        {"dialect": "sqlite", "datasets": datasets},
        ontology={
            "non_personal": [
                {
                    "table": "keep_me_table",
                    "column": "keep_me_col",
                    "source": "fixture",
                    "field": "keep_me_col",
                }
            ]
        },
    ).prompt
    assert estimate_tokens(prompt) <= MAX_PROMPT_TOKENS
    assert "keep_me_table" in prompt
    assert "KEEPTOKEN" in prompt
    assert "DIALECT: sqlite" in prompt
    assert "postgres" not in prompt.lower()
    present = sum(1 for i in range(25) if f"batch_{i:02d}" in prompt)
    assert present < 25


def test_context_does_not_load_scored_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.schema_context as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    for banned in _BANNED_SOURCE:
        assert banned not in source
    opened: list[str] = []

    def _spy(real: Any) -> Any:
        def _wrapped(target: Any, *args: Any, **kwargs: Any) -> Any:
            opened.append(str(target))
            return real(target, *args, **kwargs)

        return _wrapped

    monkeypatch.setattr(builtins, "open", _spy(builtins.open))
    monkeypatch.setattr(io, "open", _spy(io.open))
    monkeypatch.setattr(os, "open", _spy(os.open))
    for name in ("open", "read_text", "read_bytes"):
        monkeypatch.setattr(pathlib.Path, name, _spy(getattr(pathlib.Path, name)))
    prompt = _crm_prompt({})
    blob = "\n".join(opened)
    assert "curated_ceo" not in blob
    assert "oracles.yaml" not in blob
    assert "questions.yaml" not in blob
    assert _PACK_QUESTION not in prompt
    assert _PACK_ID not in prompt


def test_prompt_stays_on_the_request_and_off_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "schema_prompt.txt"
    monkeypatch.setenv("DMS_SCHEMA_PROMPT_LOG", str(audit))
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    writes: list[str] = []

    def _no_write(self: Path, *args: Any, **kwargs: Any) -> Any:
        writes.append(str(self))
        raise AssertionError("schema context wrote a file")

    monkeypatch.setattr(pathlib.Path, "write_text", _no_write)
    ctx = prepare_generate_context(
        {"schema": [{"table": "account", "columns": ["segment"]}]},
        question="which accounts are midmarket",
        schema=_crm_schema(),
        ontology={},
        grantable={"account", "contact"},
        space_id="space-audit",
    )
    stored = ctx["schema_context"]
    assert not audit.exists()
    assert writes == []
    for raw in _PII:
        assert raw not in stored
    assert _EMAIL not in stored
    assert "account.segment = midmarket" in stored
    assert _SEGMENT not in stored
    assert "DIALECT: mysql" in stored


def test_schema_context_field_is_behind_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = {
        "schema": [{"table": "account", "columns": ["segment"]}],
        "schema_context": "PROMPT_TEXT",
    }
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    off = _insights_body(
        "which accounts are midmarket",
        session_id=None,
        space_id=None,
        ontology=catalog,
    )
    assert "schema_context" not in off
    assert "schema_context" not in off["ontology"]
    assert off["ontology"]["schema"][0]["table"] == "account"
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    on = _insights_body(
        "which accounts are midmarket",
        session_id=None,
        space_id=None,
        ontology=dict(catalog),
    )
    assert on["schema_context"] == "PROMPT_TEXT"
    assert "schema_context" not in on["ontology"]


def test_insights_post_and_client_forward_schema_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_request(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["body"] = kwargs.get("json_body")
        return {"ok": True}

    monkeypatch.setattr("cortex_client.insights._request", _fake_request)
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    insights_post(
        "http://127.0.0.1:9",
        question="open receivable",
        generate=False,
        schema_context="PROMPT_TEXT",
    )
    assert "schema_context" not in captured["body"]
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    insights_post(
        "http://127.0.0.1:9",
        question="open receivable",
        generate=False,
        schema_context="PROMPT_TEXT",
    )
    assert captured["body"]["schema_context"] == "PROMPT_TEXT"

    seen: dict[str, Any] = {}

    def _fake_post(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr("cortex_client.client.insights_post", _fake_post)
    CortexClient("http://127.0.0.1:9").insights_ask(
        question="open receivable", generate=False, schema_context="PROMPT_TEXT"
    )
    assert seen["schema_context"] == "PROMPT_TEXT"


def _seed_crm(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE account (account_id INTEGER PRIMARY KEY, segment VARCHAR)"
        )
        con.execute(f"INSERT INTO account VALUES (1, '{_SEGMENT}')")
        con.execute(
            "CREATE TABLE contact ("
            "contact_id INTEGER PRIMARY KEY, account_id INTEGER, "
            "customer_name VARCHAR, email VARCHAR, phone VARCHAR, "
            "date_of_birth DATE, passport_no VARCHAR)"
        )
        con.execute(
            "INSERT INTO contact VALUES "
            f"(9, 1, '{_NAME}', '{_EMAIL}', '{_PHONE}', DATE '{_DOB}', '{_PASSPORT}')"
        )
        con.execute("CREATE TABLE restricted_note (note_body VARCHAR)")
        con.execute("INSERT INTO restricted_note VALUES ('RESTRICTEDTOKEN')")
    finally:
        con.close()


def test_generative_ask_attaches_reflected_context_when_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serving = tmp_path / "crm.duckdb"
    _seed_crm(serving)
    onto = Ontology()
    onto.verified = True
    assert (
        build_space_index(serving, "space-1", {"account", "contact"}, "mysql") == ""
    )
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    seen: dict[str, Any] = {}

    def _compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen["ctx"] = ctx
        return {"unsure": True}

    env = maybe_generative_ask(
        "which accounts are midmarket",
        warehouse=serving,
        grantable={"account", "contact"},
        ontology=onto,
        compute=_compute,
        submit=lambda _sql: None,
        ledger_append=lambda _payload: None,
        space_id="space-1",
        dialect="mysql",
    )
    prompt = seen["ctx"]["schema_context"]
    assert env is not None
    assert "_schema_context_envelope" not in seen["ctx"]
    assert env["schema_context"] != prompt
    assert "DMSHINT_" in env["schema_context"]
    assert _SEGMENT not in json.dumps(env)
    assert "midmarket" not in json.dumps(env)
    assert "account.segment = midmarket" in prompt
    assert _SEGMENT not in prompt
    assert f"samples={_SEGMENT}" not in prompt
    for raw in _PII:
        assert raw not in prompt
    assert "RESTRICTEDTOKEN" not in prompt
    assert "postgres" not in prompt.lower()
    assert "undeclared" not in prompt.lower()
    assert prompt.startswith("DIALECT: mysql")
    assert "MEASURES" not in prompt


def _text_column(name: str, values: list[str], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "type": "varchar",
        "distinct": extra.pop("distinct", len(values)),
        "values": values,
    }
    body.update(extra)
    return body


def _one_table(column: dict[str, Any], *, dialect: str = "mysql") -> dict[str, Any]:
    return {"dialect": dialect, "datasets": [{"name": "person", "columns": [column]}]}


def test_opaque_text_column_sends_no_samples() -> None:
    prompt = build_schema_context(
        "list people",
        _one_table(_text_column("attr_8", ["Nora Voss", "Mina Cole"], distinct=40)),
    ).prompt
    assert "Nora Voss" not in prompt
    assert "Mina Cole" not in prompt
    assert "samples=" not in prompt
    assert "distinct=40" in prompt
    assert "attr_8" in prompt


def test_description_contact_sends_no_samples() -> None:
    prompt = build_schema_context(
        "list people",
        _one_table(
            _text_column(
                "field_z",
                ["Nora Voss"],
                distinct=8,
                description="contact",
            )
        ),
    ).prompt
    assert "Nora Voss" not in prompt
    assert "samples=" not in prompt
    assert "description=contact" in prompt
    assert "field_z" in prompt


def test_low_cardinality_names_send_no_samples() -> None:
    names = ["Nora Voss", "Mina Cole", "Jon Pell", "Ada Quinn", "Ruth Hale"]
    prompt = build_schema_context(
        "who sold this",
        _one_table(_text_column("rep", names, distinct=5)),
    ).prompt
    for name in names:
        assert name not in prompt
    assert "samples=" not in prompt
    assert "distinct=5" in prompt


def test_partial_name_sends_nothing() -> None:
    prompt = build_schema_context(
        "show Ali",
        _one_table(_text_column("rep", ["Ali Bakar", "Ali Hassan"], distinct=2)),
    ).prompt
    assert "Ali Bakar" not in prompt
    assert "Ali Hassan" not in prompt
    assert "FILTER HINTS" not in prompt
    assert "samples=" not in prompt


def test_typo_sends_no_hint() -> None:
    prompt = build_schema_context(
        "how many chemcals",
        {
            "dialect": "mysql",
            "datasets": [
                {
                    "name": "item",
                    "columns": [_text_column("category", ["CHEMICALS"], distinct=1)],
                }
            ],
        },
    ).prompt
    assert "CHEMICALS" not in prompt
    assert "FILTER HINTS" not in prompt
    assert "samples=" not in prompt


def _sql_from_exact_hint(prompt: str) -> str:
    """Stub model: the only filter it may emit is an exact hint from the prompt."""
    for line in prompt.splitlines():
        if line.startswith("- ") and " = " in line and "~" not in line:
            left, value = line[2:].split(" = ", 1)
            column = left.strip().split(".")[-1]
            return f"SELECT COUNT(*) FROM item WHERE {column} = '{value.strip()}'"
    raise AssertionError(prompt)


def test_exact_normalised_hint_filters_sql() -> None:
    prompt = build_schema_context(
        "how many chemicals SKUs",
        {
            "dialect": "mysql",
            "datasets": [
                {
                    "name": "item",
                    "columns": [
                        _text_column("category", ["CHEMICALS"], distinct=1),
                        {
                            "name": "qty",
                            "type": "integer",
                            "samples": ["4"],
                        },
                    ],
                }
            ],
        },
    ).prompt
    assert "item.category = chemicals" in prompt
    assert "CHEMICALS" not in prompt
    assert "samples=CHEMICALS" not in prompt
    for line in prompt.splitlines():
        if "category" in line and line.strip().startswith("- category"):
            assert "samples=" not in line
    sql = _sql_from_exact_hint(prompt)
    assert sql == "SELECT COUNT(*) FROM item WHERE category = 'chemicals'"


def test_tagged_column_fuzzy_hints_are_capped() -> None:
    values = [
        "midmarket east",
        "midmarket north",
        "midmarket south",
        "midmarket west",
        "enterprise",
    ]
    prompt = build_schema_context(
        "midmarket coverage",
        _one_table(_text_column("shade", values, distinct=5)),
        ontology={
            "non_personal": [
                {
                    "table": "person",
                    "column": "shade",
                    "source": "fixture",
                    "field": "shade",
                }
            ]
        },
    ).prompt
    fuzzy = [line for line in prompt.splitlines() if " ~ " in line]
    assert len(fuzzy) == HINT_CANDIDATE_CAP
    assert HINT_CANDIDATE_CAP < 4
    assert "midmarket west" not in prompt
    assert "enterprise" not in prompt


def test_two_asks_on_different_spaces_do_not_cross(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    def _seed(path: Path, token: str) -> None:
        con = duckdb.connect(str(path))
        try:
            con.execute("CREATE TABLE person (rep VARCHAR)")
            con.execute("INSERT INTO person VALUES (?)", [token])
        finally:
            con.close()

    left_db = tmp_path / "left.duckdb"
    right_db = tmp_path / "right.duckdb"
    _seed(left_db, "ONLYSPACEA")
    _seed(right_db, "ONLYSPACEB")
    assert build_space_index(left_db, "space-left", {"person"}, "mysql") == ""
    assert build_space_index(right_db, "space-right", {"person"}, "mysql") == ""
    onto = Ontology()
    onto.verified = True
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    prompts: dict[str, str] = {}
    envelopes: dict[str, dict[str, Any]] = {}
    errors: list[BaseException] = []

    def _ask(path: Path, space: str, question: str) -> None:
        try:
            def _compute(ctx: dict[str, Any]) -> dict[str, Any]:
                prompts[space] = str(ctx.get("schema_context") or "")
                return {"unsure": True}

            env = maybe_generative_ask(
                question,
                warehouse=path,
                grantable={"person"},
                ontology=onto,
                compute=_compute,
                submit=lambda _sql: None,
                ledger_append=lambda _payload: None,
                space_id=space,
                session_id=space,
            )
            if env is None:
                raise AssertionError(space)
            envelopes[space] = env
        except BaseException as exc:  # noqa: BLE001 -- surfaced after join
            errors.append(exc)

    left = threading.Thread(
        target=_ask, args=(left_db, "space-left", "show ONLYSPACEA")
    )
    right = threading.Thread(
        target=_ask, args=(right_db, "space-right", "show ONLYSPACEB")
    )
    left.start()
    right.start()
    left.join()
    right.join()
    assert errors == []
    assert "person.rep = ONLYSPACEA" in prompts["space-left"]
    assert "ONLYSPACEB" not in prompts["space-left"]
    assert "person.rep = ONLYSPACEB" in prompts["space-right"]
    assert "ONLYSPACEA" not in prompts["space-right"]
    left_copy = envelopes["space-left"]["schema_context"]
    right_copy = envelopes["space-right"]["schema_context"]
    assert "DMSHINT_" in left_copy
    assert "DMSHINT_" in right_copy
    assert "ONLYSPACEA" not in left_copy
    assert "ONLYSPACEB" not in left_copy
    assert "ONLYSPACEB" not in right_copy
    assert "ONLYSPACEA" not in right_copy
    assert "ONLYSPACEB" not in json.dumps(envelopes["space-left"])
    assert "ONLYSPACEA" not in json.dumps(envelopes["space-right"])
    assert "ONLYSPACEA" in cached_value_index("space-left")["person.rep"]
    assert "ONLYSPACEB" not in cached_value_index("space-left")["person.rep"]
    assert "ONLYSPACEB" in cached_value_index("space-right")["person.rep"]
    assert "ONLYSPACEA" not in cached_value_index("space-right").get("person.rep", ())


def test_flag_off_calls_no_schema_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_executor.schema_context as mod

    called: list[str] = []

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        called.append("called")
        raise AssertionError("schema_context ran while the flag was off")

    for name, obj in list(vars(mod).items()):
        if name.startswith("_") or not callable(obj):
            continue
        if getattr(obj, "__module__", "") == mod.__name__:
            monkeypatch.setattr(mod, name, _boom)
    monkeypatch.setattr("dms_executor.generative_ask.prepare_generate_context", _boom)
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    serving = tmp_path / "crm.duckdb"
    _seed_crm(serving)
    onto = Ontology()
    onto.verified = True
    seen: dict[str, Any] = {}

    def _compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen["ctx"] = ctx
        return {"unsure": True}

    env = maybe_generative_ask(
        "which accounts are midmarket",
        warehouse=serving,
        grantable={"account", "contact"},
        ontology=onto,
        compute=_compute,
        submit=lambda _sql: None,
        ledger_append=lambda _payload: None,
        space_id="space-off",
    )
    assert called == []
    assert "schema_context" not in seen["ctx"]
    assert env is not None
    assert "schema_context" not in env


def test_blank_dialect_is_omitted_and_not_invented() -> None:
    import dms_executor.schema_context as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "undeclared" not in source
    assert "postgres" not in source.lower()
    assert "column_is_pii" not in source
    assert "write_text" not in source
    prompt = build_schema_context(
        "count rows",
        {"datasets": [{"name": "item", "columns": [{"name": "qty", "type": "integer"}]}]},
    ).prompt
    assert "DIALECT" not in prompt
    assert prompt.startswith("SCHEMA")


def test_measures_are_reserved_before_columns() -> None:
    columns = [
        {
            "name": f"wide_{i:02d}",
            "type": "varchar",
            "description": "y" * 180,
            "distinct": 10,
        }
        for i in range(8)
    ]
    prompt = build_schema_context(
        "open receivable",
        {"dialect": "sqlserver", "datasets": [{"name": "invoice", "columns": columns}]},
        ontology=_finance_model(),
        max_tokens=90,
    ).prompt
    assert _EXPR in prompt
    assert sum(1 for i in range(8) if f"wide_{i:02d}" in prompt) < 8


def test_description_email_is_masked() -> None:
    prompt = _crm_prompt(None)
    assert _EMAIL not in prompt


def test_non_personal_without_evidence_stays_untagged() -> None:
    column = _text_column(
        "rep",
        ["Ali Bakar", "Ali Hassan"],
        distinct=2,
        non_personal=True,
    )
    prompt = build_schema_context("show Ali", _one_table(column)).prompt
    assert "FILTER HINTS" not in prompt
    assert "samples=" not in prompt
    assert "Ali Bakar" not in prompt
    blank = _text_column(
        "rep",
        ["Ali Bakar"],
        distinct=1,
        non_personal={"evidence": "  "},
    )
    blank_prompt = build_schema_context("show Ali", _one_table(blank)).prompt
    assert "FILTER HINTS" not in blank_prompt
    assert "samples=" not in blank_prompt
    self_set = _text_column(
        "rep",
        ["Ali Bakar", "Ali Hassan"],
        distinct=2,
        non_personal={"evidence": "column says so"},
    )
    self_prompt = build_schema_context("show Ali", _one_table(self_set)).prompt
    assert " ~ " not in self_prompt
    assert "FILTER HINTS" not in self_prompt
    assert "Ali Bakar" not in self_prompt
    missing_field = build_schema_context(
        "show Ali",
        _one_table(_text_column("rep", ["Ali Bakar", "Ali Hassan"], distinct=2)),
        ontology={
            "non_personal": [
                {"table": "person", "column": "rep", "source": "fixture"}
            ]
        },
    ).prompt
    assert "FILTER HINTS" not in missing_field
    assert "Ali Bakar" not in missing_field


def test_untagged_folded_ties_send_nothing() -> None:
    prompt = build_schema_context(
        "acme",
        _one_table(_text_column("label", ["Acme", "ACME"], distinct=2)),
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "Acme" not in prompt
    assert "ACME" not in prompt


def _people(*values: str) -> dict[str, Any]:
    return _one_table(_text_column("rep", list(values), distinct=len(values)))


def test_show_ali_sends_the_typed_span() -> None:
    prompt = build_schema_context(
        "show Ali",
        _people("Ali", "Mina Cole", "Jon Pell"),
    ).prompt
    assert "person.rep = Ali" not in prompt
    assert "Ali" not in prompt
    assert "Mina Cole" not in prompt
    assert "Jon Pell" not in prompt
    assert "FILTER HINTS" not in prompt


def test_show_ali_bang_keeps_the_question_characters() -> None:
    prompt = build_schema_context(
        "show ALI!",
        _people("Ali", "Mina Cole", "Jon Pell"),
    ).prompt
    assert "person.rep = ALI!" not in prompt
    assert "person.rep = Ali" not in prompt
    assert "FILTER HINTS" not in prompt
    assert "Ali" not in prompt


def test_list_chemicals_in_stock_sends_the_typed_span() -> None:
    prompt = build_schema_context(
        "list chemicals in stock",
        {
            "dialect": "mysql",
            "datasets": [
                {
                    "name": "item",
                    "columns": [_text_column("category", ["CHEMICALS"], distinct=1)],
                }
            ],
        },
    ).prompt
    assert "item.category = chemicals" in prompt
    assert "CHEMICALS" not in prompt
    assert "samples=" not in prompt


def test_longer_granted_value_blocks_the_short_span() -> None:
    prompt = build_schema_context(
        "show ali",
        _people("Ali", "Ali bin Abu"),
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "Ali" not in prompt
    assert "Ali bin Abu" not in prompt


def test_show_ali_bin_hints_when_ali_is_the_only_value() -> None:
    """A one-word person value sends nothing, even when it is the only cell."""
    prompt = build_schema_context("show ali bin", _people("Ali")).prompt
    assert "FILTER HINTS" not in prompt
    assert "person.rep = ali" not in prompt
    assert "person.rep = Ali" not in prompt
    assert "Ali" not in prompt
    assert "ali" not in prompt


def test_stubbed_model_span_cannot_create_a_hint() -> None:
    called: list[str] = []

    def _model(question: str) -> list[str]:
        called.append(question)
        return ["ali"]

    prompt = build_schema_context(
        "show ali bin",
        _people("Ali", "Ali bin Abu"),
        entity_spans=_model,
    ).prompt
    assert called == []
    assert "FILTER HINTS" not in prompt
    assert "Ali" not in prompt
    assert "ali" not in prompt


def test_show_nora_on_opaque_columns_sends_no_hint() -> None:
    attr = build_schema_context(
        "show Nora Voss",
        _one_table(_text_column("attr_8", ["Nora Voss"], distinct=40)),
    ).prompt
    assert "FILTER HINTS" not in attr
    assert "Nora Voss" not in attr
    assert "samples=" not in attr
    field = build_schema_context(
        "show Nora Voss",
        _one_table(
            _text_column("field_z", ["Nora Voss"], distinct=8, description="contact")
        ),
    ).prompt
    assert "FILTER HINTS" not in field
    assert "Nora Voss" not in field
    assert "samples=" not in field
    assert "description=contact" in field


def test_low_cardinality_names_send_no_hint() -> None:
    names = ["Nora Voss", "Mina Cole", "Jon Pell", "Ada Quinn", "Ruth Hale"]
    prompt = build_schema_context(
        "show Nora Voss",
        _one_table(_text_column("rep", names, distinct=5)),
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "samples=" not in prompt
    for name in names:
        assert name not in prompt


def test_envelope_masks_hint_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import duckdb
    from cortex_client.compute import _insights_body
    from dms_executor.session_followup import snapshot_turn

    serving = tmp_path / "people.duckdb"
    con = duckdb.connect(str(serving))
    con.execute("CREATE TABLE person (rep VARCHAR)")
    con.execute("INSERT INTO person VALUES ('Ali'), ('Mina Cole'), ('Jon Pell')")
    con.execute("CREATE TABLE item (category VARCHAR)")
    con.execute("INSERT INTO item VALUES ('CHEMICALS')")
    con.close()
    assert build_space_index(serving, "space-ali", {"person", "item"}, "mysql") == ""
    onto = Ontology()
    onto.verified = True
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    caplog.set_level(logging.DEBUG)
    ledger: list[dict[str, Any]] = []
    seen: dict[str, Any] = {}

    def _compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen["ctx"] = ctx
        return {"unsure": True}

    env = maybe_generative_ask(
        "list chemicals in stock",
        warehouse=serving,
        grantable={"person", "item"},
        ontology=onto,
        compute=_compute,
        submit=lambda _sql: None,
        ledger_append=lambda payload: ledger.append(payload),
        space_id="space-ali",
        session_id="sess-ali",
        dialect="mysql",
    )
    assert env is not None
    blob = json.dumps(env)
    assert "Ali" not in blob
    assert "Mina Cole" not in blob
    assert "Jon Pell" not in blob
    assert "CHEMICALS" not in blob
    assert "chemicals" not in env["schema_context"]
    assert "DMSHINT_" in env["schema_context"]
    prompt = seen["ctx"]["schema_context"]
    assert "item.category = chemicals" in prompt
    assert "Ali" not in prompt
    assert "person.rep = Ali" not in prompt
    assert "_schema_context_envelope" not in seen["ctx"]
    receipt = json.dumps(env.get("audit_receipt"))
    assert "Ali" not in receipt
    assert "CHEMICALS" not in receipt
    assert "Ali" not in caplog.text
    assert "Ali" not in json.dumps(ledger)
    stored = snapshot_turn(env)
    assert stored is None or "Ali" not in json.dumps(stored)
    body = _insights_body(
        "list chemicals in stock",
        session_id="sess-ali",
        space_id="space-ali",
        ontology=seen["ctx"],
    )
    assert "item.category = chemicals" in body["schema_context"]
    assert "Ali" not in body["schema_context"]
    rest = {
        key: value
        for key, value in body.items()
        if key not in {"schema_context", "question", "intent"}
    }
    assert "Ali" not in json.dumps(rest)
    assert "CHEMICALS" not in json.dumps(rest)


def test_index_file_has_no_raw_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import duckdb

    serving = tmp_path / "people.duckdb"
    con = duckdb.connect(str(serving))
    con.execute(
        "CREATE TABLE person (rep VARCHAR, email VARCHAR, passport_no VARCHAR)"
    )
    con.execute(
        "INSERT INTO person VALUES "
        "('Ali', 'ada.secret@example.com', 'A12345678'), "
        "('Mina Cole', 'mina.secret@example.com', 'B12345678'), "
        "('Jon Pell', 'jon.secret@example.com', 'C12345678')"
    )
    con.execute("CREATE TABLE item (category VARCHAR)")
    con.execute("INSERT INTO item VALUES ('CHEMICALS')")
    con.close()
    assert build_space_index(serving, "space-ali", {"person", "item"}, "mysql") == ""
    folder = Path(str(serving) + ".schema_index")
    files = sorted(folder.rglob("*"))
    assert files
    blob = "\n".join(
        path.read_text(encoding="utf-8") for path in files if path.is_file()
    )
    for raw in (
        "Ali",
        "Mina Cole",
        "Jon Pell",
        "Nora Voss",
        "ada.secret@example.com",
        "mina.secret@example.com",
        "jon.secret@example.com",
        "A12345678",
        "CHEMICALS",
    ):
        assert raw not in blob
    mem = cached_value_index("space-ali")
    assert "Ali" not in json.dumps(mem)
    assert "Mina Cole" not in json.dumps(mem)
    assert mem["item.category"] == ("CHEMICALS",)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    ctx = prepare_generate_context(
        {},
        question="list chemicals in stock",
        serving=serving,
        grantable={"person", "item"},
        space_id="space-ali",
        dialect="mysql",
    )
    assert "item.category = chemicals" in ctx["schema_context"]
    assert "Ali" not in ctx["schema_context"]
    assert "FILTER HINTS" in ctx["schema_context"]


def test_index_build_thread_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb
    from dms_executor.schema_context import schedule_index_build, stop_index_builds

    serving = tmp_path / "people.duckdb"
    con = duckdb.connect(str(serving))
    con.execute("CREATE TABLE person (rep VARCHAR)")
    con.execute("INSERT INTO person VALUES ('ONLYSPACEA')")
    con.close()
    monkeypatch.setattr(
        "dms_executor.schema_context.build_space_index",
        lambda *_args, **_kwargs: "",
    )
    schedule_index_build(serving, "space-stop", {"person"}, "mysql", "fp-stop")
    stop_index_builds()
    assert not any(
        worker.is_alive() and worker.name == "schema-index"
        for worker in threading.enumerate()
    )


def test_show_ali_bin_with_longer_value_sends_nothing() -> None:
    prompt = build_schema_context(
        "show ali bin",
        _people("Ali", "Ali bin Abu"),
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "Ali bin Abu" not in prompt
    assert "person.rep = Ali" not in prompt
    assert "person.rep = ali" not in prompt


def test_sales_by_ali_bin_abu_sends_the_long_typed_span() -> None:
    prompt = build_schema_context(
        "sales by ali bin abu",
        _people("Ali", "Ali bin Abu"),
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "ali bin abu" not in prompt
    assert "Ali bin Abu" not in prompt
    assert "Ali" not in prompt


def test_chemicals_hint_survives_a_loaded_ontology() -> None:
    measures = [
        {
            "name": f"measure_{i:02d}",
            "grain": "item",
            "expression": f"SUM(f.metric_{i:02d})",
            "description": "d" * 80,
            "aliases": [f"alias {i:02d}"],
        }
        for i in range(10)
    ]
    columns: list[dict[str, Any]] = [
        _text_column("category", ["CHEMICALS"], distinct=1, description="item class"),
    ]
    for i in range(12):
        columns.append(
            {
                "name": f"wide_{i:02d}",
                "type": "varchar",
                "description": "w" * 120,
                "distinct": 4,
            }
        )
    prompt = build_schema_context(
        "total stock value for chemicals",
        {"datasets": [{"name": "item", "columns": columns}]},
        ontology={"measures": measures},
    ).prompt
    assert estimate_tokens(prompt) <= MAX_PROMPT_TOKENS
    assert "item.category = chemicals" in prompt
    assert "CHEMICALS" not in prompt
    assert any(
        line.startswith("- category ") for line in prompt.splitlines()
    )
    assert "MEASURES" in prompt


def test_long_party_hint_survives_a_loaded_ontology() -> None:
    measures = [
        {
            "name": f"measure_{i:02d}",
            "grain": "party",
            "expression": f"SUM(f.metric_{i:02d})",
            "description": "d" * 80,
        }
        for i in range(10)
    ]
    prompt = build_schema_context(
        "orders for Delta Logistics Co",
        {
            "datasets": [
                {
                    "name": "party",
                    "columns": [
                        _text_column(
                            "name",
                            ["Delta Logistics Co", "Other Carrier"],
                            distinct=2,
                        )
                    ],
                }
            ]
        },
        ontology={"measures": measures},
    ).prompt
    assert "party.name = Delta Logistics Co" not in prompt
    assert "Delta Logistics Co" not in prompt
    assert "FILTER HINTS" not in prompt
    assert "MEASURES" in prompt


def test_description_masks_a_known_name() -> None:
    prompt = build_schema_context(
        "who sold this",
        _one_table(
            _text_column(
                "rep",
                ["Nora Voss", "Mina Cole"],
                distinct=2,
                description="primary rep Nora Voss covers the book",
            )
        ),
    ).prompt
    assert "Nora Voss" not in prompt
    assert "Mina Cole" not in prompt
    assert "DMSVAL_" in prompt
    assert "varchar" in prompt
    assert "distinct=2" in prompt
    assert "samples=" not in prompt


def test_ungranted_table_value_is_absent() -> None:
    prompt = build_schema_context(
        "show UNGRANTEDONLY",
        {
            "datasets": [
                {
                    "name": "item",
                    "columns": [_text_column("label", ["CHEMICALS"], distinct=1)],
                },
                {
                    "name": "secret",
                    "columns": [_text_column("note", ["UNGRANTEDONLY"], distinct=1)],
                },
            ]
        },
        grantable={"item"},
    ).prompt
    assert "UNGRANTEDONLY" not in prompt
    assert "secret" not in prompt
    assert "FILTER HINTS" not in prompt


def test_index_cap_drops_the_column_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    values = [f"V{i:04d}" for i in range(INDEX_COLUMN_CAP + 1)]
    values[0] = "Alpha"
    caplog.set_level(logging.WARNING, logger="dms_executor.schema_context")
    prompt = build_schema_context(
        "show Alpha",
        _one_table(_text_column("rep", values, distinct=len(values))),
        space_id="space-cap",
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "Alpha" not in prompt
    assert "schema_index_cap" in caplog.text
    assert "person.rep" not in cached_value_index("space-cap")


def test_index_total_cap_drops_the_overflow_column(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setattr("dms_executor.schema_context.INDEX_TOTAL_CAP", 2)
    caplog.set_level(logging.WARNING, logger="dms_executor.schema_context")
    prompt = build_schema_context(
        "show ALPHA",
        {
            "datasets": [
                {
                    "name": "person",
                    "columns": [
                        _text_column("rep", ["ALPHA"], distinct=1),
                        _text_column("other", ["BETA", "GAMMA"], distinct=2),
                    ],
                }
            ]
        },
        space_id="space-total-cap",
    ).prompt
    assert "person.rep = ALPHA" in prompt
    assert "BETA" not in prompt
    assert "schema_index_cap" in caplog.text
    cached = cached_value_index("space-total-cap")
    assert cached["person.rep"] == ("ALPHA",)
    assert "person.other" not in cached


def test_foreign_key_drops_when_the_target_column_is_omitted() -> None:
    prompt = build_schema_context(
        "contact link",
        {
            "datasets": [
                {
                    "name": "account",
                    "columns": [
                        {
                            "name": "account_id",
                            "type": "bigint",
                            "primary_key": True,
                            "description": "k" * 400,
                        }
                    ],
                },
                {
                    "name": "contact",
                    "columns": [
                        {
                            "name": "account_id",
                            "type": "bigint",
                            "description": "link",
                        }
                    ],
                },
            ],
            "relationships": [
                {
                    "name": "contact_account",
                    "from": "contact",
                    "from_column": "account_id",
                    "to": "account",
                    "to_column": "account_id",
                }
            ],
        },
        max_tokens=40,
    ).prompt
    assert "contact" in prompt
    assert "foreign_key=" not in prompt


def _seed_person(path: Path, value: str) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE person (rep VARCHAR, qty INTEGER)")
        con.execute("INSERT INTO person VALUES (?, 3)", [value])
    finally:
        con.close()


def test_index_pending_ask_uses_catalog_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.session_followup import snapshot_turn

    serving = tmp_path / "people.duckdb"
    _seed_person(serving, "Nora Voss")
    onto = Ontology()
    onto.verified = True
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    sampled: list[str] = []

    def _no_build(*_args: Any, **_kwargs: Any) -> str:
        return ""

    def _no_sample(*_args: Any, **_kwargs: Any) -> list[str]:
        sampled.append("read")
        return []

    monkeypatch.setattr("dms_executor.schema_context.build_space_index", _no_build)
    monkeypatch.setattr("dms_executor.schema_context._bounded_read", _no_sample)
    env = maybe_generative_ask(
        "show Nora Voss",
        warehouse=serving,
        grantable={"person"},
        ontology=onto,
        compute=lambda _ctx: {"unsure": True},
        submit=lambda _sql: None,
        ledger_append=lambda _payload: None,
        space_id="space-pending",
        session_id="sess-pending",
    )
    assert env is not None
    assert env["index_stamp"] == "index_pending"
    assert "Nora Voss" not in env["schema_context"]
    assert "rep" in env["schema_context"]
    assert "reason=score:" in env["schema_context"]
    assert sampled == []
    stored = snapshot_turn(env)
    assert stored is not None
    assert stored["index_stamp"] == "index_pending"


def test_index_failed_stamp_on_envelope_and_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.session_followup import snapshot_turn

    serving = tmp_path / "people.duckdb"
    _seed_person(serving, "Nora Voss")

    def _boom(*_args: Any, **_kwargs: Any) -> list[str]:
        raise RuntimeError("sample down")

    monkeypatch.setattr("dms_executor.schema_context._bounded_read", _boom)
    assert (
        build_space_index(serving, "space-failed", {"person"})
        == "index_failed:RuntimeError"
    )
    onto = Ontology()
    onto.verified = True
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    env = maybe_generative_ask(
        "show Nora Voss",
        warehouse=serving,
        grantable={"person"},
        ontology=onto,
        compute=lambda _ctx: {"unsure": True},
        submit=lambda _sql: None,
        ledger_append=lambda _payload: None,
        space_id="space-failed",
        session_id="sess-failed",
    )
    assert env is not None
    assert env["index_stamp"] == "index_failed:RuntimeError"
    assert "Nora Voss" not in env["schema_context"]
    assert "rep" in env["schema_context"]
    stored = snapshot_turn(env)
    assert stored is not None
    assert stored["index_stamp"] == "index_failed:RuntimeError"


def test_three_hundred_table_shortlist_p95_under_two_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    import duckdb

    serving = tmp_path / "wide.duckdb"
    con = duckdb.connect(str(serving))
    try:
        for i in range(300):
            con.execute(f"CREATE TABLE t{i:03d} (c0 VARCHAR, c1 INTEGER)")
            con.execute(f"INSERT INTO t{i:03d} VALUES ('v{i:03d}', {i})")
    finally:
        con.close()
    import dms_executor.schema_context as schema_mod

    rows_read = 0
    real_read = schema_mod._bounded_read

    def _counting(con: Any, table: str, column: str, limit: int) -> list[str]:
        nonlocal rows_read
        got = real_read(con, table, column, limit)
        rows_read += len(got)
        return got

    monkeypatch.setattr(schema_mod, "_bounded_read", _counting)
    started = time.perf_counter()
    assert build_space_index(serving, "space-wide") == ""
    build_s = time.perf_counter() - started
    build_rows = rows_read
    data_sql: list[str] = []
    real_execute = duckdb.DuckDBPyConnection.execute

    def _execute(self: Any, sql: str, *args: Any, **kwargs: Any) -> Any:
        folded = " ".join(str(sql).split()).lower()
        catalog = (
            "information_schema" in folded
            or "duckdb_tables" in folded
            or "duckdb_constraints" in folded
        )
        if not catalog and " from " in f" {folded} ":
            data_sql.append(folded)
        return real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", _execute)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    times: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        ctx = prepare_generate_context(
            {},
            question="how many of t000 are open",
            serving=serving,
            space_id="space-wide",
        )
        times.append(time.perf_counter() - t0)
        assert "schema_context" in ctx
        assert "reason=score:" in ctx["schema_context"]
        assert "index_pending" not in ctx.get("_schema_index_stamp", "")
    ordered = sorted(times)
    p50 = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, 18)]
    timing = (
        f"build_s={build_s:.3f} p50_s={p50:.3f} p95_s={p95:.3f} "
        f"build_rows={build_rows} ask_data_queries={len(data_sql)}"
    )
    assert data_sql == [], timing
    assert build_rows > 0, timing
    assert p95 < 2.0, timing
    Path("/tmp/schema_index_timing.txt").write_text(timing + "\n", encoding="utf-8")


def test_schema_from_serving_reuses_the_serving_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    serving = tmp_path / "people.duckdb"
    con = duckdb.connect(str(serving))
    con.execute("CREATE TABLE person (rep VARCHAR)")
    con.execute("INSERT INTO person VALUES ('Ada')")
    con.close()
    seen: list[dict[str, Any]] = []
    real = duckdb.connect

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", _spy)
    body = schema_from_serving(serving, {"person"}, dialect="duckdb")
    assert body["dialect"] == "duckdb"
    assert seen
    assert all(not item.get("read_only") for item in seen)
    prompt = build_schema_context("count rows", body).prompt
    assert prompt.startswith("DIALECT: duckdb")


def test_live_ask_passes_the_serving_dialect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import Executor
    from dms_executor.demo_warehouse import SERVING_DIALECT

    captured: dict[str, Any] = {}

    def _spy(*_args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        return None

    monkeypatch.setattr("dms_executor.maybe_generative_ask", _spy)
    monkeypatch.setattr("dms_executor.cca.cascade.cascade_enabled", lambda: False)
    executor = Executor(
        cortex=object(),  # type: ignore[arg-type]
        warehouse_path=tmp_path / "absent.duckdb",
    )
    executor.live_ask("count the rows please", ask_path="generative")
    assert captured["dialect"] == SERVING_DIALECT
    assert SERVING_DIALECT == "duckdb"
