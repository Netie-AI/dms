"""PROVENANCE-CARRY-01: Cortex provenance.layer / metric_id reach the envelope.

Cortex at prove pin 279cbd85 sends ``route: "sql"`` for every SQL layer and
the L2 ``L2_VALIDATED`` badge maps to ``query_skill`` on the wire
(CortexOS/api/contract_routes.py:101). Only ``provenance.layer`` tells
query_skill, session, governed_metric and generated apart. These tests fail on
6f7139a3: AskResponse dropped both fields and the envelope never carried them.
Payloads go through the pinned ``cortex_contract.answer.Answer`` and the
generated client model, the same path ``CortexClient.ask`` takes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cortex_client.generated.models.answer import Answer as GenAnswer
from cortex_client.models import AskResponse
from cortex_contract.answer import Answer, Badge, Provenance
from dms_executor import map_ask_response_to_envelope

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_curated  # noqa: E402
import test_score_mask_01 as harness  # noqa: E402

SQL = "SELECT SUM(net_value) AS revenue FROM sales"
SERVED_KEYS = {"served_provenance_layer", "served_metric_id"}

# (layer, wire badge, route, sql, metric_id): every layer value CortexOS
# answer_engine.py / contract_routes.py emit at 279cbd85.
LAYERS = [
    ("certified", Badge.CERTIFIED, "sql", SQL, None),
    ("governed_metric", Badge.GOVERNED_METRIC, "sql", SQL, "sales_by_value"),
    ("query_skill", Badge.QUERY_SKILL, "sql", SQL, "sales_by_value"),
    ("session", Badge.SESSION, "sql", SQL, "sales_by_value"),
    ("generated", Badge.QUERY_SKILL, "sql", SQL, None),
    ("catalog", Badge.ABSTAIN, "sql", None, None),
    ("rag", Badge.ABSTAIN, "rag", None, None),
    ("abstain", Badge.ABSTAIN, "needs_clarification", None, None),
    ("refused", Badge.ABSTAIN, "refused", None, None),
    ("blocked", Badge.BLOCKED, "blocked", None, None),
    ("engine", Badge.ABSTAIN, "sql", None, None),
]


def _wire(
    layer: str, badge: Badge, route: str, sql: str | None, metric_id: str | None
) -> dict[str, Any]:
    answer = Answer(
        answer="Revenue was 100." if sql else "I can't answer that.",
        sql_used=sql,
        audit_id="aud_prov",
        route=route,
        rows=[{"revenue": 100.0}] if sql else [],
        provenance=Provenance(layer=layer, badge=badge, metric_id=metric_id),
    )
    return GenAnswer.from_dict(answer.model_dump(mode="json")).to_dict()


def _env(raw: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import dms_executor

    monkeypatch.setattr(dms_executor, "datetime_now", lambda: "2024-06-15T00:00:00Z")
    resp = AskResponse.model_validate(raw)
    return map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_p")


@pytest.mark.parametrize(
    ("layer", "badge", "route", "sql", "metric_id"), LAYERS, ids=[row[0] for row in LAYERS]
)
def test_provenance_layer_and_metric_id_reach_envelope(
    layer: str,
    badge: Badge,
    route: str,
    sql: str | None,
    metric_id: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _env(_wire(layer, badge, route, sql, metric_id), monkeypatch)
    assert env["served_provenance_layer"] == layer
    if metric_id:
        assert env["served_metric_id"] == metric_id
    else:
        assert "served_metric_id" not in env


def test_query_skill_and_generated_differ_only_by_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _env(_wire("query_skill", Badge.QUERY_SKILL, "sql", SQL, None), monkeypatch)
    l2 = _env(_wire("generated", Badge.QUERY_SKILL, "sql", SQL, None), monkeypatch)
    assert (skill["route"], skill["badge"]) == (l2["route"], l2["badge"])
    assert skill["served_provenance_layer"] == "query_skill"
    assert l2["served_provenance_layer"] == "generated"


@pytest.mark.parametrize(
    ("layer", "badge", "route", "sql", "metric_id"), LAYERS, ids=[row[0] for row in LAYERS]
)
def test_missing_provenance_changes_nothing_else(
    layer: str,
    badge: Badge,
    route: str,
    sql: str | None,
    metric_id: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _wire(layer, badge, route, sql, metric_id)
    bare = json.loads(json.dumps(raw))
    bare["provenance"].pop("layer")
    bare["provenance"].pop("metric_id", None)
    with_prov = _env(raw, monkeypatch)
    without = _env(bare, monkeypatch)
    assert not SERVED_KEYS & set(without)
    added = set(with_prov) - set(without)
    assert added and added <= SERVED_KEYS
    assert {k: v for k, v in with_prov.items() if k not in SERVED_KEYS} == without
    assert with_prov["text"] == without["text"]
    assert with_prov["badge"] == without["badge"]
    assert with_prov["abstained"] == without["abstained"]
    assert with_prov["rows"] == without["rows"]


def test_case_record_carries_served_provenance() -> None:
    env = {"badge": "L1_GOVERNED_METRIC", "rows": [], "text": "x"}
    plain = score_curated._case_record("q1", "OK", "", env, "OK", None, "answer")
    assert set(plain) == set(harness.RECORD_KEYS)
    env |= {"served_provenance_layer": "query_skill", "served_metric_id": "sales_by_value"}
    rec = score_curated._case_record("q1", "OK", "", env, "OK", None, "answer")
    assert rec["served_provenance_layer"] == "query_skill"
    assert rec["served_metric_id"] == "sales_by_value"
    assert set(rec) - SERVED_KEYS == set(plain)


def test_live_case_record_file_carries_served_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harness._open_round(monkeypatch, tmp_path)
    monkeypatch.setenv("DMS_SCORE_CASE_DIR", str(tmp_path / "case_records"))
    db = harness._oracle_db(tmp_path / "oracle.duckdb")
    harness._install_http(
        monkeypatch,
        harness._by_question(
            {
                "badge": "L1_GOVERNED_METRIC",
                "abstained": False,
                "sql_used": harness.SKU_SQL,
                "rows": harness._gold(db),
                "route": "sql",
                "served_provenance_layer": "query_skill",
                "served_metric_id": "sku_count",
            }
        ),
    )
    score_curated.live("http://127.0.0.1:9", 1.0, db)
    rows = harness._record_rows(harness._report(tmp_path))
    hit = next(row for row in rows if row["id"] == harness.SKU_ID)
    assert hit["served_provenance_layer"] == "query_skill"
    assert hit["served_metric_id"] == "sku_count"
    others = [row for row in rows if row["id"] != harness.SKU_ID]
    assert others and all(not SERVED_KEYS & set(row) for row in others)
