"""VALUE-INDEX-01: the value index is whole-token and distinct, or it is silent.

The Space index stored ``LIMIT 20`` rows per column as the column's whole
value set. 30 x NORTH, 30 x NORTH EAST, 40 x SOUTH read as one value, so
"NORTH EAST" hinted ``= NORTH`` with ``distinct=1``. A wrong-subset hint
that still executes is a plausible number under a green badge (rule 12).
Synthetic data only. Pair file: test_value_mask_01.py.

The product assertions land on the POST /v1/chat/ask envelope (rule 10a): the
rendered abstain text, the empty rows, and the schema_context the envelope
carries. Hint values are DMSHINT tokens there, never the raw value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.schema_context import (
    INDEX_COLUMN_CAP,
    _forget_index,
    build_schema_context,
    build_space_index,
    lookup_schema,
)

_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_SPACE = "space-value-index"
_NORTH_Q = "total sales for NORTH EAST"
_CAPPED_Q = "total sales for ZONE ONE"


@pytest.fixture(autouse=True)
def _clean_index() -> Any:
    yield
    _forget_index(_SPACE)
    _forget_index(_FINANCE)


def _region_db(path: Path, *, table: str = "sales", column: str = "region") -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute(f"CREATE TABLE {table} ({column} VARCHAR, amount INTEGER)")
        for value, count in (("NORTH", 30), ("NORTH EAST", 30), ("SOUTH", 40)):
            for i in range(count):
                con.execute(f"INSERT INTO {table} VALUES (?, ?)", [value, i])
    finally:
        con.close()
    return path


def _prompt(db: Path, question: str, grantable: set[str], space: str = _SPACE) -> Any:
    assert build_space_index(db, space, grantable, "duckdb") == ""
    schema, stamp = lookup_schema(db, grantable, "duckdb", space)
    assert stamp == ""
    return build_schema_context(question, schema, grantable=grantable, space_id=space)


# -- distinct index ------------------------------------------------------


def test_prefix_value_is_not_hinted_from_a_twenty_row_sample(tmp_path: Path) -> None:
    db = _region_db(tmp_path / "sales.duckdb")
    built = _prompt(db, _NORTH_Q, {"sales"})
    assert "- sales.region = NORTH EAST" in built.prompt
    assert "- sales.region = NORTH\n" not in built.prompt + "\n"
    assert "- region VARCHAR distinct=3" in built.prompt
    assert "distinct=1 " not in built.prompt
    assert "NORTH" not in built.envelope_prompt
    assert "- sales.region = DMSHINT_01" in built.envelope_prompt


def test_short_value_still_hints_when_the_longer_one_exists(tmp_path: Path) -> None:
    db = _region_db(tmp_path / "sales.duckdb")
    built = _prompt(db, "total sales for SOUTH", {"sales"})
    assert "- sales.region = SOUTH" in built.prompt
    assert "- region VARCHAR distinct=3" in built.prompt


def _wide_db(path: Path, distinct_tail: int) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE sales (category VARCHAR, amount INTEGER)")
        for i in range(25):
            con.execute("INSERT INTO sales VALUES ('ZONE ONE', ?)", [i])
        for i in range(distinct_tail):
            con.execute("INSERT INTO sales VALUES (?, ?)", [f"ZONE ONE EXT {i:04d}", i])
    finally:
        con.close()
    return path


def test_capped_column_sends_no_hint_and_reports_a_floor(tmp_path: Path) -> None:
    db = _wide_db(tmp_path / "wide.duckdb", 600)
    built = _prompt(db, _CAPPED_Q, {"sales"})
    assert "FILTER HINTS" not in built.prompt
    assert "ZONE ONE" not in built.prompt
    assert f"- category VARCHAR distinct=>={INDEX_COLUMN_CAP}" in built.prompt
    assert "DMSHINT_" not in built.envelope_prompt


def test_column_exactly_at_the_cap_is_complete(tmp_path: Path) -> None:
    # 1 + (cap - 1) distinct values: exactly the cap, so nothing is hidden.
    db = _wide_db(tmp_path / "edge.duckdb", INDEX_COLUMN_CAP - 1)
    built = _prompt(db, "total sales for ZONE ONE EXT 0007", {"sales"})
    assert "- sales.category = ZONE ONE EXT 0007" in built.prompt
    assert f"- category VARCHAR distinct={INDEX_COLUMN_CAP}" in built.prompt


def test_one_past_the_cap_is_capped(tmp_path: Path) -> None:
    db = _wide_db(tmp_path / "over.duckdb", INDEX_COLUMN_CAP)
    built = _prompt(db, "total sales for ZONE ONE EXT 0007", {"sales"})
    assert "FILTER HINTS" not in built.prompt
    assert f"distinct=>={INDEX_COLUMN_CAP}" in built.prompt


def test_row_budget_cut_read_is_no_index_not_a_partial_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_executor.schema_context as mod

    monkeypatch.setattr(mod, "INDEX_ROW_BUDGET", 2)
    db = _region_db(tmp_path / "sales.duckdb")
    built = _prompt(db, _NORTH_Q, {"sales"})
    assert "FILTER HINTS" not in built.prompt
    assert "distinct=1" not in built.prompt


# -- product lane: POST /v1/chat/ask ----------------------------------------


@dataclass
class _Cortex:
    asks: list[AskRequest] = field(default_factory=list)
    computes: list[dict[str, Any]] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        self.computes.append({"question": question, **kwargs})
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run_vi01")

    def ledger_append(self, req: Any) -> Any:
        raise AssertionError("no ledger append on an abstain")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        raise AssertionError("the generative abstain must not fall through to ask")


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
            issued_at="2026-10-01T00:00:00+00:00",
            expires_at="2026-10-01T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _seed_inventory(db: Path, rows: list[tuple[str, int]]) -> None:
    """Demo warehouse, then ``inventory.category`` replaced by the repro rows."""
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("DELETE FROM inventory")
        for n, (category, qty) in enumerate(rows):
            con.execute(
                "INSERT INTO inventory VALUES (?, 'WH-A', ?, 10, 1.5, 'SUP-01', ?, NULL)",
                [f"SKU-{n:04d}", qty, category],
            )
    finally:
        con.close()


def _chat_ask(
    db: Path, minter: ManifestMinter, question: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("CORTEX_API_KEY", "k-vi01")
    cortex = _Cortex()
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    granted = [t for t in exe.grantable_tables(space_id=_FINANCE) if t in DEMO_TABLES]
    assert "inventory" in granted
    assert build_space_index(db, _FINANCE, set(granted), "duckdb") == ""
    settings_mod.get_settings.cache_clear()
    try:
        app = create_app()
        app.state.ask_service = exe
        app.state.cortex = cortex
        res = TestClient(app).post(
            "/v1/chat/ask",
            json={"question": question, "session_id": "ses_vi01", "space_id": _FINANCE},
        )
    finally:
        settings_mod.get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    assert cortex.asks == []
    assert len(cortex.computes) == 1
    return body


def _assert_abstain_with_context(body: dict[str, Any]) -> str:
    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["rows"] == []
    assert body["values"] == []
    assert str(body["text"]).strip()
    context = body["schema_context"]
    assert isinstance(context, str)
    return context


def test_chat_ask_prefix_value_envelope_reports_the_real_distinct_count(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [("NORTH", i) for i in range(30)]
    rows += [("NORTH EAST", i) for i in range(30)]
    rows += [("SOUTH", i) for i in range(40)]
    db = tmp_path / "wh.duckdb"
    _seed_inventory(db, rows)
    body = _chat_ask(db, minter, _NORTH_Q.replace("sales", "quantity"), monkeypatch)
    context = _assert_abstain_with_context(body)
    assert "- category VARCHAR distinct=3" in context
    assert "- category VARCHAR distinct=1 " not in context
    assert "- inventory.category = DMSHINT_" in context
    assert "NORTH" not in context
    assert "NORTH" not in str(body["text"])


def test_chat_ask_capped_column_envelope_has_no_hint(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [("ZONE ONE", i) for i in range(25)]
    rows += [(f"ZONE ONE EXT {i:04d}", i) for i in range(600)]
    db = tmp_path / "wh.duckdb"
    _seed_inventory(db, rows)
    body = _chat_ask(db, minter, _CAPPED_Q.replace("sales", "quantity"), monkeypatch)
    context = _assert_abstain_with_context(body)
    assert f"- category VARCHAR distinct=>={INDEX_COLUMN_CAP}" in context
    assert "inventory.category" not in context.split("FILTER HINTS")[-1]
    assert "ZONE ONE" not in context
