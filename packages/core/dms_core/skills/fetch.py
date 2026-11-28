"""Fetch a public git skill and pin it to the commit SHA that was checked out.

No credentials in the URL. No private or loopback host. The git process
gets the URL and the ref as arguments, never a shell string. A checkout
whose HEAD is not that SHA is ``pin_mismatch`` and is not stored.

ponytail: one DNS lookup before git. A host that rebinds after the check
can still move. Upgrade: fetch through a proxy that pins the resolved address.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dms_core.skills.errors import SkillError
from dms_core.skills.validate import build_bundle

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^(?:HEAD|[A-Za-z0-9][A-Za-z0-9._/-]{0,127})$")
_SUB = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")

Runner = Callable[[Sequence[str], str | None], str]
Resolve = Callable[[str], list[str]]


def parse_public_git_url(url: str) -> tuple[str, str | None]:
    """https URL with no userinfo, plus an optional fragment subpath."""
    raw = (url or "").strip()
    if not raw or len(raw) > 500:
        raise SkillError("git_url_rejected")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host:
        raise SkillError("git_url_rejected")
    if parts.username or parts.password or parts.query:
        raise SkillError("git_url_rejected")
    if parts.port not in {None, 443}:
        raise SkillError("git_url_rejected")
    sub = (parts.fragment or "").strip() or None
    if sub is not None and (not _SUB.fullmatch(sub) or ".." in sub.split("/")):
        raise SkillError("skill_path_rejected")
    netloc = host if parts.port in {None, 443} else f"{host}:{parts.port}"
    clone = urlunsplit(("https", netloc, parts.path or "", "", ""))
    return clone, sub


def assert_public_host(host: str, resolve: Resolve | None = None) -> None:
    name = host.lower().rstrip(".")
    if name in {"localhost", "metadata.google.internal"} or name.endswith(".local"):
        raise SkillError("git_host_rejected")
    try:
        addresses = [str(ipaddress.ip_address(name))]
    except ValueError:
        addresses = (resolve or _resolve)(name)
    if not addresses:
        raise SkillError("git_host_unresolved")
    for item in addresses:
        try:
            ip = ipaddress.ip_address(item)
        except ValueError as exc:
            raise SkillError("git_host_rejected") from exc
        if not ip.is_global:
            raise SkillError("git_host_rejected")


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SkillError("git_host_unresolved") from exc
    found: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in found:
            found.append(addr)
    return found


def clean_ref(ref: str) -> str:
    text = (ref or "").strip()
    if _SHA.fullmatch(text):
        return text
    if not _REF.fullmatch(text) or ".." in text.split("/"):
        raise SkillError("git_ref_rejected")
    return text


def parse_ls_remote(text: str) -> str:
    for line in (text or "").splitlines():
        head = line.split()
        if head and _SHA.fullmatch(head[0]):
            return head[0]
    raise SkillError("pin_missing")


def git_runner(argv: Sequence[str], cwd: str | None) -> str:
    """Run git. The error is ``git_failed``. stderr is dropped."""
    if not argv or argv[0] != "git":
        raise SkillError("git_failed")
    try:
        proc = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillError("git_failed") from exc
    if proc.returncode != 0:
        raise SkillError("git_failed")
    return proc.stdout


def read_skill_dir(root: Path, subpath: str | None) -> tuple[dict[str, str], str | None]:
    """Return ``(files, skill_dir_name)``. Nested SKILL.md must be one level down."""
    base = root
    skill_dir: str | None = None
    if subpath:
        base = (root / subpath).resolve()
        if not base.is_relative_to(root.resolve()):
            raise SkillError("skill_path_rejected")
        skill_dir = Path(subpath).name
    if not base.is_dir():
        raise SkillError("skill_md_missing")
    files = _read_tree(base)
    if "SKILL.md" in files:
        return files, skill_dir
    nested = [name for name in files if name.endswith("/SKILL.md") and name.count("/") == 1]
    if len(nested) != 1 or subpath:
        raise SkillError("skill_md_missing" if not nested else "skill_md_ambiguous")
    prefix = nested[0][: -len("SKILL.md")]
    skill_dir = prefix.rstrip("/")
    trimmed = {
        key[len(prefix) :]: value for key, value in files.items() if key.startswith(prefix)
    }
    return trimmed, skill_dir


def _read_tree(base: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    total = 0
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if ".git" in rel.split("/"):
            continue
        if path.is_symlink():
            raise SkillError("symlink_rejected")
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) > 65536 or b"\x00" in data:
            raise SkillError("file_too_large" if len(data) > 65536 else "binary_file")
        total += len(data)
        if total > 262144 or len(files) >= 40:
            raise SkillError("skill_too_large" if total > 262144 else "too_many_files")
        try:
            files[rel] = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillError("binary_file") from exc
    return files


def fetch_skill(
    url: str,
    *,
    ref: str = "HEAD",
    runner: Runner | None = None,
    resolve: Resolve | None = None,
) -> dict[str, object]:
    """Resolve, checkout that SHA, validate. Nothing is executed."""
    clone, subpath = parse_public_git_url(url)
    host = urlsplit(clone).hostname or ""
    assert_public_host(host, resolve)
    wanted = clean_ref(ref)
    run = runner or git_runner
    if shutil.which("git") is None and runner is None:
        raise SkillError("git_missing")
    if _SHA.fullmatch(wanted):
        pin = wanted
    else:
        pin = parse_ls_remote(run(["git", "ls-remote", clone, wanted], None))
    work = Path(tempfile.mkdtemp(prefix="dms-skill-"))
    try:
        run(["git", "init"], str(work))
        run(["git", "remote", "add", "origin", clone], str(work))
        run(["git", "fetch", "--depth", "1", "origin", pin], str(work))
        run(["git", "checkout", "--detach", "FETCH_HEAD"], str(work))
        got = run(["git", "rev-parse", "HEAD"], str(work)).strip()
        if got != pin:
            raise SkillError("pin_mismatch")
        files, skill_dir = read_skill_dir(work, subpath)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    bundle = build_bundle(
        files,
        commit_sha=pin,
        url=clone,
        origin="git",
        skill_dir=skill_dir,
    )
    return bundle
