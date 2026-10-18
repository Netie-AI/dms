"""CLOOP-PII-01: a database error must not carry a cell value out of the loop.

With ``DMS_CLOOP_B=1`` the model SQL runs on the extract and a database error
is fed back to the model for a retry. DuckDB echoes the offending cell value
in cast and constraint errors. The normal answer path masks a personal column
by its name, so the loop must not hand the same value out through the ask
envelope, the loop record, the pipeline-failure line, or the ``sql_feedback``
sent to Cortex. That holds for an execution error and for a checker EXPLAIN
error alike. The error is reduced to a typed code: exception class plus a
column the SQL itself wrote, on a table it cites, when the message names one.

All data below is synthetic. Table and column names belong in this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api import settings as settings_mod
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse, execute_sql
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.sql_loop import run_readonly
from fastapi.testclient import TestClient

_GOOD_SQL = "SELECT supplier_id, country FROM suppliers ORDER BY supplier_id"

# (case id, column, DDL type, seeded value, model SQL that makes DuckDB echo it, error column)
_CASES = [
    (
        "name",
        "contact_name",
        "VARCHAR",
        "Tan Wei Ming",
        "SELECT CAST(contact_name AS INTEGER) FROM suppliers",
        "contact_name",
    ),
    (
        "address",
        "home_address",
        "VARCHAR",
        "14 Jalan Harmoni 47400 Petaling Jaya",
        "SELECT CAST(home_address AS INTEGER) FROM suppliers",
        "home_address",
    ),
    (
        "dob",
        "date_of_birth",
        "VARCHAR",
        "born the fourteenth of March 1988",
        "SELECT CAST(date_of_birth AS DATE) FROM suppliers",
        "date_of_birth",
    ),
    (
        "passport",
        "passport_no",
        "VARCHAR",
        "K1234567X",
        "SELECT CAST(passport_no AS INTEGER) FROM suppliers",
        "passport_no",
    ),
]


@dataclass
class _Cortex:
    """Contract fake. ``compute_insights`` replays a script and records kwargs."""

    script: list[str]
    lake: Path | None = None
    feedback: list[Any] = field(default_factory=list)
    asks: list[AskRequest] = field(default_factory=list)
    #: Every request body sent to the model lane: the question and every kwarg.
    requests: list[dict[str, Any]] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.requests.append({"question": question, **kwargs})
        self.feedback.append(kwargs.get("sql_feedback"))
        idx = min(len(self.feedback) - 1, len(self.script) - 1)
        return {
            "phase": "generate",
            "served_model": "stub-model",
            "served_provider": "stub-provider",
            "ov_key_id": "ovk-stub",
            "query_sql": self.script[idx],
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql" and self.lake is not None:
            body = getattr(req, "body", None)
            sql = body.get("sql") if isinstance(body, dict) else getattr(body, "sql", None)
            rows = execute_sql(str(sql), path=self.lake)
            return QueryResult(ok=True, status="ok", run_id="run_pii_sql", output={"rows": rows})
        return QueryResult(ok=True, status="bound", run_id="run_pii")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_pii", hash="hash_pii_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Cannot answer from this Space.",
            badge="abstain",
            abstained=True,
            audit_id="aud_pii",
            route="abstain",
        )


@pytest.fixture()
def minter() -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-15T00:00:00+00:00",
            expires_at="2026-10-15T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    m.key = MagicMock()  # type: ignore[attr-defined]
    return m


@pytest.fixture()
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw

    path = tmp_path / "pii.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    return path


def _add_column(lake: Path, column: str, ddl: str, value: str) -> None:
    con = duckdb.connect(str(lake))
    try:
        con.execute(f"ALTER TABLE suppliers ADD COLUMN {column} {ddl}")
        con.execute(f"UPDATE suppliers SET {column} = ?", [value])
    finally:
        con.close()


def _table_values(lake: Path, table: str, column: str) -> list[str]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return [str(r[0]) for r in con.execute(f"SELECT {column} FROM {table}").fetchall()]
    finally:
        con.close()


def _chat_ask(
    lake: Path,
    minter: ManifestMinter,
    cortex: _Cortex,
    monkeypatch: pytest.MonkeyPatch,
    question: str = "supplier contact check",
) -> dict[str, Any]:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    try:
        app = create_app()
        app.state.ask_service = Executor(
            cortex=cortex,  # type: ignore[arg-type]
            minter=minter,
            warehouse_path=lake,
        )
        app.state.cortex = cortex
        res = TestClient(app).post(
            "/v1/chat/ask", json={"question": question, "session_id": "ses_pii"}
        )
    finally:
        settings_mod.get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    return body


def _assert_clean(blob: str, secrets: list[str], *, where: str) -> None:
    for secret in secrets:
        assert secret not in blob, f"{where} leaked {secret!r}"


@pytest.mark.parametrize(("case", "column", "ddl", "value", "sql", "named"), _CASES)
def test_cast_error_value_is_absent_from_every_envelope_field_and_feedback(
    case: str,
    column: str,
    ddl: str,
    value: str,
    sql: str,
    named: str,
    lake: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_column(lake, column, ddl, value)
    cortex = _Cortex(script=[sql])
    env = _chat_ask(lake, minter, cortex, monkeypatch)
    assert_envelope_valid(env)
    assert cortex.asks == [], "the envelope must come from the loop, not a contract miss"
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env["values"] == []
    assert env["loop"], "the loop record must exist"
    _assert_clean(json.dumps(env, default=str), [value], where=f"{case} envelope")
    _assert_clean(json.dumps(cortex.feedback, default=str), [value], where=f"{case} feedback")
    # The retry is still useful: it names the error class and the column.
    sent = [fb for fb in cortex.feedback if fb]
    assert sent, "the db error must reach compute_insights as sql_feedback"
    assert sent[0]["reason"] == f"db_error:ConversionException:{named}"
    assert env["loop"][0]["outcome"] == f"db_error:ConversionException:{named}"
    for entry in env["loop"]:
        _assert_clean(json.dumps(entry, default=str), [value], where=f"{case} loop")


def test_sku_codes_and_supplier_names_in_cast_errors_are_absent(
    lake: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    skus = _table_values(lake, "inventory", "sku")
    names = _table_values(lake, "suppliers", "supplier_name")
    assert skus and names
    for sql, secrets, column in (
        ("SELECT CAST(sku AS INTEGER) FROM inventory", skus, "sku"),
        ("SELECT CAST(supplier_name AS INTEGER) FROM suppliers", names, "supplier_name"),
    ):
        cortex = _Cortex(script=[sql])
        env = _chat_ask(lake, minter, cortex, monkeypatch, "stock code check")
        assert_envelope_valid(env)
        assert env["abstained"] is True
        assert env["rows"] == []
        _assert_clean(json.dumps(env, default=str), secrets, where=f"{column} envelope")
        _assert_clean(json.dumps(cortex.feedback, default=str), secrets, where=f"{column} feedback")
        sent = [fb for fb in cortex.feedback if fb]
        assert sent[0]["reason"] == f"db_error:ConversionException:{column}"


def test_retry_after_a_cast_error_still_recovers_and_carries_no_value(
    lake: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_column(lake, "contact_name", "VARCHAR", "Tan Wei Ming")
    cortex = _Cortex(
        script=["SELECT CAST(contact_name AS INTEGER) FROM suppliers", _GOOD_SQL],
        lake=lake,
    )
    env = _chat_ask(lake, minter, cortex, monkeypatch)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["rows"], "the corrected SQL must serve rows"
    assert {row["supplier_id"] for row in env["rows"]} >= {"SUP-01", "SUP-02"}
    assert "SUP-01" in env["text"]
    blob = json.dumps(env, default=str)
    _assert_clean(blob, ["Tan Wei Ming"], where="recovered envelope")
    _assert_clean(json.dumps(cortex.feedback, default=str), ["Tan Wei Ming"], where="feedback")
    retry = cortex.feedback[1]
    assert retry["reason"] == "db_error:ConversionException:contact_name"
    assert "contact_name" in retry["previous_sql"]
    assert retry["reason"] in env["loop"][1]["prompt"]
    assert env["loop"][0]["outcome"] == "db_error:ConversionException:contact_name"
    assert env["loop"][1]["outcome"] == "served"


def test_run_readonly_returns_a_typed_code_not_the_message(lake: Path) -> None:
    _add_column(lake, "contact_name", "VARCHAR", "Tan Wei Ming")
    rows, err = run_readonly("SELECT CAST(contact_name AS INTEGER) FROM suppliers", lake)
    assert rows is None
    assert err == "ConversionException:contact_name"
    rows, err = run_readonly("SELECT supplier_id FROM suppliers ORDER BY 1", lake)
    assert err is None
    assert rows and rows[0]["supplier_id"] == "SUP-01"


def test_run_readonly_does_not_name_a_column_outside_the_cited_tables(lake: Path) -> None:
    # sku lives on inventory. A message that mentions it must not name it when
    # the statement cites only suppliers.
    rows, err = run_readonly("SELECT sku FROM suppliers", lake)
    assert rows is None
    assert err == "BinderException"


def test_a_missing_file_error_does_not_echo_the_path(tmp_path: Path) -> None:
    rows, err = run_readonly("SELECT 1", tmp_path / "secret-customer-dir" / "x.duckdb")
    assert rows is None
    assert err is not None
    assert "secret-customer-dir" not in err
    assert ":" not in err


@pytest.mark.parametrize(
    ("exc", "columns", "expected"),
    [
        (
            type("ConstraintException", (Exception,), {})(
                'Constraint Error: Duplicate key "supplier_id: SUP-77 Kuala Lumpur" '
                "violates primary key constraint."
            ),
            ["supplier_id", "supplier_name"],
            "ConstraintException:supplier_id",
        ),
        (
            type("ConstraintException", (Exception,), {})(
                "Constraint Error: NOT NULL constraint failed: t.passport_no K1234567X"
            ),
            ["passport_no", "country"],
            "ConstraintException:passport_no",
        ),
        (
            type("ConversionException", (Exception,), {})(
                "Could not convert string 'Tan Wei Ming' to INT32"
            ),
            ["supplier_name"],
            "ConversionException",
        ),
        (
            type("ConversionException", (Exception,), {})(
                "Could not convert string '1988-03-14' to DATE when casting "
                "from source column Date_Of_Birth"
            ),
            ["date_of_birth"],
            "ConversionException:date_of_birth",
        ),
        (
            type("BinderException", (Exception,), {})(
                'Referenced column "nope" not found. Candidate bindings: '
                '"supplier_id", "supplier_name"'
            ),
            ["supplier_id", "supplier_name"],
            "BinderException",
        ),
        (
            type("Odd Name; DROP", (Exception,), {})("Could not convert string 'x'"),
            ["supplier_id"],
            "Error",
        ),
    ],
)
def test_typed_db_error_keeps_class_and_one_granted_column_only(
    exc: Exception, columns: list[str], expected: str
) -> None:
    from dms_executor.sql_loop import typed_db_error

    got = typed_db_error(exc, columns)
    assert got == expected
    for leaked in ("Tan Wei Ming", "1988-03-14", "SUP-77", "Kuala Lumpur", "K1234567X"):
        assert leaked not in got


# EXPLAIN fails before any row is read, and DuckDB can still print cell values
# while it binds. A binder error renders an ENUM column's type with its whole
# member list. A PIVOT without an IN list binds on the ON column's distinct
# values (the reviewer's repro on 6b1f4ab; main now refuses it as two
# statements before EXPLAIN). The checker reason is the error class plus a
# column the SQL itself wrote, nothing more.
_ENUM = "ENUM('Tan Wei Ming', 'Siti Aminah')"
_EXPLAIN_CASES = [
    (
        "enum_aggregate",
        _ENUM,
        "SELECT sum(contact_name) FROM suppliers",
        "checker:explain:BinderException:contact_name",
    ),
    (
        "enum_operator",
        _ENUM,
        "SELECT supplier_id, contact_name + 1 FROM suppliers",
        "checker:explain:BinderException",
    ),
    (
        "pivot_binder",
        "VARCHAR",
        "SELECT nope FROM (PIVOT suppliers ON contact_name USING count(*))",
        "checker:multi_statement",
    ),
]


@pytest.mark.parametrize(("case", "ddl", "sql", "reason"), _EXPLAIN_CASES)
def test_explain_error_value_never_reaches_the_model_request_or_the_envelope(
    case: str,
    ddl: str,
    sql: str,
    reason: str,
    lake: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = ["Tan Wei Ming", "Siti Aminah"]
    _add_column(lake, "contact_name", ddl, "Tan Wei Ming")
    cortex = _Cortex(script=[sql])
    env = _chat_ask(lake, minter, cortex, monkeypatch)
    assert_envelope_valid(env)
    assert cortex.asks == [], "the envelope must come from the loop, not a contract miss"
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env["values"] == []
    assert isinstance(env["text"], str) and env["text"].strip()
    assert env["loop"], "the loop record must exist"
    _assert_clean(env["text"], secrets, where=f"{case} text")
    _assert_clean(
        json.dumps(cortex.requests, default=str), secrets, where=f"{case} model request"
    )
    _assert_clean(json.dumps(env, default=str), secrets, where=f"{case} envelope")
    assert env["loop"][0]["outcome"] == reason
    if reason.startswith("checker:explain:"):
        # Retryable: the typed code is what the model hears back.
        sent = [fb for fb in cortex.feedback if fb]
        assert sent, "the checker reason must reach compute_insights as sql_feedback"
        assert sent[0]["reason"] == reason
        assert reason in env["loop"][1]["prompt"]


def test_retry_after_an_explain_error_recovers_and_carries_no_value(
    lake: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = ["Tan Wei Ming", "Siti Aminah"]
    _add_column(lake, "contact_name", _ENUM, "Tan Wei Ming")
    cortex = _Cortex(script=["SELECT sum(contact_name) FROM suppliers", _GOOD_SQL], lake=lake)
    env = _chat_ask(lake, minter, cortex, monkeypatch)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert {row["supplier_id"] for row in env["rows"]} >= {"SUP-01", "SUP-02"}
    assert "SUP-01" in env["text"]
    _assert_clean(json.dumps(env, default=str), secrets, where="recovered envelope")
    _assert_clean(json.dumps(cortex.requests, default=str), secrets, where="model request")
    assert cortex.feedback[1]["reason"] == "checker:explain:BinderException:contact_name"
    assert env["loop"][0]["outcome"] == "checker:explain:BinderException:contact_name"
    assert env["loop"][1]["outcome"] == "served"


def test_a_value_that_spells_a_column_the_sql_did_not_write_is_not_named(lake: Path) -> None:
    # The cell value is the name of another suppliers column. A code may name
    # only a column the SQL wrote, so the value does not ride out as a name.
    _add_column(lake, "contact_name", "VARCHAR", "risk_score")
    rows, err = run_readonly("SELECT strptime(contact_name, '%Y') FROM suppliers", lake)
    assert rows is None
    assert err == "InvalidInputException"
    rows, err = run_readonly("SELECT CAST(contact_name AS INTEGER) FROM suppliers", lake)
    assert rows is None
    assert err == "ConversionException:contact_name"


def test_cited_columns_are_written_by_the_sql_on_the_tables_it_cites(lake: Path) -> None:
    from dms_executor.sql_loop import cited_columns

    _add_column(lake, "contact_name", "VARCHAR", "Tan Wei Ming")
    con = duckdb.connect(str(lake), read_only=True)
    try:
        got = cited_columns(con, "SELECT CAST(contact_name AS INTEGER) FROM suppliers")
        assert got == ["contact_name"]
        assert cited_columns(con, "SELECT country FROM main.suppliers") == ["country"]
        # Another schema of the same table name is not this table.
        assert cited_columns(con, "SELECT country FROM other.suppliers") == []
        # A CTE named like a table is not that table.
        assert cited_columns(
            con, "WITH suppliers AS (SELECT 1 AS country) SELECT country FROM suppliers"
        ) == []
        # A column of the table that the SQL never wrote is not offered.
        assert "supplier_name" not in cited_columns(con, "SELECT country FROM suppliers")
        assert cited_columns(con, "SELEC nope FROM") == []
    finally:
        con.close()
