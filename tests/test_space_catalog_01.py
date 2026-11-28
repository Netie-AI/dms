"""FDE-CORTEX-SPACE-CATALOG-01: the session manifest carries one Space.

Two synthetic Spaces, disjoint tables. A question on Space B never puts
Space A's catalog into the prompt, the reason, or the suggestions. Flag off,
the signed bytes and the wire manifest match a bind that has no catalog.
The demo Space stays without ``space_catalog`` even when the flag is on.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cortex_client.client import _to_gen_manifest
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest, QueryResult, canonical_manifest_bytes
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dms_executor import Executor
from dms_executor.manifest import (
    IntermediateKey,
    ManifestMinter,
    SessionAcl,
    sign_space_catalog_bytes,
)
from dms_executor.ontology import Ontology
from dms_executor.space_catalog_manifest import (
    canonical_space_catalog_bytes,
    catalog_prompt,
    handoff_reason,
    release_chooser_handoff,
    session_space_catalog,
    space_catalog_after_ask,
    suggestions_for,
)

# Synthetic Ed25519 seed. A test key, not an issuer key.
_TEST_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
_VECTOR_SPACE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_VECTOR_SESSION = "ses_bbbbbbbb"
_VECTOR_CATALOG: dict[str, Any] = {
    "objects": [
        {"name": "synth_fill", "relation": "bronze.synth_fills", "key": ["fill_id"]},
        {"name": "synth_counter", "relation": "bronze.synth_counters", "key": []},
    ],
    "links": [
        {
            "name": "fill_to_counter",
            "from_object": "synth_fill",
            "from_columns": ["counter_id"],
            "to_object": "synth_counter",
            "to_columns": ["counter_id"],
            "cardinality": "many_to_one",
        }
    ],
    "measures": [
        {
            "name": "synth_fill_count",
            "grain": "synth_fill",
            "expression": "SELECT COUNT(*) AS n FROM synth_fill",
            "description": "count of synth fills",
        }
    ],
    "definitions": [
        {
            "name": "synth_fill_count",
            "grain": "synth_fill",
            "text": "count of synth fills",
        }
    ],
    "granted_tables": ["bronze.synth_fills", "bronze.synth_counters"],
}
# canonical_space_catalog_bytes from cortex_contract 1.12.0 at Cortex 46a7f2b.
# The empty key on synth_counter is pruned. The binding object stays.
_VECTOR_JSON = (
    '{"session_id":"ses_bbbbbbbb","space_catalog":{"definitions":'
    '[{"grain":"synth_fill","name":"synth_fill_count","text":"count of synth fills"}]'
    ',"granted_tables":["bronze.synth_fills","bronze.synth_counters"],"links":'
    '[{"cardinality":"many_to_one","from_columns":["counter_id"],"from_object":'
    '"synth_fill","name":"fill_to_counter","to_columns":["counter_id"],"to_object":'
    '"synth_counter"}],"measures":[{"description":"count of synth fills","expression":'
    '"SELECT COUNT(*) AS n FROM synth_fill","grain":"synth_fill","name":'
    '"synth_fill_count"}],"objects":[{"key":["fill_id"],"name":"synth_fill",'
    '"relation":"bronze.synth_fills"},{"name":"synth_counter","relation":'
    '"bronze.synth_counters"}]},"space_id":'
    '"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}'
)
_VECTOR_SIG = (
    "JD5isriNEQP3_FpsMZ28fv7HbTQ4fodyTC6-Vm8RwTilYQmHOFP4JiVJmouilo8PlXrPg0aUoY0gtKeyG1OLAQ"
)

_SPACE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_SPACE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DEMO = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_A_TABLE = "bronze.synth_visits"
_B_FILL = "bronze.synth_fills"
_B_COUNTER = "bronze.synth_counters"
_A_MEASURE = "synth_visit_count"
_B_MEASURE = "synth_fill_count"
_A_DEF = "count of synth visits"
_B_DEF = "count of synth fills"
_DEMO_WORDS = (
    "inventory",
    "suppliers",
    "shipments",
    "alerts",
    "warehouse locations",
    "expired count",
    "metric chooser",
    "synth_visit",
)


def _onto_a() -> Ontology:
    onto = Ontology()
    onto.add_object("synth_visits", _A_TABLE, ["visit_id"])
    onto.add_measure(
        _A_MEASURE,
        "synth_visits",
        _A_DEF,
        description="synth visits",
    )
    return onto


def _onto_b() -> Ontology:
    onto = Ontology()
    onto.add_object("synth_fills", _B_FILL, ["fill_id"])
    onto.add_object("synth_counters", _B_COUNTER, ["counter_id"])
    onto.add_link(
        "fill_counter",
        "synth_fills",
        ["counter_id"],
        "synth_counters",
        ["counter_id"],
    )
    onto.add_measure(
        _B_MEASURE,
        "synth_fills",
        _B_DEF,
        description="synth fills",
    )
    return onto


def _blob(*parts: object) -> str:
    return json.dumps(parts, default=str)


def test_space_b_catalog_excludes_space_a_and_the_demo_set() -> None:
    catalog = session_space_catalog(_onto_b(), [_B_FILL, _B_COUNTER])
    prompt = catalog_prompt(catalog)
    reason = handoff_reason(catalog)
    suggestions = suggestions_for(catalog)
    blob = _blob(prompt, reason, suggestions)
    assert _B_FILL in prompt
    assert _B_COUNTER in prompt
    assert _B_MEASURE in prompt
    assert _B_DEF in prompt
    assert _B_MEASURE in suggestions
    assert _A_MEASURE not in suggestions
    for word in _DEMO_WORDS:
        assert word not in blob
    assert "synth_visits" not in blob
    assert catalog["measures"][0]["expression"] == _B_DEF
    assert catalog["definitions"][0]["text"] == _B_DEF
    assert catalog["links"][0]["from_object"] == "synth_fills"
    assert catalog["links"][0]["to_object"] == "synth_counters"


def test_ungranted_object_is_left_out() -> None:
    mixed = _onto_b()
    mixed.add_object("synth_visits", _A_TABLE, ["visit_id"])
    mixed.add_measure(_A_MEASURE, "synth_visits", _A_DEF)
    catalog = session_space_catalog(mixed, [_B_FILL])
    blob = catalog_prompt(catalog)
    assert "synth_visits" not in blob
    assert _A_MEASURE not in blob
    assert _B_MEASURE in blob
    assert catalog["granted_tables"] == [_B_FILL]


def test_no_approved_measure_is_an_empty_list() -> None:
    onto = Ontology()
    onto.add_object("synth_fills", _B_FILL, ["fill_id"])
    catalog = session_space_catalog(onto, [_B_FILL])
    assert catalog["measures"] == []
    assert catalog["definitions"] == []
    assert catalog["granted_tables"] == [_B_FILL]
    reason = handoff_reason(catalog)
    assert "no approved measure" in reason
    assert _B_FILL in reason
    assert suggestions_for(catalog) == []
    for word in _DEMO_WORDS:
        assert word not in reason


def test_release_handoff_is_flag_gated() -> None:
    payload = {
        "space_catalog_handoff": "generative",
        "answer": "the metric chooser is not armed, so it cannot be served directly",
    }
    assert release_chooser_handoff(payload) is payload
    monkey_env = {"DMS_SPACE_CATALOG": "1"}
    from dms_executor import space_catalog_manifest as mod

    assert mod.space_catalog_enabled(monkey_env) is True
    assert mod.space_catalog_enabled({}) is False


def test_release_handoff_drops_the_chooser_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "space_catalog_handoff": "generative",
        "answer": "the metric chooser is not armed, so it cannot be served directly",
    }
    monkeypatch.setenv("DMS_SPACE_CATALOG", "1")
    assert release_chooser_handoff(payload) is None
    monkeypatch.setenv("DMS_SPACE_CATALOG", "0")
    assert release_chooser_handoff(payload) is payload


def test_handoff_reason_names_only_the_asking_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SPACE_CATALOG", "1")
    resp = AskResponse(
        answer="the metric chooser is not armed, so it cannot be served directly",
        abstained=True,
        badge="reconfirm",
        route="reconfirm",
        suggestions=["How many suppliers shipped alerts?"],
        space_catalog_handoff="generative",
    )
    env = space_catalog_after_ask(
        resp,
        question="How many synth fills are there?",
        space_id=_SPACE_B,
        session_id="ses_b",
        granted=[_B_FILL, _B_COUNTER],
        ontology=_onto_b(),
    )
    assert env is not None
    blob = _blob(env.get("text"), env.get("suggestions"), env.get("reconfirm"))
    assert env["values"] == []
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert env["suggestions"] == [_B_MEASURE]
    assert _B_FILL in str(env["text"])
    for word in _DEMO_WORDS:
        assert word not in blob
    assert "suppliers" not in blob
    monkeypatch.delenv("DMS_SPACE_CATALOG", raising=False)
    assert (
        space_catalog_after_ask(
            resp,
            question="How many synth fills are there?",
            space_id=_SPACE_B,
            session_id="ses_b",
            granted=[_B_FILL],
            ontology=_onto_b(),
        )
        is None
    )


def _catalog_verifies(
    catalog: dict[str, Any],
    signature: str,
    *,
    space_id: str,
    session_id: str,
) -> bool:
    pad = "=" * (-len(signature) % 4)
    try:
        raw = base64.urlsafe_b64decode(signature + pad)
        _TEST_KEY.public_key().verify(
            raw,
            canonical_space_catalog_bytes(
                catalog, space_id=space_id, session_id=session_id
            ),
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def _minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    minter = ManifestMinter()

    def _sign(catalog: dict[str, Any], *, space_id: str, session_id: str) -> str:
        return sign_space_catalog_bytes(
            _TEST_KEY, catalog, space_id=space_id, session_id=session_id
        )

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-11-28T00:00:00+00:00",
            expires_at="2026-11-28T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "sign_space_catalog", _sign)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


class _Fake:
    def __init__(self) -> None:
        self.submits: list[Any] = []

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        return QueryResult(ok=True, status="bound", run_id="run-1")


def _acl(space_id: str, tables: list[str]) -> SessionAcl:
    return SessionAcl(
        session_id=f"ses_{space_id[:8]}",
        org_id="tenant_demo",
        space_id=space_id,
        row_predicates={table: "TRUE" for table in tables},
        allowed_paths=[],
        pool_id="default",
    )


def _wire(req: Any) -> dict[str, Any]:
    return _to_gen_manifest(req.manifest).to_dict()


def test_flag_off_manifest_matches_the_unsigned_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SPACE_CATALOG", raising=False)
    monkeypatch.setattr(
        "dms_executor.space_catalog_manifest.load_space_catalog_ontology",
        lambda _space: _onto_b(),
    )
    fake = _Fake()
    exe = Executor(cortex=fake, minter=_minter(monkeypatch))  # type: ignore[arg-type]
    acl = _acl(_SPACE_B, [_B_FILL, _B_COUNTER])
    exe.bind_session(acl)
    minted = exe._minter.mint_manifest(acl)
    submitted = fake.submits[0].manifest
    assert canonical_manifest_bytes(submitted) == canonical_manifest_bytes(minted)
    wire = _wire(fake.submits[0])
    assert "space_catalog" not in wire
    assert "space_catalog_sig" not in wire
    assert wire == _to_gen_manifest(minted).to_dict()


def test_flag_on_sends_only_the_asking_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SPACE_CATALOG", "1")
    ontos = {_SPACE_A: _onto_a(), _SPACE_B: _onto_b()}
    monkeypatch.setattr(
        "dms_executor.space_catalog_manifest.load_space_catalog_ontology",
        lambda space_id: ontos[str(space_id)],
    )
    fake = _Fake()
    exe = Executor(cortex=fake, minter=_minter(monkeypatch))  # type: ignore[arg-type]
    exe.bind_session(_acl(_SPACE_A, [_A_TABLE]))
    exe.bind_session(_acl(_SPACE_B, [_B_FILL, _B_COUNTER]))
    wire_a = _wire(fake.submits[0])
    wire_b = _wire(fake.submits[1])
    prompt_b = catalog_prompt(wire_b["space_catalog"])
    assert "synth_visit" not in prompt_b
    assert _A_TABLE not in prompt_b
    assert _B_FILL in prompt_b
    assert _B_MEASURE in prompt_b
    assert wire_a["space_catalog"]["granted_tables"] == [_A_TABLE]
    assert _B_FILL not in catalog_prompt(wire_a["space_catalog"])
    minted = exe._minter.mint_manifest(_acl(_SPACE_B, [_B_FILL, _B_COUNTER]))
    assert canonical_manifest_bytes(fake.submits[1].manifest) == canonical_manifest_bytes(minted)
    assert b"space_catalog" not in canonical_manifest_bytes(fake.submits[1].manifest)
    assert _catalog_verifies(
        wire_b["space_catalog"],
        wire_b["space_catalog_sig"],
        space_id=_SPACE_B,
        session_id="ses_bbbbbbbb",
    )
    bare = {
        key: value
        for key, value in wire_b.items()
        if key not in ("space_catalog", "space_catalog_sig")
    }
    monkeypatch.delenv("DMS_SPACE_CATALOG", raising=False)
    off = _Fake()
    exe_off = Executor(cortex=off, minter=_minter(monkeypatch))  # type: ignore[arg-type]
    exe_off.bind_session(_acl(_SPACE_B, [_B_FILL, _B_COUNTER]))
    assert _wire(off.submits[0]) == bare


def test_demo_space_stays_without_a_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SPACE_CATALOG", "1")
    monkeypatch.setattr(
        "dms_executor.space_catalog_manifest.load_space_catalog_ontology",
        lambda _space: (_ for _ in ()).throw(AssertionError("demo must not load a space catalog")),
    )
    fake = _Fake()
    exe = Executor(cortex=fake, minter=_minter(monkeypatch))  # type: ignore[arg-type]
    exe.bind_session(_acl(_DEMO, ["inventory", "transactions"]))
    wire_on = _wire(fake.submits[0])
    assert "space_catalog" not in wire_on
    assert "space_catalog_sig" not in wire_on
    monkeypatch.delenv("DMS_SPACE_CATALOG", raising=False)
    off = _Fake()
    exe_off = Executor(cortex=off, minter=_minter(monkeypatch))  # type: ignore[arg-type]
    exe_off.bind_session(_acl(_DEMO, ["inventory", "transactions"]))
    assert _wire(off.submits[0]) == wire_on


def test_early_bind_puts_the_catalog_on_the_session_before_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on binds the Space catalog before the generative call."""
    monkeypatch.setenv("DMS_SPACE_CATALOG", "1")
    monkeypatch.setattr(
        "dms_executor.space_catalog_manifest.load_space_catalog_ontology",
        lambda _space: _onto_b(),
    )
    monkeypatch.setattr(
        "dms_executor.load_space_ontology",
        lambda _space, store=None: _onto_b(),
    )
    fake = _Fake()
    fake.asks = []  # type: ignore[attr-defined]

    def _ask(req: Any) -> AskResponse:
        fake.asks.append(req)  # type: ignore[attr-defined]
        return AskResponse(
            answer="the metric chooser is not armed, so it cannot be served directly",
            abstained=True,
            badge="reconfirm",
            route="reconfirm",
            space_catalog_handoff="generative",
            audit_id="aud-b",
        )

    fake.ask = _ask  # type: ignore[attr-defined]
    exe = Executor(cortex=fake, minter=_minter(monkeypatch))  # type: ignore[arg-type]
    monkeypatch.setattr(
        exe,
        "grantable_tables",
        lambda space_id=None: [_B_FILL, _B_COUNTER],
    )
    order: list[str] = []
    real_bind = exe.bind_session

    def _bind(session: Any) -> Any:
        order.append("bind")
        return real_bind(session)

    def _gen(*_a: Any, **_k: Any) -> None:
        order.append("generate")
        return None

    monkeypatch.setattr(exe, "bind_session", _bind)
    monkeypatch.setattr("dms_executor.maybe_generative_ask", _gen)
    env = exe.live_ask(
        "How many synth fills are there?",
        space_id=_SPACE_B,
        session_id="ses_live_b",
    )
    assert order[0] == "bind"
    assert "generate" in order
    wire = _wire(fake.submits[0])
    assert _B_MEASURE in catalog_prompt(wire["space_catalog"])
    assert "synth_visit" not in catalog_prompt(wire["space_catalog"])
    blob = _blob(env.get("text"), env.get("suggestions"))
    assert env["values"] == []
    assert _B_MEASURE in env["suggestions"]
    for word in ("inventory", "suppliers", "metric chooser", "synth_visit"):
        assert word not in blob


def test_catalog_signature_matches_the_contract_vector() -> None:
    """Fixed catalog and test key. Bytes and signature match contract 1.12.0."""
    raw = canonical_space_catalog_bytes(
        _VECTOR_CATALOG, space_id=_VECTOR_SPACE, session_id=_VECTOR_SESSION
    )
    assert raw.decode() == _VECTOR_JSON
    assert b'"key":[]' not in raw
    signature = sign_space_catalog_bytes(
        _TEST_KEY,
        _VECTOR_CATALOG,
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )
    assert signature == _VECTOR_SIG
    assert _catalog_verifies(
        _VECTOR_CATALOG,
        signature,
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )


def test_catalog_signature_tamper_is_rejected() -> None:
    signature = sign_space_catalog_bytes(
        _TEST_KEY,
        _VECTOR_CATALOG,
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )
    flipped = signature[:-1] + ("A" if not signature.endswith("A") else "B")
    assert not _catalog_verifies(
        _VECTOR_CATALOG,
        flipped,
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )
    changed = json.loads(json.dumps(_VECTOR_CATALOG))
    changed["granted_tables"] = ["bronze.synth_other"]
    assert not _catalog_verifies(
        changed,
        signature,
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )
    assert not _catalog_verifies(
        _VECTOR_CATALOG,
        signature,
        space_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        session_id=_VECTOR_SESSION,
    )
    assert not _catalog_verifies(
        _VECTOR_CATALOG,
        signature,
        space_id=_VECTOR_SPACE,
        session_id="ses_other",
    )


def test_catalog_signature_must_fail_without_the_binding() -> None:
    """Signing the catalog object alone is not the contract signature."""
    lone_bytes = json.dumps(
        _VECTOR_CATALOG, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    lone = base64.urlsafe_b64encode(_TEST_KEY.sign(lone_bytes)).decode("ascii").rstrip("=")
    assert lone != _VECTOR_SIG
    assert not _catalog_verifies(
        _VECTOR_CATALOG,
        lone,
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )
    assert not _catalog_verifies(
        _VECTOR_CATALOG,
        "",
        space_id=_VECTOR_SPACE,
        session_id=_VECTOR_SESSION,
    )


def test_minter_signs_the_catalog_with_the_manifest_key() -> None:
    minter = ManifestMinter()
    minter._key = IntermediateKey(
        kid="test-kid",
        private_key=_TEST_KEY,
        not_after=datetime.now(UTC) + timedelta(hours=1),
    )
    signature = minter.sign_space_catalog(
        _VECTOR_CATALOG, space_id=_VECTOR_SPACE, session_id=_VECTOR_SESSION
    )
    assert signature == _VECTOR_SIG