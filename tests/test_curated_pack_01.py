"""PROVE-CURATED-DIAG-01: score-pack expect:l0 questions are pack metrics.

On 4525b4c the base registry is ten metrics and cq_sku_count is not one of
them, so lookup_pack_metric returns None. Refuse and abstain rows stay
misses. Not a grant change. Not a verified-query seed. Not COMPLETE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.demo_pack import PACK_METRICS, lookup_pack_metric

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
)
_SPACES = {
    "finance": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "ops": "dddddddd-dddd-dddd-dddd-dddddddddddd",
}


def _pack() -> list[dict[str, Any]]:
    data = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    assert len(questions) == 52
    return questions


def _grants(space: str) -> set[str]:
    entry = DEMO_SPACE_GRANTS[_SPACES[space]]
    return set(entry[1])


def test_cq_sku_count_exact_match_on_finance_grants() -> None:
    case = next(row for row in _pack() if row["id"] == "cq_sku_count")
    hit = lookup_pack_metric(str(case["question"]), grantable=_grants("finance"))
    assert hit is not None
    assert hit.metric_id == "cq_sku_count"
    assert hit.tables == ("inventory",)
    assert "FROM inventory" in hit.sql


def test_every_expect_l0_hits_under_its_space_grants() -> None:
    misses: list[str] = []
    for case in _pack():
        if str(case.get("expect") or "").lower() != "l0":
            continue
        hit = lookup_pack_metric(str(case["question"]), grantable=_grants(str(case["space"])))
        if hit is None:
            misses.append(str(case["id"]))
    assert misses == []


def test_refuse_and_abstain_stay_misses() -> None:
    hits: list[str] = []
    for case in _pack():
        expect = str(case.get("expect") or "").lower()
        if expect not in {"refuse", "abstain"}:
            continue
        hit = lookup_pack_metric(str(case["question"]), grantable=_grants(str(case["space"])))
        if hit is not None:
            hits.append(str(case["id"]))
    assert hits == []


def test_registry_is_the_fixture_not_one_id() -> None:
    ids = {m.metric_id for m in PACK_METRICS}
    assert "cq_sku_count" in ids
    assert "cq_sales_top5_value" in ids
    assert "cq_audit_overdue" in ids
    assert "trap_how_full_synonym" not in ids
    assert "trap_alerts_ungranted" not in ids
    assert len(PACK_METRICS) > 10
