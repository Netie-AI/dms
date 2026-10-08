"""OV-MINT-BEARER-01 (dms#362): fetch the intermediate with the dms Bearer.

OV b4d68021 (OV#130) 401s a reveal-only re-register of an existing service.
DMS used to re-register on every fetch, so it never got its key. The normal
path is now ``POST /keys/intermediate`` with ``Authorization: Bearer``.
``POST /keys/services`` runs only on a first mint (no token anywhere), and the
returned token is persisted 0600. DMS never sends or reads the OV admin
credential. OpenVault is mocked; no network.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import dms_executor.manifest as manifest_mod
import httpx
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import QueryResult
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import (
    INTERMEDIATE_TTL_ENV,
    KEY_EXPIRY_SKEW,
    OV_TOKEN_MISSING,
    OV_TOKEN_UNAUTHORIZED,
    SERVICE_TOKEN_ENV,
    SERVICE_TOKEN_FILE_ENV,
    ManifestMinter,
    OpenVaultTokenError,
)
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OV = "http://ov.test"
CURRENT = "svc-current-TOKEN-do-not-log"
MINTED = "svc-minted-TOKEN-do-not-log"
ADMIN_SENTINEL = "ADMIN-SENTINEL-must-never-leave-dms"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
#: A base pack phrase, so the ask reaches pack submit and the manifest mint.
PACK_Q = "What is our total spend?"


@dataclass
class _OpenVault:
    """b4d68021 shape: register once, re-register 401, intermediate needs Bearer."""

    accept: str = CURRENT
    intermediate_status: int | None = None
    services_status: int | None = None
    wrong_token_status: int = 403
    not_after: str | None = "2099-01-01T00:00:00+00:00"
    echo_service_id: str | None = None
    calls: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/keys/services":
            if self.services_status is not None:
                return httpx.Response(self.services_status, json={"detail": "re-register"})
            self.accept = MINTED
            sid = self.echo_service_id or json.loads(request.content)["service_id"]
            return httpx.Response(200, json={"service_id": sid, "token": MINTED})
        if path == "/keys/intermediate":
            if self.intermediate_status is not None:
                return httpx.Response(self.intermediate_status, json={"detail": "no"})
            # OV verify_service is an exact, case-sensitive match on service_id.
            sid = json.loads(request.content)["service_id"]
            if sid != "dms" or request.headers.get("authorization") != f"Bearer {self.accept}":
                return httpx.Response(
                    self.wrong_token_status, json={"detail": "service is not authorised to sign"}
                )
            seed = Ed25519PrivateKey.generate().private_bytes_raw()
            body = {
                "kid": "kid-ov-1",
                "private_key": base64.urlsafe_b64encode(seed).decode().rstrip("="),
            }
            if self.not_after is not None:
                body["not_after"] = self.not_after
            return httpx.Response(200, json=body)
        if path == "/v1/contract/jwks/refresh":
            return httpx.Response(200, json={"kids": ["kid-ov-1"]})
        return httpx.Response(404)

    def minter(self) -> ManifestMinter:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return ManifestMinter(openvault_url=OV, http=client)

    def paths(self) -> list[str]:
        return [c.url.path for c in self.calls]

    def intermediate_bearers(self) -> list[str]:
        inter = [c for c in self.calls if c.url.path == "/keys/intermediate"]
        return [c.headers["authorization"] for c in inter]

    def assert_no_admin(self) -> None:
        for req in self.calls:
            for name, value in req.headers.items():
                assert "admin" not in name.lower(), f"{req.url.path} sent {name}"
                assert ADMIN_SENTINEL not in value


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SERVICE_TOKEN_ENV, raising=False)
    monkeypatch.delenv(SERVICE_TOKEN_FILE_ENV, raising=False)
    monkeypatch.setenv("CORTEX_URL", "http://cortex.test")
    # Present so a regression that reads it would put it on the wire.
    monkeypatch.setenv("OPENVAULT_ADMIN_TOKEN", ADMIN_SENTINEL)


def test_bearer_fetch_200_returns_intermediate_without_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SERVICE_TOKEN_ENV, CURRENT)
    ov = _OpenVault()
    key = ov.minter().fetch_intermediate()
    assert key.kid == "kid-ov-1"
    assert "/keys/services" not in ov.paths()
    inter = [c for c in ov.calls if c.url.path == "/keys/intermediate"]
    assert len(inter) == 1
    assert inter[0].headers["authorization"] == f"Bearer {CURRENT}"
    ov.assert_no_admin()


def test_token_file_is_read_without_register(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "ov_token"
    secret.write_text(CURRENT + "\n", encoding="utf-8")
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault()
    assert ov.minter().fetch_intermediate().kid == "kid-ov-1"
    assert ov.paths().count("/keys/services") == 0
    ov.assert_no_admin()


def test_missing_token_first_mint_persists_then_uses_bearer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = tmp_path / "secrets" / "ov_token"
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault(accept="")
    m = ov.minter()

    assert m.fetch_intermediate().kid == "kid-ov-1"
    assert ov.paths().count("/keys/services") == 1
    assert secret.read_text(encoding="utf-8") == MINTED
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600

    m.fetch_intermediate()
    assert ov.paths().count("/keys/services") == 1, "second fetch re-registered"
    inter = [c for c in ov.calls if c.url.path == "/keys/intermediate"]
    assert [c.headers["authorization"] for c in inter] == [f"Bearer {MINTED}"] * 2
    assert MINTED not in caplog.text
    ov.assert_no_admin()


def test_intermediate_401_names_unauthorized_and_never_reregisters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "ov_token"
    secret.write_text(CURRENT, encoding="utf-8")
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault(intermediate_status=401)
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert ei.value.code == OV_TOKEN_UNAUTHORIZED
    assert CURRENT not in f"{ei.value!r} {ei.value}"
    assert ov.paths() == ["/keys/intermediate", "/keys/intermediate"], "exactly one retry"
    assert ov.intermediate_bearers() == [f"Bearer {CURRENT}"] * 2
    assert secret.read_text(encoding="utf-8") == CURRENT
    ov.assert_no_admin()


def test_wrong_token_403_is_unauthorized_not_reregister(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SERVICE_TOKEN_ENV, "stale-token")
    ov = _OpenVault()
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert ei.value.code == OV_TOKEN_UNAUTHORIZED
    assert ov.paths() == ["/keys/intermediate", "/keys/intermediate"]
    ov.assert_no_admin()


@pytest.mark.parametrize("wrong_status", [401, 403])
def test_unauthorized_retry_rereads_rotated_file_token_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wrong_status: int
) -> None:
    """Token cached at startup; DevOps rotates the file; the one retry picks it up."""
    secret = tmp_path / "ov_token"
    secret.write_text("svc-old-token", encoding="utf-8")
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault(wrong_token_status=wrong_status)
    m = ov.minter()
    secret.write_text(CURRENT, encoding="utf-8")
    assert m.fetch_intermediate().kid == "kid-ov-1"
    assert ov.intermediate_bearers() == ["Bearer svc-old-token", f"Bearer {CURRENT}"]
    assert "/keys/services" not in ov.paths()
    ov.assert_no_admin()


def test_token_is_loaded_once_and_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "ov_token"
    secret.write_text(CURRENT, encoding="utf-8")
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault()
    m = ov.minter()
    secret.unlink()
    m.fetch_intermediate()
    m.fetch_intermediate()
    assert ov.intermediate_bearers() == [f"Bearer {CURRENT}"] * 2
    assert "/keys/services" not in ov.paths()


class _Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 6, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def test_key_cached_until_expiry_skew_then_refetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SERVICE_TOKEN_ENV, CURRENT)
    clock = _Clock()
    monkeypatch.setattr(manifest_mod, "_now", clock)
    t0 = clock.t
    ov = _OpenVault(not_after=(t0 + timedelta(seconds=600)).isoformat())
    m = ov.minter()

    m._ensure_key()
    clock.t = t0 + timedelta(seconds=600) - KEY_EXPIRY_SKEW - timedelta(seconds=1)
    m._ensure_key()
    assert len(ov.intermediate_bearers()) == 1, "refetched before expiry"
    clock.t = t0 + timedelta(seconds=600) - KEY_EXPIRY_SKEW
    m._ensure_key()
    assert len(ov.intermediate_bearers()) == 2, "no refetch at expiry minus skew"
    assert "/keys/services" not in ov.paths()


def test_ttl_fallback_when_ov_omits_not_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SERVICE_TOKEN_ENV, CURRENT)
    monkeypatch.setenv(INTERMEDIATE_TTL_ENV, "120")
    clock = _Clock()
    monkeypatch.setattr(manifest_mod, "_now", clock)
    t0 = clock.t
    ov = _OpenVault(not_after=None)
    m = ov.minter()

    key = m._ensure_key()
    assert key.not_after == t0 + timedelta(seconds=120)
    sent = [c for c in ov.calls if c.url.path == "/keys/intermediate"][0]
    assert b'"ttl_s":120' in sent.content.replace(b" ", b"")
    clock.t = t0 + timedelta(seconds=89)
    m._ensure_key()
    assert len(ov.intermediate_bearers()) == 1
    clock.t = t0 + timedelta(seconds=90)
    m._ensure_key()
    assert len(ov.intermediate_bearers()) == 2


def test_no_token_and_no_file_is_missing_with_no_ov_call() -> None:
    ov = _OpenVault()
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert ei.value.code == OV_TOKEN_MISSING
    assert ov.calls == []


def test_first_mint_refused_by_ov_is_missing_and_writes_no_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Registered on OV but DMS lost the token: only an admin rotate fixes it."""
    secret = tmp_path / "ov_token"
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault(services_status=401)
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert ei.value.code == OV_TOKEN_MISSING
    assert ov.paths() == ["/keys/services"]
    assert secret.read_text(encoding="utf-8") == ""
    ov.assert_no_admin()


def test_ov_service_id_is_exactly_lowercase_dms_on_every_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OV service_id is case-sensitive. ``DMS`` / ``Dms`` must never be sent."""
    assert manifest_mod.OV_SERVICE_ID == "dms"
    with pytest.raises(TypeError):
        ManifestMinter(service_id="DMS")  # type: ignore[call-arg]
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(tmp_path / "ov_token"))
    ov = _OpenVault(accept="")
    m = ov.minter()
    m.fetch_intermediate()
    m._key = None
    m.fetch_intermediate()
    ov_calls = [c for c in ov.calls if c.url.path.startswith("/keys/")]
    assert [c.url.path for c in ov_calls] == [
        "/keys/services",
        "/keys/intermediate",
        "/keys/intermediate",
    ]
    for c in ov_calls:
        assert json.loads(c.content)["service_id"] == "dms"
        assert b'"DMS"' not in c.content and b'"Dms"' not in c.content


@pytest.mark.parametrize("wrong", ["DMS", "Dms"])
def test_case_mismatched_service_id_is_refused_by_ov(
    monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    """If the pin drifted in case, OV refuses it and DMS abstains named; no register."""
    monkeypatch.setenv(SERVICE_TOKEN_ENV, CURRENT)
    monkeypatch.setattr(manifest_mod, "OV_SERVICE_ID", wrong)
    ov = _OpenVault()
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert ei.value.code == OV_TOKEN_UNAUTHORIZED
    assert "/keys/services" not in ov.paths()


@pytest.mark.parametrize("echo", ["DMS", "Dms"])
def test_first_mint_rejects_register_echo_with_wrong_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, echo: str
) -> None:
    """A token OV issued for another-cased service is never persisted or used."""
    secret = tmp_path / "ov_token"
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(secret))
    ov = _OpenVault(accept="", echo_service_id=echo)
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert ei.value.code == "ov_mint_failed"
    assert secret.read_text(encoding="utf-8") == ""
    assert "/keys/intermediate" not in ov.paths()


def test_dms_source_never_names_the_ov_admin_credential() -> None:
    hits = []
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if "openvault_admin" in text or "x-openvault-admin" in text:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []


@dataclass
class _Cortex:
    submits: list[Any] = field(default_factory=list)
    asks: list[AskRequest] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True, status="ok", run_id="run_sql", output={"rows": [{"sku_count": 4217}]}
            )
        return QueryResult(ok=True, status="bound", run_id="run_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_ok", hash="hash_ok_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(answer="Spoof 999999.", badge="certified", route="certified_metric")


@pytest.mark.parametrize("fallback", ["0", "1"])
@pytest.mark.parametrize(
    ("status", "code"), [(401, OV_TOKEN_UNAUTHORIZED), (None, OV_TOKEN_MISSING)]
)
def test_chat_ask_abstains_with_named_ov_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fallback: str,
    status: int | None,
    code: str,
) -> None:
    """POST /v1/chat/ask: the pack lane names the OV reason, not ``Cortex SQL fail``."""
    from dms_api import settings as settings_mod

    if status is not None:
        monkeypatch.setenv(SERVICE_TOKEN_ENV, CURRENT)
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", fallback)
    settings_mod.get_settings.cache_clear()
    ov = _OpenVault(intermediate_status=status)
    cortex = _Cortex()
    app = create_app()
    exe = Executor(cortex=cortex, minter=ov.minter(), warehouse_path=tmp_path / "w.duckdb")  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    try:
        resp = TestClient(app).post(
            "/v1/chat/ask",
            json={"question": PACK_Q, "space_id": FINANCE, "session_id": "ses_ov"},
        )
    finally:
        exe.close()
        settings_mod.get_settings.cache_clear()

    assert resp.status_code == 200
    env = resp.json()
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert env.get("demo_fallback_used") is not True
    assert code in env["assumptions"]
    assert code in env["text"]
    assert "Cortex SQL fail" not in env["text"]
    assert "999999" not in env["text"]
    assert CURRENT not in resp.text
    assert cortex.submits == [] and cortex.asks == [] and cortex.insights == []
    assert "/keys/services" not in ov.paths()
    assert len(ov.intermediate_bearers()) == (2 if status is not None else 0)
    ov.assert_no_admin()


def test_n_asks_reuse_one_client_and_one_intermediate_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Five asks on five sessions mint five manifests from one key and one client."""
    monkeypatch.setenv(SERVICE_TOKEN_ENV, CURRENT)
    ov = _OpenVault()
    made: list[httpx.Client] = []

    class _Counting(httpx.Client):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(ov.handler)
            super().__init__(**kwargs)
            made.append(self)

    monkeypatch.setattr(manifest_mod.httpx, "Client", _Counting)
    db = tmp_path / "w.duckdb"
    ensure_demo_warehouse(db)
    cortex = _Cortex()
    exe = Executor(cortex=cortex, minter=ManifestMinter(openvault_url=OV), warehouse_path=db)  # type: ignore[arg-type]
    try:
        envs = [
            exe.live_ask(PACK_Q, space_id=FINANCE, session_id=f"ses_n{i}") for i in range(5)
        ]
    finally:
        exe.close()

    for env in envs:
        assert_envelope_valid(env)
        assert env["badge"] == "L1_GOVERNED_METRIC"
        assert env["rows"] == [{"sku_count": 4217}]
        assert "4217" in env["text"]
    assert len(made) == 1
    assert made[0].timeout.connect == 3.0 and made[0].timeout.read == 10.0
    assert ov.intermediate_bearers() == [f"Bearer {CURRENT}"]
    assert "/keys/services" not in ov.paths()
    ov.assert_no_admin()
