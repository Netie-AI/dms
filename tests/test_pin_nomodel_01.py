"""PIN-NOMODEL-01. The pin applies only when the answer called a model.

Each live test drives ``live()`` into ``_ask``. On 7a8d6c11
``envelope_mismatch`` ignores ``model_calls`` and ``lane``. No served fields
is INVALID ``pin_mismatch:missing/missing``. A served pair that matches the
pin is scored as the pin.

``rules`` and ``curated`` submit through Cortex, so they are not no-model.
A recorded zero on either lane is INVALID ``pin_mismatch:<lane>``.
"""

from __future__ import annotations

import ast
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


def _wipe(tmp_path: Path) -> None:
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


def _pack_n(report: dict[str, Any]) -> int:
    n_pack = len(harness._questions())
    assert report["n"] == n_pack
    assert n_pack > 1
    assert int(report["abstained"]) != n_pack
    return n_pack


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_cortex_lane_zero_calls_is_invalid(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Cortex-routed lane with zero calls is INVALID, not a row score.

    ``rules`` is verified_query (``maybe_verified_ask``). ``curated`` is
    governed_metric (``maybe_pack_ask``). Both submit through Cortex. On
    7a8d6c11 the reason is ``pin_mismatch:missing/missing``.
    """
    body = _envelope(model_calls=0, lane=lane)
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", row
        assert row["reason"] == f"pin_mismatch:{lane}", row
        assert report["n"] == n_pack
        assert int(report["invalid"]) >= 1
    finally:
        _wipe(tmp_path)


def test_live_zero_calls_with_served_pin_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls plus a served pair, even the pin itself, is pin_mismatch."""
    body = _envelope(
        model_calls=0,
        lane="rules",
        served_provider=_PROVIDER,
        served_model=_PIN,
    )
    try:
        report, script, sent = _score(monkeypatch, tmp_path, body, gold=True)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert len(script.calls) == n_pack + 1
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"zero calls carrying served ids was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert str(row["reason"]).startswith("pin_mismatch")
        assert int(report["invalid"]) >= 1
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_model_lane_zero_calls_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model lane with zero calls is pin_mismatch and stays in n."""
    body = _envelope(model_calls=0, lane="generative")
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
