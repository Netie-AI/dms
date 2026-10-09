"""PIN-NOMODEL-01. The pin applies only when the answer called a model.

Each live test drives ``live()`` into ``_ask``. On 7a8d6c11
``envelope_mismatch`` ignores ``model_calls`` and ``lane``. No served fields
is INVALID ``pin_mismatch:missing/missing``. A served pair that matches the
pin is scored as the pin.

``rules`` and ``curated`` reach Cortex. A recorded zero with attribution
``none`` and no served ids is scored only when ``cortex_l2_scan.json`` says
``cortex_l2`` is ``off`` for Cortex sha ``279cbd85``, and the wrapper's
``service_started_now`` still matches ``service_started_at`` on the same
Cortex unit. The pin does not read ``DMS_L2_*``, does not read a service
manager, and does not read ``cortex_l2`` from ``score_curated.json``.
Lane comes from the executor route, not a payload ``lane`` or ``plan_source``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_strict_pin_01 as harness  # noqa: E402
from score_curated import live  # noqa: E402

_PIN = harness._PIN
_PROVIDER = harness._PROVIDER
_ROUTE = {"rules": "verified_query", "curated": "governed_metric"}
_SCAN = "cortex_l2_scan.json"
_SCAN_SHA = "279cbd85"
_STARTED_AT = "2026-10-05T09:00:00Z"
_UNIT = "cortex.service"
_START_SOURCE = "cortex_l2_scan.json:service_started_now"


def _wipe(tmp_path: Path) -> None:
    for name in (_SCAN, "score_curated.json"):
        path = tmp_path / name
        if path.is_file():
            path.unlink()
    for path in tmp_path.glob("score_*"):
        if path.is_file():
            path.unlink()


def _rows() -> list[dict[str, Any]]:
    return [dict(item) for item in harness._ANSWER_ROWS]


def _envelope(**fields: Any) -> dict[str, Any]:
    rows = _rows()
    body: dict[str, Any] = {
        "badge": "L0_CERTIFIED",
        "abstained": False,
        "rows": rows,
        "values": [dict(item) for item in rows],
        "text": "1",
        "engine_as_of": harness._ENGINE_DAY,
        "engine_as_of_after": harness._ENGINE_DAY,
        "engine_timezone": harness._TZ,
        "engine_timezone_after": harness._TZ,
    }
    body.update(fields)
    return body


def _matched() -> dict[str, Any]:
    return _envelope(
        served_provider=_PROVIDER,
        served_model=_PIN,
        served_attribution="reported",
    )


def _install_scored(
    monkeypatch: pytest.MonkeyPatch,
    first: dict[str, Any],
    rest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace the chat ask body. Question 1 is ``first``. The rest match."""
    sent: list[dict[str, Any]] = []
    n = {"i": 0}

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> Any:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return harness._Http(
                200,
                {
                    "status": "ok",
                    "engine_as_of": harness._ENGINE_DAY,
                    "engine_as_of_after": harness._ENGINE_DAY,
                    "engine_timezone": harness._TZ,
                    "engine_timezone_after": harness._TZ,
                },
                {},
            )
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            sent.append(dict(json_body or {}))
            n["i"] += 1
            body = first if n["i"] == 1 else rest
            return harness._Http(200, body, {})
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)
    return sent


def _score(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first: dict[str, Any],
    *,
    gold: bool = False,
) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    db = harness._oracle_db(tmp_path)
    _asks, script = harness._arm(monkeypatch, tmp_path, repeat=harness._ok(_PIN))
    if gold:
        harness._match_gold(monkeypatch)
    sent = _install_scored(monkeypatch, first, _matched())
    live("http://score.test", 1.0, db)
    return harness._report(tmp_path), script, sent


def _seed_l2(
    tmp_path: Path,
    value: str,
    *,
    cortex_sha: str = _SCAN_SHA,
    now: str | None = None,
    unit_now: str | None = None,
    omit_now: bool = False,
) -> str:
    """Write the Platform scan. Returns the sha256 of those bytes.

    Does not write ``score_curated.json`` and does not write a service-manager
    file. ``now`` is ``service_started_now`` for this run. ``unit_now`` is the
    unit that read names. Both match the scan unless the test plants a drift.
    """
    started = _STARTED_AT
    body: dict[str, Any] = {
        "cortex_l2": value,
        "cortex_sha": cortex_sha,
        "scanned_at": "2026-10-05T10:00:00Z",
        "service_started_at": started,
        "cortex_unit": _UNIT,
        "cortex_unit_now": _UNIT if unit_now is None else unit_now,
    }
    if not omit_now:
        body["service_started_now"] = started if now is None else now
    raw = (json.dumps(body, sort_keys=True) + "\n").encode("utf-8")
    (tmp_path / _SCAN).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _pack_n(report: dict[str, Any]) -> int:
    n_pack = len(harness._questions())
    assert report["n"] == n_pack
    assert n_pack > 1
    assert int(report["abstained"]) != n_pack
    return n_pack


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_cortex_lane_matching_stamps_scores(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero DMS calls plus stamps that match the pin are scored on the rows."""
    body = _envelope(
        model_calls=0,
        route=_ROUTE[lane],
        lane=lane,
        served_provider=_PROVIDER,
        served_model=_PIN,
    )
    try:
        report, script, sent = _score(monkeypatch, tmp_path, body, gold=True)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert len(script.calls) == n_pack + 1
        row = report["cases"][0]
        assert row["verdict"] == "OK", (
            f"lane {lane!r} with zero calls and matching stamps was "
            f"{row['verdict']} reason={row.get('reason')!r}"
        )
        assert not str(row.get("reason") or "").startswith("pin_")
        assert int(report["correct"]) >= 1
        assert row["rows"] == 1
        rec = harness._record_line(report, str(row["id"]))
        assert rec["outcome"] == "OK"
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_cortex_lane_missing_stamps_is_invalid(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls on a Cortex lane with no served stamps is pin_mismatch."""
    body = _envelope(model_calls=0, route=_ROUTE[lane], lane=lane)
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", row
        assert row["reason"] == "pin_mismatch:missing/missing", row
        assert report["n"] == n_pack
        assert int(report["invalid"]) >= 1
    finally:
        _wipe(tmp_path)


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_cortex_lane_reported_stamps_are_judged(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls on a Cortex lane with a reported pair are judged on values.

    DMS does not compare the pair to a catalog. A missing stamp stays invalid.
    """
    body = _envelope(
        model_calls=0,
        route=_ROUTE[lane],
        lane=lane,
        served_provider="together",
        served_model=_PIN,
    )
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "WRONG", row
        assert not str(row.get("reason") or "").startswith("pin_"), row
        assert report["n"] == n_pack
        assert int(report["invalid"]) == 0
    finally:
        _wipe(tmp_path)


def test_live_model_lane_zero_calls_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model lane with zero calls is pin_mismatch and stays in n."""
    body = _envelope(model_calls=0, route="generated", lane="generative")
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "INVALID"
        assert row["reason"] == "pin_mismatch:generative", row
        assert report["n"] == n_pack
        assert int(report["invalid"]) >= 1
    finally:
        _wipe(tmp_path)


def test_live_missing_lane_is_lane_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls and no lane is INVALID lane_unknown, kept in n."""
    body = _envelope(model_calls=0)
    assert "lane" not in body
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "INVALID"
        assert row["reason"] == "lane_unknown", row
        assert report["n"] == n_pack
        assert int(report["invalid"]) >= 1
    finally:
        _wipe(tmp_path)


def _unstamped_none(lane: str) -> dict[str, Any]:
    body = _envelope(
        model_calls=0,
        route=_ROUTE[lane],
        served_attribution="none",
    )
    assert "served_provider" not in body
    assert "served_model" not in body
    return body


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_unstamped_none_scores_when_cortex_l2_off(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attribution none and no served ids score when the scan says off."""
    digest = _seed_l2(tmp_path, "off")
    scan_before = (tmp_path / _SCAN).read_bytes()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    try:
        report, _script, sent = _score(
            monkeypatch, tmp_path, _unstamped_none(lane), gold=True
        )
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "OK", (
            f"lane {lane!r} with cortex_l2 off was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert int(report["correct"]) >= 1
        assert report["cortex_l2"] == "off"
        assert report["cortex_l2_scan_sha256"] == digest
        assert report["cortex_service_start_source"] == _START_SOURCE
        assert (tmp_path / _SCAN).read_bytes() == scan_before
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_unstamped_none_invalid_when_cortex_l2_missing(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No scan file fail-closes to pin_mismatch."""
    assert not (tmp_path / _SCAN).exists()
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, _unstamped_none(lane))
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", row
        assert str(row["reason"]).startswith("pin_mismatch"), row
        assert report["cortex_l2"] == "missing"
        assert report["cortex_l2_scan_sha256"] is None
        assert report["cortex_service_start_source"] is None
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_unstamped_none_invalid_when_cortex_l2_on(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cortex_l2 on fail-closes a zero-call rules or curated answer."""
    digest = _seed_l2(tmp_path, "on")
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, _unstamped_none(lane))
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", row
        assert str(row["reason"]).startswith("pin_mismatch"), row
        assert report["cortex_l2"] == "on"
        assert report["cortex_l2_scan_sha256"] == digest
        assert report["cortex_service_start_source"] == _START_SOURCE
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_stale_score_off_without_scan_stays_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old score report that says off is not a scan. No scan stays INVALID."""
    (tmp_path / "score_curated.json").write_text(
        json.dumps({"cortex_l2": "off"}) + "\n",
        encoding="utf-8",
    )
    assert not (tmp_path / _SCAN).exists()
    try:
        report, _script, sent = _score(
            monkeypatch, tmp_path, _unstamped_none("rules")
        )
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"stale score_curated.json off with no scan was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert str(row["reason"]).startswith("pin_mismatch"), row
        assert report["cortex_l2"] == "missing"
        assert report["cortex_l2_scan_sha256"] is None
        assert report["cortex_service_start_source"] is None
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_service_start_changed_since_scan_stays_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scan off with sha 279cbd85 is unknown once service_started_now moved."""
    digest = _seed_l2(tmp_path, "off", now="2026-10-05T11:00:00Z")
    try:
        report, _script, sent = _score(
            monkeypatch, tmp_path, _unstamped_none("rules")
        )
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"service start newer than the scan was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert str(row["reason"]).startswith("pin_mismatch"), row
        assert report["cortex_l2"] == "unknown"
        assert report["cortex_l2_scan_sha256"] == digest
        assert report["cortex_service_start_source"] == _START_SOURCE
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("unreadable service_started_now", {"omit_now": True}),
        ("cortex_unit_now other.service", {"unit_now": "other.service"}),
    ],
)
def test_live_start_source_not_the_scan_unit_stays_invalid(
    label: str,
    kwargs: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unread start time, or a different Cortex unit, stays unknown."""
    digest = _seed_l2(tmp_path, "off", **kwargs)
    try:
        report, _script, sent = _score(
            monkeypatch, tmp_path, _unstamped_none("rules")
        )
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"{label} was {row['verdict']} reason={row.get('reason')!r}"
        )
        assert str(row["reason"]).startswith("pin_mismatch"), row
        assert report["cortex_l2"] == "unknown"
        assert report["cortex_l2_scan_sha256"] == digest
        assert report["cortex_service_start_source"] == _START_SOURCE
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_scan_sha_not_279cbd85_stays_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that says off for any other cortex_sha is unknown."""
    digest = _seed_l2(tmp_path, "off", cortex_sha="deadbeef")
    try:
        report, _script, sent = _score(
            monkeypatch, tmp_path, _unstamped_none("rules")
        )
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"scan sha other than 279cbd85 was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert str(row["reason"]).startswith("pin_mismatch"), row
        assert report["cortex_l2"] == "unknown"
        assert report["cortex_l2_scan_sha256"] == digest
        assert report["cortex_service_start_source"] == _START_SOURCE
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_generative_payload_lane_stays_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generative route stays generative when the payload says rules."""
    _seed_l2(tmp_path, "off")
    body = _envelope(
        model_calls=0,
        route="generated",
        lane="rules",
        plan_source="rules",
        served_attribution="none",
    )
    assert "served_provider" not in body
    assert "served_model" not in body
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body, gold=True)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"generative path with payload lane rules was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert row["reason"] == "pin_mismatch:generative", row
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_listed_lane_attribution_none_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """served_attribution none alone does not make a listed lane INVALID."""
    import cortex_client.strict_pin as pin

    monkeypatch.setattr(
        pin, "NO_MODEL_LANES", frozenset({"bronze"}), raising=False
    )
    body = _envelope(
        model_calls=0,
        route="bronze_sheet",
        served_attribution="none",
    )
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body, gold=True)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "OK", (
            f"listed lane with attribution none was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_listed_lane_other_attribution_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An attribution other than none still names a model."""
    import cortex_client.strict_pin as pin

    monkeypatch.setattr(
        pin, "NO_MODEL_LANES", frozenset({"bronze"}), raising=False
    )
    body = _envelope(
        model_calls=0,
        route="bronze_sheet",
        served_attribution="reported",
    )
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", row
        assert row["reason"] == "pin_mismatch:missing/missing", row
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_sheet_lane_allows_bronze_on_nomodel_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_MODEL_LANES may name bronze. sheet_lane raises only for a model lane."""
    import dms_executor.bronze_sheet_ask as sheet

    monkeypatch.setattr(sheet, "NO_MODEL_LANES", frozenset({"bronze"}))
    assert sheet.sheet_lane() == "bronze"


def test_sheet_lane_rejects_model_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model lane is still not the sheet path."""
    import dms_executor.bronze_sheet_ask as sheet

    monkeypatch.setattr(sheet, "MODEL_LANES", frozenset({"bronze"}))
    with pytest.raises(RuntimeError, match="bronze"):
        sheet.sheet_lane()


# Handler module and function for a lane name. A name on NO_MODEL_LANES must be here.
_LANE_HANDLER: dict[str, tuple[Path, str]] = {
    "rules": (
        ROOT / "packages/executor/dms_executor/verified_queries.py",
        "maybe_verified_ask",
    ),
    "curated": (
        ROOT / "packages/executor/dms_executor/demo_pack.py",
        "maybe_pack_ask",
    ),
}

# Production methods the handler's submit / ledger callbacks call.
_CORTEX_WIRE = (
    "_submit_verified_sql",
    "submit_sql",
    "bind_session",
    "_ledger_verified_query",
)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _cortex_import_or_call(path: Path) -> list[str]:
    """Import or call of cortex_client anywhere in this handler module."""
    rel = path.relative_to(ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] == "cortex_client":
                    hits.append(f"{rel}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".", 1)[0] == "cortex_client":
                hits.append(f"{rel}:{node.lineno}: from {mod}")
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.split(".", 1)[0] == "cortex_client":
                hits.append(f"{rel}:{node.lineno}: call {name}")
    return hits


def _calls_name(fn: ast.AST, name: str) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == name:
                return True
    return False


def _handler_fn(path: Path, fn_name: str) -> ast.AST | None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return node
    return None


def _wired_cortex(path: Path, methods: tuple[str, ...]) -> list[str]:
    """``self._cortex`` inside the production methods a handler callback uses."""
    rel = path.relative_to(ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name in methods:
                found[item.name] = item
    hits: list[str] = []
    for name in methods:
        fn = found.get(name)
        if fn is None:
            hits.append(f"{rel}: missing {name}")
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == "_cortex":
                hits.append(f"{rel}:{node.lineno}: {name} uses _cortex")
    return hits


def test_listed_nomodel_handler_does_not_reach_cortex() -> None:
    """A lane on NO_MODEL_LANES whose handler imports or calls cortex_client fails.

    ``rules`` and ``curated`` are not on the list. Their handlers call
    ``submit`` / ``ledger_append``, and those callbacks use ``_cortex``.
    """
    from dms_core.ask import NO_MODEL_LANES

    init = ROOT / "packages/executor/dms_executor/__init__.py"
    for lane in sorted(NO_MODEL_LANES):
        pair = _LANE_HANDLER.get(lane)
        assert pair is not None, f"lane {lane!r} has no handler proof"
        path, fn_name = pair
        hits = _cortex_import_or_call(path)
        assert hits == [], f"lane {lane!r} handler reaches cortex_client: {hits}"
        fn = _handler_fn(path, fn_name)
        assert fn is not None, fn_name
        if _calls_name(fn, "submit") or _calls_name(fn, "ledger_append"):
            wire = _wired_cortex(init, _CORTEX_WIRE)
            assert wire == [], f"lane {lane!r} is wired to Cortex: {wire}"
