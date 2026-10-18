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
    cached_value_index,
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


# -- the index is the complete raw set or it is nothing -------------------


def test_a_value_the_pii_pass_drops_leaves_no_hint_for_its_prefix(tmp_path: Path) -> None:
    """Rule 12: a dropped longer value must not leave a hint on its prefix.

    The PII pass removes 'NORTH Tan Wei Ming' from the stored set. If the
    remaining NORTH and SOUTH were treated as the whole column, a question
    naming the dropped value would be hinted to the wrong subset (NORTH).
    """
    db = tmp_path / "pii.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE sales (region VARCHAR, amount INTEGER)")
        for value, count in (("NORTH", 30), ("NORTH Tan Wei Ming", 30), ("SOUTH", 40)):
            for i in range(count):
                con.execute("INSERT INTO sales VALUES (?, ?)", [value, i])
    finally:
        con.close()
    ctx = _prompt(db, "total sales for NORTH Tan Wei Ming", {"sales"})
    assert "sales.region = NORTH" not in ctx.prompt
    assert "region VARCHAR distinct=2" not in ctx.prompt


def _spaced_rows() -> list[tuple[str, int]]:
    """'NORTH' and 'NORTH ' are two stored values; whitespace folding makes one."""
    rows = [("NORTH", 1) for _ in range(30)]
    rows += [("NORTH ", 1) for _ in range(20)]
    rows += [("SOUTH", 1) for _ in range(40)]
    return rows


def test_values_that_fold_to_one_leave_no_hint(tmp_path: Path) -> None:
    """Rule 12: the completeness proof counts raw values, not folded ones.

    Three raw values, two after folding. Hinting ``= NORTH`` with
    ``distinct=2`` filters 30 rows where the column holds 50 that read as
    NORTH, so the column sends no hint and claims no distinct count.
    """
    db = tmp_path / "spaced.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE sales (region VARCHAR, amount INTEGER)")
        con.executemany("INSERT INTO sales VALUES (?, ?)", _spaced_rows())
        assert con.execute("SELECT COUNT(DISTINCT region) FROM sales").fetchone() == (3,)
    finally:
        con.close()
    built = _prompt(db, "total sales for NORTH", {"sales"})
    assert "sales.region = NORTH" not in built.prompt
    assert "FILTER HINTS" not in built.prompt
    assert "distinct=2" not in built.prompt
    assert "DMSHINT_" not in built.envelope_prompt


def test_chat_ask_values_that_fold_to_one_envelope_has_no_hint(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "wh.duckdb"
    _seed_inventory(db, _spaced_rows())
    body = _chat_ask(db, minter, "total quantity for NORTH", monkeypatch)
    context = _assert_abstain_with_context(body)
    assert "inventory.category = DMSHINT_" not in context
    assert "- category VARCHAR distinct=2" not in context
    assert "NORTH" not in context
    assert "NORTH" not in str(body["text"])


# -- the build reads beside live asks, inside a budget ----------------------


def test_a_serving_read_during_the_build_is_not_queued_behind_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build scans whole columns; it must not hold the ingest lease to do it.

    The build is parked inside a distinct count. A serving read, the lease an
    ask's SQL runs on, opens and answers beside it. Under the exclusive ingest
    lease it waited out ``DMS_LAKE_SERVING_LEASE_WAIT_S`` and raised
    ServingLeaseCap, which the ask path renders as an abstain.
    """
    import threading

    import dms_executor.schema_context as sc
    from dms_executor.demo_warehouse import connect_serving

    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "0.5")
    monkeypatch.setattr(sc, "INDEX_SCAN_BUDGET_S", 30.0, raising=False)
    db = _region_db(tmp_path / "sales.duckdb")
    inside = threading.Event()
    release = threading.Event()
    real_count = sc._count_distinct

    def _parked(con: Any, table: str, column: str) -> int | None:
        inside.set()
        release.wait(10.0)
        return real_count(con, table, column)

    monkeypatch.setattr(sc, "_count_distinct", _parked)
    stamps: list[str] = []
    worker = threading.Thread(
        target=lambda: stamps.append(build_space_index(db, _SPACE, {"sales"}, "duckdb"))
    )
    worker.start()
    try:
        assert inside.wait(10.0)
        con = connect_serving(db)
        try:
            rows = con.execute(
                "SELECT SUM(amount) FROM sales WHERE region = 'NORTH EAST'"
            ).fetchall()
        finally:
            con.close()
    finally:
        release.set()
        worker.join(15.0)
    assert rows == [(435,)]
    assert stamps == [""]
    schema, stamp = lookup_schema(db, {"sales"}, "duckdb", _SPACE)
    assert stamp == ""
    built = build_schema_context(_NORTH_Q, schema, grantable={"sales"}, space_id=_SPACE)
    assert "- sales.region = NORTH EAST" in built.prompt
    assert "- region VARCHAR distinct=3" in built.prompt


def test_a_scan_past_its_budget_is_interrupted_and_the_build_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A distinct scan that will not finish is cut at its budget, not at the end.

    ``a_slow`` is a view over ten billion generated rows, read first. Its scan
    is interrupted, it indexes nothing, and the build still reaches ``sales``
    and finishes well inside the build budget.
    """
    import threading
    import time

    import dms_executor.schema_context as sc

    monkeypatch.setattr(sc, "INDEX_SCAN_BUDGET_S", 0.2, raising=False)
    db = _region_db(tmp_path / "slow.duckdb")
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "CREATE VIEW a_slow AS SELECT CAST(r % 3 AS VARCHAR) AS zone "
            "FROM range(10000000000) t(r)"
        )
    finally:
        con.close()
    stamps: list[str] = []
    started = time.monotonic()
    worker = threading.Thread(
        target=lambda: stamps.append(
            build_space_index(db, _SPACE, {"a_slow", "sales"}, "duckdb")
        ),
        daemon=True,
    )
    worker.start()
    worker.join(10.0)
    assert not worker.is_alive(), "the slow scan was not interrupted"
    assert time.monotonic() - started < sc.INDEX_BUILD_BUDGET_S
    assert stamps == [""]
    schema, stamp = lookup_schema(db, {"a_slow", "sales"}, "duckdb", _SPACE)
    assert stamp == ""
    built = build_schema_context(
        "total sales for NORTH EAST", schema, grantable={"a_slow", "sales"}, space_id=_SPACE
    )
    assert "- sales.region = NORTH EAST" in built.prompt
    held = cached_value_index(_SPACE)
    assert "a_slow.zone" not in held
    assert held["sales.region"] == ("NORTH", "NORTH EAST", "SOUTH")


def test_capped_columns_do_not_spend_the_row_budget_of_later_tables(tmp_path: Path) -> None:
    """Twenty wide columns must not starve a small table of its hints."""
    db = tmp_path / "wide.duckdb"
    con = duckdb.connect(str(db))
    try:
        cols = ", ".join(f"c{i} VARCHAR" for i in range(20))
        con.execute(f"CREATE TABLE a_big ({cols})")
        con.execute(
            "INSERT INTO a_big SELECT "
            + ", ".join(f"'v{i}_' || CAST(r AS VARCHAR)" for i in range(20))
            + " FROM range(600) t(r)"
        )
        con.execute("CREATE TABLE z_small (region VARCHAR, amount INTEGER)")
        con.execute("INSERT INTO z_small VALUES ('NORTH', 1), ('SOUTH', 2)")
    finally:
        con.close()
    ctx = _prompt(db, "total amount for SOUTH in z_small", {"a_big", "z_small"})
    assert "z_small.region = SOUTH" in ctx.prompt
    assert "region VARCHAR distinct=2" in ctx.prompt


def test_description_mask_is_compiled_once_per_schema_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One matcher per build; rebuilding it per description cost about 5x."""
    import dms_executor.schema_context as sc

    calls = {"n": 0}
    real = sc._compile_mask

    def counting(pairs: Any) -> Any:
        calls["n"] += 1
        return real(pairs)

    monkeypatch.setattr(sc, "_compile_mask", counting)
    db = _region_db(tmp_path / "r.duckdb")
    schema = {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": f"t{t}",
                "description": "Net revenue in ringgit",
                "columns": [
                    {
                        "name": f"c{c}",
                        "type": "varchar",
                        "values": ["NORTH", "SOUTH"],
                        "description": "Region code",
                    }
                    for c in range(5)
                ],
            }
            for t in range(4)
        ],
    }
    build_schema_context("net revenue by region", schema)
    assert calls["n"] == 1
    assert db.exists()
