"""OV-MINT-TOKEN-ATOMIC-01 (dms#377): the first mint never leaves a half state.

Before this ticket ``_first_mint`` opened ``DMS_OV_SERVICE_TOKEN_FILE`` with
``O_TRUNC`` before ``POST /keys/services``. A failed register left an empty
0600 file, and a file that was already there was truncated and re-moded.

The rule under test: nothing a later boot reads is created, truncated or
replaced before the register call has returned a token. The token goes to a
0600 temp file in the same directory, is fsynced, and is renamed into place.
OpenVault is mocked; no network.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import dms_executor.manifest as manifest_mod
import httpx
import pytest
from dms_executor.manifest import (
    OV_MINT_FAILED,
    OV_TOKEN_MISSING,
    SERVICE_TOKEN_ENV,
    SERVICE_TOKEN_FILE_ENV,
    ManifestMinter,
    OpenVaultTokenError,
)

OV = "http://ov.test"
MINTED = "svc-minted-TOKEN-do-not-log"
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes and symlinks")


@dataclass
class _OpenVault:
    """Register route only. ``reply`` builds the answer, or raises a transport error."""

    reply: Callable[[httpx.Request], httpx.Response] | None = None
    during_register: Callable[[], None] | None = None
    registered: bool = False
    calls: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path != "/keys/services":
            return httpx.Response(404)
        if self.during_register is not None:
            self.during_register()
        if self.reply is not None:
            return self.reply(request)
        with self.lock:
            if self.registered:
                return httpx.Response(401, json={"detail": "re-register"})
            self.registered = True
        sid = json.loads(request.content)["service_id"]
        return httpx.Response(200, json={"service_id": sid, "token": MINTED})

    def minter(self) -> ManifestMinter:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return ManifestMinter(openvault_url=OV, http=client)


def _status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(code, json={"detail": "no"})


def _json(body: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(200, json=body)


def _lost(request: httpx.Request) -> httpx.Response:
    """OV may have registered; the answer never arrives."""
    raise httpx.ReadTimeout("response lost", request=request)


def _not_json(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>proxy error</html>")


FAILURES = {
    "http_500": (_status(500), OV_MINT_FAILED),
    "http_401_already_registered": (_status(401), OV_TOKEN_MISSING),
    "response_lost": (_lost, OV_MINT_FAILED),
    "connect_refused": (
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
        OV_MINT_FAILED,
    ),
    "wrong_service_id": (_json({"service_id": "DMS", "token": MINTED}), OV_MINT_FAILED),
    "no_token": (_json({"service_id": "dms"}), OV_MINT_FAILED),
    "blank_token": (_json({"service_id": "dms", "token": "  "}), OV_MINT_FAILED),
    "body_not_json": (_not_json, OV_MINT_FAILED),
    "token_is_a_bool": (_json({"service_id": "dms", "token": True}), OV_MINT_FAILED),
    "token_is_a_number": (_json({"service_id": "dms", "token": 123}), OV_MINT_FAILED),
    "token_is_a_list": (_json({"service_id": "dms", "token": ["x"]}), OV_MINT_FAILED),
    "token_has_a_newline": (_json({"service_id": "dms", "token": "a\nb"}), OV_MINT_FAILED),
    "token_has_a_space": (_json({"service_id": "dms", "token": "a b"}), OV_MINT_FAILED),
    "token_has_a_control_char": (_json({"service_id": "dms", "token": "a\x00b"}), OV_MINT_FAILED),
    "body_not_an_object": (_json(["dms", MINTED]), OV_MINT_FAILED),
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SERVICE_TOKEN_ENV, raising=False)
    monkeypatch.delenv(SERVICE_TOKEN_FILE_ENV, raising=False)


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "secrets" / "ov_token"
    path.parent.mkdir()
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(path))
    return path


def _fail(ov: _OpenVault) -> OpenVaultTokenError:
    with pytest.raises(OpenVaultTokenError) as ei:
        ov.minter().fetch_intermediate()
    assert MINTED not in f"{ei.value!r} {ei.value} {ei.value.__cause__!r}"
    return ei.value


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_failed_register_leaves_no_file_behind(secret: Path, case: str) -> None:
    """The issue's own words: no file and no empty file left behind."""
    reply, code = FAILURES[case]
    ov = _OpenVault(reply=reply)
    assert _fail(ov).code == code
    assert ov.calls == ["/keys/services"]
    assert not secret.exists(), "a failed first mint created the token file"
    assert list(secret.parent.iterdir()) == [], "a failed first mint left a file behind"


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_failed_register_leaves_an_existing_file_untouched(secret: Path, case: str) -> None:
    """A file with no token in it still reaches the first mint. It must not change."""
    secret.write_bytes(b"\n")
    os.chmod(secret, 0o640)
    before = secret.stat()
    reply, code = FAILURES[case]
    ov = _OpenVault(reply=reply)
    assert _fail(ov).code == code
    after = secret.stat()
    assert secret.read_bytes() == b"\n", "the existing token file was rewritten"
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    if os.name == "posix":
        assert stat.S_IMODE(after.st_mode) == 0o640, "the existing token file was re-moded"
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]


def test_second_boot_after_a_failed_first_mint_registers_and_persists(secret: Path) -> None:
    """Boot one fails. Boot two must see a clean first-mint state, not a trusted one."""
    assert _fail(_OpenVault(reply=_status(503))).code == OV_MINT_FAILED
    assert manifest_mod._load_service_token() == ""
    assert not secret.exists()

    ov = _OpenVault()
    assert ov.minter()._first_mint() == MINTED
    assert secret.read_text(encoding="utf-8") == MINTED
    assert manifest_mod._load_service_token() == MINTED, "boot three would not read it back"


def test_success_writes_the_token_0600_and_leaves_only_the_token_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "made" / "by" / "dms" / "ov_token"
    monkeypatch.setenv(SERVICE_TOKEN_FILE_ENV, str(path))
    ov = _OpenVault()
    assert ov.minter()._first_mint() == MINTED
    assert path.read_text(encoding="utf-8") == MINTED
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_token_file_does_not_exist_until_register_has_answered(secret: Path) -> None:
    """While OV is still deciding, a second process must see no token file at all."""
    seen: list[tuple[bool, list[tuple[int, int]]]] = []

    def look() -> None:
        others = [p.stat() for p in secret.parent.iterdir()]
        seen.append((secret.exists(), [(stat.S_IMODE(s.st_mode), s.st_size) for s in others]))

    ov = _OpenVault(during_register=look)
    assert ov.minter()._first_mint() == MINTED
    assert len(seen) == 1
    target_existed, others = seen[0]
    assert target_existed is False, "the token file existed before OV issued a token"
    for mode, size in others:
        assert size == 0
        if os.name == "posix":
            assert mode == 0o600, "the temp file was not 0600 from creation"


def test_existing_empty_file_is_replaced_whole_and_is_0600(secret: Path) -> None:
    secret.write_bytes(b"")
    os.chmod(secret, 0o644)
    assert _OpenVault().minter()._first_mint() == MINTED
    assert secret.read_text(encoding="utf-8") == MINTED
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_token_is_fsynced_before_the_rename(secret: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A power cut after the rename must not leave a named, empty token file."""
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            events.append(f"fsync:{os.fstat(fd).st_size}")
        real_fsync(fd)

    def replace(src: str, dst: str) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(manifest_mod.os, "fsync", fsync)
    monkeypatch.setattr(manifest_mod.os, "replace", replace)
    assert _OpenVault().minter()._first_mint() == MINTED
    assert events == [f"fsync:{len(MINTED)}", "replace"]


def test_directory_at_the_token_path_refuses_before_any_ov_call(secret: Path) -> None:
    """OV must not issue a token DMS already knows it cannot store."""
    secret.mkdir()
    ov = _OpenVault()
    assert _fail(ov).code == OV_TOKEN_MISSING
    assert ov.calls == [] and ov.registered is False
    assert list(secret.iterdir()) == []
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]


def test_unwritable_directory_refuses_before_any_ov_call(
    secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patched, not chmod: a chmod proves nothing when the suite runs as root."""

    def denied(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(manifest_mod.tempfile, "mkstemp", denied)
    ov = _OpenVault()
    assert _fail(ov).code == OV_TOKEN_MISSING
    assert ov.calls == [] and ov.registered is False
    assert list(secret.parent.iterdir()) == []


@posix_only
@pytest.mark.parametrize("case", ["http_500", "response_lost", "success"])
def test_symlink_at_the_token_path_never_writes_through_it(secret: Path, case: str) -> None:
    """The old O_TRUNC followed the link and emptied whatever it pointed at."""
    victim = secret.parent.parent / "victim.conf"
    victim.write_bytes(b"   \n")
    os.chmod(victim, 0o644)
    secret.symlink_to(victim)
    ov = _OpenVault(reply=None if case == "success" else FAILURES[case][0])
    if case == "success":
        assert ov.minter()._first_mint() == MINTED
        assert not secret.is_symlink(), "the token was written through the link"
        assert secret.read_text(encoding="utf-8") == MINTED
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    else:
        _fail(ov)
        assert secret.is_symlink()
    assert victim.read_bytes() == b"   \n", "the link target was modified"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]


def test_rename_refused_falls_back_to_an_in_place_write_after_success(
    secret: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bind-mounted single file cannot be renamed over. OV has issued by then."""
    secret.write_bytes(b"")
    inode = secret.stat().st_ino

    def busy(_src: str, _dst: str) -> None:
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(manifest_mod.os, "replace", busy)
    with caplog.at_level("WARNING"):
        assert _OpenVault().minter()._first_mint() == MINTED
    assert secret.read_text(encoding="utf-8") == MINTED
    assert secret.stat().st_ino == inode
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]
    assert "not atomic" in caplog.text, "the degraded write was silent"
    assert MINTED not in caplog.text
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_rename_refused_with_no_file_to_write_in_place_is_a_named_failure(
    secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issued and not stored is said out loud, and nothing half-made stays behind."""

    def denied(_src: str, _dst: str) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(manifest_mod.os, "replace", denied)
    ov = _OpenVault()
    err = _fail(ov)
    assert err.code == OV_MINT_FAILED
    assert "not stored" in str(err)
    assert ov.registered is True
    assert list(secret.parent.iterdir()) == []


def test_crash_while_writing_the_token_leaves_no_file(
    secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(manifest_mod.os, "fsync", boom)
    with pytest.raises(KeyboardInterrupt):
        _OpenVault().minter()._first_mint()
    assert list(secret.parent.iterdir()) == []


def test_two_first_mints_at_once_leave_one_whole_token(secret: Path) -> None:
    """OV registers one and 401s the other. The file is the winner's token, whole."""
    ov = _OpenVault()
    gate = threading.Barrier(2, timeout=10)
    ov.during_register = lambda: gate.wait()
    results: list[str] = []

    def run() -> None:
        try:
            results.append(ov.minter()._first_mint())
        except OpenVaultTokenError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(results) == sorted([MINTED, OV_TOKEN_MISSING])
    assert secret.read_text(encoding="utf-8") == MINTED
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]


def _refuse_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(_src: str, _dst: str) -> None:
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(manifest_mod.os, "replace", busy)


def test_short_in_place_write_leaves_no_part_of_a_token(
    secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversary P1: a disk-full in-place write left 8 bytes the next boot sent as Bearer."""
    secret.write_bytes(b"")
    _refuse_rename(monkeypatch)
    real_fsync = os.fsync
    calls: list[int] = []

    def fsync(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 2:  # the in-place write: keep 8 bytes, then fail
            os.ftruncate(fd, 8)
            raise OSError(28, "No space left on device")
        real_fsync(fd)

    monkeypatch.setattr(manifest_mod.os, "fsync", fsync)
    ov = _OpenVault()
    err = _fail(ov)
    assert err.code == OV_MINT_FAILED and "not stored" in str(err)
    assert len(calls) == 2
    assert secret.read_bytes() == b"", "part of a token was left in the token file"
    assert manifest_mod._load_service_token() == ""
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]


@posix_only
@pytest.mark.parametrize("reader", [False, True])
def test_in_place_write_refuses_a_fifo_without_blocking(
    secret: Path, monkeypatch: pytest.MonkeyPatch, reader: bool
) -> None:
    """With a reader the open succeeds, so only the regular-file check refuses it."""
    os.mkfifo(secret)
    os.chmod(secret, 0o644)
    rfd = os.open(secret, os.O_RDONLY | os.O_NONBLOCK) if reader else None
    try:
        _refuse_rename(monkeypatch)
        err = _fail(_OpenVault())
        assert err.code == OV_MINT_FAILED and "not stored" in str(err)
        assert rfd is None or os.read(rfd, 64) == b"", "the token was written into the FIFO"
    finally:
        if rfd is not None:
            os.close(rfd)
    assert stat.S_ISFIFO(secret.lstat().st_mode)
    assert stat.S_IMODE(secret.lstat().st_mode) == 0o644, "the FIFO was re-moded"
    assert [p.name for p in secret.parent.iterdir()] == [secret.name]
