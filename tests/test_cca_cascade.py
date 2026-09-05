"""CCA-05 — the cascade runs before L0, and only when the ask needs it.

Two failures are asserted here, and they pull in opposite directions.

The first is the one the epic exists for: "rental across SEA, commercial only"
against a warehouse with no country and no asset-class column must not reach a
number. That ask executes cleanly and returns a plausible total, and no
downstream check can tell it is the wrong total.

The second is the one a gate like this creates: a control that refuses correct
work is a failure, not a win (R-0005). "capacity of warehouse A" and "how many
units sold last month" are the product's own vocabulary, and neither is an
asset-class or tenure filter. Both directions have tests, because shipping only
the first one trades a rare wrong answer for a common refused one.

Rule 10a: the end-to-end cases assert on the envelope from POST /v1/chat/ask,
not on the cascade's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import AskRequest, AskResponse, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import Executor
from dms_executor.cca.cascade import engages, run_cascade
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

#: The epic's own phrasing. It carries a polarity cue ("only"), so since the
#: polarity guard landed it ABSTAINS rather than certifying: this cascade cannot
#: read polarity reliably and an inverted filter is a confident answer to the
#: opposite question. That trade is deliberate and is asserted below.
ASK = "rental across SEA, commercial only"

#: The same ask with no polarity on it, which is what a certified run looks like
#: now.
PLAIN_ASK = "lease revenue across SEA for commercial property"


@pytest.fixture()
def bound_lake(tmp_path: Path) -> Path:
    """A warehouse that carries every encoding the ask needs."""
    db = tmp_path / "bound.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE deals ("
        "  country VARCHAR, asset_class VARCHAR, transaction_type VARCHAR, amount DOUBLE"
        ")"
    )
    con.execute(
        "INSERT INTO deals VALUES "
        "('MY', 'COM', 'LEASE', 100.0), "
        "('SG', 'COM', 'LEASE', 250.0), "
        "('TH', 'RES', 'LEASE', 40.0), "
        "('Japan', 'COM', 'LEASE', 900.0)"
    )
    con.close()
    return db


@pytest.fixture()
def unbound_lake(tmp_path: Path) -> Path:
    """Real rows, real money, and none of the encodings the ask names."""
    db = tmp_path / "unbound.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (deal_id VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO deals VALUES ('D1', 100.0), ('D2', 250.0)")
    con.close()
    return db


def test_certifies_every_stage_when_every_encoding_is_landed(bound_lake: Path) -> None:
    out = run_cascade(PLAIN_ASK, warehouse=bound_lake, tables=["deals"])
    assert out.engaged is True
    assert out.blocked_at is None
    assert [c["type"] for c in out.trace] == ["sense", "asset_class", "geo"]
    assert {c["status"] for c in out.trace} == {"CERTIFIED"}
    # The filters carry the column's own spelling, which is the hard-rule-12 case.
    bindings = " ".join(str(c["binding"]) for c in out.trace)
    assert "'COM'" in bindings
    assert "'MY'" in bindings


def test_blocks_at_the_first_stage_it_cannot_bind(unbound_lake: Path) -> None:
    out = run_cascade(ASK, warehouse=unbound_lake, tables=["deals"])
    assert out.engaged is True
    # Sense is stage 0 and there is no tenure column, so that is where it stops.
    assert out.blocked_at == "sense"
    # Later stages were not run at all, so they are absent rather than green.
    assert [c["type"] for c in out.trace] == ["sense"]
    assert "transaction_type" in out.blocked_reason


def test_geo_blocks_when_only_the_country_encoding_is_missing(tmp_path: Path) -> None:
    db = tmp_path / "noc.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (asset_class VARCHAR, transaction_type VARCHAR)")
    con.execute("INSERT INTO deals VALUES ('COM', 'LEASE')")
    con.close()
    out = run_cascade(PLAIN_ASK, warehouse=db, tables=["deals"])
    assert out.blocked_at == "geo"
    assert [c["status"] for c in out.trace] == ["CERTIFIED", "CERTIFIED", "ABSTAIN"]


def test_a_stage_the_ask_does_not_constrain_is_marked_unconstrained(
    bound_lake: Path,
) -> None:
    out = run_cascade("top sales across SEA", warehouse=bound_lake, tables=["deals"])
    assert out.certified is True
    by_stage = {c["type"]: c for c in out.trace}
    # Not left out: leaving it out would make CCA-01 block geo, and abstaining
    # on an ask for the sole reason that it named no asset class is the
    # refuses-correct-work failure.
    #
    # The label says what the cascade knows, not what the customer asked. It
    # read "(unconstrained)" until an independent run showed "rental in
    # Malaysia, commercial only" printing "the ask places no geo constraint"
    # over an ask that named a country.
    assert by_stage["asset_class"]["candidate"] == "(no recognised term)"
    assert "recognises" in by_stage["asset_class"]["evidence"][0]
    assert by_stage["asset_class"]["binding"] is None
    assert by_stage["geo"]["status"] == "CERTIFIED"


@pytest.mark.parametrize(
    "question",
    [
        "capacity of warehouse A?",
        "how many units sold last month",
        "total sales value by location",
        "which SKU is below its reorder level",
        "top 3 categories by sales value",
        "show me the delayed shipments",
        "what is the average unit cost for SKU-BETA",
    ],
)
def test_product_vocabulary_does_not_engage_the_cascade(question: str) -> None:
    """A control that blocks legitimate work is itself a failure (R-0005).

    Every one of these answers today. None of them names a tenure, an asset
    class, an industry segment or a region, so the cascade must stay out of the
    way rather than abstain on a word it recognised out of context.
    """
    assert engages(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "rental across SEA, commercial only",
        "top sales in agricultural across SEA",
        "lease revenue for commercial property, ignore residential",
        "which markets in Southeast Asia lead on rent",
    ],
)
def test_ambiguous_filter_asks_do_engage(question: str) -> None:
    assert engages(question) is True


# ---------------------------------------------------------------------------
# End to end on the customer envelope (rule 10a)
# ---------------------------------------------------------------------------


@dataclass
class _MarkerCortex:
    """Records whether the engine was ever asked. A blocked ask must not reach it."""

    asks: list[AskRequest] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_cca_sql",
                output={"rows": [{"total_kg": 42.0}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_cca")

    def ledger_append(self, req: Any) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_cca_vq", hash="hash_cca_vq_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Lease revenue is 350.0 MYR.",
            badge="certified",
            sql_used="SELECT SUM(amount) AS total FROM deals",
            rows=[{"total": 350.0}],
            assumptions="cca-e2e",
            audit_id="aud_cca",
            route="sql",
        )


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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    return m


def _seeded_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "cca_e2e.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def _client(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    cortex: _MarkerCortex,
) -> TestClient:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=warehouse,
    )
    app.state.cortex = cortex
    return TestClient(app)


def test_unbindable_ask_abstains_on_the_envelope_and_never_reaches_the_engine(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The epic's headline case, asserted where the customer reads it.

    The demo warehouse has real money in it and no country, asset-class or
    tenure column. The wrong outcome is a green badge over a real total for a
    filter that never applied.
    """
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    warehouse = _seeded_warehouse(tmp_path, monkeypatch)
    cortex = _MarkerCortex()
    client = _client(warehouse, minter, monkeypatch, cortex)

    r = client.post("/v1/chat/ask", json={"question": ASK, "session_id": "ses_cca_1"})
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)

    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["values"] == []
    assert not env.get("sql_used")
    # No query ran, so no number was available to state.
    assert cortex.asks == []

    trace = env["constraint_trace"]
    assert [c["status"] for c in trace][-1] in {"ABSTAIN", "REFUSE"}
    # The customer is told which binding is missing, by name. The demo
    # warehouse does have a txn_type column, carrying inbound and outbound, so
    # the honest reason is not "no column" but "that column carries no tenure
    # value" - which is the filter-matches-nothing case in full.
    reason = " ".join(trace[-1]["reasons"])
    assert "transactions.txn_type" in reason
    assert "never landed" in reason


def test_certified_cascade_rides_along_on_the_answer(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every encoding is landed, the ask proceeds and the trace is on the answer."""
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    warehouse = _seeded_warehouse(tmp_path, monkeypatch)
    con = duckdb.connect(str(warehouse))
    con.execute("ALTER TABLE transactions ADD COLUMN country VARCHAR")
    con.execute("ALTER TABLE transactions ADD COLUMN asset_class VARCHAR")
    con.execute("ALTER TABLE transactions ADD COLUMN transaction_type VARCHAR")
    con.execute("UPDATE transactions SET country='MY', asset_class='COM', transaction_type='LEASE'")
    con.close()

    cortex = _MarkerCortex()
    client = _client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask", json={"question": PLAIN_ASK, "session_id": "ses_cca_2"}
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)

    assert cortex.asks, "a certified cascade must not stop the ask"
    trace = env["constraint_trace"]
    assert [c["type"] for c in trace] == ["sense", "asset_class", "geo"]
    assert {c["status"] for c in trace} == {"CERTIFIED"}

    # The buyer's sentences: what was covered, on which column, and what the
    # data does not carry.
    assumptions = " ".join(env.get("assumptions") or [])
    assert "transactions.country" in assumptions
    assert "1 of 11" in assumptions
    assert "Indonesia" in assumptions


def test_verified_query_is_not_gated_by_the_cascade(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A steward already decided this question. The cascade does not overrule them."""
    warehouse = _seeded_warehouse(tmp_path, monkeypatch)
    cortex = _MarkerCortex()
    client = _client(warehouse, minter, monkeypatch, cortex)

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", allow)

    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    space = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    question = "lease revenue for commercial property in SEA"
    reg = client.post(
        "/v1/studio/verified-queries",
        json={
            "space_id": space,
            "question": question,
            "sql": "SELECT SUM(quantity_kg) AS total_kg FROM transactions",
        },
    )
    assert reg.status_code == 200, reg.text

    r = client.post(
        "/v1/chat/ask",
        json={"question": question, "space_id": space, "session_id": "ses_cca_3"},
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert "total_kg" in (env.get("sql_used") or "")
    assert cortex.appends, "VQ L0 must append the Cortex ledger (F83)"
    assert cortex.asks == []


# ---------------------------------------------------------------------------
# What a second independent run forced (R-0003, round two)
# ---------------------------------------------------------------------------


def test_the_ask_path_hook_is_off_by_default(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engagement rule is not measured well enough to gate a customer's ask.

    46 of 106 ordinary questions engaged and abstained; 35 of 37 asks that
    plainly name a filter were not recognised and certified anyway. Both
    directions are wrong, so the hook ships off and the flag is the honest
    state of it.
    """
    from dms_executor.cca.cascade import cascade_enabled

    monkeypatch.delenv("DMS_CCA_CASCADE", raising=False)
    assert cascade_enabled() is False

    warehouse = _seeded_warehouse(tmp_path, monkeypatch)
    cortex = _MarkerCortex()
    client = _client(warehouse, minter, monkeypatch, cortex)
    r = client.post("/v1/chat/ask", json={"question": ASK, "session_id": "ses_off"})
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    # Off means the ask takes the path it always took, not a silent abstention.
    assert "constraint_trace" not in env
    assert cortex.asks, "with the hook off the engine answers as it always did"

    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    assert cascade_enabled() is True


def test_a_negated_ask_never_gets_an_inclusive_certification(bound_lake: Path) -> None:
    """An inverted filter is a confident answer to the opposite question.

    "residential is excluded" certified `asset_class IN ('RES')`, and "all of
    SEA other than Singapore" certified `country IN ('SG')`. Polarity is not
    read reliably here, so a negated ask does not get an inclusive
    certification at all.
    """
    from dms_executor.cca.cascade import polarity_is_unsettled

    for question in (
        "rental across SEA, commercial only",
        "residential is excluded",
        "all of SEA other than Singapore",
        "residential versus commercial revenue",
        "excluding tax, commercial revenue by month",
        "anything but residential",
    ):
        assert polarity_is_unsettled(question) is True, question
    assert polarity_is_unsettled("commercial property revenue in Malaysia") is False

    out = run_cascade(
        "lease across SEA, residential is excluded",
        warehouse=bound_lake,
        tables=["deals"],
    )
    assert out.blocked_at is not None
    blocked = [c for c in out.trace if c["status"] != "CERTIFIED"]
    assert blocked, "a negated ask must not certify its way through"
    assert "polarity" in " ".join(blocked[0]["reasons"])


def test_the_cascade_cannot_read_a_table_the_space_does_not_grant(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grounded_tables is a request, not a grant.

    The hook read `tables or grantable_tables(...)` with the request body's
    grounded_tables unvalidated. On the answering path demo_acl caught it; on
    the abstain path the envelope came back 200 carrying up to twelve distinct
    values per scanned column from a table the same endpoint answers 403 for.
    """
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    warehouse = _seeded_warehouse(tmp_path, monkeypatch)
    con = duckdb.connect(str(warehouse))
    con.execute("CREATE TABLE hr_confidential (country VARCHAR, salary DOUBLE)")
    con.execute("INSERT INTO hr_confidential VALUES ('Switzerland', 1.0), ('Monaco', 2.0)")
    con.close()

    cortex = _MarkerCortex()
    client = _client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": ASK,
            "session_id": "ses_leak",
            "grounded_tables": ["hr_confidential"],
        },
    )
    assert r.status_code in (200, 403), r.text
    env = r.json()
    # No value from the ungranted table reaches the caller, and the envelope
    # does not claim to have been grounded on it either.
    blob = r.text
    assert "Switzerland" not in blob
    assert "Monaco" not in blob
    assert "hr_confidential" not in str(env.get("grounded_tables") or [])
    for entry in env.get("constraint_trace") or []:
        assert "hr_confidential" not in " ".join(entry.get("evidence") or [])
        assert "hr_confidential" not in " ".join(entry.get("reasons") or [])
