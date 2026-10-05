"""CCA-SPACE-ISO-01 / dms#348: DMS_CCA_CASCADE must not read another Space's upload.

Real path is POST /v1/chat/ask with the flag on. A CSV is ingested into Finance.
Warehouse Ops asks, including with that file ticked. The upload's spellings must
not appear on the Ops envelope, and the cascade's read SQL must not name the
upload. Finance, with the same file ticked, must name that upload in the trace
and in the scan SQL. If it does not, an absent needle is an invented pass.

ponytail: scan_landed_columns strips the schema, so bronze.<upload> rows are
not opened for either Space. This proves the table list the cascade is handed.
A scan that opens bronze rows and ignores the Space grant fails the needle
asserts. Upgrade path: qualify the bronze read, then require the owned tick to
surface Brunei on the envelope and delete this ceiling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import Executor
from dms_executor.bronze import ingest_csv_bytes
from dms_executor.cca.cascade import engages
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
# Not in the demo seed (suppliers are MY/SG/TH) and not in the question text.
NEEDLE_COUNTRY = "Brunei"
NEEDLE_TOKEN = "CI_SPACEISO_ZZ9"
NEEDLE_AMOUNT = "999001.77"
NEEDLES = (NEEDLE_COUNTRY, NEEDLE_TOKEN, NEEDLE_AMOUNT)
# Engages sense, class, and geo. Names none of the needles.
QUESTION = "lease revenue across SEA for commercial property"
FILENAME = "cca_iso_finance.csv"


@dataclass
class _MarkerCortex:
    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True, status="ok", run_id="run_iso_sql", output={"rows": []}
            )
        return QueryResult(ok=True, status="bound", run_id="run_iso")

    def ledger_append(self, req: Any) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_iso", hash="hash_iso_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="CI fixture ask stub.",
            badge="certified",
            sql_used="SELECT 1 AS n",
            rows=[{"n": 1}],
            audit_id="aud_iso",
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
            issued_at="2026-10-05T00:00:00+00:00",
            expires_at="2026-10-05T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _install_scan_spy(monkeypatch: pytest.MonkeyPatch, sql_log: list[str]) -> None:
    """Record SQL from the cascade scan only.

    ``scan_landed_columns`` looks up ``duckdb`` in its own module. Replacing
    that name leaves every other reader on the real driver. DuckDB connections
    do not allow assigning ``execute``.
    """
    import dms_executor.cca.binder as binder

    real_connect = duckdb.connect

    class _Conn:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, query: str, *params: Any, **kwargs: Any) -> Any:
            sql_log.append(str(query))
            return self._inner.execute(query, *params, **kwargs)

        def close(self) -> None:
            self._inner.close()

    class _Duck:
        @staticmethod
        def connect(*args: Any, **kwargs: Any) -> _Conn:
            return _Conn(real_connect(*args, **kwargs))

    monkeypatch.setattr(binder, "duckdb", _Duck)


@dataclass
class _World:
    client: TestClient
    table: str
    ident: str
    sql: list[str]


def _world(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> _World:
    from dms_executor import demo_warehouse as dw

    path = tmp_path / "cca_space_iso.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    dw._SEEDED.clear()
    get_settings.cache_clear()

    csv = (
        "country,transaction_type,asset_class,amount\n"
        f"{NEEDLE_COUNTRY},LEASE,COM,{NEEDLE_AMOUNT}\n"
        f"{NEEDLE_TOKEN},LEASE,COM,1\n"
    ).encode()
    receipt = ingest_csv_bytes(
        filename=FILENAME, data=csv, path=path, space_id=FINANCE
    )
    assert receipt.ingested == 2, receipt
    assert receipt.table, receipt
    assert engages(QUESTION) is True
    ident = receipt.table.split(".")[-1]
    con = duckdb.connect(str(path), read_only=True)
    try:
        stored = con.execute(
            f'SELECT country, amount FROM bronze."{ident}"'
        ).fetchall()
    finally:
        con.close()
    flat = " ".join(str(cell) for row in stored for cell in row)
    for needle in NEEDLES:
        assert needle in flat, stored

    sql: list[str] = []
    _install_scan_spy(monkeypatch, sql)
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    app = create_app()
    cortex = _MarkerCortex()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=path,
    )
    app.state.cortex = cortex
    return _World(client=TestClient(app), table=str(receipt.table), ident=ident, sql=sql)


def _ask(world: _World, *, space_id: str, tables: list[str] | None, session: str) -> Any:
    world.sql.clear()
    body: dict[str, Any] = {
        "question": QUESTION,
        "space_id": space_id,
        "session_id": session,
    }
    if tables is not None:
        body["grounded_tables"] = tables
    response = world.client.post("/v1/chat/ask", json=body)
    return response


def _trace_blob(env: dict[str, Any]) -> str:
    return json.dumps(env.get("constraint_trace") or [])


def _assert_ops_closed(response: Any, world: _World, *, where: str) -> None:
    assert response.status_code == 200, response.text
    env = response.json()
    assert_envelope_valid(env)
    assert env.get("constraint_trace"), f"{where}: cascade did not run"
    blob = response.text
    for needle in NEEDLES:
        assert needle not in blob, f"{where}: envelope leaked {needle}"
    trace = _trace_blob(env)
    assert world.ident not in trace, f"{where}: trace named {world.ident}: {trace}"
    assert world.table not in trace, f"{where}: trace named {world.table}: {trace}"
    read = "\n".join(world.sql)
    assert world.ident not in read, f"{where}: cascade SQL read {world.ident}: {read}"


def test_ops_default_ask_does_not_read_finance_upload(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ticked. Ops cascade stays on the Ops demo grant."""
    world = _world(tmp_path, minter, monkeypatch)
    response = _ask(world, space_id=OPS, tables=None, session="ses_iso_ops_default")
    _assert_ops_closed(response, world, where="ops default")


def test_ops_ticked_finance_upload_does_not_read_it(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticking the other Space's file is a request, not a grant."""
    world = _world(tmp_path, minter, monkeypatch)
    for label, tables in (
        ("qualified", [world.table]),
        ("bare", [world.ident]),
    ):
        response = _ask(
            world, space_id=OPS, tables=tables, session=f"ses_iso_ops_{label}"
        )
        _assert_ops_closed(response, world, where=f"ops tick {label}")


def test_finance_tick_reaches_the_cascade_table_list(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owned tick must name the upload. If it does not, the Ops asserts are vacuous."""
    world = _world(tmp_path, minter, monkeypatch)
    response = _ask(
        world, space_id=FINANCE, tables=[world.table], session="ses_iso_finance"
    )
    assert response.status_code == 200, response.text
    env = response.json()
    assert_envelope_valid(env)
    trace = _trace_blob(env)
    assert world.ident in trace, trace
    read = "\n".join(world.sql)
    assert world.ident in read, read
