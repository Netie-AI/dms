"""REVEAL-01 — allowlisted Explorer reveal for filesystem origin_uri."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.reveal import is_filesystem_uri, reveal_path
from fastapi.testclient import TestClient


def test_is_filesystem_uri() -> None:
    assert is_filesystem_uri(r"D:\DMS\data\notes.xlsx")
    assert is_filesystem_uri("/tmp/notes.xlsx")
    assert not is_filesystem_uri("duckdb://dms_demo/transactions")
    assert not is_filesystem_uri("https://example.com/a.xlsx")
    assert not is_filesystem_uri("notes.xlsx")


def test_reveal_path_allowlist_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wh = tmp_path / "reveal.duckdb"
    wh.write_bytes(b"x")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(wh))

    inside = tmp_path / "notes.xlsx"
    inside.write_text("hello", encoding="utf-8")
    outside = Path.cwd() / "outside_reveal_probe.xlsx"

    ok = reveal_path(str(inside), open_explorer=False)
    assert ok["ok"] is True
    assert ok["action"] == "dry_run"

    missing = reveal_path(str(tmp_path / "gone.xlsx"), open_explorer=False)
    assert missing["ok"] is False
    assert "not_found" in str(missing.get("error"))

    refused = reveal_path(str(outside), open_explorer=False)
    assert refused["ok"] is False
    assert refused["error"] == "path_not_allowlisted"

    non_fs = reveal_path("duckdb://dms_demo/transactions", open_explorer=False)
    assert non_fs["ok"] is False
    assert non_fs["error"] == "not_filesystem_path"


def test_library_reveal_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setenv("DMS_REVEAL_ROOTS", str(root))
    get_settings.cache_clear()

    target = root / "studio_upload.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")

    import dms_api.routes.library as library_routes

    monkeypatch.setattr(
        library_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )
    opened: list[str] = []

    def _fake_popen(cmd, *a, **k):
        opened.append(str(cmd))
        return None

    monkeypatch.setattr("dms_executor.reveal.subprocess.Popen", _fake_popen)

    client = TestClient(create_app())
    r = client.post("/v1/library/reveal", json={"path": str(target)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    # ``path`` is the file that was asked for, on every platform. Only ``opened``
    # is allowed to differ: Explorer can select a file, xdg-open can only show
    # the folder that holds it. Asserting the file here previously passed on
    # Windows and failed on Linux CI, which made the check a platform accident
    # rather than a contract.
    assert body["path"] == str(target.resolve())
    assert body["opened"] in {str(target.resolve()), str(target.resolve().parent)}
    if os.name == "nt":
        assert body["action"] == "explorer_select"
        assert body["opened"] == str(target.resolve())
    else:
        assert body["action"] == "xdg_or_open"
        assert body["opened"] == str(target.resolve().parent)
    assert opened, "the file manager should have been invoked"

    bad = client.post("/v1/library/reveal", json={"path": str(Path.cwd() / "nope.xlsx")})
    assert bad.status_code == 403
    assert bad.json()["detail"] == "path_not_allowlisted"

    soft = client.post(
        "/v1/library/reveal", json={"path": "duckdb://dms_demo/transactions"}
    )
    assert soft.status_code == 200
    assert soft.json()["ok"] is False
    assert soft.json()["error"] == "not_filesystem_path"

    get_settings.cache_clear()
    monkeypatch.delenv("DMS_REVEAL_ROOTS", raising=False)
    get_settings.cache_clear()
