"""FDE-BUDGET-01: OpenVault preflight stays read-only and does not default to PASS."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT_PATH = ROOT / "scripts" / "fde" / "kits" / "budget.py"
NOW = 1_700_000_000.0
KEY = "vk_live_0123456789abcdef"
MASK = "vk_l...cdef"
SECRET = "ov_live_NOTASECRET_zzzz_value"
MODEL = "hidden-model-name-xyz"
PASSWORD = "planted-password-value-99"
BEARER = "Bearer planted-bearer-token-value"

_spec = importlib.util.spec_from_file_location("fde_budget", KIT_PATH)
assert _spec is not None and _spec.loader is not None
budget = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(budget)


def _clock() -> float:
    return NOW


def _policy(roles: dict[str, int], caps: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "roles": {
            role: [{"provider": "p", "model": MODEL}] * count for role, count in roles.items()
        },
        "caps": {"keys": caps or {}, "callers": {}},
    }


def _write_policy(path: Path, roles: dict[str, int], caps: dict[str, Any] | None = None) -> None:
    path.write_text(json.dumps(_policy(roles, caps)), encoding="utf-8")


def _engagement(directory: Path, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "engagement.yaml").write_text(text, encoding="utf-8")


def _sheet(directory: Path, policy: Path, *, create: str = "", per_day: str = "") -> None:
    extra = f"create_roles: [{create}]\n" if create else ""
    day = f"questions_per_day: {per_day}\n" if per_day else ""
    _engagement(
        directory,
        "tenant: acme\n"
        "tenant_id: acme-1\n"
        "openvault:\n"
        f"  key_id: {KEY}\n"
        f"  policy_file: {policy}\n"
        f"{extra}{day}",
    )


def _scripted(
    responses: dict[str, tuple[int, Any]],
) -> tuple[list[tuple[str, str, dict[str, str]]], budget.Transport]:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def send(path: str, params: Mapping[str, str]) -> tuple[int, Any]:
        calls.append(("GET", path, dict(params)))
        status, body = responses.get(path, (404, {}))
        return status, body

    return calls, send


def _ctx(
    directory: Path,
    transport: budget.Transport,
    environ: dict[str, str] | None = None,
) -> budget.Ctx:
    data, error = budget.load_engagement(directory)
    assert error == ""
    env = {budget.FLAG: "1"}
    if environ:
        env.update(environ)
    return budget.Ctx(directory, data, transport, env, clock=_clock)


def _by_id(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in report["checks"]}


def _responses(**overrides: tuple[int, Any]) -> dict[str, tuple[int, Any]]:
    base: dict[str, tuple[int, Any]] = {
        budget.PATH_ROUTE_POLICY: (404, {}),
        budget.PATH_USAGE: (200, {"events": []}),
        budget.PATH_QUOTA: (404, {}),
        budget.PATH_SHIP_BUDGET: (200, {"monthly_cap_usd": 25.0}),
        budget.PATH_SEAT: (404, {}),
    }
    base.update(overrides)
    return base


def test_rollup_empty_is_skipped() -> None:
    assert budget.rollup([]) == "SKIPPED"


@pytest.mark.parametrize("value", [None, "", "true", "01", " 1 ", "yes", "on"])
def test_flag_off_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.delenv(budget.FLAG, raising=False)
    if value is not None:
        monkeypatch.setenv(budget.FLAG, value)
    _engagement(tmp_path, "tenant: acme\n")
    code = budget.main(["--engagement", str(tmp_path)])
    assert code == 2
    assert not (tmp_path / "runs").exists()


def test_flag_on_digit_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(budget.FLAG, "1")
    assert budget.kit_enabled(__import__("os").environ)


def test_password_in_engagement_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(budget.FLAG, "1")
    _engagement(tmp_path, f"tenant: acme\npassword: {PASSWORD}\n")
    code = budget.main(["--engagement", str(tmp_path)])
    assert code == 2
    assert not (tmp_path / "runs").exists()


def test_missing_role_is_critical_fail_and_names_it(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1}, {KEY: {"daily_usd": 5, "monthly_usd": None}})
    _sheet(tmp_path / "acme", policy, create="summarize")
    _calls, send = _scripted(_responses())
    report = budget.run(_ctx(tmp_path / "acme", send))
    row = _by_id(report)["K7.01"]
    assert report["status"] == "FAIL"
    assert row["status"] == "FAIL"
    assert row["severity"] == "critical"
    assert row["evidence"]["missing"] == ["ontology", "summarize"]
    assert "ontology" in row["fix"]
    assert "summarize" in row["fix"]
    assert MODEL not in json.dumps(report)


def test_shared_key_is_high_fail_and_ids_are_masked(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 2}, {KEY: {"daily_usd": 5}})
    _sheet(tmp_path / "acme", policy)
    usage = {
        "events": [
            {
                "api_key_id": KEY,
                "identity": "other-eval",
                "created_at": NOW - 10,
                "error_type": SECRET,
                "model_served": MODEL,
                "note": PASSWORD,
                "authorization": BEARER,
            },
            {
                "api_key_id": KEY,
                "identity": "acme",
                "created_at": NOW - 4000,
                "model_served": MODEL,
            },
        ]
    }
    _calls, send = _scripted(_responses(**{budget.PATH_USAGE: (200, usage)}))
    report = budget.run(_ctx(tmp_path / "acme", send))
    row = _by_id(report)["K7.03"]
    assert row["status"] == "FAIL"
    assert row["severity"] == "high"
    assert row["evidence"]["masked_key"] == MASK
    assert "other-eval" in row["evidence"]["identities"]
    folder = budget.write_report(tmp_path / "acme", report, NOW)
    blob = (folder / "report.json").read_text(encoding="utf-8") + (
        folder / "report.md"
    ).read_text(encoding="utf-8")
    for planted in (KEY, SECRET, MODEL, PASSWORD, BEARER):
        assert planted not in blob
    assert MASK in blob


def test_env_secret_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_API_KEY", SECRET)
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", PASSWORD)
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 1}})
    _sheet(tmp_path / "acme", policy, per_day="4")
    _calls, send = _scripted(_responses())
    report = budget.run(_ctx(tmp_path / "acme", send))
    folder = budget.write_report(tmp_path / "acme", report, NOW)
    blob = (folder / "report.json").read_text(encoding="utf-8")
    assert SECRET not in blob
    assert PASSWORD not in blob
    assert KEY not in blob


def test_kit_issues_only_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = tmp_path / "route_policy.json"
    before = None
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 2}})
    before = policy.stat().st_mtime_ns
    _sheet(tmp_path / "acme", policy)
    monkeypatch.setenv(budget.FLAG, "1")
    seen: list[tuple[str, str, dict[str, str] | None]] = []

    def fake_get(url: str, params: dict[str, str] | None = None, timeout: float = 0) -> Any:
        seen.append(("GET", url, params))
        request = httpx.Request("GET", url)
        return httpx.Response(404, json={"ok": False}, request=request)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("write")

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "put", boom)
    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setattr(httpx, "patch", boom)
    monkeypatch.setattr(httpx, "delete", boom)
    monkeypatch.setattr(httpx, "request", boom)
    code = budget.main(["--engagement", str(tmp_path / "acme")])
    assert seen
    assert {method for method, _url, _params in seen} == {"GET"}
    paths = [url.split("http://127.0.0.1:5000", 1)[-1] for _m, url, _p in seen]
    assert paths == list(budget.ALLOWED)
    assert all(KEY not in (url + str(params)) for _m, url, params in seen)
    assert policy.stat().st_mtime_ns == before
    assert code == 1
    source = KIT_PATH.read_text(encoding="utf-8")
    assert "httpx.put" not in source
    assert "httpx.post" not in source
    assert "httpx.patch" not in source
    assert "httpx.delete" not in source


def test_disabled_seat_route_is_skipped_and_clean_run_passes(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 2, "ontology": 1}, {KEY: {"daily_usd": 5, "monthly_usd": 20}})
    _sheet(tmp_path / "acme", policy, per_day="10")
    accuracy = tmp_path / "acme" / "runs" / "20261128T000000Z-accuracy"
    accuracy.mkdir(parents=True)
    (accuracy / "report.json").write_text(
        json.dumps(
            {
                "schema": budget.SCHEMA,
                "kit": "accuracy",
                "stage": "K5",
                "finished_at": "2026-11-28T00:00:00Z",
                "cost": {"ov_calls": 4, "usd": 0.4, "source": "OV /api/usage"},
                "metrics": {"questions": 10},
            }
        ),
        encoding="utf-8",
    )
    _calls, send = _scripted(
        _responses(
            **{
                budget.PATH_USAGE: (
                    200,
                    {"events": [{"api_key_id": KEY, "identity": "acme", "created_at": NOW - 5}]},
                )
            }
        )
    )
    report = budget.run(_ctx(tmp_path / "acme", send))
    rows = _by_id(report)
    assert rows["K7.05"]["status"] == "SKIPPED"
    assert rows["K7.05"]["evidence"]["reason"] == "seat_plane_absent"
    assert rows["K7.01"]["status"] == "PASS"
    assert rows["K7.02"]["status"] == "PASS"
    assert rows["K7.03"]["status"] == "PASS"
    assert rows["K7.04"]["status"] == "PASS"
    assert rows["K7.04"]["evidence"]["per_question_usd"] == 0.04
    assert rows["K7.04"]["evidence"]["per_day_usd"] == 0.4
    assert report["status"] == "PASS"
    assert report["schema"] == budget.SCHEMA
    folder = budget.write_report(tmp_path / "acme", report, NOW)
    loaded = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    assert (folder / "report.md").read_text(encoding="utf-8") == budget._scrub(
        budget.render_markdown(loaded)
    )
    assert loaded["status"] == "PASS"
    for key in (
        "schema",
        "kit",
        "stage",
        "engagement",
        "tenant_id",
        "dms_sha",
        "started_at",
        "finished_at",
        "flags",
        "status",
        "checks",
        "metrics",
        "artifacts",
        "cost",
    ):
        assert key in loaded


def test_projected_cost_over_cap_fails(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 0.1}})
    _sheet(tmp_path / "acme", policy, per_day="10")
    accuracy = tmp_path / "acme" / "runs" / "t-accuracy"
    accuracy.mkdir(parents=True)
    (accuracy / "report.json").write_text(
        json.dumps(
            {
                "kit": "accuracy",
                "finished_at": "2026-11-28T00:00:00Z",
                "cost": {"usd": 2.0},
                "metrics": {"questions": 10},
            }
        ),
        encoding="utf-8",
    )
    _calls, send = _scripted(_responses())
    report = budget.run(_ctx(tmp_path / "acme", send))
    row = _by_id(report)["K7.04"]
    assert row["status"] == "FAIL"
    assert row["severity"] == "high"
    assert report["status"] == "FAIL"


def test_no_accuracy_report_skips_projection(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"monthly_usd": 10}})
    _sheet(tmp_path / "acme", policy)
    _calls, send = _scripted(_responses())
    report = budget.run(_ctx(tmp_path / "acme", send))
    assert _by_id(report)["K7.04"]["status"] == "SKIPPED"
    assert _by_id(report)["K7.04"]["evidence"]["reason"] == "no_accuracy_report"


def test_unreachable_openvault_is_not_pass(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 1}})
    _sheet(tmp_path / "acme", policy)
    down = {path: (0, {"error": "ConnectError"}) for path in budget.ALLOWED}
    _calls, send = _scripted(down)
    report = budget.run(_ctx(tmp_path / "acme", send))
    assert _by_id(report)["K7.00"]["status"] == "FAIL"
    assert report["status"] == "FAIL"
    assert report["status"] != "PASS"


def test_unreadable_policy_does_not_pass(tmp_path: Path) -> None:
    _engagement(
        tmp_path,
        f"tenant: acme\nopenvault:\n  key_id: {KEY}\n",
    )
    _calls, send = _scripted(_responses())
    report = budget.run(_ctx(tmp_path, send))
    assert _by_id(report)["K7.01"]["status"] == "SKIPPED"
    assert report["status"] == "SKIPPED"
    assert report["status"] != "PASS"


def test_no_cap_is_critical_fail(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {})
    _sheet(tmp_path / "acme", policy)
    _calls, send = _scripted(
        _responses(
            **{
                budget.PATH_QUOTA: (
                    200,
                    {"keys": [{"masked_id": MASK, "daily_limit": None, "is_free_tier": True}]},
                )
            }
        )
    )
    report = budget.run(_ctx(tmp_path / "acme", send))
    row = _by_id(report)["K7.02"]
    assert row["status"] == "FAIL"
    assert row["severity"] == "critical"
    assert row["evidence"]["free_tier"] is True
    assert row["evidence"]["masked_key"] == MASK
    assert KEY not in json.dumps(report)


def test_exception_is_a_fail_check_not_a_crash(tmp_path: Path) -> None:
    _engagement(tmp_path, f"tenant: acme\nopenvault:\n  key_id: {KEY}\n")

    def explode(_path: str, _params: Mapping[str, str]) -> tuple[int, Any]:
        raise RuntimeError(PASSWORD)

    report = budget.run(_ctx(tmp_path, explode))
    assert report["status"] == "FAIL"
    assert _by_id(report)["K7.00"]["evidence"]["exception"] == "RuntimeError"
    assert PASSWORD not in json.dumps(report)


def test_usage_query_does_not_carry_the_key(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 1}})
    _sheet(tmp_path / "acme", policy)
    calls, send = _scripted(_responses())
    budget.run(_ctx(tmp_path / "acme", send))
    assert [method for method, _path, _params in calls] == ["GET"] * len(budget.ALLOWED)
    usage = [item for item in calls if item[1] == budget.PATH_USAGE][0]
    assert "key" not in usage[2]
    assert KEY not in json.dumps(usage[2])


def test_old_hour_does_not_count_as_shared(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 1}})
    _sheet(tmp_path / "acme", policy)
    usage = {
        "events": [
            {"api_key_id": KEY, "identity": "other-eval", "created_at": NOW - 3601},
        ]
    }
    _calls, send = _scripted(_responses(**{budget.PATH_USAGE: (200, usage)}))
    report = budget.run(_ctx(tmp_path / "acme", send))
    assert _by_id(report)["K7.03"]["status"] == "PASS"
    assert _by_id(report)["K7.03"]["evidence"]["events"] == 0


def test_full_usage_page_is_not_a_pass(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 1}})
    _sheet(tmp_path / "acme", policy)
    events = [
        {"api_key_id": "other-key-0123456789", "identity": "acme", "created_at": NOW - 5}
        for _ in range(budget.USAGE_LIMIT)
    ]
    _calls, send = _scripted(_responses(**{budget.PATH_USAGE: (200, {"events": events})}))
    report = budget.run(_ctx(tmp_path / "acme", send))
    row = _by_id(report)["K7.03"]
    assert row["status"] == "SKIPPED"
    assert row["evidence"]["reason"] == "usage_window_truncated"


def test_http_policy_snapshot_drops_model_names(tmp_path: Path) -> None:
    _engagement(tmp_path, f"tenant: acme\nopenvault:\n  key_id: {KEY}\n")
    snapshot = {
        "roles": {"sql": {"hops": 1}, "ontology": {"hops": 0}},
        "caps": {"keys": {KEY: {"daily_usd": 3}}, "callers": {}},
        "model": MODEL,
    }
    _calls, send = _scripted(_responses(**{budget.PATH_ROUTE_POLICY: (200, snapshot)}))
    report = budget.run(_ctx(tmp_path, send))
    assert _by_id(report)["K7.01"]["evidence"]["missing"] == ["ontology"]
    assert MODEL not in json.dumps(report)
    assert _by_id(report)["K7.02"]["status"] == "PASS"


def test_seat_policy_error_fails_without_the_message(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.json"
    _write_policy(policy, {"sql": 1, "ontology": 1}, {KEY: {"daily_usd": 1}})
    _sheet(tmp_path / "acme", policy)
    _calls, send = _scripted(
        _responses(
            **{budget.PATH_SEAT: (200, {"ok": True, "policy_error": PASSWORD, "using": "file"})}
        )
    )
    report = budget.run(_ctx(tmp_path / "acme", send))
    row = _by_id(report)["K7.05"]
    assert row["status"] == "FAIL"
    assert row["evidence"]["policy_error"] == "present"
    assert PASSWORD not in json.dumps(report)
