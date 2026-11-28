"""FDE-ONTO-SPACE-SCOPE-01. Approved items stay in the Space that owns them.

``DMS_ONTO_SPACE_SCOPE`` defaults off. Off, a Space id on the read is ignored
and the approved list, the diff, and the ask prompts match a read with the
flag unset. On, an ask in Space B does not carry Space A's item, and a null
``space_id`` is included only when a steward set ``tenant_wide`` true.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.control_plane.onto_data import (
    PostgresOntologyDataStore,
    ask_space_id,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import Ontology
from fastapi.testclient import TestClient

SPACE_A = "11111111-1111-4111-8111-111111111111"
SPACE_B = "22222222-2222-4222-8222-222222222222"
_SCHEMA = {"dialect": "postgres", "datasets": []}


class _BoomGrains:
    def __iter__(self):  # noqa: ANN204
        raise AssertionError("demo grains")

    def __contains__(self, _name: object) -> bool:
        raise AssertionError("demo grains")


def _body(name: str, measure: str) -> dict[str, Any]:
    return {
        "status": "proposed",
        "served": False,
        "vendor_sha": "fde-onto-space-scope-01",
        "terms": [
            {
                "name": name,
                "table": name.lower(),
                "description": "",
                "attributes": [],
                "key": ["id"],
            }
        ],
        "measures": [
            {
                "name": measure,
                "grain": name,
                "expression": "sum(margin_cents)",
                "description": measure,
                "additive": True,
            }
        ],
    }


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _seed(store: Any) -> dict[str, str]:
    """Approve one item set per scope. Returns measure name to item id."""
    specs: tuple[tuple[str, str, str | None, bool], ...] = (
        ("AlphaLot", "alpha_margin", SPACE_A, False),
        ("BetaLot", "beta_margin", SPACE_B, False),
        ("LooseLot", "loose_margin", None, False),
        ("SharedLot", "shared_margin", None, True),
    )
    ids: dict[str, str] = {}
    for obj, measure, space, wide in specs:
        stored = store.store_proposal(
            _body(obj, measure),
            created_by="steward",
            space_id=space,
            tenant_wide=wide,
        )
        for item in stored["items"]:
            assert store.approve(item["id"], actor="steward") is not None
            if item["kind"] == "measure":
                ids[measure] = item["id"]
    text_body = _body("TextLot", "textish_margin")
    text_body["provenance"] = {"tenant_wide": "true"}
    stored_text = store.store_proposal(text_body, created_by="steward", space_id=None)
    for item in stored_text["items"]:
        assert store.approve(item["id"], actor="steward") is not None
        if item["kind"] == "measure":
            ids["textish_margin"] = item["id"]
    version = store._versions[stored_text["version_id"]]
    assert version.provenance.get("tenant_wide") == "true"
    marked = store._versions
    wide_id = next(
        vid
        for vid, row in marked.items()
        if row.provenance.get("tenant_wide") is True and row.space_id is None
    )
    assert marked[wide_id].provenance["tenant_wide"] is True
    return ids


def _diff_ids(body: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("proposed_only", "approved_only", "changed", "same"):
        for row in body[key]:
            found.add(str(row["id"]))
    return found


def _prompt(onto: Ontology, space_id: str) -> str:
    from dms_executor.schema_context import build_schema_context

    return build_schema_context(
        "how many lots",
        _SCHEMA,
        ontology=onto,
        space_id=space_id,
    ).prompt


def _forbid_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Ontology:
        raise AssertionError("demo_ontology")

    monkeypatch.setattr("dms_executor.ontology.demo_ontology", _boom)
    monkeypatch.setattr("dms_executor.generative_ask.demo_ontology", _boom)
    monkeypatch.setattr("dms_executor.ontology.SUPPLY_CHAIN_GRAINS", _BoomGrains())


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_client.gate import ComplianceDecision

    def _allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.ontology.compliance_gate", _allow)


def _minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


class _FakeCortex:
    def __init__(self) -> None:
        self.asks: list[Any] = []

    def submit(self, req: Any) -> Any:
        return req

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="no figure",
            badge="abstain",
            sql_used=None,
            rows=[],
            assumptions="fixture",
            audit_id="aud-onto-space-scope",
            route="abstain",
        )


def _ask(monkeypatch: pytest.MonkeyPatch, space_id: str, session_id: str) -> dict[str, str]:
    import dms_executor
    from dms_executor.grain_ai import approved_grain_names

    captured: dict[str, str] = {}

    def _capture(*_a: Any, **kwargs: Any) -> None:
        onto = kwargs.get("ontology")
        assert isinstance(onto, Ontology)
        captured["schema_prompt"] = _prompt(onto, space_id)
        captured["grain_prompt"] = json.dumps(
            {"question": "how many lots", "candidates": list(approved_grain_names())},
            ensure_ascii=True,
        )
        captured["objects"] = ",".join(sorted(onto.objects))
        captured["measures"] = ",".join(sorted(onto.measures))
        return None

    monkeypatch.setattr(dms_executor, "maybe_generative_ask", _capture)
    exe = dms_executor.Executor(cortex=_FakeCortex(), minter=_minter(monkeypatch))
    monkeypatch.setattr(exe, "grantable_tables", lambda *, space_id=None: ["rxlotb"])
    exe.live_ask("onto space scope probe question", space_id=space_id, session_id=session_id)
    assert captured, "the ask did not build an ontology"
    assert ask_space_id() is None
    return captured


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_ontology_data_store()
    get_settings.cache_clear()
    yield
    reset_ontology_data_store()
    get_settings.cache_clear()


def test_flag_off_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ONTO_SPACE_SCOPE", raising=False)
    monkeypatch.delenv("DMS_ONTO_PG", raising=False)
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    _forbid_demo(monkeypatch)
    _gate_allows(monkeypatch)
    store = ontology_data_store()
    _seed(store)
    unset_items = _canon(store.approved_items(SPACE_B))
    unset_diff = _canon(store.diff(space_id=SPACE_B))
    unset_plain = _canon(store.diff())
    client = TestClient(create_app())
    unset_http = client.get("/v1/ontology/data/diff", params={"space_id": SPACE_B}).json()
    unset_http_plain = client.get("/v1/ontology/data/diff").json()
    unset_ask = _ask(monkeypatch, SPACE_B, "ses_scope_unset")

    monkeypatch.setenv("DMS_ONTO_SPACE_SCOPE", "0")
    zero_items = _canon(store.approved_items(SPACE_B))
    zero_diff = _canon(store.diff(space_id=SPACE_B))
    zero_ask = _ask(monkeypatch, SPACE_B, "ses_scope_zero")
    zero_http = client.get("/v1/ontology/data/diff", params={"space_id": SPACE_B}).json()

    assert unset_items == zero_items
    assert unset_diff == zero_diff == unset_plain
    assert unset_http == unset_http_plain == zero_http
    assert unset_ask["schema_prompt"] == zero_ask["schema_prompt"]
    assert unset_ask["grain_prompt"] == zero_ask["grain_prompt"]
    assert "- alpha_margin grain=" in unset_ask["schema_prompt"]
    assert "AlphaLot" in unset_ask["grain_prompt"]
    assert "- shared_margin grain=" in unset_ask["schema_prompt"]


def test_ask_in_space_b_omits_space_a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ONTO_SPACE_SCOPE", "1")
    monkeypatch.delenv("DMS_ONTO_PG", raising=False)
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    _forbid_demo(monkeypatch)
    _gate_allows(monkeypatch)
    store = ontology_data_store()
    ids = _seed(store)
    asked = _ask(monkeypatch, SPACE_B, "ses_scope_b")
    prompt = asked["schema_prompt"]
    grains = asked["grain_prompt"]
    assert "- alpha_margin grain=" not in prompt
    assert "- loose_margin grain=" not in prompt
    assert "- textish_margin grain=" not in prompt
    assert "- beta_margin grain=" in prompt
    assert "- shared_margin grain=" in prompt
    assert "AlphaLot" not in grains
    assert "LooseLot" not in grains
    assert "TextLot" not in grains
    assert "BetaLot" in grains
    assert "SharedLot" in grains
    assert "AlphaLot" not in asked["objects"]
    assert "BetaLot" in asked["objects"]
    assert asked["measures"] == "beta_margin,shared_margin"

    client = TestClient(create_app())
    res = client.get("/v1/ontology/data/diff", params={"space_id": SPACE_B})
    assert res.status_code == 200, res.text
    found = _diff_ids(res.json())
    assert ids["alpha_margin"] not in found
    assert ids["loose_margin"] not in found
    assert ids["textish_margin"] not in found
    assert ids["beta_margin"] in found
    assert ids["shared_margin"] in found
    scoped = store.approved_items(SPACE_A)
    names = {row["payload"]["name"] for row in scoped}
    assert names >= {"AlphaLot", "alpha_margin", "shared_margin"}
    assert "BetaLot" not in names


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL is unset; Postgres ontology tests skipped")
    try:
        conn = psycopg.connect(url, connect_timeout=2)
    except Exception as exc:
        pytest.skip(f"DATABASE_URL is set but Postgres is unreachable ({type(exc).__name__})")
    else:
        conn.close()
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    return url


def test_postgres_approved_items_are_space_scoped(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_ONTO_SPACE_SCOPE", "1")
    tenant = str(uuid.uuid4())
    space_a = str(uuid.uuid4())
    space_b = str(uuid.uuid4())
    from dms_core.control_plane.session import set_tenant_context

    conn = psycopg.connect(pg_url)
    try:
        conn.execute(
            "INSERT INTO dms.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (tenant, "scope-" + tenant.split("-", 1)[0], "scope"),
        )
        set_tenant_context(conn, tenant, role="admin")
        conn.execute(
            "INSERT INTO dms.spaces (id, tenant_id, name) VALUES (%s, %s, %s), (%s, %s, %s)",
            (space_a, tenant, "space-a", space_b, tenant, "space-b"),
        )
        conn.commit()
    finally:
        conn.close()
    store = PostgresOntologyDataStore(pg_url, tenant_id=tenant)
    actor = str(uuid.uuid4())
    try:
        stored_a = store.store_proposal(
            _body("AlphaLot", "alpha_margin"),
            created_by=actor,
            space_id=space_a,
            tenant_id=tenant,
        )
        stored_b = store.store_proposal(
            _body("BetaLot", "beta_margin"),
            created_by=actor,
            space_id=space_b,
            tenant_id=tenant,
        )
        stored_wide = store.store_proposal(
            _body("SharedLot", "shared_margin"),
            created_by=actor,
            tenant_id=tenant,
            tenant_wide=True,
        )
        for stored in (stored_a, stored_b, stored_wide):
            assert store.approve_version(stored["version_id"], actor) == len(stored["items"])
        names = {row["payload"]["name"] for row in store.approved_items(space_b)}
        assert "BetaLot" in names
        assert "beta_margin" in names
        assert "shared_margin" in names
        assert "AlphaLot" not in names
        assert "alpha_margin" not in names
        monkeypatch.setenv("DMS_ONTO_SPACE_SCOPE", "0")
        off = {row["payload"]["name"] for row in store.approved_items(space_b)}
        assert "AlphaLot" in off and "BetaLot" in off
    finally:
        conn = psycopg.connect(pg_url, autocommit=True)
        try:
            conn.execute("DELETE FROM dms.tenants WHERE id = %s", (tenant,))
        finally:
            conn.close()
