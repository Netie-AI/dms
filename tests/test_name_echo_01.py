"""Ungranted relation names stay off the user-visible abstain fields.

The name is a whole identifier of any length. Ids are not rewritten.
The ticket line still carries the name, and it does not carry the
question, the SQL, or a database error.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import dms_executor.pipeline_failure as tickets
import pytest
from dms_executor.abstain import build_abstain

_VISIBLE = ("abstain_reason", "assumptions", "message", "messages", "text")


def _visible(env: dict) -> str:
    picked = {key: env.get(key) for key in _VISIBLE}
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict):
            picked["why"] = unsure.get("why")
    loop = env.get("loop")
    if isinstance(loop, list):
        picked["outcomes"] = [
            item.get("outcome") for item in loop if isinstance(item, dict)
        ]
    return json.dumps(picked)


def _ticket(caplog: pytest.LogCaptureFixture) -> dict:
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.getMessage().startswith("pipeline_failure ")
    ]
    assert lines, caplog.text
    return json.loads(lines[-1].split("pipeline_failure ", 1)[1])


@pytest.fixture(autouse=True)
def _clear() -> None:
    tickets._reset_pipeline_failures()


@contextmanager
def _turn(
    space_id: str | None, warehouse: Path, session_store: object | None = None
) -> Iterator[list[str]]:
    """A turn whose grant set comes from ``Executor.grantable_tables``, as in live_ask."""
    from dms_executor import Executor
    from dms_executor.name_echo import begin_grant_turn, end_grant_turn

    exe = Executor(cortex=None, warehouse_path=warehouse, session_store=session_store)
    token = begin_grant_turn(exe, space_id)
    try:
        yield exe.grantable_tables(space_id=space_id)
    finally:
        end_grant_turn(token)


@pytest.mark.parametrize("name", ["t", "po", "sku"])
def test_short_table_name_never_echoes(
    name: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fewer than four characters is still a name. Ids stay. Ticket keeps it."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    question = "how many pallets moved"
    sql = "SELECT 1"
    env = build_abstain(
        reason=f"ungranted:{name}",
        question=question,
        sql=sql,
        stage="followup",
        space_id=name,
        session_id=name,
        answer_id="ans_followup",
        text=f"Follow-up cannot use {name}.",
        assumptions=[
            f"validate:ungranted:{name}",
            "sku_count stays beside a shorter name",
        ],
        abstain_reason=f"ungranted:{name}",
    )
    env["message"] = f"ungranted:{name}"
    env["messages"] = [f"Follow-up cannot use {name}."]
    env = build_abstain(
        reason=f"ungranted:{name}",
        question=question,
        sql=sql,
        stage="followup",
        ask_id="ans_followup",
        demote=env,
    )
    blob = _visible(env)
    assert re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", blob
    ) is None
    assert "sku_count" in blob
    assert env["space_id"] == name
    assert env["session_id"] == name
    assert env["answer_id"] == "ans_followup"
    ticket = _ticket(caplog)
    assert name in ticket["names"]
    raw = json.dumps(ticket)
    assert question not in raw
    assert sql not in raw
    assert "ticket_id" in env


def test_qualified_name_and_followup_text_are_scrubbed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    env = build_abstain(
        reason="ungranted_table:bronze.po",
        question="sheet total",
        stage="bronze",
        space_id="space-po",
        session_id="ses_po_1",
        text="ABSTAIN ungranted_table:bronze.po",
        assumptions=["validate:ungranted_table:bronze.po"],
        answer_id="ans_bronze",
    )
    env["message"] = "needs bronze.po"
    env = build_abstain(
        reason="ungranted_table:bronze.po",
        question="sheet total",
        stage="bronze",
        ask_id="ans_bronze",
        demote=env,
    )
    blob = _visible(env)
    assert "bronze.po" not in blob
    assert "po" not in blob
    assert env["space_id"] == "space-po"
    assert env["session_id"] == "ses_po_1"
    ticket = _ticket(caplog)
    assert "bronze.po" in ticket["names"]
    assert "sheet total" not in json.dumps(ticket)


def test_flag_off_adds_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    plain = build_abstain(
        reason="no_path",
        question="how many pallets moved",
        text="I cannot certify that.",
        assumptions=["no_path"],
        space_id="po",
        session_id="po",
        answer_id="ans_plain",
    )
    named = build_abstain(
        reason="ungranted:po",
        question="how many pallets moved",
        text="I cannot certify po.",
        assumptions=["validate:ungranted:po"],
        space_id="po",
        session_id="po",
        answer_id="ans_named",
    )
    assert "ticket_id" not in plain
    assert "ticket_id" not in named
    assert set(named) == set(plain)
    assert named["space_id"] == "po"
    assert "po" not in _visible(named)


def test_reason_codes_file_and_unparsed_stay() -> None:
    for code in ("ungranted:file", "ungranted:unparsed"):
        env = build_abstain(
            reason=code,
            text=code,
            assumptions=[code],
            abstain_reason=code,
            answer_id="ans_code",
        )
        assert code in env["text"]
        assert code in env["assumptions"][0]


def test_missing_join_name_is_ticket_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The grain stays. The ungranted relation is ticket-only."""
    from dms_executor.generative_ask import _abstain
    from dms_executor.ontology import missing_join_for_ungranted

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    gap = missing_join_for_ungranted("ungranted:shipments", ("sku", "plant"))
    assert gap is not None
    question = "shipping cost by SKU and plant"
    env = _abstain(
        question,
        gap,
        space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        session_id="ses_mj",
    )
    blob = _visible(env)
    assert "shipments" not in blob
    assert "plant" in blob
    assert "missing_join" in blob
    ticket = _ticket(caplog)
    assert ticket["reason"] == "missing_join"
    assert "shipments" in ticket["names"]
    raw = json.dumps(ticket)
    assert question not in raw
    assert "SELECT" not in raw


@pytest.mark.parametrize(
    "reason",
    ['ungranted: "t"', "ungranted: 'po'", "ungranted table sku"],
)
def test_quoted_and_space_tails_stay_off_the_envelope(
    reason: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    name = {"ungranted: \"t\"": "t", "ungranted: 'po'": "po", "ungranted table sku": "sku"}[reason]
    env = build_abstain(
        reason=reason,
        question="how many pallets moved",
        text=f"Follow-up cannot use {reason}.",
        assumptions=[reason, "sku_count stays beside a shorter name"],
        abstain_reason=reason,
        answer_id="ans_tail",
        space_id="space-keep",
        session_id="ses_keep",
    )
    blob = _visible(env)
    assert re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", blob
    ) is None
    assert "sku_count" in blob
    assert env["space_id"] == "space-keep"
    ticket = _ticket(caplog)
    assert name in ticket["names"]


def test_schema_qualifier_is_not_scrubbed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """bronze.po is the relation. The word bronze in ordinary prose stays."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    env = build_abstain(
        reason="ungranted_table:bronze.po",
        question="sheet total",
        text="The bronze layer still cites bronze.po.",
        assumptions=["bronze layer note", "validate:ungranted_table:bronze.po"],
        answer_id="ans_schema",
    )
    blob = _visible(env)
    assert "bronze layer" in blob
    assert "bronze.po" not in blob
    assert re.search(r"(?<![A-Za-z0-9_])po(?![A-Za-z0-9_])", blob) is None
    ticket = _ticket(caplog)
    assert "bronze.po" in ticket["names"]
    assert "bronze" not in ticket["names"]


_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.mark.parametrize(
    ("reason", "ticket_name"),
    [
        ("ungranted:`hr`", "hr"),
        ('ungranted:`hr`."payroll"', "hr.payroll"),
        ('ungranted:"hr".`payroll`', "hr.payroll"),
        ("ungranted:[hr].[payroll]", "hr.payroll"),
    ],
)
def test_any_quoting_drops_ungranted_names_and_keeps_granted(
    reason: str,
    ticket_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Backticks, mixed quotes, and brackets are identifiers. Grants stay."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    with _turn(_FINANCE, tmp_path / "w.duckdb"):
        env = build_abstain(
            reason=reason,
            question="headcount by site",
            text=f"The bronze layer still cites ungranted:inventory beside {reason}.",
            assumptions=["bronze layer", "ungranted:inventory", reason],
            abstain_reason=reason,
            space_id=_FINANCE,
            session_id="ses_hr_1",
            answer_id="ans_hr",
        )
    blob = _visible(env)
    assert "bronze layer" in blob
    assert re.search(r"(?<![A-Za-z0-9_])inventory(?![A-Za-z0-9_])", blob)
    for part in ticket_name.split("."):
        assert (
            re.search(
                rf"(?i)(?<![A-Za-z0-9_]){re.escape(part)}(?![A-Za-z0-9_])",
                blob,
            )
            is None
        )
    assert env["session_id"] == "ses_hr_1"
    assert env["space_id"] == _FINANCE
    ticket = _ticket(caplog)
    assert ticket_name in ticket["names"]
    assert "headcount by site" not in json.dumps(ticket)


def _surface(env: dict) -> str:
    chunks = [str(env.get(key) or "") for key in ("text", "abstain_reason", "message")]
    assumptions = env.get("assumptions") or []
    if isinstance(assumptions, list):
        chunks.extend(str(item) for item in assumptions)
    return "\n".join(chunks)


@pytest.mark.parametrize(
    ("reason", "logical"),
    [
        ('ungranted:"hr data"', "hr data"),
        ('ungranted:"a""b"', 'a"b'),
        ("ungranted:`a``b`", "a`b"),
        ("ungranted:人员", "人员"),
    ],
)
def test_sqlglot_identifier_shapes_stay_off_the_envelope(
    reason: str,
    logical: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Spaces, escaped quotes, and non-ASCII names are identifier parts."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    with _turn(_FINANCE, tmp_path / "w.duckdb"):
        env = build_abstain(
            reason=reason,
            question="headcount by site",
            text=f"The bronze layer still cites ungranted:inventory beside {reason}.",
            assumptions=["bronze layer", "ungranted:inventory", reason],
            abstain_reason=reason,
            space_id=_FINANCE,
            session_id="ses_hr_1",
            answer_id="ans_ident",
        )
    surface = _surface(env)
    assert "bronze layer" in surface
    assert re.search(r"(?<![A-Za-z0-9_])inventory(?![A-Za-z0-9_])", surface)
    assert logical not in surface
    assert reason.split(":", 1)[1] not in surface
    assert env["session_id"] == "ses_hr_1"
    ticket = _ticket(caplog)
    assert logical in ticket["names"]
    assert "headcount by site" not in json.dumps(ticket)


def test_granted_prefix_stays_when_longer_name_is_ungranted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """hr is granted. hr_payroll is a different identifier and is not."""
    from dms_executor.demo_grants import DEMO_SPACE_GRANTS

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    label, tables = DEMO_SPACE_GRANTS[_FINANCE]
    monkeypatch.setitem(DEMO_SPACE_GRANTS, _FINANCE, (label, (*tables, "hr")))
    with _turn(_FINANCE, tmp_path / "w.duckdb") as granted:
        assert "hr" in granted
        env = build_abstain(
            reason="ungranted:hr_payroll",
            question="headcount by site",
            text=(
                "The bronze layer still cites ungranted:hr beside "
                "ungranted:inventory and ungranted:hr_payroll."
            ),
            assumptions=["bronze layer", "ungranted:hr", "ungranted:hr_payroll"],
            abstain_reason="ungranted:hr_payroll",
            space_id=_FINANCE,
            session_id="ses_hr_1",
            answer_id="ans_prefix",
        )
    surface = _surface(env)
    assert "bronze layer" in surface
    assert re.search(r"(?<![A-Za-z0-9_])hr(?![A-Za-z0-9_])", surface)
    assert re.search(r"(?<![A-Za-z0-9_])inventory(?![A-Za-z0-9_])", surface)
    assert "hr_payroll" not in surface
    assert env["session_id"] == "ses_hr_1"
    ticket = _ticket(caplog)
    assert "hr_payroll" in ticket["names"]
    assert "headcount by site" not in json.dumps(ticket)


def test_object_cites_ungranted_is_a_tail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ontology.py names the table after 'cites ungranted', not after a colon."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    reason = (
        "missing_join: no granted join path for grain plant "
        "(object plant cites ungranted shipments)"
    )
    env = build_abstain(
        reason=reason,
        question="shipping cost by SKU and plant",
        text=reason,
        assumptions=["bronze layer", reason],
        abstain_reason=reason,
        space_id=_FINANCE,
        session_id="ses_cite",
        answer_id="ans_cite",
    )
    blob = _visible(env)
    assert "shipments" not in blob
    assert "bronze layer" in blob
    assert "plant" in blob
    ticket = _ticket(caplog)
    assert "shipments" in ticket["names"]


def test_unbalanced_quote_clears_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tail that does not tokenize is fail-closed, not left on the envelope."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    env = build_abstain(
        reason='ungranted:"unterminated',
        question="headcount",
        text='The bronze layer cites ungranted:"unterminated',
        assumptions=["bronze layer", 'ungranted:"unterminated'],
        abstain_reason='ungranted:"unterminated',
        space_id=_FINANCE,
        session_id="ses_open",
        answer_id="ans_open",
    )
    assert env["text"] == ""
    assert env["abstain_reason"] == ""
    assert "bronze layer" in env["assumptions"]
    assert all("unterminated" not in str(item) for item in env["assumptions"])
    assert env["session_id"] == "ses_open"


def test_space_upload_is_granted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """An uploaded table is granted through the validator's own grant function."""
    from dms_executor.demo_grants import DemoSessionStore

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")

    def _uploads(path: object = None, *, space_id: str | None = None) -> tuple[str, ...]:
        if space_id == _FINANCE:
            return ("uploaded_sheet",)
        return ()

    monkeypatch.setattr("dms_executor.demo_grants.ingested_bronze_tables", _uploads)
    store = DemoSessionStore(uploads=lambda: ("uploaded_sheet",))
    with _turn(_FINANCE, tmp_path / "w.duckdb", store) as granted:
        assert "uploaded_sheet" in granted
        env = build_abstain(
            reason="ungranted:hr_payroll,uploaded_sheet",
            question="headcount",
            text="ungranted:hr_payroll beside ungranted:uploaded_sheet",
            assumptions=["bronze layer", "ungranted:uploaded_sheet"],
            abstain_reason="ungranted:hr_payroll,uploaded_sheet",
            space_id=_FINANCE,
            session_id="ses_up",
            answer_id="ans_up",
        )
    blob = _visible(env)
    assert "hr_payroll" not in blob
    assert "uploaded_sheet" in blob
    assert "bronze layer" in blob
    ticket = _ticket(caplog)
    assert "hr_payroll" in ticket["names"]


def test_sixty_ungranted_names_scrub_under_200ms(monkeypatch: pytest.MonkeyPatch) -> None:
    """One serving-dialect parse per tail. Sixty names stay under 200 ms."""
    import time

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    names = [f"tbl_{index:02d}_secret" for index in range(60)]
    blob = " ".join(f"ungranted:{name}" for name in names) + " bronze layer"
    env = {
        "text": blob,
        "abstain_reason": blob,
        "assumptions": [blob],
        "space_id": "not-a-space",
        "session_id": "ses_speed",
    }
    from dms_executor.name_echo import hide_echo

    started = time.perf_counter()
    hide_echo(env, blob)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.2, elapsed
    for name in names:
        assert name not in env["text"]
        assert name not in env["abstain_reason"]
    assert "bronze layer" in env["text"]


def test_db_error_text_stays_out_of_the_ticket(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    marker = "boom-secret-7c2e"
    question = "how many pallets moved"
    build_abstain(
        reason=f"db_error:ConversionException: {marker}",
        question=question,
        sql=f"SELECT '{marker}'",
        text=f"db_error: {marker}",
        assumptions=[marker],
        answer_id="ans_db",
    )
    ticket = _ticket(caplog)
    raw = json.dumps(ticket)
    assert "names" not in ticket
    assert marker not in raw
    assert question not in raw
    assert "SELECT" not in raw


# P04 is the loop outcome attached after build_abstain. The other eight are
# the same HTTP path with the flag on: short, schema, and bare ungranted names.
_HTTP_PROBES = (
    ("p01", "how many florbs did wibble sell last quarter", "SELECT 1 FROM t", "t"),
    ("p02", "how many florbs did wibble sell last quarter", "SELECT 1 FROM po", "po"),
    (
        "p03",
        "how many florbs did wibble sell last quarter",
        "SELECT 1 FROM secret_hr",
        "secret_hr",
    ),
    ("p04", "List chemicals in inventory", "SELECT 1 FROM hr_payroll", "hr_payroll"),
    (
        "p05",
        "how many florbs did wibble sell last quarter",
        "SELECT 1 FROM not_granted",
        "not_granted",
    ),
    (
        "p06",
        "how many florbs did wibble sell last quarter",
        "SELECT 1 FROM zz_payroll",
        "zz_payroll",
    ),
    (
        "p07",
        "how many florbs did wibble sell last quarter",
        "SELECT 1 FROM bronze.po_sheet",
        "po_sheet",
    ),
    (
        "p08",
        "how many florbs did wibble sell last quarter",
        'SELECT 1 FROM "hr data"',
        "hr",
    ),
    (
        "p09",
        "how many florbs did wibble sell last quarter",
        "SELECT 1 FROM sku_shadow",
        "sku_shadow",
    ),
)


def _allowed_name_hit(path: str) -> bool:
    """NAME-ECHO-LOOP-01 still owns loop SQL and the raw reply."""
    if re.fullmatch(r"\$\.loop\[\d+\]\.sql", path):
        return True
    return ".raw_reply" in path


def _name_hits(body: dict, name: str) -> list[str]:
    """Paths in the full JSON whose text contains the whole identifier."""
    pat = re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    found: list[str] = []

    def walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_s = str(key)
                child = f"{path}.{key_s}"
                if pat.search(key_s) and not _allowed_name_hit(child):
                    found.append(f"{child}(key)")
                walk(value, child)
            return
        if isinstance(obj, list):
            for index, value in enumerate(obj):
                walk(value, f"{path}[{index}]")
            return
        if isinstance(obj, str) and pat.search(obj) and not _allowed_name_hit(path):
            found.append(f"{path}={obj}")

    walk(body, "$")
    return found


def _post_ask(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    sql: str,
    *,
    lane: str = "product",
    **kwargs: Any,
) -> dict:
    """One POST /v1/chat/ask. See ``_post_asks``."""
    return _post_asks(monkeypatch, [question], sql, lane=lane, **kwargs)[0]


def _post_asks(
    monkeypatch: pytest.MonkeyPatch,
    questions: list[str],
    sql: str,
    *,
    lane: str = "product",
    answer: str | None = None,
    rows: list[dict] | None = None,
    space_id: str | Callable[[Any], str] | None = None,
    session_store: object | None = None,
    status: int = 200,
) -> list[dict]:
    """POST /v1/chat/ask once per question, one app, with the extract loop on.

    ``product`` and ``generative`` feed one Insights SQL. ``generated``
    is an Insights transport miss; Cortex ask returns that SQL on the
    generated route, with no loop, answering ``answer`` (default: the
    SQL) over ``rows``. ``space_id`` may be a callable that creates the
    Space on the app and returns its id.
    """
    from dataclasses import dataclass, field
    from types import SimpleNamespace
    from unittest.mock import patch

    from cortex_client.compute import COMPUTE_PATH, compute_insights
    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from cortex_contract.execution import Manifest, QueryResult
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from dms_executor import Executor
    from dms_executor.manifest import ManifestMinter, SessionAcl
    from fastapi.testclient import TestClient

    class _Http:
        def __init__(self, body: dict) -> None:
            self.body = body
            self.calls: list[str] = []

        def __call__(self, *a: object, **k: object) -> _Http:
            return self

        def __enter__(self) -> _Http:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def post(self, url: str, json: object = None, headers: object = None) -> object:
            self.calls.append("POST " + url)
            if str(url).endswith(COMPUTE_PATH):
                raise AssertionError("ask lane must never POST /dms/query")
            return SimpleNamespace(status_code=200, json=lambda: self.body)

        def get(self, url: str, params: object = None, headers: object = None) -> object:
            self.calls.append("GET " + url)
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"phase": "ontology", "ontology": {"metrics": []}},
            )

    @dataclass
    class _Cortex:
        fake: _Http
        asks: list[AskRequest] = field(default_factory=list)

        def compute_insights(self, question: str, **kwargs: object) -> dict | None:
            if lane == "generated":
                return None
            with patch("cortex_client.compute.httpx.Client", self.fake):
                return compute_insights(
                    "http://127.0.0.1:8010",
                    question=question,
                    session_id=kwargs.get("session_id"),
                    space_id=kwargs.get("space_id"),
                    ontology=kwargs.get("ontology"),
                    api_key="fake-name-echo-key",
                )

        def submit(self, req: object) -> QueryResult:
            return QueryResult(ok=True, status="bound", run_id="run_name_echo")

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            return LedgerAppendResponse(entry_id="led_name_echo", hash="hash_name_echo")

        def ask(self, req: AskRequest) -> AskResponse:
            self.asks.append(req)
            if lane == "generated":
                return AskResponse(
                    answer=sql if answer is None else answer,
                    badge="generated",
                    sql_used=sql,
                    assumptions=[sql],
                    rows=[{"n": 1}] if rows is None else rows,
                    audit_id="aud_name_echo_generated",
                    route="generated",
                )
            return AskResponse(
                answer="There are 5 locations.",
                badge="certified",
                sql_used="SELECT COUNT(*) AS location_count FROM locations",
                rows=[{"location_count": 5}],
                audit_id="aud_name_echo",
                route="sql",
            )

    wire = {
        "phase": "generate",
        "generative": {"sql": sql, "ok": True, "raw_reply": sql},
        "raw_reply": sql,
    }
    cortex = _Cortex(fake=_Http(wire))
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-26T00:00:00+00:00",
            expires_at="2026-09-26T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("CORTEX_API_KEY", "fake-name-echo-key")
    if lane == "generative":
        monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    settings_mod.get_settings.cache_clear()
    bodies: list[dict] = []
    try:
        app = create_app()
        app.state.ask_service = Executor(  # type: ignore[arg-type]
            cortex=cortex, minter=minter, session_store=session_store
        )
        app.state.cortex = cortex
        sid = space_id(app) if callable(space_id) else space_id
        client = TestClient(app)
        for question in questions:
            payload: dict[str, object] = {
                "question": question,
                "session_id": "ses_name_echo_http",
            }
            if sid:
                payload["space_id"] = sid
            if lane == "generative":
                payload["ask_path"] = "generative"
            res = client.post("/v1/chat/ask", json=payload)
            assert res.status_code == status, res.text
            body = res.json()
            assert isinstance(body, dict)
            bodies.append(body)
    finally:
        settings_mod.get_settings.cache_clear()
    return bodies


@pytest.mark.parametrize(
    ("probe", "question", "sql", "name"),
    _HTTP_PROBES,
    ids=[row[0] for row in _HTTP_PROBES],
)
def test_http_loop_outcome_drops_ungranted_name(
    probe: str,
    question: str,
    sql: str,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Flag on, over HTTP. The loop is attached after build_abstain."""
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    body = _post_ask(monkeypatch, question, sql)
    assert body.get("abstained") is True, probe
    loop = body.get("loop")
    assert isinstance(loop, list) and loop, probe
    entry = loop[0]
    assert isinstance(entry, dict)
    outcome = str(entry.get("outcome") or "")
    assert re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", outcome
    ) is None, outcome
    assert entry.get("sql") == sql, probe
    # Lead has not ruled on loop SQL. The statement and the raw reply stay.
    assert entry.get("raw_reply") == sql, probe
    visible = _visible(body)
    assert re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", visible
    ) is None, visible
    tickets = [
        json.loads(rec.getMessage().split("pipeline_failure ", 1)[1])
        for rec in caplog.records
        if rec.getMessage().startswith("pipeline_failure ")
    ]
    assert any(name in (item.get("names") or []) for item in tickets), tickets


def _tickets(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(rec.getMessage().split("pipeline_failure ", 1)[1])
        for rec in caplog.records
        if rec.getMessage().startswith("pipeline_failure ")
    ]


_SERVED_REFUSAL = "read a table this Space does not grant"


@pytest.mark.parametrize("lane", ["product", "generative", "generated"])
def test_p04_full_response_json_hides_hr_payroll(
    lane: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Flag on. The only whole-identifier hits are loop SQL and raw_reply.

    Product and generative attach the extract loop, then the curated
    re-attach. Generated is the Cortex stub: no loop, and its served SQL
    reads hr_payroll, which this turn does not grant, so the answer
    abstains. The name is not hidden under a validated badge.
    """
    from dms_executor.envelope import assert_envelope_valid

    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    question = "List chemicals in inventory"
    sql = "SELECT 1 FROM hr_payroll"
    name = "hr_payroll"
    body = _post_ask(monkeypatch, question, sql, lane=lane)
    assert_envelope_valid(body)
    leaked = _name_hits(body, name)
    assert leaked == [], leaked
    assert body.get("abstained") is True
    assert body.get("badge") == "ABSTAIN"
    assert not body.get("values")
    assert not body.get("rows")
    if lane == "generated":
        assert body.get("route") == "generated"
        assert body.get("sql_used") is None
        assert _SERVED_REFUSAL in str(body.get("text"))
        assert not body.get("loop")
        assert any(name in (t.get("names") or []) for t in _tickets(caplog))
        return
    loop = body.get("loop")
    assert isinstance(loop, list) and loop
    entry = loop[0]
    assert isinstance(entry, dict)
    assert entry.get("sql") == sql
    assert entry.get("raw_reply") == sql


@pytest.mark.parametrize("space_id", [None, _FINANCE, "sp_warehouse_ops", "sp_user_created_7"])
def test_scrub_opens_no_serving_connection(space_id: str | None) -> None:
    """CONN-POOL-01 blocks serving opens while a lease abstain is built.

    The scrub reads the turn's grant set and opens nothing, so the
    abstain is built and an answer passes through unchanged.
    """
    from dms_executor.lake_registry import block_serving_lease, reset_serving_lease_block
    from dms_executor.name_echo import hide_echo

    token = block_serving_lease("serving_lease_cap")
    try:
        env = build_abstain(
            reason="serving_lease_cap",
            question="how many locations",
            stage="serving_lease",
            answer_id="ans_serving_lease_cap",
            text="I can't answer this right now: serving connections are at the cap.",
            values=[],
            rows=[],
            sql_used=None,
            assumptions=["serving_lease_cap", "validate:ungranted:hr_payroll"],
            space_id=space_id,
            session_id="ses_lease",
            ask_mode="live",
            route="abstain",
        )
        answered = {
            "abstained": False,
            "badge": "L2_VALIDATED",
            "text": "There are 5 locations.",
            "sql_used": "SELECT COUNT(*) AS n FROM locations",
            "assumptions": ["checker:ungranted:hr_payroll"],
            "space_id": space_id,
        }
        hide_echo(answered, "")
    finally:
        reset_serving_lease_block(token)
    assert env["badge"] == "ABSTAIN"
    assert "serving connections are at the cap" in env["text"]
    assert "hr_payroll" not in json.dumps(env["assumptions"])
    assert answered["text"] == "There are 5 locations."
    assert answered["sql_used"] == "SELECT COUNT(*) AS n FROM locations"
    assert "hr_payroll" not in json.dumps(answered["assumptions"])


@pytest.mark.parametrize("space_id", [_FINANCE, "sp_margin"])
def test_http_serving_lease_abstain_in_a_seeded_space(
    space_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease cap in a seeded Space is the ABSTAIN envelope, not a 503."""
    from dms_executor import Executor
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.lake_registry import ServingLeaseCap

    def _cap(self: object, *args: object, **kwargs: object) -> dict:
        raise ServingLeaseCap(32, 0.0)

    monkeypatch.setattr(Executor, "_live_ask", _cap)
    body = _post_ask(
        monkeypatch, "How many locations are there?", "SELECT 1", space_id=space_id
    )
    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert "serving connections are at the cap" in body["text"]
    assert not body.get("values")
    assert not body.get("rows")


@pytest.mark.parametrize("cte", ["t", "revenue", "excluded"])
def test_http_cte_answer_is_untouched(cte: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A CTE is not a relation. The validated answer, its SQL, and its rows stand."""
    from dms_executor.envelope import assert_envelope_valid

    sql = f"WITH {cte} AS (SELECT COUNT(*) AS n FROM locations) SELECT n FROM {cte}"
    answer = f"Location count ({cte}): there are 5 locations."
    body = _post_ask(
        monkeypatch,
        "How many locations are there?",
        sql,
        lane="generated",
        answer=answer,
        rows=[{"n": 5}],
    )
    assert_envelope_valid(body)
    assert body["abstained"] is False
    assert body["badge"] != "ABSTAIN"
    assert body["text"] == answer
    assert body["sql_used"] == sql
    assert body["rows"] == [{"n": 5}]


def test_cte_in_loop_sql_is_not_scrubbed_from_an_abstain(tmp_path: Path) -> None:
    """On an abstain, the base table a CTE reads is dropped; the CTE name is prose."""
    from dms_executor.name_echo import hide_echo

    env = {
        "abstained": True,
        "badge": "ABSTAIN",
        "text": "I could not certify revenue from hr_payroll.",
        "assumptions": ["revenue is gross"],
        "sql_used": None,
        "loop": [
            {
                "sql": "WITH revenue AS (SELECT * FROM hr_payroll) SELECT * FROM revenue",
                "outcome": "checker:explain",
            }
        ],
    }
    with _turn(None, tmp_path / "w.duckdb"):
        hide_echo(env, "")
    assert env["text"] == "I could not certify revenue from ."
    assert env["assumptions"] == ["revenue is gross"]
    assert env["loop"][0]["sql"].startswith("WITH revenue AS")


class _UserSpaceStore:
    """``SessionStore`` that also knows a Space created through POST /v1/spaces."""

    def __init__(self, tables: tuple[str, ...]) -> None:
        from dms_executor.demo_grants import DemoSessionStore

        self.base = DemoSessionStore(uploads=lambda: ())
        self.spaces: set[str] = set()
        self.tables = tables

    def is_space_member(self, space_id: str, user_id: str) -> bool:
        return space_id in self.spaces or self.base.is_space_member(space_id, user_id)

    def list_space_source_ids(self, space_id: str) -> list:
        from dms_executor.demo_grants import source_id_for

        if space_id in self.spaces:
            return [source_id_for(t) for t in self.tables]
        return self.base.list_space_source_ids(space_id)

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list:
        from dms_executor.acl import SourceGrant
        from dms_executor.demo_grants import source_id_for

        extra = [
            SourceGrant(
                source_id=source_id_for(t), kind="sql", table_name=t, row_predicate="TRUE"
            )
            for t in self.tables
        ]
        return [*self.base.list_user_source_grants(tenant_id, user_id), *extra]

    def default_pool_id(self, tenant_id: str) -> str:
        return "default"


@pytest.mark.parametrize("granted", [True, False], ids=["granted", "not_granted"])
def test_http_user_created_space_reads_the_session_store_grant(
    granted: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The check reads ``Executor.grantable_tables``, not the demo seed.

    A Space made through POST /v1/spaces is not in DEMO_SPACE_GRANTS. Its
    grant comes from the session store. Granted, the answer stands with
    its text and rows. Not granted, the answer abstains.
    """
    from dms_executor.envelope import assert_envelope_valid

    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    store = _UserSpaceStore(("q3_po_Sales",) if granted else ())

    def _space(app: Any) -> str:
        sid = str(app.state.space_store.create("Procurement").id)
        store.spaces.add(sid)
        return sid

    sql = "SELECT SUM(amount) AS total FROM bronze.q3_po_Sales"
    answer = "Total from bronze.q3_po_Sales: 1,200."
    body = _post_ask(
        monkeypatch,
        "how many florbs did wibble sell last quarter",
        sql,
        lane="generated",
        answer=answer,
        rows=[{"total": 1200}],
        space_id=_space,
        session_store=store,
    )
    assert_envelope_valid(body)
    if granted:
        assert body["abstained"] is False
        assert body["text"] == answer
        assert body["sql_used"] == sql
        assert body["rows"] == [{"total": 1200}]
        return
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert _SERVED_REFUSAL in body["text"]
    assert _name_hits(body, "q3_po_Sales") == []
    assert not body.get("rows")
    assert any("bronze.q3_po_Sales" in (t.get("names") or []) for t in _tickets(caplog))


def test_http_answer_reading_an_ungranted_table_abstains(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Finance does not grant shipments. The figure is not shipped, and not hidden.

    The follow-up turn does not reuse the refused figure either.
    """
    from dms_executor.envelope import assert_envelope_valid

    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    sql = "SELECT COUNT(*) AS n FROM shipments"
    first, follow = _post_asks(
        monkeypatch,
        ["How many shipments are there?", "average of them"],
        sql,
        lane="generated",
        answer="There are 4 shipments.",
        rows=[{"n": 4}],
        space_id=_FINANCE,
    )
    assert_envelope_valid(first)
    assert first["abstained"] is True
    assert first["badge"] == "ABSTAIN"
    assert first["sql_used"] is None
    assert not first.get("values")
    assert not first.get("rows")
    assert not first.get("drillthrough_token")
    assert _SERVED_REFUSAL in first["text"]
    assert _name_hits(first, "shipments") == []
    assert any("shipments" in (t.get("names") or []) for t in _tickets(caplog))
    assert follow["abstained"] is True
    assert "4" not in str(follow.get("text"))
    assert not follow.get("values")


def test_http_answer_reading_a_granted_table_stands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warehouse Ops grants shipments. The same answer ships with its text and rows."""
    from dms_executor.envelope import assert_envelope_valid

    sql = "SELECT COUNT(*) AS n FROM shipments"
    body = _post_ask(
        monkeypatch,
        "How many shipments are there?",
        sql,
        lane="generated",
        answer="There are 4 shipments.",
        rows=[{"n": 4}],
        space_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
    )
    assert_envelope_valid(body)
    assert body["abstained"] is False
    assert body["text"] == "There are 4 shipments."
    assert body["sql_used"] == sql
    assert body["rows"] == [{"n": 4}]


_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_ALIAS_SPEND_SQL = (
    "SELECT s.country, ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) "
    "AS total_spend_myr FROM warehouse_inventory AS i "
    "JOIN warehouse_suppliers AS s ON i.supplier_id = s.supplier_id "
    "GROUP BY s.country ORDER BY total_spend_myr DESC"
)
_ALIAS_SPEND_ROWS = [
    {"country": "MY", "total_spend_myr": 20516.0},
    {"country": "SG", "total_spend_myr": 7524.0},
    {"country": "TH", "total_spend_myr": 1800.0},
]
_ALIAS_SPEND_TEXT = "Spend by country: MY 20,516.00, SG 7,524.00, TH 1,800.00."


@pytest.mark.parametrize("space_id", [None, _FINANCE], ids=["no_space", "finance"])
def test_http_warehouse_alias_answer_over_granted_tables_stands(
    space_id: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cortex cites the lake as ``warehouse_<table>``. Both Spaces grant the tables.

    The answer ships with its text, SQL and rows, as on main.
    """
    from dms_executor.envelope import assert_envelope_valid

    body = _post_ask(
        monkeypatch,
        "break down florb outlay for each supplier country",
        _ALIAS_SPEND_SQL,
        lane="generated",
        answer=_ALIAS_SPEND_TEXT,
        rows=_ALIAS_SPEND_ROWS,
        space_id=space_id,
    )
    assert_envelope_valid(body)
    assert body["abstained"] is False
    assert body["badge"] != "ABSTAIN"
    assert body["text"] == _ALIAS_SPEND_TEXT
    assert body["sql_used"] == _ALIAS_SPEND_SQL
    assert body["rows"] == _ALIAS_SPEND_ROWS
    assert _SERVED_REFUSAL not in body["text"]


@pytest.mark.parametrize(
    ("space_id", "sql", "name"),
    [
        (_OPS, _ALIAS_SPEND_SQL, "warehouse_suppliers"),
        (_FINANCE, "SELECT COUNT(*) AS n FROM warehouse_shipments", "warehouse_shipments"),
        (None, "SELECT COUNT(*) AS n FROM warehouse_hr_payroll", "warehouse_hr_payroll"),
    ],
    ids=["ops_suppliers", "finance_shipments", "not_a_lake_table"],
)
def test_http_warehouse_alias_of_an_ungranted_table_abstains(
    space_id: str | None,
    sql: str,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The alias is the lake table's grant, no wider. Ops does not grant suppliers."""
    from dms_executor.envelope import assert_envelope_valid

    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    body = _post_ask(
        monkeypatch,
        "break down florb outlay for each supplier country",
        sql,
        lane="generated",
        answer=_ALIAS_SPEND_TEXT,
        rows=_ALIAS_SPEND_ROWS,
        space_id=space_id,
    )
    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["sql_used"] is None
    assert not body.get("rows")
    assert not body.get("values")
    assert _SERVED_REFUSAL in body["text"]
    assert "20,516" not in body["text"]
    assert _name_hits(body, name) == []
    assert any(name in (t.get("names") or []) for t in _tickets(caplog))


@pytest.mark.parametrize(
    ("sql", "grants", "refused"),
    [
        ("SELECT * FROM warehouse_inventory", {"inventory"}, []),
        ('SELECT * FROM "WAREHOUSE_Inventory"', {"inventory"}, []),
        ("SELECT * FROM warehouse_inventory", {"warehouse_inventory"}, []),
        ("SELECT * FROM warehouse_inventory", {"locations"}, [("warehouse_inventory",)]),
        (
            "SELECT * FROM warehouse_inventory",
            {"bronze.inventory"},
            [("warehouse_inventory",)],
        ),
        (
            "SELECT * FROM hr.warehouse_inventory",
            {"inventory"},
            [("hr", "warehouse_inventory")],
        ),
        ("SELECT * FROM warehouse_q3_po", {"q3_po"}, [("warehouse_q3_po",)]),
    ],
    ids=[
        "alias_of_grant",
        "alias_any_case",
        "grant_is_alias",
        "lake_not_granted",
        "upload_grant_opens_no_lake",
        "qualified_alias",
        "alias_of_upload",
    ],
)
def test_served_lake_alias_maps_onto_the_lake_grant_only(
    sql: str, grants: set[str], refused: list[tuple[str, ...]]
) -> None:
    """Only an unqualified ``warehouse_<lake table>`` maps, onto that table's grant."""
    from dms_executor.name_echo import served_ungranted

    env = {"abstained": False, "badge": "L2_VALIDATED", "sql_used": sql}
    assert served_ungranted(env, grants) == refused


def test_granted_lake_alias_stays_on_an_abstain() -> None:
    """A granted lake table keeps its alias in abstain text and sql_used."""
    from dms_executor.name_echo import hide_echo

    sql = "SELECT SUM(x) FROM warehouse_inventory JOIN warehouse_shipments ON true"
    env = {
        "abstained": True,
        "badge": "ABSTAIN",
        "text": "Could not certify the spend from warehouse_inventory.",
        "assumptions": ["checker:single_total"],
        "sql_used": sql,
    }
    hide_echo(env, "", grants={"inventory", "suppliers", "locations", "transactions"})
    assert env["text"] == "Could not certify the spend from warehouse_inventory."
    assert env["sql_used"] == "SELECT SUM(x) FROM warehouse_inventory JOIN  ON true"
