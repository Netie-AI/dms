"""Until BADGE-GUARD-01, the summary prints no badge or crag figure.

A 2026-09-25 shape (42 L2_VALIDATED / ontology_plan / crag validated, plus
10 abstains) goes through live(). fee155e4 prints the badge and crag on
each case line and fails on that assert. The head prints
badges_unverified: cortex_l2_off next to n, and the grid does the same.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_curated  # noqa: E402
from score_curated import (  # noqa: E402
    grid_score_hook,
    live,
    load_pack,
    merge_pack_questions,
)

try:
    from score_curated import BADGES_UNVERIFIED_LINE  # noqa: E402
except ImportError:  # fee155e4 has no line constant
    BADGES_UNVERIFIED_LINE = "badges_unverified: cortex_l2_off"


def _pack_questions() -> list[dict[str, object]]:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return list(merge_pack_questions(list(pack["questions"])))


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


def _install(monkeypatch: pytest.MonkeyPatch) -> None:
    health = {
        "status": "ok",
        "engine_as_of": "2024-06-15",
        "engine_as_of_after": "2024-06-15",
        "engine_timezone": "UTC",
        "engine_timezone_after": "UTC",
    }
    l2 = {
        "badge": "L2_VALIDATED",
        "abstained": False,
        "route": "generated",
        "plan_source": "ontology_plan",
        "rows": [],
        "text": "n",
    }
    abstain = {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"}
    sent = {"n": 0}

    def fake(method: str, url: str, **_kwargs: object) -> _Ok:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Ok(health)
        sent["n"] += 1
        if sent["n"] <= 42:
            return _Ok(l2)
        return _Ok(abstain)

    monkeypatch.setattr(score_curated, "score_http", fake)


def _keyed_figure(text: str) -> str | None:
    for line in text.splitlines():
        if "crag=" in line or "\tL2_VALIDATED\t" in line:
            return line
    return None


def _beside_n(text: str) -> bool:
    for line in text.splitlines():
        if BADGES_UNVERIFIED_LINE in line and " n " in f" {line} ":
            return True
    return False


def test_sep25_shape_prints_no_badge_or_crag_figure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """42 validated answers and 10 abstains. No badge or crag figure."""
    assert len(_pack_questions()) == 52
    _open_round(monkeypatch, tmp_path)
    _install(monkeypatch)
    db = _oracle_db(tmp_path / "oracle.duckdb")
    live("http://127.0.0.1:9", 1.0, db)
    text = capsys.readouterr().out
    report = json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))
    cases = list(report["cases"])
    l2 = [
        row
        for row in cases
        if row.get("badge") == "L2_VALIDATED"
        and row.get("plan_source") == "ontology_plan"
        and row.get("crag") == "validated"
    ]
    abstain = [
        row
        for row in cases
        if row.get("badge") == "ABSTAIN" and row.get("crag") == "abstain"
    ]
    n = report.get("n")
    outcomes = (
        int(report["correct"])
        + int(report["layer"])
        + int(report["abstained"])
        + int(report["wrong"])
        + int(report["oracle_error"])
        + int(report["invalid"])
        + int(report["rate_limit"])
    )
    reasons = list(report.get("baseline_ineligible_reasons") or [])
    assert (len(l2), len(abstain), n, outcomes, len(cases)) == (42, 10, 52, 52, 52)
    assert report.get("baseline_eligible") is (reasons == [])
    figure = _keyed_figure(text)
    shown = None if figure is None else figure[:180]
    assert figure is None and _beside_n(text), (
        f"badge_or_crag_figure={shown!r} "
        f"badges_unverified_beside_n={_beside_n(text)} n={n} reasons={reasons}"
    )
    assert BADGES_UNVERIFIED_LINE == "badges_unverified: cortex_l2_off"
    row = grid_score_hook("http://127.0.0.1:9", 1.0, db)
    grid_text = capsys.readouterr().out
    grid_figure = _keyed_figure(grid_text)
    grid_shown = None if grid_figure is None else grid_figure[:180]
    assert grid_figure is None and _beside_n(grid_text), (
        f"grid badge_or_crag_figure={grid_shown!r} "
        f"badges_unverified_beside_n={_beside_n(grid_text)} n={row.get('n')}"
    )
    grid_outcomes = (
        int(row["ok"])
        + int(row["layer"])
        + int(row["abstain"])
        + int(row["wrong"])
        + int(row["oracle_error"])
        + int(row["invalid"])
    )
    assert row.get("n") == 52 and grid_outcomes == 52
    assert row.get("badges_unverified") == "cortex_l2_off"
