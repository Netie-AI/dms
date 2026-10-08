"""SCHEMA-RETRIEVE: connector schema prompt, masked samples, token cap.

Two synthetic sources: a CRM (mysql) with an empty ontology, and a finance
ledger (sqlserver) with a model-shaped ontology. No scored-pack questions.
"""

from __future__ import annotations

import builtins
import io
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
    MAX_PROMPT_TOKENS,
    build_schema_context,
    cached_value_index,
    estimate_tokens,
    ontology_payload,
    prepare_generate_context,
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
        assert f"account.segment = {_SEGMENT}" in prompt
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
    assert "invoice.status = OPEN" in prompt
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
                    "non_personal": {"evidence": "fixture-keep"},
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
    assert f"account.segment = {_SEGMENT}" in stored
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
    assert env["schema_context"] == prompt
    assert f"account.segment = {_SEGMENT}" in prompt
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
    assert "item.category = CHEMICALS" in prompt
    assert "samples=CHEMICALS" not in prompt
    for line in prompt.splitlines():
        if "category" in line and line.strip().startswith("- category"):
            assert "samples=" not in line
    sql = _sql_from_exact_hint(prompt)
    assert sql == "SELECT COUNT(*) FROM item WHERE category = 'CHEMICALS'"


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
        _one_table(
            _text_column(
                "shade",
                values,
                distinct=5,
                non_personal={"evidence": "fixture-shade"},
            )
        ),
    ).prompt
    fuzzy = [line for line in prompt.splitlines() if " ~ " in line]
    assert len(fuzzy) == HINT_CANDIDATE_CAP
    assert HINT_CANDIDATE_CAP < 4
    assert "midmarket west" not in prompt
    assert "enterprise" not in prompt


def test_two_spaces_do_not_share_a_value_index(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: dict[str, str] = {}

    def _run(space: str, token: str) -> None:
        ctx = prepare_generate_context(
            {},
            question=token,
            schema=_one_table(_text_column("label", [token], distinct=1)),
            space_id=space,
        )
        prompts[space] = ctx["schema_context"]

    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    left = threading.Thread(target=_run, args=("space-left", "ONLYSPACEA"))
    right = threading.Thread(target=_run, args=("space-right", "ONLYSPACEB"))
    left.start()
    right.start()
    left.join()
    right.join()
    assert "ONLYSPACEA" in prompts["space-left"]
    assert "ONLYSPACEB" not in prompts["space-left"]
    assert "ONLYSPACEB" in prompts["space-right"]
    assert "ONLYSPACEA" not in prompts["space-right"]
    assert "ONLYSPACEA" in cached_value_index("space-left")["person.label"]
    assert "ONLYSPACEB" not in cached_value_index("space-left")["person.label"]
    assert "ONLYSPACEB" in cached_value_index("space-right")["person.label"]
    assert "ONLYSPACEA" not in cached_value_index("space-right").get("person.label", ())


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


def test_untagged_folded_ties_send_nothing() -> None:
    prompt = build_schema_context(
        "acme",
        _one_table(_text_column("label", ["Acme", "ACME"], distinct=2)),
    ).prompt
    assert "FILTER HINTS" not in prompt
    assert "Acme" not in prompt
    assert "ACME" not in prompt
