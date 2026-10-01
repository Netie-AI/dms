"""SERVING-PRECHECK-01 (Refs dms#303): a skipped serving precheck is not a baseline.

Every case goes through live(), which writes baseline_eligible. HTTP is mocked.
No OpenVault call, no real key, no scored network round.

Must-fail cases are eligible on fee155e4 (the parent ignores DMS_SERVING_PRECHECK)
and fail there on the tuple assert. A complete record over the tables the pack
oracle SQL names stays eligible. Those names are the parse result. Correct SQL
that names no table is the same ineligible reason.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_curated  # noqa: E402
from score_curated import (  # noqa: E402
    live,
    load_oracles,
    load_pack,
    merge_pack_questions,
)

try:
    from score_curated import (  # noqa: E402
        SERVING_HASH_ALG,
        SERVING_INODE,
        SERVING_MTIME,
        SERVING_PATH,
        SERVING_ROW_COUNTS,
        SERVING_SNAPSHOT_HASH,
        serving_hash_ok,
        serving_mtime_iso,
        serving_mtime_ok,
    )
except ImportError:  # fee155e4: no interface constants; the parent ignores the JSON
    SERVING_PATH = "serving_path"
    SERVING_INODE = "serving_inode"
    SERVING_MTIME = "serving_mtime"
    SERVING_SNAPSHOT_HASH = "serving_snapshot_hash"
    SERVING_ROW_COUNTS = "serving_row_counts"
    SERVING_HASH_ALG = "sha256"

    def serving_mtime_iso(_epoch: float) -> str:
        return "2023-11-14T22:13:20+00:00"

    def serving_mtime_ok(_value: object) -> bool:
        return False

    def serving_hash_ok(_value: object) -> bool:
        return False

_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_SQL_TABLE_SKIP = frozenset(
    {
        "and",
        "as",
        "cross",
        "from",
        "full",
        "group",
        "inner",
        "join",
        "lateral",
        "left",
        "limit",
        "on",
        "or",
        "order",
        "outer",
        "right",
        "select",
        "values",
        "where",
    }
)


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _pack_tables() -> tuple[str, ...]:
    """Same rule as score_curated.pack_question_tables. Local so fee155e4 can import this file."""
    pack = load_pack(score_curated.DEFAULT_PACK)
    ids = {str(row.get("id") or "") for row in merge_pack_questions(list(pack["questions"]))}
    found: set[str] = set()
    for qid, row in load_oracles().items():
        if str(qid) not in ids or not isinstance(row, dict):
            continue
        if str(row.get("expect") or "").strip().lower() == "refuse":
            continue
        sql = row.get("sql")
        if not isinstance(sql, str):
            continue
        for match in _SQL_TABLE_RE.finditer(sql):
            name = match.group(1).lower()
            if name not in _SQL_TABLE_SKIP:
                found.add(name)
    return tuple(sorted(found))


def _oracle_db(path: Path) -> Path:
    import duckdb

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE inventory (sku VARCHAR)")
        con.execute("INSERT INTO inventory VALUES ('A')")
    finally:
        con.close()
    return path


class _Ok:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _open_round(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import dms_executor.demo_warehouse as warehouse

    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "out_records"))
    monkeypatch.delenv("OPENVAULT_URL", raising=False)
    monkeypatch.setattr(score_curated, "round_date_label", lambda _before, _after: None)
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

    def fake(method: str, url: str, **_kwargs: object) -> _Ok:
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
        return _Ok({"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"})

    monkeypatch.setattr(score_curated, "score_http", fake)


def _report(tmp_path: Path) -> dict[str, object]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _plant(monkeypatch: pytest.MonkeyPatch, record: dict[str, object]) -> None:
    monkeypatch.setenv("DMS_SERVING_PRECHECK", json.dumps(record))


def _complete_fields(tables: tuple[str, ...], counts: dict[str, int]) -> dict[str, object]:
    import hashlib

    digest = hashlib.new(SERVING_HASH_ALG, b"fixture").hexdigest()
    return {
        SERVING_PATH: "/var/cortex/data/dms_demo.duckdb",
        SERVING_INODE: 4242,
        SERVING_MTIME: serving_mtime_iso(1700000000.0),
        SERVING_SNAPSHOT_HASH: digest,
        SERVING_ROW_COUNTS: counts,
        "serving_tables_note": list(tables),
    }


def _assert_ineligible(label: str, report: dict[str, object]) -> None:
    reasons = list(report.get("baseline_ineligible_reasons") or [])
    n = report.get("n")
    eligible = report.get("baseline_eligible")
    assert n == _pack_n(), label
    assert eligible is False and "serving_precheck_missing" in reasons, (
        f"{label} baseline_eligible={eligible} n={n} reasons={reasons}"
    )


def test_no_serving_fields_is_not_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No serving fields. Eligible on fee155e4. Not eligible once the reason exists."""
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, {})
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    _assert_ineligible("no_serving_fields", _report(tmp_path))


def test_missing_question_table_is_not_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every serving field is present. One table the questions use is absent from the counts."""
    tables = _pack_tables()
    assert tables
    missing = tables[0]
    counts = {name: 3 for name in tables if name != missing}
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, _complete_fields(tables, counts))
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    report = _report(tmp_path)
    _assert_ineligible("missing_question_table", report)
    assert missing not in (report.get(SERVING_ROW_COUNTS) or {})


def test_zero_row_question_table_is_not_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same table is present and has 0 rows."""
    tables = _pack_tables()
    assert tables
    empty = tables[0]
    counts = {name: 3 for name in tables}
    counts[empty] = 0
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, _complete_fields(tables, counts))
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    report = _report(tmp_path)
    _assert_ineligible("zero_row_question_table", report)
    got = report.get(SERVING_ROW_COUNTS)
    assert isinstance(got, dict) and got.get(empty) == 0


def test_no_snapshot_hash_is_not_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Path, inode, mtime, and positive row counts. No snapshot hash."""
    tables = _pack_tables()
    assert tables
    record = _complete_fields(tables, {name: 3 for name in tables})
    record.pop(SERVING_SNAPSHOT_HASH)
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, record)
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    report = _report(tmp_path)
    _assert_ineligible("no_snapshot_hash", report)
    assert not report.get(SERVING_SNAPSHOT_HASH)


def test_pack_named_table_missing_from_row_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Correct SQL names a table the round record's row counts do not list."""
    tables = _pack_tables()
    named = "suppliers"
    assert named in tables and named != tables[0]
    spend = load_oracles()["cq_spend_by_country"]["sql"]
    assert named in _SQL_TABLE_RE.findall(str(spend).lower())
    counts = {name: 3 for name in tables if name != named}
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, _complete_fields(tables, counts))
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    report = _report(tmp_path)
    _assert_ineligible("pack_names_unlisted_table", report)
    assert named not in (report.get(SERVING_ROW_COUNTS) or {})


def test_unparsed_correct_sql_is_not_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Correct SQL with no FROM or JOIN table does not pass."""
    tables = _pack_tables()
    assert tables
    base = load_oracles()
    broken = {
        qid: (dict(row) if isinstance(row, dict) else row) for qid, row in base.items()
    }
    sku = dict(broken["cq_sku_count"])
    sku["sql"] = "SELECT 1"
    broken["cq_sku_count"] = sku
    monkeypatch.setattr(score_curated, "load_oracles", lambda: broken)
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, _complete_fields(tables, {name: 4 for name in tables}))
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    _assert_ineligible("unparsed_correct_sql", _report(tmp_path))


def test_complete_fixture_tables_stay_eligible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Eligible only because the parse of correct SQL produced these tables."""
    tables, unparsed = score_curated.parse_pack_serving()
    assert unparsed == ()
    assert tables == _pack_tables()
    assert tables == (
        "inventory",
        "locations",
        "shipments",
        "suppliers",
        "transactions",
    )
    _open_round(monkeypatch, tmp_path)
    _plant(monkeypatch, _complete_fields(tables, {name: 4 for name in tables}))
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    report = _report(tmp_path)
    reasons = list(report.get("baseline_ineligible_reasons") or [])
    n = report.get("n")
    eligible = report.get("baseline_eligible")
    assert n == _pack_n() and eligible is True and reasons == [], (
        f"complete_fixture_tables baseline_eligible={eligible} n={n} reasons={reasons}"
    )
    assert serving_mtime_ok(report.get(SERVING_MTIME))
    assert serving_hash_ok(report.get(SERVING_SNAPSHOT_HASH))
    assert isinstance(report.get(SERVING_INODE), int)
    assert isinstance(report.get(SERVING_PATH), str)


def test_fixture_snapshot_writes_the_interface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scorer-built snapshot uses the same keys, zone, hash, and bare names."""
    assert score_curated.SERVING_PATH == SERVING_PATH == "serving_path"
    assert score_curated.SERVING_INODE == SERVING_INODE == "serving_inode"
    assert score_curated.SERVING_MTIME == SERVING_MTIME == "serving_mtime"
    assert score_curated.SERVING_SNAPSHOT_HASH == SERVING_SNAPSHOT_HASH
    assert SERVING_SNAPSHOT_HASH == "serving_snapshot_hash"
    assert score_curated.SERVING_ROW_COUNTS == SERVING_ROW_COUNTS == "serving_row_counts"
    assert score_curated.SERVING_HASH_ALG == SERVING_HASH_ALG == "sha256"
    tables = _pack_tables()
    _open_round(monkeypatch, tmp_path)
    monkeypatch.delenv("DMS_SERVING_PRECHECK", raising=False)
    live("http://127.0.0.1:9", 1.0, _oracle_db(tmp_path / "oracle.duckdb"))
    report = _report(tmp_path)
    reasons = list(report.get("baseline_ineligible_reasons") or [])
    assert report.get("baseline_eligible") is True and reasons == [], (
        f"fixture_snapshot baseline_eligible={report.get('baseline_eligible')} "
        f"n={report.get('n')} reasons={reasons}"
    )
    assert isinstance(report[SERVING_PATH], str) and report[SERVING_PATH]
    inode = report[SERVING_INODE]
    assert isinstance(inode, int) and not isinstance(inode, bool)
    assert serving_mtime_ok(report[SERVING_MTIME])
    assert str(report[SERVING_MTIME]).endswith("+00:00")
    assert serving_hash_ok(report[SERVING_SNAPSHOT_HASH])
    counts = report[SERVING_ROW_COUNTS]
    assert isinstance(counts, dict)
    assert set(counts) == set(tables)
    assert all("." not in str(key) and int(counts[key]) > 0 for key in counts)
