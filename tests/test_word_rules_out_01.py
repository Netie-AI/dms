"""WORD-RULES-OUT-01. Flag default off. No phrase table in the linker. No skip."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from dms_executor.generative_ask import ontology_catalog
from dms_executor.keyword_routes import locked_measure
from dms_executor.measure_link import (
    REASON_TIE,
    REASON_UNRANKED,
    no_word_rules_enabled,
    rank_measure_link,
    reconfirm_for_reason,
)
from dms_executor.ontology import Ontology, demo_ontology
from dms_executor.semantic_retrieve import bind_plan, intent_slots, retrieve_short_context

_ROOT = Path(__file__).resolve().parents[1]
_GUARDED = (
    _ROOT / "packages" / "executor" / "dms_executor" / "semantic_retrieve.py",
    _ROOT / "packages" / "executor" / "dms_executor" / "generative_ask.py",
)
_QUESTION_NAMES = frozenset({"q", "qn", "question"})
# The phrases the keyword locker used. The paraphrase must not contain them.
_OLD = {
    "outbound_kg": ("quantity sold", "qty sold"),
    "sku_count": ("sku count", "how many sku", "how many unique sku"),
    "shipping_cost_myr": ("shipment cost", "shipping cost", "freight"),
    "utilisation_pct": (
        "capacity utilisation",
        "capacity utilization",
        "cold storage",
        "percent",
        "cctv",
        "camera",
    ),
    "below_reorder_lots": ("reorder",),
    "supplier_rank_score": ("rank", "ranking", "supplier"),
    "audit_overdue": ("audit", "overdue"),
    "stock_value_myr": (
        "expir",
        "chemical",
        "stock",
        "inventory",
        "worth",
        "spend",
    ),
    "outbound_value_myr": (
        "revenue",
        "selling",
        "categoty",
        "sales",
        "category",
        "categories",
    ),
}


def _is_question_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in _QUESTION_NAMES:
        return True
    if isinstance(node, ast.Attribute) and node.attr in {"lower", "casefold", "strip"}:
        return _is_question_expr(node.value)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"lower", "casefold", "strip"}:
            return _is_question_expr(node.func.value)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_is_question_expr(item) for item in node.values)
    return False


def _routing_hits(source: str) -> list[str]:
    """Comparisons against the question, and regex search/match on it."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        ):
            if any(_is_question_expr(item) for item in node.comparators):
                hits.append(f"in-question:{node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in {"search", "match", "fullmatch"}:
                continue
            if any(_is_question_expr(arg) for arg in node.args):
                hits.append(f"regex-question:{node.lineno}")
    return hits


def _demo() -> Ontology:
    return demo_ontology(Path("/no/such/dms-word-rules-lake"))


def _tokens(text: str) -> set[str]:
    return set("".join(ch if ch.isalnum() else " " for ch in text.casefold()).split())


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    assert no_word_rules_enabled() is False
    monkeypatch.setenv("DMS_NO_WORD_RULES", "0")
    assert no_word_rules_enabled() is False
    monkeypatch.setenv("DMS_NO_WORD_RULES", "true")
    assert no_word_rules_enabled() is False


def test_flag_off_does_not_rank_and_matches_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onto = _demo()
    question = "capacity utilisation by warehouse"
    context = {
        "measures": {
            "utilisation_pct": {"grain": "location", "description": "occupancy"},
            "sku_count": {"grain": "product", "description": "sku count"},
        },
        "columns": {"location": ["location_code"]},
        "objects": {"location": {"key": ["location_id"]}},
    }

    def _boom(_question: str, _docs: list[str], _embedder: object, **_kw: object) -> list:
        raise AssertionError("ranker called with the flag off")

    monkeypatch.setattr("dms_executor.verified_queries.rank_texts", _boom)
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    slots = intent_slots(question, onto, embedder=_boom)
    plan = bind_plan(question, context, ontology=onto, embedder=_boom)
    ctx = retrieve_short_context(question, warehouse=None, ontology=onto, embedder=_boom)
    catalog = json.dumps(ontology_catalog(onto), sort_keys=True)
    monkeypatch.setenv("DMS_NO_WORD_RULES", "0")
    assert intent_slots(question, onto) == slots
    assert bind_plan(question, context, ontology=onto) == plan
    again = retrieve_short_context(question, warehouse=None, ontology=onto)
    assert json.dumps(again, sort_keys=True, default=str) == json.dumps(
        ctx, sort_keys=True, default=str
    )
    assert json.dumps(ontology_catalog(onto), sort_keys=True) == catalog
    assert slots.get("measure") == "utilisation_pct"
    assert "closed circuit monitor" not in catalog
    assert reconfirm_for_reason(
        REASON_TIE, question=question, space_id=None, session_id=None
    ) is None


def test_paraphrase_reaches_each_old_measure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_NO_WORD_RULES", "1")
    onto = _demo()
    seen: set[str] = set()
    for name, forbiddens in _OLD.items():
        measure = onto.measures[name]
        assert measure.synonyms, name
        for phrase in measure.synonyms:
            hay = phrase.casefold()
            assert all(flag not in hay for flag in forbiddens), phrase
            link = rank_measure_link(phrase, onto, embedder=None)
            assert link.reason_code is None, (name, link)
            assert link.name == name
            assert link.ranked
            assert len(link.ranked) <= 3
            assert all(score > 0.5 for _item, score in link.ranked)
            seen.add(name)
    assert seen == set(_OLD)


def test_renamed_measure_links_from_its_own_synonym(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_NO_WORD_RULES", "1")
    onto = Ontology()
    onto.add_object("site", "sites", ["site_id"])
    onto.add_measure(
        "fill_ratio",
        "site",
        "SUM(f.load_kg)",
        description="site fill ratio",
        synonyms=("closed circuit monitor",),
    )
    link = rank_measure_link("closed circuit monitor", onto, embedder=None)
    assert link.name == "fill_ratio"
    assert link.reason_code is None
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    slots = intent_slots("closed circuit monitor", onto)
    assert "measure" not in slots
    assert locked_measure("closed circuit monitor") is None


def test_tie_and_low_score_reconfirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_NO_WORD_RULES", "1")
    onto = Ontology()
    onto.add_object("box", "boxes", ["box_id"])
    onto.add_measure("left_rule", "box", "SUM(f.a)", synonyms=("qxvmarker",))
    onto.add_measure("right_rule", "box", "SUM(f.b)", synonyms=("qxvmarker",))
    for index in range(4):
        onto.add_measure(f"decoy_{index}", "box", "SUM(f.n)", description=f"decoy {index}")
    tied = rank_measure_link("qxvmarker", onto, embedder=None)
    assert tied.name is None
    assert tied.reason_code == REASON_TIE
    env = reconfirm_for_reason(
        tied.reason_code, question="qxvmarker", space_id="s", session_id="n"
    )
    assert env is not None
    assert env["reconfirm"]["reason_code"] == REASON_TIE
    assert env["values"] == []
    assert env.get("sql_used") in (None, "")
    low = rank_measure_link("zzzzqqqq", onto, embedder=None)
    assert low.name is None
    assert low.reason_code == REASON_UNRANKED
    plan = bind_plan("zzzzqqqq", {"measures": {}}, ontology=onto)
    assert plan is not None
    assert plan.get("unsure") is True
    assert plan.get("reason_code") == REASON_UNRANKED
    assert "query_plan" not in plan


def test_embedder_links_without_shared_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_NO_WORD_RULES", "1")
    onto = Ontology()
    onto.add_object("site", "sites", ["site_id"])
    onto.add_measure("fill_ratio", "site", "SUM(f.load_kg)", description="qxv pile")
    onto.add_measure("other_rule", "site", "SUM(f.other_amt)", description="qxv decoy")
    question = "alpha beta gamma"

    def _embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for index, text in enumerate(texts):
            if index == 0 or text.startswith("fill ratio"):
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out

    asked = _tokens(question)
    for measure in onto.measures.values():
        body = " ".join((measure.name, measure.description, measure.expression))
        assert asked.isdisjoint(_tokens(body))
    link = rank_measure_link(question, onto, embedder=_embed)
    assert link.name == "fill_ratio"
    assert link.reason_code is None

    def _raise(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedder down")

    fallen = rank_measure_link("qxv pile", onto, embedder=_raise)
    assert fallen.name == "fill_ratio"


def test_rank_keeps_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_NO_WORD_RULES", "1")
    onto = Ontology()
    onto.add_object("box", "boxes", ["box_id"])
    for index in range(5):
        onto.add_measure(f"rule_{index}", "box", "SUM(f.n)", description=f"rule {index}")

    def _embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _text in texts]

    link = rank_measure_link("anything", onto, embedder=_embed)
    assert link.reason_code == REASON_TIE
    assert link.name is None
    assert len(link.ranked) == 3


def test_guard_catches_question_routing_and_files_are_clean() -> None:
    bad = "def f(qn):\n    if 'capacity' in qn:\n        return 1\n"
    assert _routing_hits(bad)
    bad_call = "def f(q):\n    return pat.search(q.lower())\n"
    assert _routing_hits(bad_call)
    tokens = (
        "import re\n"
        "def f(question):\n"
        "    return re.findall(r'[a-z0-9]+', (question or '').lower())\n"
    )
    assert _routing_hits(tokens) == []
    for path in _GUARDED:
        hits = _routing_hits(path.read_text(encoding="utf-8"))
        assert hits == [], (path.name, hits)
