"""MASK-VALUES-01 / dms#303. values and unknown envelope keys are masked.

Synthetic values only. HTTP is mocked. No network, no keys, no live model.
Each test fails on 87a94978 on its own assertion. Record files are deleted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.pii import fail_closed_mask_envelope, mask_payload
from dms_executor.envelope import build_answer_envelope

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_curated  # noqa: E402
from score_curated import live, load_pack, merge_pack_questions  # noqa: E402

DOB = "1990-01-15"
EMAIL = "buyer.mask@example.com"
_START = "2024-06-15"
_TZ = "UTC"


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _values() -> list[dict[str, str]]:
    return [
        {"id": "v_dob", "value": DOB, "label": "when"},
        {"id": "v_mail", "value": EMAIL, "label": "who"},
    ]


def _debug() -> dict[str, dict[str, str]]:
    return {"raw": {"dob": DOB, "email": EMAIL}}


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


def _oracle_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'mask-values-01')")
    finally:
        con.close()
    return path


def _health() -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
        "engine_as_of": _START,
        "engine_as_of_after": _START,
        "engine_timezone": _TZ,
        "engine_timezone_after": _TZ,
    }


def _planted_answer() -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "text": "no",
        "rows": [],
        "values": _values(),
        "debug": _debug(),
    }


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "records"))

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        del json_body, timeout
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Resp(_health())
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            return _Resp(_planted_answer())
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _unlink_records(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        path.unlink()


def test_live_values_and_unknown_key_are_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DOB and email in values and debug.raw stay out of the served envelope and the record.

    Fails on 87a94978: problems == [] is
    ['served values dob', 'served values dob token', 'record values dob',
    'record values dob token', 'debug.raw email', 'debug.raw dob',
    'debug.raw token', 'record file'].
    """
    served = build_answer_envelope(
        answer_id="ans_mask_values",
        text="Listed.",
        badge="L2_VALIDATED",
        sql_used=None,
        rows=[{"label": "listed"}],
        values=_values(),
        audit_id="aud_mask_values",
        as_of="2026-10-01T00:00:00Z",
        ask_mode="live",
    )
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(monkeypatch, tmp_path)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        path = Path(str(report["case_record"]))
        file_text = path.read_text(encoding="utf-8")
        record_env = json.loads(file_text.splitlines()[0])["envelope"]
        served_values = json.dumps(served["values"])
        record_values = json.dumps(record_env.get("values"))
        debug_raw = json.dumps((record_env.get("debug") or {}).get("raw"))
        problems: list[str] = []
        if served.get("abstained") is not False:
            problems.append("served abstained")
        if DOB in served_values:
            problems.append("served values dob")
        if "DMSMASK_dob_" not in served_values:
            problems.append("served values dob token")
        if DOB in record_values:
            problems.append("record values dob")
        if "DMSMASK_dob_" not in record_values:
            problems.append("record values dob token")
        if EMAIL in debug_raw:
            problems.append("debug.raw email")
        if DOB in debug_raw:
            problems.append("debug.raw dob")
        if "DMSMASK_dob_" not in debug_raw or "DMSMASK_email_" not in debug_raw:
            problems.append("debug.raw token")
        if DOB in file_text or EMAIL in file_text:
            problems.append("record file")
        assert report["n"] == _pack_n()
        assert problems == []
    finally:
        _unlink_records(tmp_path)


def test_key_not_on_safe_list_is_scanned() -> None:
    """A key that is not a known safe envelope key is scanned, including nested lists.

    Fails on 87a94978: 1990-01-15 and buyer.mask@example.com are still in trace_blob.
    """
    planted = {
        "answer_id": "ans_mask_values",
        "engine_as_of": _START,
        "trace_blob": {"raw": [{"note": DOB}, {"note": EMAIL}]},
    }
    got = fail_closed_mask_envelope(planted)
    blob = json.dumps(got["trace_blob"])
    assert got["engine_as_of"] == _START
    assert DOB not in blob and EMAIL not in blob
    assert "DMSMASK_dob_" in blob and "DMSMASK_email_" in blob


def test_remask_leaves_values_and_unknown_keys_unchanged() -> None:
    """A second mask leaves values, debug.raw, and the clock unchanged.

    Fails on 87a94978: the second mask still holds 1990-01-15 and the email.
    """
    planted = {
        "answer_id": "ans_mask_values",
        "badge": "ABSTAIN",
        "abstained": True,
        "text": "no",
        "rows": [],
        "values": _values(),
        "debug": _debug(),
        "engine_as_of": _START,
    }
    once = fail_closed_mask_envelope(planted)
    twice = fail_closed_mask_envelope(once)
    payload = mask_payload(values=_values())
    again = mask_payload(values=payload["values"])
    blob = json.dumps({"envelope": twice, "values": again["values"]})
    assert twice == once
    assert again["values"] == payload["values"]
    assert DOB not in blob and EMAIL not in blob
    assert twice["engine_as_of"] == _START
