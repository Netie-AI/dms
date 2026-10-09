"""Ungranted relation names stay off the user-visible abstain fields.

The name is a whole identifier of any length. Ids are not rewritten.
The ticket line still carries the name, and it does not carry the
question, the SQL, or a database error.
"""

from __future__ import annotations

import json
import logging
import re

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
) -> None:
    """Backticks, mixed quotes, and brackets are identifiers. Grants stay."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
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
) -> None:
    """Spaces, escaped quotes, and non-ASCII names are identifier parts."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
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
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """hr is granted. hr_payroll is a different identifier and is not."""
    from dms_executor.demo_grants import DEMO_SPACE_GRANTS

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    label, tables = DEMO_SPACE_GRANTS[_FINANCE]
    monkeypatch.setitem(DEMO_SPACE_GRANTS, _FINANCE, (label, (*tables, "hr")))
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
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An uploaded table uses the same grant source as the validator."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")

    def _uploads(path: object = None, *, space_id: str | None = None) -> tuple[str, ...]:
        if space_id == _FINANCE:
            return ("uploaded_sheet",)
        return ()

    monkeypatch.setattr("dms_executor.demo_grants.ingested_bronze_tables", _uploads)
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
) -> dict:
    """POST /v1/chat/ask with the extract loop on.

    ``product`` and ``generative`` feed one Insights SQL. ``generated``
    is an Insights transport miss; Cortex ask returns that SQL on the
    generated route, with no loop.
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
                    answer=sql,
                    badge="generated",
                    sql_used=sql,
                    assumptions=[sql],
                    rows=[{"n": 1}],
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
    try:
        app = create_app()
        app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
        app.state.cortex = cortex
        payload: dict[str, object] = {
            "question": question,
            "session_id": "ses_name_echo_http",
        }
        if lane == "generative":
            payload["ask_path"] = "generative"
        res = TestClient(app).post("/v1/chat/ask", json=payload)
    finally:
        settings_mod.get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    return body


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


@pytest.mark.parametrize("lane", ["product", "generative", "generated"])
def test_p04_full_response_json_hides_hr_payroll(
    lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag on. The only whole-identifier hits are loop SQL and raw_reply.

    Product and generative attach the extract loop, then the curated
    re-attach. Generated is the Cortex stub: no loop, the name sits in
    the answer and in sql_used until the HTTP boundary scrubs it.
    """
    question = "List chemicals in inventory"
    sql = "SELECT 1 FROM hr_payroll"
    name = "hr_payroll"
    body = _post_ask(monkeypatch, question, sql, lane=lane)
    leaked = _name_hits(body, name)
    assert leaked == [], leaked
    if lane == "generated":
        assert body.get("route") == "generated"
        assert body.get("abstained") is False
        assert not body.get("loop")
        return
    assert body.get("abstained") is True
    loop = body.get("loop")
    assert isinstance(loop, list) and loop
    entry = loop[0]
    assert isinstance(entry, dict)
    assert entry.get("sql") == sql
    assert entry.get("raw_reply") == sql
