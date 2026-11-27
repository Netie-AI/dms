"""STUDIO-FIX-A. A Python call must not hand a FastAPI Query to UUID.

``list_sources(settings)`` used to pass ``Query(None)``. That object is
truthy, so ``_list_sources`` called ``UUID(space_id)`` and raised
``AttributeError: 'Query' object has no attribute 'replace'`` only when
``DATABASE_URL`` was set. The memory fixture never calls ``UUID``, and an
empty list still satisfies ``count == len(sources)``.

The route default is ``None``. Anything that is not a string or ``None``
raises ``TypeError`` before Postgres opens.
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID

import pytest
from dms_api.app import create_app
from dms_api.routes.library import _list_sources, list_sources
from dms_api.settings import get_settings
from fastapi import Query
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_list_sources_default_is_none_not_a_query() -> None:
    default = inspect.signature(list_sources).parameters["space_id"].default
    assert default is None
    assert type(default).__name__ != "Query"


class _Conn:
    def __init__(self) -> None:
        self.params: tuple[Any, ...] | None = None

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def execute(self, _sql: str, params: tuple[Any, ...] | None = None) -> _Conn:
        self.params = params
        return self

    def fetchall(self) -> list[Any]:
        return []

    def commit(self) -> None:
        return None


def test_python_call_binds_a_uuid_string_not_a_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call shape that 500'd: list_sources(settings), DATABASE_URL set."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:1/dms")
    get_settings.cache_clear()
    conn = _Conn()
    seen: list[object] = []
    real_uuid = UUID

    def _uuid(value: object) -> UUID:
        seen.append(value)
        assert isinstance(value, str)
        assert type(value).__name__ != "Query"
        return real_uuid(str(value))

    monkeypatch.setattr("dms_api.routes.library.psycopg.connect", lambda *_a, **_k: conn)
    monkeypatch.setattr("dms_api.routes.library.set_tenant_context", lambda *_a, **_k: None)
    monkeypatch.setattr("dms_api.routes.library.UUID", _uuid)
    try:
        list_sources(get_settings())
    finally:
        get_settings.cache_clear()
    assert conn.params is not None
    assert len(conn.params) == 1
    assert isinstance(conn.params[0], UUID)
    assert seen and all(isinstance(value, str) for value in seen)


def test_query_object_never_reaches_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:1/dms")
    get_settings.cache_clear()

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("postgres must not open for a Query default")

    monkeypatch.setattr("dms_api.routes.library.psycopg.connect", _boom)
    try:
        with pytest.raises(TypeError, match="space_id"):
            _list_sources(get_settings(), space_id=Query(None))  # type: ignore[arg-type]
    finally:
        get_settings.cache_clear()


def test_space_sources_passes_the_path_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:1/dms")
    get_settings.cache_clear()
    seen: list[object] = []

    def _spy(_settings: object, *, space_id: object = None) -> list[dict[str, Any]]:
        seen.append(space_id)
        return []

    monkeypatch.setattr("dms_api.routes.library._list_sources", _spy)
    try:
        client = TestClient(create_app())
        res = client.get(f"/v1/spaces/{FINANCE}/sources")
    finally:
        get_settings.cache_clear()
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == len(body["sources"])
    assert seen == [FINANCE]
    assert type(seen[0]) is str
