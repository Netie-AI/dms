"""A1-02: one provider 429/5xx must not abort a Mini-Dev run.

Measured on the BIRD Mini-Dev local run (dms PR #312): an unguarded ``ask_fn``
call in ``score_cases`` meant a single 429 raised out of the loop and threw away
every graded question. The run had to go through an outside wrapper. These plant
a real ``httpx.HTTPStatusError`` - the exception ``_ask_live`` raises - and assert
on the graded report and the printed summary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bird_minidev import (  # noqa: E402
    PROVIDER_ATTEMPTS,
    PROVIDER_ERROR,
    SYNTHETIC,
    asked_text,
    duck_gold_fn,
    load_minidev_source,
    minidev_self_check,
    print_summary,
    run_minidev,
)


def _http_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://dms.test/v1/chat/ask")
    resp = httpx.Response(status, request=req, headers=headers or {})
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


def _questions() -> list[dict[str, Any]]:
    questions, _meta = load_minidev_source(str(SYNTHETIC), dest_dir=Path("/nonexistent"))
    return questions


def _right_answer(gold: Any, question: dict[str, Any]) -> dict[str, Any]:
    rows, err = gold(str(question.get("SQL") or ""))
    if err:
        return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"}
    return {"badge": "L0_CERTIFIED", "abstained": False, "rows": rows, "text": "ok"}


def _run(ask_fn: Any, questions: list[dict[str, Any]], tmp_path: Path) -> dict[str, Any]:
    slept: list[float] = []
    code, report, err = run_minidev(
        questions,
        ask_fn=ask_fn,
        gold_fn=duck_gold_fn(),
        data_meta={"bytes": 0, "n": len(questions)},
        env={"DMS_SCORE_DIR": str(tmp_path)},
        write=False,
        sleep=slept.append,
    )
    assert err is None and report is not None
    report["_slept"] = slept
    return report


@pytest.mark.parametrize("status", [429, 503])
def test_one_provider_error_still_grades_the_rest(
    status: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    questions = _questions()
    gold = duck_gold_fn()
    answers = {asked_text(q, with_evidence=False): _right_answer(gold, q) for q in questions}
    unlucky = asked_text(questions[0], with_evidence=False)
    calls: dict[str, int] = {}

    def ask_fn(question: str) -> dict[str, Any]:
        calls[question] = calls.get(question, 0) + 1
        if question == unlucky:
            raise _http_error(status)
        return answers[question]

    report = _run(ask_fn, questions, tmp_path)
    cases = report["cases"]
    summary = report["summary"]

    assert len(cases) == len(questions)
    assert cases[0]["verdict"] == PROVIDER_ERROR
    assert cases[0]["provider_error"] == f"http_{status}"
    assert calls[unlucky] == PROVIDER_ATTEMPTS  # bounded retry, then give up
    assert len(report["_slept"]) == PROVIDER_ATTEMPTS - 1
    # Everything else graded exactly as it would have without the error.
    rest = [c["verdict"] for c in cases[1:]]
    assert rest and all(v in {"OK", "GOLD_ERROR"} for v in rest), rest
    assert summary["provider_error"] == 1
    graded = [c for c in cases if c["verdict"] not in {"GOLD_ERROR", PROVIDER_ERROR}]
    assert summary["n"] == len(graded)
    assert summary["right"] == len([c for c in cases if c["verdict"] == "OK"])
    assert summary["wrong"] == 0
    assert summary["ex_on_answered_pct"] == 100.0

    print_summary(summary, limit=None, total=len(questions))
    out = capsys.readouterr().out
    assert "PROVIDER_ERROR=1 (excluded from n)" in out
    assert "provider errors=1 after retry" in out


def test_retry_that_succeeds_is_graded_normally(tmp_path: Path) -> None:
    questions = _questions()
    gold = duck_gold_fn()
    first = asked_text(questions[0], with_evidence=False)
    failed = {"n": 0}

    def ask_fn(question: str) -> dict[str, Any]:
        if question == first and failed["n"] == 0:
            failed["n"] += 1
            raise _http_error(429, {"retry-after": "7"})
        item = next(q for q in questions if asked_text(q, with_evidence=False) == question)
        return _right_answer(gold, item)

    report = _run(ask_fn, questions, tmp_path)
    assert report["cases"][0]["verdict"] == "OK"
    assert report["cases"][0]["retries"] == 1
    assert report["summary"]["provider_error"] == 0
    assert report["summary"]["retries"] == 1
    assert report["_slept"] == [7.0]  # Retry-After honoured


def test_non_provider_error_still_stops_the_run(tmp_path: Path) -> None:
    def ask_fn(question: str) -> dict[str, Any]:
        raise _http_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        _run(ask_fn, _questions(), tmp_path)


def test_self_check_covers_provider_error() -> None:
    assert minidev_self_check() == []
