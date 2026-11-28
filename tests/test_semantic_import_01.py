"""FEAT-SEMANTIC-IMPORT-01: semantic model import, behind DMS_SEMANTIC_IMPORT.

Flag off, the import and export routes are absent. The ask path is not
mounted here, so this file starts with the route absence. Later tests cover
the five formats, the gates, and flag-off prompt bytes.
"""

from __future__ import annotations

import pytest
from dms_api.app import create_app
from fastapi.testclient import TestClient

FLAG = "DMS_SEMANTIC_IMPORT"
SPACE = "space-semantic-import-01"


def _clear_settings() -> None:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    _clear_settings()


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    from dms_api.routes.semantic_import import enabled

    assert enabled() is False


def test_flag_off_routes_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    client = TestClient(create_app())
    space = SPACE
    missing = (
        ("post", f"/v1/spaces/{space}/semantic-import"),
        ("get", f"/v1/spaces/{space}/semantic-export"),
    )
    for method, path in missing:
        res = client.post(path) if method == "post" else client.get(path)
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}
