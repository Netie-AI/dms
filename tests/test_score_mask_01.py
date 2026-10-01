"""SCORE-MASK-01 / dms#299: served masks and ask failures are not WRONG.

Every case goes through live() or --ab (main). HTTP is mocked. No unmask
flag and no side channel. These tests fail on dd4162ec: a masked compared
column was WRONG, a 429 was WRONG, and a transport error was WRONG.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_curated  # noqa: E402
from oracle_row_match import run_oracle_select  # noqa: E402
from score_curated import (  # noqa: E402
    EXIT_FAIL,
    live,
    load_oracles,
    load_pack,
    main,
    merge_pack_questions,
)

OVERMASK_STAR_KEY = "overmask_star:dms#284"

SKU_ID = "cq_sku_count"
SKU_SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
STAR_SQL = "SELECT * FROM transactions UNION ALL SELECT created_at FROM transactions"
CLOSED = frozenset(
    {"OK", "WRONG", "ABSTAIN", "LAYER", "RATE_LIMIT", "ORACLE_ERROR", "INVALID"}
)


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _sku_question() -> str:
    pack = load_pack(score_curated.DEFAULT_PACK)
    for case in pack["questions"]:
        if case["id"] == SKU_ID:
            return str(case["question"])
    raise AssertionError(SKU_ID)


def _oracle_db(path: Path) -> Path:
    import duckdb

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE inventory (sku VARCHAR)")
        con.execute("INSERT INTO inventory VALUES ('A'), ('B')")
    finally:
        con.close()
    return path


def _gold(db: Path) -> list[dict[str, object]]:
    sql = str(load_oracles()[SKU_ID]["sql"]).strip()
    assert sql == SKU_SQL
    rows, err = run_oracle_select(db, sql)
    assert err is None and rows, err
    return rows


def _abstain() -> dict[str, object]:
    return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"}


def _open_round(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A recorded engine date so live() judges. The product gate stays closed
    when no date is recorded; that case has its own test.
    """
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setattr(score_curated, "round_date_label", lambda _before, _after: None)


def _install_http(monkeypatch: pytest.MonkeyPatch, responder):
    calls: list[dict[str, object]] = []

    def fake(method: str, url: str, **kwargs):
        body = kwargs.get("json_body")
        if isinstance(body, dict):
            calls.append(body)
        return responder(method, url, body)

    monkeypatch.setattr(score_curated, "score_http", fake)
    return calls


class _Ok:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _finance_space() -> str:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return str(pack["spaces"]["finance"])


def _by_question(special: dict[str, object]):
    """Only the finance cq_sku_count ask. The ops copy shares the question text."""
    question = _sku_question()
    space = _finance_space()

    def responder(_method: str, _url: str, body: dict[str, object] | None):
        if (
            isinstance(body, dict)
            and body.get("question") == question
            and body.get("space_id") == space
        ):
            return _Ok(special)
        return _Ok(_abstain())

    return responder


def _report(tmp_path: Path) -> dict[str, object]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _case(report: dict[str, object], qid: str) -> dict[str, object]:
    cases = report["cases"]
    assert isinstance(cases, list)
    hit = next(row for row in cases if row["id"] == qid)
    assert hit["verdict"] in CLOSED
    return hit


def test_live_masked_compared_column_is_invalid_and_stays_in_n(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: the mask was WRONG, or the case was dropped from n."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "rows": [{"sku_count": "DMSMASK_unknown_01"}],
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    assert "cq_sku_count\tINVALID\t" in text
    assert "reason=masked_compare:sku_count" in text
    assert "cq_sku_count\tWRONG\t" not in text
    assert "cq_sku_count\tOK\t" not in text
    report = _report(tmp_path)
    hit = _case(report, SKU_ID)
    assert hit["verdict"] == "INVALID"
    assert hit["reason"] == "masked_compare:sku_count"
    assert report["total"] == _pack_n()
    assert len(report["cases"]) == report["total"]
    assert report["invalid"] >= 1
    assert report[OVERMASK_STAR_KEY] == 0


def test_live_masked_uncompared_column_judged_normally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: the extra mask turned a matching answer into WRONG."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    rows = [dict(row) for row in _gold(db)]
    assert "sku_count" in rows[0]
    rows[0]["notes"] = "DMSMASK_email_01"
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "rows": rows,
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    assert "cq_sku_count\tOK\t" in text
    assert "cq_sku_count\tWRONG\t" not in text
    assert "masked_compare:" not in text
    report = _report(tmp_path)
    hit = _case(report, SKU_ID)
    assert hit["verdict"] == "OK"
    assert hit["reason"] == ""
    assert report["wrong"] == 0
    assert report[OVERMASK_STAR_KEY] == 0


def test_live_star_union_masked_typed_date_counts_overmask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: overmask_star:dms#284 was not a count on the report."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": STAR_SQL,
                "rows": [
                    {
                        "sku_count": "DMSMASK_unknown_01",
                        "ts": "DMSMASK_dob_01",
                    }
                ],
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    assert f"{OVERMASK_STAR_KEY} 1" in text
    assert "cq_sku_count\tINVALID\t" in text
    assert "reason=masked_compare:sku_count" in text
    report = _report(tmp_path)
    hit = _case(report, SKU_ID)
    assert hit["verdict"] == "INVALID"
    assert hit["reason"] == "masked_compare:sku_count"
    assert report[OVERMASK_STAR_KEY] == 1
    assert "OVERMASK" not in CLOSED
    verdicts = {row["verdict"] for row in report["cases"]}
    assert verdicts <= CLOSED
    assert OVERMASK_STAR_KEY not in verdicts


def test_live_429_is_rate_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: HTTP 429 incremented WRONG."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")

    def responder(method: str, url: str, _body):
        request = httpx.Request(method, url)
        httpx.Response(429, request=request).raise_for_status()

    _install_http(monkeypatch, responder)
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = _report(tmp_path)
    assert report["wrong"] == 0
    assert "\tRATE_LIMIT\t" in text
    assert "\tWRONG\t" not in text
    assert report["total"] == _pack_n()
    assert report["rate_limit"] == report["total"]
    assert all(row["verdict"] == "RATE_LIMIT" for row in report["cases"])


def test_live_connection_error_is_abstain_ask_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: ConnectionError incremented WRONG."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")

    def responder(_method: str, _url: str, _body):
        raise ConnectionError("refused")

    _install_http(monkeypatch, responder)
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    assert report["wrong"] == 0
    assert "ask_error:ConnectionError" in capsys.readouterr().out
    assert report["total"] == _pack_n()
    assert all(
        row["verdict"] == "ABSTAIN" and row["reason"] == "ask_error:ConnectionError"
        for row in report["cases"]
    )


def test_live_timeout_is_abstain_ask_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: a timeout incremented WRONG."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")

    def responder(_method: str, _url: str, _body):
        raise TimeoutError("timed out")

    _install_http(monkeypatch, responder)
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    assert report["wrong"] == 0
    assert "ask_error:TimeoutError" in capsys.readouterr().out
    assert report["total"] == _pack_n()
    assert all(
        row["verdict"] == "ABSTAIN" and row["reason"] == "ask_error:TimeoutError"
        for row in report["cases"]
    )


def test_live_no_envelope_never_increments_wrong(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: the no-envelope branch did tallies['WRONG'] += 1."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")

    def responder(_method: str, _url: str, _body):
        raise RuntimeError("broke")

    _install_http(monkeypatch, responder)
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = _report(tmp_path)
    assert report["wrong"] == 0
    assert "ask_error:RuntimeError" in text
    assert "\tWRONG\t" not in text
    assert report["total"] == _pack_n()
    assert all(row["verdict"] != "WRONG" for row in report["cases"])
    assert all(row["reason"] == "ask_error:RuntimeError" for row in report["cases"])


def test_live_unmasked_match_stays_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    calls = _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "rows": _gold(db),
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = _report(tmp_path)
    assert "cq_sku_count\tOK\t" in text
    assert "cq_sku_count\tWRONG\t" not in text
    assert report["wrong"] == 0
    assert calls
    for body in calls:
        assert "unmask" not in json.dumps(body)
        assert set(body) <= {"question", "space_id", "ask_path"}
        assert body.get("unmask") is None


def test_live_unmasked_mismatch_stays_wrong(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "rows": [{"sku_count": 0}],
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    assert "cq_sku_count\tWRONG\t" in text
    assert "reason=rows_mismatch:" in text
    assert "masked_compare:" not in text
    report = _report(tmp_path)
    assert report["wrong"] >= 1


def test_live_demo_fallback_stays_wrong(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "demo_fallback_used": True,
                "sql_used": SKU_SQL,
                "rows": _gold(db),
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    assert "cq_sku_count\tWRONG\t" in text
    assert "reason=demo_fallback_used" in text
    report = _report(tmp_path)
    assert report["wrong"] >= 1


def test_live_grant_403_stays_abstain_not_rate_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")

    def responder(method: str, url: str, _body):
        request = httpx.Request(method, url)
        httpx.Response(403, request=request, text="grant refused").raise_for_status()

    _install_http(monkeypatch, responder)
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = _report(tmp_path)
    assert report["wrong"] == 0
    assert "GRANT_REFUSE" in text
    assert "\tRATE_LIMIT\t" not in text
    assert "\tWRONG\t" not in text


def test_live_without_engine_date_asks_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    db = _oracle_db(tmp_path / "oracle.duckdb")

    def boom(*_a, **_k):
        raise AssertionError("live() must not ask without an engine date")

    monkeypatch.setattr(score_curated, "score_http", boom)
    code = live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    assert code == EXIT_FAIL
    assert report["total"] == 0
    assert report["wrong"] == 0
    assert report["round_label"] == "INVALID"


def test_ab_offline_counts_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """52-pack --ab. Order is OK/LAYER/ABSTAIN/WRONG/ORACLE_ERROR."""
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    main(["--ab"])
    report = json.loads((tmp_path / "ab_gen01.json").read_text(encoding="utf-8"))
    exact = report["exact_match"]
    gen = report["generative"]
    keys = ("ok", "layer", "abstain", "wrong", "oracle_error")
    exact_counts = [exact[key] for key in keys]
    gen_counts = [gen[key] for key in keys]
    assert exact_counts == [0, 16, 36, 0, 0]
    assert gen_counts == [0, 26, 11, 15, 0]
    assert exact["n"] == 52
    assert gen["n"] == 52
