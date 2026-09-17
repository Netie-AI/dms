"""GEN-03 (dms#194): no keyword-bound answer reaches any caller under a confident badge.

Cortex POST /dms/query ignores ``mode``/``ontology`` and returns no typed
``query_plan.measure``, so every compute call missed. On ask_path=generative the
miss fell through to the DMS keyword binder ``bind_plan``, which answered under
L2_VALIDATED with wrong numbers (SKU-BETA stock value 29840.0, truth 7875.0;
"not cold storage" filtered to cold storage). On the product lane the POST was
made and the reply thrown away.

Contract pinned here, on the POST /v1/chat/ask envelope (hard rule 10a):
- ask_path exact|generative is refused with 400 ask_path_not_allowed unless the
  server sets DMS_HARNESS_ASK_PATHS, and a refused request reaches nothing.
- no lane calls Cortex compute_query and no lane calls bind_plan.
- the product lane still returns the Cortex contract ask answer, and the vague
  pre-gate still abstains before Cortex (R-0005).

The fake Cortex models the real engine: compute_query answers 200 with no
typed plan, exactly the reply the ask path used to throw away or bind over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

#: D03 probe questions. Each one was answered green-wrong on ask_path=generative
#: by the keyword binder before GEN-03 (independent verifier, 0 refuted).
D03_PROBES = (
    "Which locations are not cold storage?",
    "What is the total stock value of SKU-BETA?",
    "Which SKUs are below reorder level in warehouse B?",
    "What is the average stock value by location?",
    "Top 3 SKUs by lowest revenue",
    "What was revenue in 2025?",
    "Predict how much revenue we will make",
    "How many distinct products do we keep in stock?",
)

#: Figures the binder shipped under L2_VALIDATED. None may reach rendered text.
_GREEN_WRONG_FIGURES = ("29840", "29,840")

_PRODUCT_QUESTION = "Top 5 selling SKUs by revenue"
_CORTEX_ANSWER = "Top 5 SKUs by revenue, highest first."
_CORTEX_ROWS = [
    {"sku": "SKU-00397", "sales_value_myr": 726158.36},
    {"sku": "SKU-00183", "sales_value_myr": 581836.43},
    {"sku": "SKU-00171", "sales_value_myr": 538201.10},
    {"sku": "SKU-00042", "sales_value_myr": 401002.75},
    {"sku": "SKU-00311", "sales_value_myr": 388940.12},
]


@dataclass
class _RecordingCortex:
    """Fake engine that records every call the ask path makes into it."""

    asks: list[AskRequest] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[LedgerAppendRequest] = field(default_factory=list)
    computes: list[dict[str, Any]] = field(default_factory=list)

    def compute_query(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        self.computes.append({"question": question, **kwargs})
        # What Cortex origin/main actually returns: an answer, no typed plan.
        return {"answer": "Here is what I found.", "audit_id": "aud_dms_query", "query_plan": None}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_gen03_sql",
                output={"rows": [{"stock_value_myr": 29840.0}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_gen03_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_gen03", hash="hash_gen03_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer=_CORTEX_ANSWER,
            badge="certified",
            sql_used=(
                "SELECT sku, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
                "FROM transactions WHERE txn_type = 'OUT' "
                "GROUP BY sku ORDER BY sales_value_myr DESC LIMIT 5"
            ),
            rows=[dict(r) for r in _CORTEX_ROWS],
            assumptions="fixture",
            audit_id="aud_gen03_cortex",
            route="sql",
        )

    def untouched(self) -> bool:
        return not (self.asks or self.submits or self.appends or self.computes)


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
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
            issued_at="2026-09-17T00:00:00+00:00",
            expires_at="2026-09-17T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


@pytest.fixture()
def bind_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Trap the keyword binder. A call is recorded and fails the ask loudly."""
    calls: list[str] = []

    def _trap(question: str, *_a: Any, **_k: Any) -> dict[str, Any] | None:
        calls.append(question)
        raise AssertionError(f"GEN-03: bind_plan called on the ask path for {question!r}")

    monkeypatch.setattr("dms_executor.generative_ask.bind_plan", _trap)
    return calls


def _client(
    minter: ManifestMinter,
    cortex: _RecordingCortex,
    *,
    harness: bool,
) -> tuple[TestClient, Executor]:
    app = create_app()
    exe = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    # Explicit values, no .env and no process env, so the switch under test is
    # the only thing deciding the lane.
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=harness,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), exe


def _assert_not_confident(env: dict[str, Any], question: str) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (question, env.get("badge"), env.get("sql_used"))
    assert env["abstained"] is True, question
    assert env["values"] == [], question
    assert env["rows"] == [], question
    text = str(env.get("text") or "")
    assert text.strip(), f"empty rendered text for {question!r}"
    for figure in _GREEN_WRONG_FIGURES:
        assert figure not in text, (question, text)


# ---------------------------------------------------------------------------
# (a) Harness lanes are refused on a customer server, and reach nothing.
# ---------------------------------------------------------------------------


def test_harness_setting_defaults_off_and_reads_its_env_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_HARNESS_ASK_PATHS", raising=False)
    assert Settings(_env_file=None).dms_harness_ask_paths is False  # type: ignore[call-arg]
    monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    assert Settings(_env_file=None).dms_harness_ask_paths is True  # type: ignore[call-arg]


@pytest.mark.parametrize("ask_path", ["generative", "exact"])
def test_harness_lane_is_refused_without_the_server_setting(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    bind_calls: list[str],
    ask_path: str,
) -> None:
    gate_calls: list[dict[str, Any]] = []

    def _gate(**kwargs: Any) -> Any:
        gate_calls.append(kwargs)
        raise AssertionError("compliance_gate reached on a refused ask_path")

    monkeypatch.setattr("dms_api.routes.chat.compliance_gate", _gate)
    cortex = _RecordingCortex()
    client, exe = _client(minter, cortex, harness=False)
    live_calls: list[str] = []

    def _live_ask(question: str, **_k: Any) -> dict[str, Any]:
        live_calls.append(question)
        raise AssertionError("live_ask reached on a refused ask_path")

    monkeypatch.setattr(exe, "live_ask", _live_ask)

    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "What is the total stock value of SKU-BETA?",
            "session_id": f"ses_gen03_refuse_{ask_path}",
            "ask_path": ask_path,
        },
    )

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "ask_path_not_allowed"
    assert "DMS_HARNESS_ASK_PATHS" in detail["message"]
    assert "measurement" in detail["message"]
    assert gate_calls == []
    assert live_calls == []
    assert bind_calls == []
    assert cortex.asks == []
    assert cortex.submits == []
    assert cortex.appends == []
    assert cortex.computes == []


# ---------------------------------------------------------------------------
# (b) The product lane is not refused, with or without naming it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ask_path", [None, "product"])
def test_product_lane_is_not_refused(
    minter: ManifestMinter, bind_calls: list[str], ask_path: str | None
) -> None:
    cortex = _RecordingCortex()
    client, _ = _client(minter, cortex, harness=False)
    body: dict[str, Any] = {
        "question": _PRODUCT_QUESTION,
        "session_id": f"ses_gen03_product_{ask_path}",
    }
    if ask_path is not None:
        body["ask_path"] = ask_path

    r = client.post("/v1/chat/ask", json=body)

    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    assert len(cortex.asks) == 1
    assert bind_calls == []


# ---------------------------------------------------------------------------
# (c) With the harness on, the D03 probes get no confident envelope.
# ---------------------------------------------------------------------------


def test_d03_probes_are_not_confident_on_the_generative_harness_lane(
    minter: ManifestMinter, bind_calls: list[str]
) -> None:
    cortex = _RecordingCortex()
    client, _ = _client(minter, cortex, harness=True)
    statuses: dict[str, int] = {}
    envelopes: dict[str, dict[str, Any]] = {}
    for i, question in enumerate(D03_PROBES):
        r = client.post(
            "/v1/chat/ask",
            json={
                "question": question,
                "session_id": f"ses_gen03_d03_{i}",
                "ask_path": "generative",
            },
        )
        statuses[question] = r.status_code
        envelopes[question] = r.json()

    # A reached binder raises out of client.post with its own message; these
    # name the seam first if a later change turns that into a 5xx instead.
    assert bind_calls == [], f"bind_plan called for {bind_calls}"
    assert cortex.computes == [], f"Cortex compute_query called: {cortex.computes}"
    assert statuses == {q: 200 for q in D03_PROBES}, statuses
    for question in D03_PROBES:
        _assert_not_confident(envelopes[question], question)
        # Every refusal says why in the envelope the customer receives (R-0011),
        # never an empty assumptions list.
        assert envelopes[question].get("assumptions"), question
    # The lane's own miss names the closed seam rather than implying a plan was
    # tried and failed. Pre-gate abstains (vague, paraphrase) carry their own
    # reasons, so this is asserted over the set, not over every question.
    reasons = " ".join(
        str(a) for q in D03_PROBES for a in (envelopes[q].get("assumptions") or [])
    )
    assert "no plan source on this lane" in reasons, reasons
    # Isolated lane: no Cortex mix, nothing executed, nothing ledgered.
    assert cortex.asks == []
    assert cortex.submits == []
    assert cortex.appends == []


def test_exact_harness_lane_still_runs_when_enabled(
    minter: ManifestMinter, bind_calls: list[str]
) -> None:
    """R-0005: the switch opens the lane it names; it is not a blanket refusal."""
    cortex = _RecordingCortex()
    client, _ = _client(minter, cortex, harness=True)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": _PRODUCT_QUESTION,
            "session_id": "ses_gen03_exact_on",
            "ask_path": "exact",
        },
    )
    assert r.status_code == 200, r.text
    _assert_not_confident(r.json(), _PRODUCT_QUESTION)
    assert cortex.untouched()
    assert bind_calls == []


# ---------------------------------------------------------------------------
# (d) Product lane: Cortex compute is never called; answers are unchanged.
# ---------------------------------------------------------------------------


def test_product_lane_never_posts_compute_and_returns_the_contract_answer(
    minter: ManifestMinter, bind_calls: list[str]
) -> None:
    cortex = _RecordingCortex()
    client, _ = _client(minter, cortex, harness=False)

    r = client.post(
        "/v1/chat/ask",
        json={"question": _PRODUCT_QUESTION, "session_id": "ses_gen03_product_answer"},
    )

    assert r.status_code == 200, r.text
    env = r.json()
    assert cortex.computes == [], f"Cortex compute_query called: {cortex.computes}"
    assert bind_calls == []
    assert len(cortex.asks) == 1
    assert cortex.asks[0].question == _PRODUCT_QUESTION
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    assert env["audit_id"] == "aud_gen03_cortex"
    assert _CORTEX_ANSWER.rstrip(".") in env["text"]
    assert env["rows"] == _CORTEX_ROWS
    values = [
        float(v["value"])
        for v in env["values"]
        if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
    ]
    assert any(abs(v - 726158.36) <= 0.01 for v in values), env["values"]


def test_product_lane_vague_pre_gate_still_abstains_before_cortex(
    minter: ManifestMinter, bind_calls: list[str]
) -> None:
    """Unchanged: the _UNSURE_ASK pre-gate still runs with the closed seam."""
    cortex = _RecordingCortex()
    client, _ = _client(minter, cortex, harness=False)
    question = "Just give me last month's number"

    r = client.post(
        "/v1/chat/ask",
        json={"question": question, "session_id": "ses_gen03_vague"},
    )

    assert r.status_code == 200, r.text
    env = r.json()
    _assert_not_confident(env, question)
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "too vague" in notes, notes
    assert cortex.asks == []
    assert cortex.computes == []
    assert cortex.submits == []
    assert bind_calls == []


# ---------------------------------------------------------------------------
# R-0007: the traps above can fail. A trap that cannot fire proves nothing.
# ---------------------------------------------------------------------------


def test_the_bind_trap_fires_when_the_binder_is_reached(bind_calls: list[str]) -> None:
    from dms_executor.generative_ask import maybe_generative_ask

    with pytest.raises(AssertionError, match="bind_plan called"):
        maybe_generative_ask(
            "What is the total stock value of SKU-BETA?",
            compute=lambda _ctx: None,
            submit=lambda _sql: None,
            ledger_append=lambda _payload: None,
            bind_on_miss=True,
        )
    assert bind_calls == ["What is the total stock value of SKU-BETA?"]


def test_the_compute_recorder_counts_a_call() -> None:
    cortex = _RecordingCortex()
    out = cortex.compute_query("q", session_id="s", ontology={})
    assert out is not None and out.get("query_plan") is None
    assert len(cortex.computes) == 1
    assert not cortex.untouched()
