"""Pytest defaults.

``DMS_CLOOP_B`` and ``DMS_LANE_ONTOLOGY_RANKED`` stay off unless a test sets
them. The suite exercises those defaults.

Hermetic OpenVault (F125a, refs #273). ``dms_api.app`` runs ``create_app()`` at
import, which runs ``Executor.startup()``: it probes OpenVault on :5000 and, if
one answers, loads the live intermediate signing key into the pytest process.
Patched here, at conftest import, before any test module imports ``dms_api``:

* discovery never offers a live-stack URL, so with no fake configured
  ``probe_openvault`` reports OpenVault absent and does no network I/O. A test
  that points ``OPENVAULT_URL`` or ``preferred_url`` at a loopback fake on any
  other port still discovers it;
* ``ManifestMinter.fetch_intermediate`` refuses a live-stack URL the way an
  unreachable vault does, so no lazy fetch can reach the live key either;
* every other httpx request to a live-stack port (``/health`` probes Cortex
  :8010 and OpenVault :5000, a default ``CortexClient`` points at :8010) is
  refused with ``httpx.ConnectError`` before a socket opens: the stack reads
  as down, deterministically, whether or not it is running.

A test that must exercise the real discovery, key fetch, or transport requests
the ``real_openvault`` fixture.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from urllib.parse import urlsplit

import dms_executor.openvault_discovery as _ovd
import httpx
import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.manifest import OV_MINT_FAILED, ManifestMinter, OpenVaultTokenError

# The local live stack: OpenVault, Cortex, and the two sidecars beside them.
LIVE_STACK_PORTS = frozenset({5000, 8010, 8011, 8090})


def _targets_live_stack(url: str | None) -> bool:
    try:
        return urlsplit(str(url or "")).port in LIVE_STACK_PORTS
    except ValueError:
        return False


_real_candidate_urls = _ovd._candidate_urls
_real_fetch_intermediate = ManifestMinter.fetch_intermediate


def _hermetic_candidate_urls(preferred: str | None) -> list[str]:
    return [u for u in _real_candidate_urls(preferred) if not _targets_live_stack(u)]


def _hermetic_fetch_intermediate(self: ManifestMinter, *, ttl_s: int | None = None):
    if _targets_live_stack(self.openvault_url):
        raise OpenVaultTokenError(
            OV_MINT_FAILED, "intermediate: ConnectError (hermetic tests: live OpenVault)"
        )
    return _real_fetch_intermediate(self, ttl_s=ttl_s)


_real_handle = httpx.HTTPTransport.handle_request
_real_handle_async = httpx.AsyncHTTPTransport.handle_async_request


def _refuse_live(request: httpx.Request) -> None:
    if request.url.port in LIVE_STACK_PORTS:
        raise httpx.ConnectError(
            f"hermetic tests: live-stack port {request.url.port} refused", request=request
        )


@functools.wraps(_real_handle)
def _hermetic_handle(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
    _refuse_live(request)
    return _real_handle(self, request)


@functools.wraps(_real_handle_async)
async def _hermetic_handle_async(
    self: httpx.AsyncHTTPTransport, request: httpx.Request
) -> httpx.Response:
    _refuse_live(request)
    return await _real_handle_async(self, request)


_ovd._candidate_urls = _hermetic_candidate_urls
ManifestMinter.fetch_intermediate = _hermetic_fetch_intermediate  # type: ignore[method-assign]
httpx.HTTPTransport.handle_request = _hermetic_handle  # type: ignore[method-assign]
httpx.AsyncHTTPTransport.handle_async_request = _hermetic_handle_async  # type: ignore[method-assign]


@pytest.fixture
def real_openvault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Opt out: the real discovery, key fetch, and transport, for this test only.

    Pair it with a loopback fake or a socket sentinel. Nothing else stops it
    reaching a live OpenVault on :5000.
    """
    monkeypatch.setattr(_ovd, "_candidate_urls", _real_candidate_urls)
    monkeypatch.setattr(ManifestMinter, "fetch_intermediate", _real_fetch_intermediate)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _real_handle)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _real_handle_async)
    yield


@pytest.fixture(autouse=True)
def _fresh_answer_model_calls() -> None:
    """F124: every test starts with zero generate calls recorded.

    ``cortex_client.compute._answer_model_calls`` is a ContextVar; pytest runs
    every sync test in one context, so a count spent by one test (a cap test
    calling ``note_model_call``) otherwise carries into the next.
    """
    begin_answer_model_calls()
