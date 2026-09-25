"""GRANT-READ-01 / dms#297: uploads not selected never enter generate context.

CI fixtures, not live. No Cortex ranking claim. Fake httpx transport only.
Never reads env files, OpenVault, or Secret Manager.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from cortex_client.compute import INSIGHTS_PATH, compute_insights
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.bronze import ingest_csv_bytes
from dms_executor.demo_grants import DemoSessionStore, ingested_bronze_tables, source_id_for
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.manifest import ManifestMinter, SessionAcl

# Seeded CI fixtures. Must not appear unless the caller ticked the upload.
SECRET_TABLE = "grantread_secret"
SECRET_COL = "grantread_col"
SECRET_VALUE = "CI_FIXTURE_GRANTREAD_ZZ9"
SECRET_COUNTRY = "CI_FIXTURE_GRANTREAD_LAND"
SECRET_TXN = "CI_FIXTURE_GRANTREAD_LEASE"
# Distinct samples / schema names. Not the question text (GEN_Q names the table).
CONTEXT_NEEDLES = (SECRET_TABLE, SECRET_COL, SECRET_VALUE, SECRET_COUNTRY, SECRET_TXN)
SAMPLE_NEEDLES = (SECRET_VALUE, SECRET_COUNTRY, SECRET_TXN)

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GEN_Q = "Show grantread_col values from grantread_secret"
# CCA exact-match path: sense/geo scan DISTINCT samples from granted tables.
EXACT_Q = "lease revenue across SEA for commercial property"
# Seeded fake. Not a live OpenVault token.
_FAKE_KEY = "ov_ci_fixture_grantread01"


def _blob(obj: object) -> str:
    return json.dumps(obj, default=str)


def _assert_absent(blob: str, needles: tuple[str, ...], *, where: str) -> None:
    for needle in needles:
        assert needle not in blob, f"{where} leaked CI fixture {needle!r}"


def _ontology_blob(body: dict[str, Any]) -> str:
    return _blob(body.get("ontology") or {})


class _CaptureHttp:
    """Records Insights POST bodies. No network."""

    def __init__(self, posts: list[dict[str, Any]]) -> None:
        self.posts = posts

    def __call__(self, *a: Any, timeout: Any = None, **k: Any) -> _CaptureHttp:
        return self

    def __enter__(self) -> _CaptureHttp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def post(self, url: str, json: dict[str, Any] | None = None, headers: Any = None) -> Any:
        self.posts.append({"url": url, "json": json or {}, "headers": headers})

        class _Resp:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return {"phase": "generate", "generative": {"ok": False, "sql": None}}

        return _Resp()

    def get(self, url: str, params: Any = None, headers: Any = None) -> Any:
        self.posts.append({"url": url, "json": params or {}, "headers": headers})

        class _Resp:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return {}

        return _Resp()


@dataclass
class _AskCortex:
    posts: list[dict[str, Any]]
    asks: list[Any] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        return compute_insights(
            "http://127.0.0.1:8010",
            question=question,
            session_id=kwargs.get("session_id"),
            space_id=kwargs.get("space_id"),
            ontology=kwargs.get("ontology"),
            api_key=_FAKE_KEY,
        )

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        return QueryResult(ok=True, status="ok", run_id="run_grantread", output={"rows": []})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_grantread", hash="hash_grantread_ci")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="CI fixture ask stub.",
            badge="certified",
            sql_used="SELECT 1 AS n",
            rows=[{"n": 1}],
            audit_id="aud_grantread",
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
            issued_at="2026-09-25T00:00:00+00:00",
            expires_at="2026-09-25T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _seed_secret(path: Path) -> Path:
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            f"CREATE TABLE {SECRET_TABLE} ({SECRET_COL} VARCHAR, country VARCHAR, txn_type VARCHAR)"
        )
        con.execute(
            f"INSERT INTO {SECRET_TABLE} VALUES "
            f"('{SECRET_VALUE}', '{SECRET_COUNTRY}', '{SECRET_TXN}')"
        )
    finally:
        con.close()
    return path


def _store(path: Path) -> DemoSessionStore:
    # extra_grants is the seeded fake upload. Do not pass warehouse= here so
    # the leak tests still import on parent 38f8924a (R-0007).
    return DemoSessionStore(
        extra_grants=(SECRET_TABLE,),
        uploads=lambda: ingested_bronze_tables(path),
    )


def _executor(path: Path, minter: ManifestMinter, posts: list[dict[str, Any]]) -> Executor:
    return Executor(
        cortex=_AskCortex(posts=posts),  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=path,
        session_store=_store(path),
    )


def _insights_bodies(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        p["json"]
        for p in posts
        if str(p.get("url") or "").endswith(INSIGHTS_PATH) and isinstance(p.get("json"), dict)
    ]


def _submit_ok(_sql: str) -> Any:
    return QueryResult(ok=True, status="ok", run_id="run_grantread_tick", output={"rows": []})


def _ledger_ok(_payload: dict[str, Any]) -> LedgerAppendResponse:
    return LedgerAppendResponse(entry_id="led_grantread_tick", hash="hash_grantread_tick")


def test_unticked_upload_absent_from_insights_body_generative(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    """Generative retrieve must not read an upload nobody selected.

    Fails on parent 38f8924a: grantable=grantable_tables includes extra_grants,
    so retrieve_short_context puts table/column/values into the Insights body.
    """
    wh = _seed_secret(tmp_path / "grant_gen.duckdb")
    posts: list[dict[str, Any]] = []
    exe = _executor(wh, minter, posts)
    assert SECRET_TABLE not in DEMO_TABLES
    assert SECRET_TABLE in exe.grantable_tables(space_id=FINANCE)
    acl = exe.demo_acl(session_id="ses_grant_gen", space_id=FINANCE, tables=None)
    assert SECRET_TABLE not in acl.row_predicates

    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        env = exe.live_ask(
            GEN_Q, session_id="ses_grant_gen", space_id=FINANCE, ask_path="generative"
        )

    bodies = _insights_bodies(posts)
    assert bodies, posts
    for body in bodies:
        onto = body.get("ontology") or {}
        _assert_absent(_ontology_blob(body), CONTEXT_NEEDLES, where="generative Insights ontology")
        assert onto.get("schema") in ([], None) or SECRET_TABLE not in _blob(onto.get("schema"))
        _assert_absent(
            _blob(onto.get("encodings") or {}), SAMPLE_NEEDLES, where="generative encodings"
        )
    assert_envelope_valid(env)
    _assert_absent(_blob(env), SAMPLE_NEEDLES, where="generative envelope")


def test_unticked_upload_absent_from_exact_cascade_evidence(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CCA exact-match cascade must not scan an upload nobody selected.

    Fails on parent 38f8924a: run_cascade(tables=requested or granted) opens
    extra_grants and puts DISTINCT samples on envelope evidence.
    """
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    wh = _seed_secret(tmp_path / "grant_exact.duckdb")
    posts: list[dict[str, Any]] = []
    exe = _executor(wh, minter, posts)
    env = exe.live_ask(EXACT_Q, session_id="ses_grant_exact", space_id=FINANCE)
    assert_envelope_valid(env)
    _assert_absent(_blob(env), CONTEXT_NEEDLES, where="exact-path envelope evidence")
    for entry in env.get("constraint_trace") or []:
        _assert_absent(
            _blob(entry.get("evidence") or []), CONTEXT_NEEDLES, where="cascade evidence"
        )
        _assert_absent(_blob(entry.get("reasons") or []), CONTEXT_NEEDLES, where="cascade reasons")


def test_ticked_upload_appears_in_insights_and_cascade(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once ticked, the same CI fixtures must be visible (oracle is not a tautology)."""
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    wh = _seed_secret(tmp_path / "grant_tick.duckdb")
    posts: list[dict[str, Any]] = []
    exe = _executor(wh, minter, posts)
    acl = exe.demo_acl(session_id="ses_grant_tick", space_id=FINANCE, tables=[SECRET_TABLE])
    assert SECRET_TABLE in acl.row_predicates

    from dms_executor.ontology import Ontology

    tiny = Ontology()
    tiny.add_object("secret", SECRET_TABLE, [SECRET_COL])
    tiny.add_measure("grantread_count", "secret", "COUNT(*)")
    tiny.verified = True

    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        maybe_generative_ask(
            GEN_Q,
            space_id=FINANCE,
            session_id="ses_grant_tick_gen",
            warehouse=wh,
            grantable={SECRET_TABLE},
            ontology=tiny,
            compute=lambda catalog: compute_insights(
                "http://127.0.0.1:8010",
                question=GEN_Q,
                ontology=catalog,
                api_key=_FAKE_KEY,
            ),
            submit=_submit_ok,
            ledger_append=_ledger_ok,
        )
    bodies = _insights_bodies(posts)
    assert bodies, posts
    onto_joined = " ".join(_ontology_blob(b) for b in bodies)
    assert SECRET_TABLE in onto_joined, onto_joined
    assert SECRET_COL in onto_joined, onto_joined
    assert SECRET_VALUE in onto_joined, onto_joined

    env = exe.live_ask(
        EXACT_Q,
        session_id="ses_grant_tick_exact",
        space_id=FINANCE,
        tables=[SECRET_TABLE],
    )
    assert_envelope_valid(env)
    env_blob = _blob(env)
    assert SECRET_TABLE in env_blob or SECRET_TXN in env_blob, env_blob


def test_unread_grant_refuses_empty_generate_context(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the grant cannot be read, retrieve is empty — never the whole space."""
    wh = _seed_secret(tmp_path / "grant_boom.duckdb")
    posts: list[dict[str, Any]] = []

    def _boom(self: Executor, *, space_id: str | None = None) -> list[str]:
        raise RuntimeError("grant unread")

    monkeypatch.setattr(Executor, "grantable_tables", _boom)
    exe = _executor(wh, minter, posts)
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        env = exe.live_ask(
            GEN_Q, session_id="ses_grant_boom", space_id=FINANCE, ask_path="generative"
        )
    assert_envelope_valid(env)
    _assert_absent(_blob(env), SAMPLE_NEEDLES, where="unread-grant envelope")
    bodies = _insights_bodies(posts)
    assert bodies, posts
    for body in bodies:
        onto = body.get("ontology") or {}
        _assert_absent(
            _ontology_blob(body), CONTEXT_NEEDLES, where="unread-grant Insights ontology"
        )
        assert not (onto.get("schema") or []), onto.get("schema")


def test_list_space_source_ids_reads_executor_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Side defect: space bronze listing used the default warehouse path."""
    from dms_executor import demo_warehouse as dw

    default_wh = tmp_path / "default.duckdb"
    exe_wh = tmp_path / "executor.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(default_wh))
    dw._SEEDED.clear()
    ensure_demo_warehouse(default_wh)
    dw._SEEDED.clear()
    ensure_demo_warehouse(exe_wh)

    default_receipt = ingest_csv_bytes(
        filename="default_only.csv",
        data=b"sku,qty\nDEF,1\n",
        path=default_wh,
        space_id=FINANCE,
    )
    exe_receipt = ingest_csv_bytes(
        filename="executor_only.csv",
        data=b"sku,qty\nEXE,1\n",
        path=exe_wh,
        space_id=FINANCE,
    )
    default_table = str(default_receipt.table)
    exe_table = str(exe_receipt.table)
    assert default_table and exe_table
    assert default_table != exe_table

    exe = Executor(cortex=None, warehouse_path=exe_wh)
    granted = exe.grantable_tables(space_id=FINANCE)
    assert exe_table in granted, granted
    assert default_table not in granted, granted
    ids = exe._session_store.list_space_source_ids(FINANCE)
    assert source_id_for(exe_table) in ids, ids
    assert source_id_for(default_table) not in ids, ids
