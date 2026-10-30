"""No header-only schema_context on the Insights POST (Refs #276, #297; proposed F118).

With ``DMS_SCHEMA_CONTEXT=1`` a prompt that lists no table went out as
``DIALECT`` / ``SCHEMA`` / ``MEASURES``. Cortex abstains ``schema_context_empty``
on that without calling the model; an armed all-flags run hit it on 20 of 41
Insights posts.

Why the table set was empty for a granted Space: ``read_catalog`` opened the
lake on the ingest (exclusive) lease. That lease waits for every serving cursor
to come back and gives up after ``DMS_LAKE_INGEST_WAIT_S``; the error was
swallowed and the catalog came back with no dataset. Any serving read in
flight (another ask, a Space's index build) was enough. The catalog read now
takes the read-only serving lease, still filtered by the grant. A prompt that
still names no table (zero granted tables) is not sent at all.

Loopback fakes only: the Insights HTTP client is replaced, submit runs the SQL
on a temporary demo warehouse.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.client import CortexClient
from cortex_client.compute import _insights_body
from cortex_client.insights import apply_schema_context
from cortex_contract.execution import QueryResult
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor.demo_warehouse import connect_serving, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.ontology import demo_ontology
from dms_executor.schema_context import (
    SCHEMA_CONTEXT_FIELD,
    prepare_generate_context,
    read_catalog,
)
from test_drop_route_fields_01 import _KEY, _LOOPBACK, _Executing, _minter, _Ranked

QUESTION = "how many SKUs per category"
# What the fake model returns. Distinct SKUs, so the gold below is a different
# computation (Python over raw rows), not the same statement run twice.
MODEL_SQL = (
    "SELECT category, COUNT(DISTINCT sku) AS sku_count FROM inventory "
    "GROUP BY category ORDER BY category"
)
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
FINANCE_GRANT = {"locations", "inventory", "transactions", "suppliers"}
_SECTIONS = ("SCHEMA", "JOINS", "FILTER HINTS", "MEASURES")
_HEAD = re.compile(r"-\s+(\S+)(?:\s+(?:reason|description)=.*)?")


def _tables_in(prompt: str) -> list[str]:
    """Table heads under ``SCHEMA``, by Cortex's head rule. Independent of DMS code."""
    section = ""
    out: list[str] = []
    for raw in prompt.split("\n"):
        line = raw.strip()
        if line in _SECTIONS:
            section = line
            continue
        if section == "SCHEMA":
            head = _HEAD.fullmatch(line)
            if head:
                out.append(head.group(1))
    return out


def _schema_prompts(body: dict[str, Any]) -> list[str]:
    """Every schema_context the body carries: top level and inside ``ontology``."""
    found = []
    if SCHEMA_CONTEXT_FIELD in body:
        found.append(str(body[SCHEMA_CONTEXT_FIELD]))
    onto = body.get("ontology")
    if isinstance(onto, dict) and SCHEMA_CONTEXT_FIELD in onto:
        found.append(str(onto[SCHEMA_CONTEXT_FIELD]))
    return found


def _gold(db: Path) -> dict[str, int]:
    """SKU count per category from the raw inventory rows, counted in Python."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        pairs = con.execute("SELECT category, sku FROM inventory").fetchall()
    finally:
        con.close()
    return dict(Counter(str(cat) for cat, _sku in set(pairs)))


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_CCA_CASCADE",
        "DMS_STRICT_MODEL",
        "DMS_STRICT_PROVIDER",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    # A held serving lease makes an ingest-lease open wait this long, then fail.
    monkeypatch.setenv("DMS_LAKE_INGEST_WAIT_S", "0.5")
    db = tmp_path / "no_empty.duckdb"
    dw._SEEDED.discard(str(db.resolve()))
    ensure_demo_warehouse(db)
    return db


def test_zero_granted_tables_sends_no_schema_context_key(warehouse: Path) -> None:
    """Acceptance 2: no granted table, so the Insights body has no schema_context."""
    onto = demo_ontology(warehouse)
    ctx = prepare_generate_context(
        {},
        question=QUESTION,
        serving=warehouse,
        grantable=set(),
        ontology=onto,
        dialect="duckdb",
        space_id=None,
    )
    assert SCHEMA_CONTEXT_FIELD not in ctx, ctx.get(SCHEMA_CONTEXT_FIELD)
    onto_block: dict[str, Any] = {"source": "demo"}
    if SCHEMA_CONTEXT_FIELD in ctx:
        onto_block[SCHEMA_CONTEXT_FIELD] = ctx[SCHEMA_CONTEXT_FIELD]
    body = _insights_body(QUESTION, session_id="ses_no_empty", space_id=None, ontology=onto_block)
    assert _schema_prompts(body) == []
    # The wire guard holds on its own: a header-only prompt handed straight to
    # the body builder is dropped, and a prompt that lists a table still goes out.
    header_only = "DIALECT: duckdb\nSCHEMA\nMEASURES\n- sku_count grain=product expression=COUNT(*)"
    body = _insights_body(
        QUESTION,
        session_id="ses_no_empty",
        space_id=None,
        ontology={"source": "demo", SCHEMA_CONTEXT_FIELD: header_only},
    )
    assert _schema_prompts(body) == []
    assert SCHEMA_CONTEXT_FIELD not in apply_schema_context({}, header_only)
    assert SCHEMA_CONTEXT_FIELD not in apply_schema_context({}, "DIALECT: duckdb\nSCHEMA")
    listed = "DIALECT: duckdb\nSCHEMA\n- inventory reason=score:0.35\n- sku VARCHAR"
    assert apply_schema_context({}, listed)[SCHEMA_CONTEXT_FIELD] == listed


def test_catalog_lists_granted_tables_while_a_serving_read_is_in_flight(
    warehouse: Path,
) -> None:
    """Root cause: the catalog read queued behind serving cursors and came back empty."""
    held = connect_serving(warehouse)
    try:
        fingerprint, body = read_catalog(warehouse, set(FINANCE_GRANT), "duckdb")
    finally:
        held.close()
    names = sorted(str(item["name"]) for item in body["datasets"])
    assert fingerprint
    # Every granted table, and only granted tables (#297): shipments and alerts
    # exist in the warehouse and are not in the Finance grant.
    assert names == sorted(FINANCE_GRANT)


class _ServingSubmit(_Executing):
    """Runs the submitted SQL on a serving cursor, as a reader beside the held lease."""

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_no_empty_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_serving(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_no_empty_sql", output={"rows": rows})


def _ask_over_http(
    db: Path, monkeypatch: pytest.MonkeyPatch, space_id: str | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from fastapi.testclient import TestClient

    posts: list[dict[str, Any]] = []
    base = _Ranked(MODEL_SQL, "sku_count").client()

    class _Capture(base):  # type: ignore[misc, valid-type]
        def post(
            self,
            url: str,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            if str(url).rstrip("/").endswith("/v1/insights"):
                posts.append(dict(json or {}))
            return super().post(url, json=json, headers=headers)

    monkeypatch.setattr("cortex_client.compute.httpx.Client", _Capture)
    cortex = _ServingSubmit(db, CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0))
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    app = create_app()
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    payload: dict[str, Any] = {
        "question": QUESTION,
        "session_id": "ses_no_empty",
        "ask_path": "generative",
    }
    if space_id:
        payload["space_id"] = space_id
    # A serving read in flight for the whole ask (a concurrent ask, or this
    # Space's index build). Before the fix the catalog read timed out behind it.
    held = connect_serving(db)
    try:
        res = TestClient(app).post("/v1/chat/ask", json=payload)
    finally:
        held.close()
    assert res.status_code == 200, res.text
    return res.json(), posts


@pytest.mark.parametrize("space_id", [None, FINANCE], ids=["company", "finance"])
def test_chat_ask_sends_no_empty_schema_and_serves_gold(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch, space_id: str | None
) -> None:
    """Acceptance 1: POST /v1/chat/ask; every Insights body lists a granted table or no key."""
    gold = _gold(warehouse)
    assert gold
    env, posts = _ask_over_http(warehouse, monkeypatch, space_id)

    assert posts, "the ask never reached Insights"
    for body in posts:
        for prompt in _schema_prompts(body):
            tables = _tables_in(prompt)
            assert tables, f"header-only schema_context sent:\n{prompt}"
            # Within grants only (#297). Both scopes grant inventory; neither
            # grants alerts.
            assert "alerts" not in tables
            if space_id == FINANCE:
                assert set(tables) <= FINANCE_GRANT, tables
    # The flag is on and the catalog read now succeeds: the prompt is there.
    assert any(_schema_prompts(body) for body in posts)

    assert_envelope_valid(env)
    assert env.get("abstained") is not True, env.get("text")
    rows = env.get("rows") or []
    served = {str(row["category"]): int(row["sku_count"]) for row in rows}
    assert served == gold
    assert len(rows) == len(gold)
    text = str(env.get("text") or "")
    for category, count in gold.items():
        assert category in text, text
        assert str(count) in text, text
