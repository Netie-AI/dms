"""SERVED-ATTR-01 (dms#305): per-call served_* on every ask envelope.

Platform's 19:25 run had ``served_*`` null in all 52 envelopes and had to
infer attribution from OpenVault logs. Under Gating's validity rule a grid
cell without per-call attribution is INVALID, so the envelope must say which
of three things happened: Cortex reported the provider and model
(``reported``), a model may have been called and attribution is absent or
null (``missing``), or no model was called (``none``). Values are copied as
Cortex sent them; nothing is inferred from logs, config or the SQL.

Every answer-path assertion is on the customer envelope (rendered text, rows,
``assert_envelope_valid``). The wire is Cortex ``/v1/insights`` behind a fake
``httpx.Client``, run through the real ``compute_insights``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cortex_client.compute import (
    COMPUTE_PATH,
    INSIGHTS_FAIL_BEARER_MISSING,
    compute_insights,
)
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import demo_ontology

_BASE = "http://127.0.0.1:8010"
_KEY = "fake-steward-key-for-served-attr-tests"
_COLD_Q = "Which locations are cold storage?"
_COLD_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
# Distinctive values: must not match any DMS default, config or guess.
_SERVED = {"served_provider": "prov-305-unique", "served_model": "model-305-unique"}
_STAMP = {"generative": {"stamp": {"impl": "stub-305"}}}
# Cortex's generate wire shape: validated SQL under ``generative.sql``.
_SQL_WIRE = {
    "phase": "generate",
    "generative": {"sql": _COLD_SQL, "ok": True, "stamp": {"impl": "stub-305"}},
}
#: Off by default; a prove run sets it (swap scenario in generative_ask).
SERVED_ATTR_DIAG_ENV = "DMS_SERVED_ATTR_DIAG"
_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}


class _Http:
    """Fake httpx.Client: scripted generate POSTs, optional ontology ranking."""

    def __init__(
        self,
        generates: list[dict[str, Any] | None],
        *,
        ranking: list[str] | None = None,
    ) -> None:
        self.generates = list(generates)
        self.ranking = ranking
        self.calls: list[str] = []

    def __call__(self, *a: Any, **k: Any) -> _Http:
        return self

    def __enter__(self) -> _Http:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    @staticmethod
    def _resp(body: dict[str, Any]) -> Any:
        return SimpleNamespace(status_code=200, json=lambda: body)

    def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
        self.calls.append("POST " + url)
        if str(url).endswith(COMPUTE_PATH):
            raise AssertionError("ask lane must never POST /dms/query")
        body = self.generates.pop(0) if self.generates else {"phase": "generate"}
        if body is None:
            raise httpx.ReadTimeout("generate still running in Cortex")
        return self._resp(body)

    def get(self, url: str, params: Any = None, headers: Any = None) -> Any:
        self.calls.append("GET " + url)
        metrics = [{"id": m} for m in (self.ranking or [])]
        return self._resp({"phase": "ontology", "ontology": {"metrics": metrics}})


def _submit(db: Path) -> Any:
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_sa", output={"rows": rows})

    return submit


def _ledger(_p: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_sa", hash="hash_sa_not_entry")


def _ask(
    tmp_path: Path, question: str, fake: _Http, *, api_key: str = _KEY
) -> dict[str, Any] | None:
    db = tmp_path / "sa.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))

    def compute(ctx: dict[str, Any]) -> dict[str, Any] | None:
        with patch("cortex_client.compute.httpx.Client", fake):
            return compute_insights(_BASE, question=question, ontology=ctx, api_key=api_key)

    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )


def _assert_answered_wh_c(env: dict[str, Any] | None) -> dict[str, Any]:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert len(env["rows"]) == 1
    assert list(env["rows"][0].values()) == ["WH-C"]
    assert "WH-C" in env["text"]
    return env


# -- generate_sql path ------------------------------------------------------


def test_generate_sql_path_reports_served_fields_and_per_leg(tmp_path: Path) -> None:
    fake = _Http([{**_SQL_WIRE, **_SERVED}])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["plan_origin"] == "generate_sql"
    assert env["served_provider"] == "prov-305-unique"
    assert env["served_model"] == "model-305-unique"
    assert env["served_attribution"] == "reported"
    assert env["generate_legs"]["count"] == 1
    assert env["generate_legs"]["legs"] == [{"returned": "sql", **_SERVED}]


def test_generate_sql_path_without_served_fields_is_missing(tmp_path: Path) -> None:
    fake = _Http([{**_SQL_WIRE}])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert "served_provider" not in env  # never inferred
    assert env["served_attribution"] == "missing"


def test_generate_sql_path_with_null_served_fields_is_missing(tmp_path: Path) -> None:
    fake = _Http(
        [{**_SQL_WIRE, "served_provider": None, "served_model": None}]
    )
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["served_provider"] is None
    assert env["served_model"] is None
    assert env["served_attribution"] == "missing"
    assert env["generate_legs"]["legs"][0]["served_provider"] is None


def test_provider_without_model_is_missing(tmp_path: Path) -> None:
    fake = _Http(
        [{**_SQL_WIRE, "served_provider": "prov-305-unique"}]
    )
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["served_attribution"] == "missing"


# -- ranking / ontology plan (fallback after an empty generate) --------------


def test_ranking_after_model_call_reports_served_fields(tmp_path: Path) -> None:
    # Both legs (first generate and the ranked retry) report attribution.
    leg = {"phase": "generate", **_STAMP, **_SERVED}
    fake = _Http([leg, leg], ranking=["cq_cold_storage"])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["plan_origin"] == "ontology_ranking"
    assert env["served_attribution"] == "reported"
    assert env["generate_legs"]["legs"][0] == {"returned": "nothing", **_SERVED}


def test_ranking_after_model_call_without_served_is_missing(tmp_path: Path) -> None:
    fake = _Http([{"phase": "generate", **_STAMP}], ranking=["cq_cold_storage"])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["plan_origin"] == "ontology_ranking"
    assert env["served_attribution"] == "missing"


def test_one_unattributed_retry_leg_makes_the_ask_missing(tmp_path: Path) -> None:
    # First generate reported; the ranked retry came back without served_*.
    first = {"phase": "generate", **_STAMP, **_SERVED}
    retry = {"phase": "generate", **_STAMP}
    fake = _Http([first, retry], ranking=["cq_cold_storage"])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    legs = env["generate_legs"]["legs"]
    assert len(legs) == 2
    assert legs[0] == {"returned": "nothing", **_SERVED}
    assert "served_provider" not in legs[1]
    assert env["served_attribution"] == "missing"


def test_ranking_only_with_no_model_call_is_none(tmp_path: Path) -> None:
    # Cortex was unarmed: generate answered REFUSE without calling a model and
    # the no-model ontology ranking produced the answer.
    unarmed = {
        "phase": "generate",
        "status": "REFUSE",
        "generative": {"climb": {"final": "UNARMED"}},
    }
    fake = _Http([unarmed, unarmed], ranking=["cq_cold_storage"])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["plan_origin"] == "ontology_ranking"
    assert env["generate_legs"]["count"] >= 1
    assert env["served_attribution"] == "none"


def test_generate_timeout_then_ranking_is_missing(tmp_path: Path) -> None:
    # The generate call went out and never answered: attribution is unknown,
    # which is missing, not none.
    fake = _Http([None], ranking=["cq_cold_storage"])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert env["generate_legs"]["legs"] == [{"returned": "timeout"}]
    assert env["served_attribution"] == "missing"


# -- ABSTAIN paths -----------------------------------------------------------


def _assert_abstain(env: dict[str, Any] | None) -> dict[str, Any]:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env.get("chart") is None
    return env


def test_abstain_after_model_call_carries_served_fields(tmp_path: Path) -> None:
    refused = {"phase": "generate", "status": "REFUSE", **_STAMP, **_SERVED}
    fake = _Http([refused, refused], ranking=[])
    env = _assert_abstain(_ask(tmp_path, "How many florbs did wibble sell?", fake))
    assert env["served_provider"] == "prov-305-unique"
    assert env["served_model"] == "model-305-unique"
    assert env["served_attribution"] == "reported"


def test_abstain_after_model_call_without_served_is_missing(tmp_path: Path) -> None:
    refused = {"phase": "generate", "status": "REFUSE", **_STAMP}
    fake = _Http([refused, refused], ranking=[])
    env = _assert_abstain(_ask(tmp_path, "How many florbs did wibble sell?", fake))
    assert env["served_attribution"] == "missing"


def test_bearer_refuse_makes_no_call_and_is_none(tmp_path: Path) -> None:
    fake = _Http([{**_SQL_WIRE, **_SERVED}])
    env = _assert_abstain(_ask(tmp_path, _COLD_Q, fake, api_key=""))
    assert fake.calls == []
    assert INSIGHTS_FAIL_BEARER_MISSING in " ".join(env["assumptions"])
    assert env["served_attribution"] == "none"


# -- diagnostic flag ----------------------------------------------------------


def test_diag_flag_off_by_default_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guard, passes on the base by design: the flag stays off unless set.
    monkeypatch.delenv(SERVED_ATTR_DIAG_ENV, raising=False)
    fake = _Http([{**_SQL_WIRE, **_SERVED}])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    assert "served_payload_keys" not in env


def test_diag_flag_records_top_level_keys_never_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SERVED_ATTR_DIAG_ENV, "1")
    fake = _Http([{**_SQL_WIRE, **_SERVED}])
    env = _assert_answered_wh_c(_ask(tmp_path, _COLD_Q, fake))
    diag = env["served_payload_keys"]
    assert diag["count"] == len(diag["keys"])
    assert {"served_provider", "served_model", "generative", "generate_legs"} <= set(
        diag["keys"]
    )
    dumped = json.dumps(diag)
    for value in (*_SERVED.values(), _COLD_SQL, "stub-305"):
        assert value not in dumped


# -- product lane: POST /v1/chat/ask ----------------------------------------


@dataclass
class _Cortex:
    """Contract fake. ``compute_insights`` runs the real client over _Http."""

    fake: _Http
    asks: list[AskRequest] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        with patch("cortex_client.compute.httpx.Client", self.fake):
            return compute_insights(
                _BASE,
                question=question,
                session_id=kwargs.get("session_id"),
                space_id=kwargs.get("space_id"),
                ontology=kwargs.get("ontology"),
                api_key=_KEY,
            )

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run_sa_http")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_sa_http", hash="hash_sa_http")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="There are 5 locations.",
            badge="certified",
            sql_used="SELECT COUNT(*) AS location_count FROM locations",
            rows=[{"location_count": 5}],
            audit_id="aud_sa_http",
            route="sql",
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
            issued_at="2026-09-26T00:00:00+00:00",
            expires_at="2026-09-26T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m


def _chat_ask(
    cortex: _Cortex, minter: ManifestMinter, question: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("CORTEX_API_KEY", _KEY)
    settings_mod.get_settings.cache_clear()
    try:
        app = create_app()
        app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
        app.state.cortex = cortex
        res = TestClient(app).post(
            "/v1/chat/ask", json={"question": question, "session_id": "ses_sa_http"}
        )
    finally:
        settings_mod.get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    return body


def test_chat_ask_contract_fallback_after_generate_keeps_served_fields(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Generate ran (a model was called and reported), returned no SQL or plan,
    # and the ranking named nothing DMS compiles: the product lane misses into
    # the Cortex contract ask. That envelope used to drop served_* entirely.
    reached = {"phase": "generate", "status": "ABSTAIN", **_STAMP, **_SERVED}
    fake = _Http([reached, reached], ranking=["totally_unknown_metric_305"])
    cortex = _Cortex(fake=fake)
    body = _chat_ask(cortex, minter, "How many locations do we have?", monkeypatch)
    assert len(cortex.asks) == 1, "expected the contract-ask fallback"
    assert_envelope_valid(body)
    assert body["abstained"] is False
    assert body["rows"] == [{"location_count": 5}]
    assert "5" in body["text"]
    assert body["served_provider"] == "prov-305-unique"
    assert body["served_model"] == "model-305-unique"
    assert body["served_attribution"] == "reported"
    assert body["generate_legs"]["count"] >= 1


def test_chat_ask_contract_fallback_without_served_is_missing(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    reached = {"phase": "generate", "status": "ABSTAIN", **_STAMP}
    fake = _Http([reached, reached], ranking=["totally_unknown_metric_305"])
    cortex = _Cortex(fake=fake)
    body = _chat_ask(cortex, minter, "How many locations do we have?", monkeypatch)
    assert len(cortex.asks) == 1
    assert_envelope_valid(body)
    assert body["rows"] == [{"location_count": 5}]
    assert "served_provider" not in body
    assert body["served_attribution"] == "missing"


def test_chat_ask_pre_gate_abstain_no_model_is_none(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Http([{**_SQL_WIRE, **_SERVED}])
    cortex = _Cortex(fake=fake)
    body = _chat_ask(cortex, minter, "Predict how much revenue we will make", monkeypatch)
    assert fake.calls == []
    assert cortex.asks == []
    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["rows"] == []
    assert body["served_attribution"] == "none"
    assert "served_provider" not in body
