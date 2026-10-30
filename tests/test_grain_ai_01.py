"""GRAIN-AI-01. No skip, no xfail. CI runs this file inside ``pytest tests/``."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_executor.grain_ai import (
    bind_grain_transport,
    map_question_to_grains,
    reset_grain_transport,
)
from dms_executor.ontology import (
    SUPPLY_CHAIN_GRAINS,
    Ontology,
    Refusal,
    detect_supply_chain_grains,
    missing_join_for_ungranted,
    try_compile_multi_grain,
)

ROOT = Path(__file__).resolve().parents[1]
GRAIN_AI = ROOT / "packages" / "executor" / "dms_executor" / "grain_ai.py"
QUESTION = "How many visits did each clinician handle?"
BANNED = frozenset({"sku", "supplier", "plant", "lane", "day"})


class _BoomGrains:
    def __iter__(self):  # noqa: ANN204
        raise AssertionError("SUPPLY_CHAIN_GRAINS")

    def __contains__(self, item: object) -> bool:
        raise AssertionError("SUPPLY_CHAIN_GRAINS")


class _Script:
    def __init__(self, build: Callable[[str, int], dict[str, Any]]) -> None:
        self.build = build
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        user = json.loads(body["messages"][1]["content"])
        return self.build(str(user["question"]), int(body["reading"]))


def _span(question: str, grain: str, snippet: str) -> dict[str, Any]:
    start = question.find(snippet)
    assert start >= 0
    return {
        "grain": grain,
        "start": start,
        "end": start + len(snippet),
        "text": snippet,
    }


def _clinic_body() -> dict[str, Any]:
    return {
        "status": "proposed",
        "served": False,
        "vendor_sha": "clinic-fixture",
        "terms": [
            {
                "name": "Visit",
                "table": "visits",
                "description": "one clinic visit",
                "attributes": [],
                "key": ["visit_id"],
            },
            {
                "name": "Clinician",
                "table": "clinicians",
                "description": "a clinician",
                "attributes": [],
                "key": ["clinician_id"],
            },
        ],
        "attributes": [],
        "inferred_fks": [],
        "declared_fks": [],
        "joins": [
            {
                "left_table": "visits",
                "left_column": "clinician_id",
                "right_table": "clinicians",
                "right_column": "clinician_id",
            }
        ],
        "grains": [
            {"name": "visit", "object": "Visit"},
            {"name": "clinician", "object": "Clinician"},
        ],
        "measures": [],
        "pii": [],
    }


def _approve_clinic() -> None:
    stored = store_proposal(_clinic_body(), created_by="steward")
    store = ontology_data_store()
    for item in stored["items"]:
        store.approve(item["id"], actor="steward")
    ward = store_proposal(
        {
            "status": "proposed",
            "served": False,
            "vendor_sha": "clinic-ward",
            "terms": [],
            "grains": [{"name": "ward", "object": "Ward"}],
        },
        created_by="steward",
    )
    assert ward["items"]
    assert {row["status"] for row in ward["items"]} == {"proposed"}


def _agree(question: str, _reading: int) -> dict[str, Any]:
    return {
        "grains": ["visit", "clinician"],
        "evidence": [
            _span(question, "visit", "visits"),
            _span(question, "clinician", "clinician"),
        ],
        "confidence": 0.93,
        "reading": "counts visits for each clinician",
    }


@contextmanager
def _bound(monkeypatch: pytest.MonkeyPatch, script: _Script) -> Iterator[None]:
    monkeypatch.setenv("DMS_GRAIN_AI", "1")
    token = bind_grain_transport(script)
    try:
        yield
    finally:
        reset_grain_transport(token)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


def test_clinic_visits_get_grains_without_code_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approve_clinic()
    monkeypatch.setattr("dms_executor.ontology.SUPPLY_CHAIN_GRAINS", _BoomGrains())
    script = _Script(_agree)
    with _bound(monkeypatch, script):
        got = map_question_to_grains(QUESTION, space_id="sp", session_id="se")
    assert set(got["grains"]) == {"visit", "clinician"}
    assert "ward" not in got["grains"]
    assert got["reading"] == "counts visits for each clinician"
    for span in got["evidence"]:
        assert QUESTION[span["start"] : span["end"]] == span["text"]
    assert len(script.bodies) == 2
    assert [body["reading"] for body in script.bodies] == [1, 2]
    assert script.bodies[0]["role"] == "grain"
    assert "model" not in script.bodies[0]
    blob = json.dumps(script.bodies).casefold()
    assert not (set(re.findall(r"[a-z]+", blob)) & BANNED)
    systems = [body["messages"][0]["content"] for body in script.bodies]
    assert systems[0] != systems[1]
    assert detect_supply_chain_grains(QUESTION) == ()
    catalog = Ontology.from_store().supply_chain_catalog()
    assert "visit" in catalog
    assert "ward" not in catalog
    assert not (set(catalog) & BANNED)
    outside = _Script(lambda _q, _r: {"grains": ["ward"], "evidence": [], "confidence": 0.99})
    token = bind_grain_transport(outside)
    try:
        refused = map_question_to_grains(QUESTION)
    finally:
        reset_grain_transport(token)
    assert refused["reconfirm_yes"] == "yes"
    assert refused["abstained"] is True


def test_new_path_has_no_hard_coded_grain_literals() -> None:
    src = GRAIN_AI.read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words = set(re.findall(r"[A-Za-z]+", node.value.casefold()))
            found = words & BANNED
            if found:
                hits.append((node.value[:80], sorted(found)))
    assert hits == []
    assert "SUPPLY_CHAIN_GRAINS" not in src
    assert "detect_supply_chain_grains" not in src
    assert "_GRAIN_RES" not in src


def test_split_reading_returns_reconfirm(monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_clinic()

    def build(question: str, reading: int) -> dict[str, Any]:
        if reading == 1:
            return _agree(question, reading)
        return {
            "grains": ["visit"],
            "evidence": [_span(question, "visit", "visits")],
            "confidence": 0.95,
            "reading": "counts visits",
        }

    with _bound(monkeypatch, _Script(build)):
        got = try_compile_multi_grain(None, None, QUESTION, space_id="sp", session_id="se")
    assert isinstance(got, dict)
    assert not isinstance(got, Refusal)
    assert got["reconfirm_yes"] == "yes"
    assert isinstance(got["reconfirm"], dict)
    assert got["reconfirm"]["why"] == "the two grain readings did not name the same grains"
    assert got["abstained"] is True
    assert got["badge"] == "ABSTAIN"
    assert got["values"] == []
    assert got.get("sql_used") in (None, "")


def test_low_confidence_returns_reconfirm(monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_clinic()

    def build(question: str, reading: int) -> dict[str, Any]:
        payload = _agree(question, reading)
        payload["confidence"] = 0.2
        return payload

    with _bound(monkeypatch, _Script(build)):
        got = map_question_to_grains(QUESTION)
    assert got["reconfirm_yes"] == "yes"
    assert got["reconfirm"]["why"] == "the grain reading is not confident enough to choose"
    assert got["abstained"] is True
    assert "grains" not in got


def test_understand_question_plug_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_clinic()

    def fake(question: str, examples: Any = None) -> dict[str, Any]:
        assert examples is None
        return {
            "question": "HEARD " + question,
            "examples": [],
            "sources": ["plug"],
            "reading": None,
        }

    monkeypatch.setattr("dms_executor.grain_ai.load_understand_question", lambda: fake)

    def build(question: str, _reading: int) -> dict[str, Any]:
        payload = _agree(question, _reading)
        if _reading == 1:
            payload = dict(payload)
            payload.pop("reading", None)
        else:
            payload = dict(payload)
            payload["reading"] = "   "
        return payload

    script = _Script(build)
    with _bound(monkeypatch, script):
        got = map_question_to_grains(QUESTION)
    assert set(got["grains"]) == {"visit", "clinician"}
    assert got["reading"] is None
    user = json.loads(script.bodies[0]["messages"][1]["content"])
    assert user["question"] == "HEARD " + QUESTION


def test_flag_off_byte_equal_to_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_GRAIN_AI", raising=False)
    monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)

    def boom(_body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("grain reader")

    token = bind_grain_transport(boom)
    try:
        assert detect_supply_chain_grains("shipping cost by SKU and plant") == (
            "sku",
            "plant",
        )
        assert detect_supply_chain_grains("stock value by supplier and SKU") == (
            "sku",
            "supplier",
        )
        assert detect_supply_chain_grains("SKU count by category") == ("sku",)
        assert detect_supply_chain_grains("shipment cost by SKU and day") == (
            "sku",
            "day",
        )
        assert detect_supply_chain_grains("shipment cost by SKU and lane") == (
            "sku",
            "lane",
        )
        onto = Ontology()
        catalog = onto.supply_chain_catalog()
        assert tuple(catalog) == SUPPLY_CHAIN_GRAINS
        assert "not declared on this warehouse" in (catalog["lane"]["missing"] or "")
        assert missing_join_for_ungranted("ungranted", ["sku", "plant"]) == (
            "missing_join: no granted join path for grain plant. "
            "Declare the link on a granted table, "
            "or ask for a measure defined at that grain."
        )
        assert try_compile_multi_grain(onto, None, "shipping cost by SKU and plant") is None
    finally:
        reset_grain_transport(token)
