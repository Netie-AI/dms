"""CCA-06 — stack-free eval corpus. Fail on one WRONG. Precision-on-answered=100%.

Product-path live warehouse SEA/class dims are not landed (demo suppliers have
no country column). This job scores binders + live_ask abstain. It does not
POST a real Cortex engine and must not be named as customer-envelope accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

from dms_executor.cascade_orchestrator import (
    cascade_allows_l0,
    run_constraint_cascade,
)
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

_CORPUS = Path(__file__).resolve().parent / "fixtures" / "cca_eval" / "corpus.json"


def _load() -> dict:
    return json.loads(_CORPUS.read_text(encoding="utf-8"))


def _wrong(case_id: str, detail: str) -> None:
    raise AssertionError(f"WRONG {case_id}: {detail}")


def test_cca_eval_corpus_precision_on_answered() -> None:
    data = _load()
    answered = 0
    correct = 0
    covered = 0
    for case in data["cases"]:
        cid = case["id"]
        q = case["question"]
        expect = case["expect"]
        applies, trace = run_constraint_cascade(
            q,
            class_encodings=case.get("class_encodings"),
            landed_class_dim=case.get("landed_class_dim"),
            region_members=case.get("region_members"),
            landed_geo_dim=case.get("landed_geo_dim"),
        )
        if expect == "skip_cascade":
            covered += 1
            if applies:
                _wrong(cid, "cascade ran on an ordinary SKU ask")
            continue
        covered += 1
        if not applies:
            _wrong(cid, "cascade did not apply")
        if expect == "abstain":
            if cascade_allows_l0(trace):
                _wrong(cid, "abstain golden allowed L0")
            env = build_answer_envelope(
                answer_id=f"a_{cid}",
                text="PLANTED 8953922.60",
                badge="L0_CERTIFIED",
                sql_used="SELECT 1 AS sales_value_myr",
                rows=[{"sales_value_myr": 8953922.60}],
                cascade_path=True,
                constraint_trace=trace,
                ask_mode="live",
            )
            assert_envelope_valid(env)
            if env["badge"] != "ABSTAIN" or env["rows"] or env["values"]:
                _wrong(cid, "abstain golden shipped numbers")
            if "8953922.60" in env["text"]:
                _wrong(cid, "abstain golden kept planted total")
            continue
        if expect == "certified_trace":
            answered += 1
            if not cascade_allows_l0(trace):
                _wrong(cid, "certified golden did not certify priors")
            if [c["type"] for c in trace] != ["sense", "asset_class", "geo"]:
                _wrong(cid, f"certified golden stages { [c['type'] for c in trace] }")
            if any(c["status"] != "CERTIFIED" for c in trace):
                _wrong(cid, "certified golden had a non-CERTIFIED stage")
            correct += 1
            continue
        _wrong(cid, f"unknown expect {expect}")
    precision = 1.0 if answered == 0 else correct / answered
    coverage = covered / len(data["cases"])
    assert precision == data["precision_on_answered_must_be"], (
        f"precision-on-answered={precision} coverage={coverage}"
    )
    assert coverage == 1.0
    assert answered == 1
    assert correct == answered
