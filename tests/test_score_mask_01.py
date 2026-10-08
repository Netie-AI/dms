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
    import dms_executor.demo_warehouse as warehouse

    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setattr(score_curated, "round_date_label", lambda _before, _after: None)
    # Same before/after. #321 opens the round from this clock, then asks.
    monkeypatch.setattr(
        warehouse,
        "_ENGINE_CLOCK",
        {
            "engine_as_of": "2024-06-15",
            "engine_as_of_after": "2024-06-15",
            "engine_timezone": "UTC",
            "engine_timezone_after": "UTC",
        },
    )


def _install_http(monkeypatch: pytest.MonkeyPatch, responder):
    calls: list[dict[str, object]] = []

    def fake(method: str, url: str, **kwargs):
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Ok(
                {
                    "status": "ok",
                    "engine_as_of": "2024-06-15",
                    "engine_as_of_after": "2024-06-15",
                    "engine_timezone": "UTC",
                    "engine_timezone_after": "UTC",
                }
            )
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


SECRET_EMAIL = "ada.lovelace@example.com"
RECORD_KEYS = (
    "id",
    "outcome",
    "reason",
    "served_provider",
    "served_model",
    "envelope",
    "rows",
    "oracle_verdict",
    "engine_date",
    "clock_source",
)


def test_live_case_record_lines_match_n_and_stay_masked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fails on dd4162ec: live() writes no per-case JSONL."""
    _open_round(monkeypatch, tmp_path)
    rec_dir = tmp_path / "case_records"
    monkeypatch.setenv("DMS_SCORE_CASE_DIR", str(rec_dir))
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "text": f"reach {SECRET_EMAIL}",
                "served_provider": "groq",
                "served_model": "llama-3.3-70b",
                "rows": [
                    {"sku_count": "DMSMASK_unknown_01", "note": SECRET_EMAIL},
                ],
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    named = report.get("case_record")
    assert isinstance(named, str) and named
    path = Path(named)
    assert path.is_file()
    assert path.parent == rec_dir
    assert path.name == f"score_cases_{report['run_id']}_{report['commit_sha']}.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == report["n"] == _pack_n()
    blob = path.read_text(encoding="utf-8")
    assert SECRET_EMAIL not in blob
    from dms_core.pii import fail_closed_mask_envelope, is_mask_token

    rows = [json.loads(ln) for ln in lines]
    outcomes = {row["outcome"] for row in rows}
    assert "INVALID" in outcomes
    assert "ABSTAIN" in outcomes
    assert {row["id"] for row in rows} == {row["id"] for row in report["cases"]}
    for row in rows:
        assert set(row) == set(RECORD_KEYS)
        assert row["oracle_verdict"] == row["outcome"]
        assert "engine_date" in row
        stored = row["envelope"]
        if stored is None:
            assert row["rows"] == []
            continue
        again = fail_closed_mask_envelope(stored)
        assert again.get("text") == stored.get("text")
        assert again.get("rows") == stored.get("rows")
        assert row["rows"] == stored.get("rows")
    hit = next(row for row in rows if row["id"] == SKU_ID)
    assert hit["outcome"] == "INVALID"
    assert hit["reason"] == "masked_compare:sku_count"
    assert hit["served_provider"] == "groq"
    assert hit["served_model"] == "llama-3.3-70b"
    assert hit["rows"][0]["sku_count"] == "DMSMASK_unknown_01"
    assert is_mask_token(hit["rows"][0]["note"])
    assert SECRET_EMAIL not in json.dumps(hit)


def test_live_round_without_case_record_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fails on dd4162ec: a scored round with no record file stays unlabeled."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(
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
    if hasattr(score_curated, "write_case_records"):
        monkeypatch.setattr(score_curated, "write_case_records", lambda *_a, **_k: None)
    code = live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = _report(tmp_path)
    assert report["round_label"] == "INVALID"
    assert "case record file missing" in text
    assert report["wrong"] == 0
    assert report["n"] == report["total"] == _pack_n()
    assert report["oracle_error"] > 0
    assert report["passed"] is False
    assert code == EXIT_FAIL
    named = report.get("case_record")
    assert isinstance(named, str) and named
    assert Path(named).parent == tmp_path
    assert not Path(named).is_file()


def _record_rows(report: dict[str, object]) -> list[dict[str, object]]:
    named = report.get("case_record")
    if not isinstance(named, str):
        return []
    path = Path(named)
    if not path.is_file():
        return []
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def test_live_case_record_mask_payload_stays_fixed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mask_payload again leaves every record line unchanged."""
    from dms_core.pii import is_mask_token, mask_payload

    _open_round(monkeypatch, tmp_path)
    monkeypatch.setenv("DMS_SCORE_CASE_DIR", str(tmp_path / "case_records"))
    db = _oracle_db(tmp_path / "oracle.duckdb")
    served_text = f"reach {SECRET_EMAIL}"
    served_rows = [{"sku_count": "DMSMASK_unknown_01", "note": SECRET_EMAIL}]
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "text": served_text,
                "rows": served_rows,
            }
        ),
    )
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    assert report["n"] == _pack_n()
    rows = _record_rows(report)
    blob = ""
    named = report.get("case_record")
    if isinstance(named, str) and Path(named).is_file():
        blob = Path(named).read_text(encoding="utf-8")
    masked = mask_payload(text=served_text, rows=served_rows)
    token = masked["rows"][0]["note"]
    problems: list[str] = []
    if not is_mask_token(token) or SECRET_EMAIL in blob or token not in blob:
        problems.append("unmasked value in a record line")
    for row in rows:
        env = row.get("envelope")
        stored = env if isinstance(env, dict) else {}
        again = mask_payload(
            text=str(stored.get("text") or ""),
            rows=list(row.get("rows") or []),
            values=list(stored.get("values") or []),
            sources=list(stored.get("contributing_sources") or []),
            chart=stored.get("chart"),
            sql_used=stored.get("sql_used"),
        )
        if (
            again["text"] != stored.get("text")
            or again["rows"] != row.get("rows")
            or again["sql_used"] != stored.get("sql_used")
        ):
            problems.append("mask_payload changed a record line")
            break
    if not rows:
        problems.append("mask_payload: no record lines")
    assert problems == []


def test_live_case_record_outcome_counts_match_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Line count is n. File outcomes match the round, including INVALID, ABSTAIN, WRONG."""
    from collections import Counter

    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    question = _sku_question()
    finance = _finance_space()
    ops = str(load_pack(score_curated.DEFAULT_PACK)["spaces"]["ops"])

    def responder(_method: str, _url: str, body: dict[str, object] | None):
        if not isinstance(body, dict) or body.get("question") != question:
            return _Ok(_abstain())
        if body.get("space_id") == finance:
            return _Ok(
                {
                    "badge": "L0_CERTIFIED",
                    "abstained": False,
                    "sql_used": SKU_SQL,
                    "rows": [{"sku_count": "DMSMASK_unknown_01"}],
                }
            )
        if body.get("space_id") == ops:
            return _Ok(
                {
                    "badge": "L0_CERTIFIED",
                    "abstained": False,
                    "sql_used": SKU_SQL,
                    "rows": [{"sku_count": 0}],
                }
            )
        return _Ok(_abstain())

    _install_http(monkeypatch, responder)
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    cases = report["cases"]
    assert isinstance(cases, list)
    round_counts = Counter(str(row["verdict"]) for row in cases)
    file_counts = Counter(str(row["outcome"]) for row in _record_rows(report))
    assert (len(list(file_counts.elements())), file_counts) == (
        report["n"],
        round_counts,
    )
    assert {"INVALID", "ABSTAIN", "WRONG"} <= set(file_counts)


def test_live_unreadable_commit_is_record_unidentified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable commit sha makes the round INVALID record_unidentified."""
    _open_round(monkeypatch, tmp_path)
    monkeypatch.setattr(score_curated, "merge_commit_sha", lambda: "unknown", raising=False)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    assert (report.get("reason"), report.get("round_label"), report.get("n")) == (
        "record_unidentified",
        "INVALID",
        _pack_n(),
    )


def test_live_record_write_failure_is_record_write_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed write or flush makes the round INVALID record_write_failed."""
    _open_round(monkeypatch, tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("flush failed")

    monkeypatch.setattr(score_curated, "write_case_records", _boom, raising=False)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    assert (report.get("reason"), report.get("round_label"), report.get("n")) == (
        "record_write_failed",
        "INVALID",
        _pack_n(),
    )


SECRET_DOB = "1985-03-22"
_SCAN_SKIP = frozenset({".duckdb", ".wal"})


def _file_sigs(roots: list[Path]) -> dict[Path, int]:
    sigs: dict[Path, int] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                sigs[path.resolve()] = path.stat().st_mtime_ns
    return sigs


def _files_written(roots: list[Path], before: dict[Path, int]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix in _SCAN_SKIP:
                continue
            if before.get(path.resolve()) != path.stat().st_mtime_ns:
                found.append(path)
    return found


def test_live_written_files_omit_raw_dob_and_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No file the round writes holds the raw DOB or the raw email."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    served_text = f"born {SECRET_DOB} reach {SECRET_EMAIL}"
    served_rows = [
        {
            "sku_count": "DMSMASK_unknown_01",
            "dob": SECRET_DOB,
            "email": SECRET_EMAIL,
        }
    ]
    _install_http(
        monkeypatch,
        _by_question(
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "sql_used": SKU_SQL,
                "text": served_text,
                "rows": served_rows,
            }
        ),
    )
    roots = [tmp_path, ROOT / ".tmp", ROOT / "logs"]
    before = _file_sigs(roots)
    live("http://127.0.0.1:9", 1.0, db)
    written = _files_written(roots, before)
    report = _report(tmp_path)
    named = report.get("case_record")
    if isinstance(named, str):
        extra = Path(named)
        if extra.is_file() and extra.resolve() not in {p.resolve() for p in written}:
            written.append(extra)
    leaks: list[str] = []
    blob = ""
    for path in written:
        text = path.read_text(encoding="utf-8", errors="replace")
        blob += text
        if SECRET_DOB in text or SECRET_EMAIL in text:
            leaks.append(path.name)
    assert (
        leaks,
        SECRET_DOB in blob,
        SECRET_EMAIL in blob,
        "DMSMASK_dob_" in blob,
        "DMSMASK_email_" in blob,
    ) == ([], False, False, True, True)


def _printed_case_record(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("case_record="):
            return line.split("=", 1)[1]
    return ""


def _jsonl_count(path: Path | None) -> int:
    if path is None or not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def test_live_unset_case_record_dir_is_scratch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unset DMS_CASE_RECORD_DIR writes `.tmp/score_cases` and is not a baseline."""
    _open_round(monkeypatch, tmp_path)
    monkeypatch.delenv("DMS_SCORE_DIR", raising=False)
    monkeypatch.delenv("DMS_CASE_RECORD_DIR", raising=False)
    monkeypatch.delenv("DMS_SCORE_CASE_DIR", raising=False)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = json.loads((ROOT / ".tmp" / "score_curated.json").read_text(encoding="utf-8"))
    named = report.get("case_record")
    path = Path(named) if isinstance(named, str) else None
    scratch = ROOT / ".tmp" / "score_cases"
    assert (
        report.get("record_path_scratch"),
        report.get("baseline_eligible"),
        report.get("record_path_in_repo"),
        path == scratch / path.name if path else None,
        _printed_case_record(text),
        str(path) if path else "",
        _jsonl_count(path),
        report.get("n"),
    ) == (
        True,
        False,
        False,
        True,
        str(path) if path else "",
        str(path) if path else "",
        _pack_n(),
        _pack_n(),
    )


def test_live_in_repo_case_record_dir_is_not_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A relative path or an absolute in-repo path is record_path_in_repo."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    dirs = (
        ".tmp/rel_case_records",
        str((ROOT / ".tmp" / "abs_case_records").resolve()),
    )
    got = []
    for raw in dirs:
        monkeypatch.setenv("DMS_CASE_RECORD_DIR", raw)
        live("http://127.0.0.1:9", 1.0, db)
        text = capsys.readouterr().out
        report = _report(tmp_path)
        named = report.get("case_record")
        path = Path(named) if isinstance(named, str) else None
        got.append(
            (
                report.get("record_path_in_repo"),
                report.get("baseline_eligible"),
                report.get("record_path_scratch"),
                bool(path and path.is_absolute() and str(path) == _printed_case_record(text)),
                report.get("n"),
            )
        )
    assert got == [
        (True, False, False, True, _pack_n()),
        (True, False, False, True, _pack_n()),
    ]


def test_live_absolute_out_of_tree_record_is_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absolute dir outside the work tree is eligible and the printed path is the file."""
    _open_round(monkeypatch, tmp_path)
    outside = tmp_path / "out_records"
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(outside))
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = _report(tmp_path)
    named = report.get("case_record")
    path = Path(named) if isinstance(named, str) else None
    printed = _printed_case_record(text)
    assert (
        report.get("baseline_eligible"),
        report.get("record_path_scratch"),
        report.get("record_path_in_repo"),
        printed,
        str(path) if path else "",
        bool(path and path.is_file() and path.is_absolute() and path.parent == outside),
        _jsonl_count(path),
        report.get("n"),
    ) == (
        True,
        False,
        False,
        str(path) if path else "",
        str(path) if path else "",
        True,
        _pack_n(),
        _pack_n(),
    )


def test_live_scratch_reason_blocks_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unset DMS_CASE_RECORD_DIR adds record_path_scratch and is not eligible."""
    _open_round(monkeypatch, tmp_path)
    monkeypatch.delenv("DMS_CASE_RECORD_DIR", raising=False)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    reasons = report.get("baseline_ineligible_reasons")
    assert (
        report.get("baseline_eligible"),
        reasons,
        report.get("baseline_eligible") is (reasons == []),
        report.get("n"),
    ) == (False, ["record_path_scratch"], True, _pack_n())


def test_live_in_repo_reason_blocks_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative record dir adds record_path_in_repo and is not eligible."""
    _open_round(monkeypatch, tmp_path)
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", ".tmp/rel_case_records")
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    reasons = report.get("baseline_ineligible_reasons")
    assert (
        report.get("baseline_eligible"),
        reasons,
        report.get("baseline_eligible") is (reasons == []),
        report.get("n"),
    ) == (False, ["record_path_in_repo"], True, _pack_n())


def test_live_write_failed_reason_blocks_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed write adds record_write_failed and is not eligible."""
    _open_round(monkeypatch, tmp_path)
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "out_records"))

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("flush failed")

    monkeypatch.setattr(score_curated, "write_case_records", _boom, raising=False)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    reasons = report.get("baseline_ineligible_reasons")
    assert (
        report.get("baseline_eligible"),
        reasons,
        report.get("baseline_eligible") is (reasons == []),
        report.get("n"),
    ) == (False, ["record_write_failed"], True, _pack_n())


def test_live_unidentified_reason_blocks_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable commit adds record_unidentified and is not eligible."""
    _open_round(monkeypatch, tmp_path)
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "out_records"))
    monkeypatch.setattr(score_curated, "merge_commit_sha", lambda: "unknown", raising=False)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    reasons = report.get("baseline_ineligible_reasons")
    assert (
        report.get("baseline_eligible"),
        reasons,
        report.get("baseline_eligible") is (reasons == []),
        report.get("n"),
    ) == (False, ["record_unidentified"], True, _pack_n())


def test_live_round_invalid_reason_blocks_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A round INVALID puts that reason in the list and is not eligible."""
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "out_records"))

    def _no_http(*_args: object, **_kwargs: object) -> None:
        raise OSError("no health")

    monkeypatch.setattr(score_curated, "score_http", _no_http)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    live("http://127.0.0.1:9", 1.0, db)
    report = _report(tmp_path)
    reasons = report.get("baseline_ineligible_reasons")
    assert (
        report.get("baseline_eligible"),
        reasons,
        report.get("baseline_eligible") is (reasons == []),
        report.get("reason"),
        report.get("n"),
    ) == (False, ["engine_date_unread"], True, "engine_date_unread", 0)


def test_live_baseline_eligible_agrees_with_empty_reasons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """baseline_eligible is true only when baseline_ineligible_reasons is empty."""
    _open_round(monkeypatch, tmp_path)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install_http(monkeypatch, _by_question(_abstain()))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "out_records"))
    live("http://127.0.0.1:9", 1.0, db)
    eligible = _report(tmp_path)
    monkeypatch.delenv("DMS_CASE_RECORD_DIR")
    live("http://127.0.0.1:9", 1.0, db)
    scratch = _report(tmp_path)
    pairs = (
        (
            row.get("baseline_eligible"),
            row.get("baseline_ineligible_reasons"),
        )
        for row in (eligible, scratch)
    )
    agreed = []
    listed = []
    for flag, reasons in pairs:
        agreed.append(flag is (reasons == []))
        listed.append(reasons)
    assert (agreed, listed) == ([True, True], [[], ["record_path_scratch"]])


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
    """52-pack --ab. Order is OK/LAYER/ABSTAIN/WRONG/ORACLE_ERROR.

    Exact quint is the score-pack allowlist on top of the ten base
    metrics (0/23/29/0/0). Generative layer 26->28 and wrong 15->13:
    the two chemicals-list asks are a grammar list (LAYER), not the
    invented stock_value aggregate (WRONG).
    """
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    main(["--ab"])
    report = json.loads((tmp_path / "ab_gen01.json").read_text(encoding="utf-8"))
    exact = report["exact_match"]
    gen = report["generative"]
    keys = ("ok", "layer", "abstain", "wrong", "oracle_error")
    exact_counts = [exact[key] for key in keys]
    gen_counts = [gen[key] for key in keys]
    assert exact_counts == [0, 23, 29, 0, 0]
    assert gen_counts == [0, 28, 11, 13, 0]
    assert exact["n"] == 52
    assert gen["n"] == 52
