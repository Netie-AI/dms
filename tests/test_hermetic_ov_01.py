"""F125a (refs #273) - the suite never reaches a live OpenVault or its key.

``dms_api.app`` builds the app at import and ``Executor.startup()`` probes
:5000, then fetches the intermediate signing key from whatever answers. The
conftest keeps both off the live stack. A socket sentinel refuses and records
every connect to a live-stack port, so a regression shows as an attempt, never
as a real connection.
"""

from __future__ import annotations

import errno
import importlib
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from dms_executor.manifest import (
    SERVICE_TOKEN_ENV,
    SERVICE_TOKEN_FILE_ENV,
    ManifestMinter,
    OpenVaultTokenError,
)

LIVE_STACK_PORTS = frozenset({5000, 8010, 8011, 8090})


def _port(address: Any) -> int | None:
    if isinstance(address, tuple) and len(address) >= 2 and isinstance(address[1], int):
        return address[1]
    return None


@pytest.fixture
def sentinel(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Refuse and record every socket connect to a live-stack port."""
    attempts: list[Any] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        if _port(address) in LIVE_STACK_PORTS:
            attempts.append(address)
            raise ConnectionRefusedError(errno.ECONNREFUSED, f"sentinel refused {address}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if _port(address) in LIVE_STACK_PORTS:
            attempts.append(address)
            return errno.ECONNREFUSED
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.delenv("OPENVAULT_URL", raising=False)
    # A token in env sends a key fetch straight to /keys/intermediate, and keeps
    # a first-mint off any real token file.
    monkeypatch.setenv(SERVICE_TOKEN_ENV, "hermetic-test-token")
    monkeypatch.delenv(SERVICE_TOKEN_FILE_ENV, raising=False)
    return attempts


@pytest.fixture
def fake_openvault() -> Iterator[str]:
    """A loopback OpenVault that answers health on an ephemeral port."""

    class _Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200 if self.path == "/api/healthz" else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_a: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_the_sentinel_sees_a_connect_to_5000(sentinel: list[Any]) -> None:
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 5000), timeout=1)
    assert [_port(a) for a in sentinel] == [5000]


def test_import_and_create_app_make_no_live_connection(sentinel: list[Any]) -> None:
    import dms_api

    saved = sys.modules.pop("dms_api.app", None)
    try:
        mod = importlib.import_module("dms_api.app")
        created = mod.create_app()
    finally:
        if saved is not None:
            sys.modules["dms_api.app"] = saved
            dms_api.app = saved  # type: ignore[attr-defined]
    assert sentinel == []
    for app in (mod.app, created):
        assert app.state.ask_service._minter._key is None


def test_a_default_minter_never_fetches_the_live_key(sentinel: list[Any]) -> None:
    minter = ManifestMinter()
    try:
        with pytest.raises(OpenVaultTokenError):
            minter.fetch_intermediate()
    finally:
        minter.close()
    assert sentinel == []


def test_discovery_still_finds_a_loopback_fake(sentinel: list[Any], fake_openvault: str) -> None:
    from dms_executor import probe_openvault

    assert probe_openvault()[0] is None
    assert probe_openvault(preferred_url=fake_openvault)[0] == fake_openvault
    assert sentinel == []


def test_real_openvault_opts_back_in(sentinel: list[Any], real_openvault: None) -> None:
    from dms_executor import probe_openvault

    assert probe_openvault(timeout=1.0)[0] is None
    assert 5000 in {_port(a) for a in sentinel}
    sentinel.clear()
    minter = ManifestMinter()
    try:
        with pytest.raises(OpenVaultTokenError):
            minter.fetch_intermediate()
    finally:
        minter.close()
    assert sentinel, "the real fetch went to :5000 and the sentinel refused it"


def test_health_probes_reach_no_live_port(sentinel: list[Any]) -> None:
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        got = client.get("/health")
    assert got.status_code == 200
    assert sentinel == []
