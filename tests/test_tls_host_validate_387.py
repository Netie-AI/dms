"""TLS-HOST-VALIDATE (#387): DMS_TLS_HOST is checked before Caddy reads it.

Caddy substitutes ``{$DMS_TLS_HOST}`` into the Caddyfile as text, before it
parses. On main a value with a newline and a brace added a second site and a
``respond`` directive to the rendered config (shown with real Caddy in the pull
request). The ui service's entry guard now admits only an RFC 1123 host name
(which covers a dotted IPv4 address) and exits otherwise, naming the variable
and the reason, never the value.

Docker-free: the guard is taken from the shipped ``docker-compose.yml`` and run
by ``sh`` with a stand-in ``caddy`` on PATH, so "Caddy started" is observed, not
inferred. The live check is section F of ``scripts/compose_tls_smoke.sh``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
CADDYFILE = ROOT / "deploy" / "compose" / "Caddyfile"
SMOKE = ROOT / "scripts" / "compose_tls_smoke.sh"

SHELLS = [s for s in ("sh", "dash", "bash", "busybox") if shutil.which(s)]

GOOD = [
    "localhost",
    "dms.bank.example",
    "DMS.Bank.Example",
    "dms-01.internal",
    "a",
    "10.0.0.5",
    "192.168.1.20",
    "xn--mnchen-3ya.example",  # an IDN in its ASCII (punycode) form
    "3com.example",
    "1.2.3.4.example",  # numeric labels are fine when the last label is a name
    "255.255.255.255",
    "a" * 63 + ".example",
    ".".join(["a" * 61] * 4),  # 247 characters, labels of 61
    ".".join(["a" * 63] * 3 + ["a" * 61]),  # exactly 253 characters
]

REFUSED = {
    "second site and directive": 'localhost:8080 {\n\trespond "INJECTED"\n}\nhttps://second.example',
    "newline": "dms.example\nrespond ok",
    "carriage return": "dms.example\r",
    "trailing newline": "localhost\n",
    "only a newline": "\n",
    "non-breaking space": "dms\u00a0example",
    "vertical tab": "dms.example\x0b",
    "DEL": "dms.example\x7f",
    "open brace": "dms.example{",
    "close brace": "dms.example}",
    "placeholder": "{$HOME}",
    "space": "dms.example other.example",
    "leading space": " dms.example",
    "trailing space": "dms.example ",
    "tab": "dms.example\tx",
    "only spaces": "   ",
    "hash": "dms.example#x",
    "comma (a second site address)": "a.example,b.example",
    "port": "dms.example:8443",
    "ipv6": "::1",
    "bracketed ipv6": "[::1]",
    "scheme": "http://dms.example",
    "path": "dms.example/x",
    "wildcard": "*.example",
    "underscore": "dms_01.example",
    "raw IDN": "münchen.example",
    "fullwidth dot": "dms．example",
    "backslash": "dms\\.example",
    "quote": '"dms.example"',
    "leading dot": ".dms.example",
    "trailing dot": "dms.example.",
    "empty label": "dms..example",
    "leading hyphen": "-dms.example",
    "trailing hyphen": "dms.example-",
    "label ends with hyphen": "dms-.example",
    "label starts with hyphen": "dms.-example",
    "label of 64": "a" * 64 + ".example",
    "254 characters": ".".join(["a" * 62] * 4) + ".bb",
    "import": "localhost\nimport /etc/caddy/tls/*",
    # An all-numeric last label is a host only as a dotted IPv4 address.
    "numeric, not IPv4 (999)": "999.999.999.999",
    "numeric, five parts": "1.2.3.4.5",
    "numeric, one part (decimal IPv4)": "2130706433",
    "numeric, two parts": "0x7f.1",
    "numeric, three parts": "1.2.3",
    "numeric zero": "0",
    "unspecified address": "0.0.0.0",
    "leading zero part": "010.1.1.1",
    "part of 256": "256.1.1.1",
}


def _guard() -> str:
    ui = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["ui"]
    command = ui["command"]
    assert command[:2] == ["sh", "-c"]
    return command[2].replace("$$", "$")  # compose writes a literal $ as $$


def _run(shell: str, tmp_path: Path, env_host: str | None, mode: str = "internal"):
    """Run the shipped guard. Returns (process, whether Caddy was started)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    marker = tmp_path / "caddy-started"
    marker.unlink(missing_ok=True)
    stub = bindir / "caddy"
    stub.write_text(f'#!/bin/sh\necho started > "{marker}"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "DMS_TLS_MODE": mode}
    if env_host is not None:
        env["DMS_TLS_HOST"] = env_host
    argv = ["busybox", "sh", "-c", _guard()] if shell == "busybox" else [shell, "-c", _guard()]
    proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
    return proc, marker.exists()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("host", GOOD)
def test_good_host_reaches_caddy(shell: str, host: str, tmp_path: Path) -> None:
    proc, started = _run(shell, tmp_path, host)
    assert proc.returncode == 0, proc.stderr
    assert started, "a valid host did not reach Caddy"
    assert proc.stderr == ""


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("label", sorted(REFUSED))
def test_refused_host_never_reaches_caddy(shell: str, label: str, tmp_path: Path) -> None:
    value = REFUSED[label]
    proc, started = _run(shell, tmp_path, value)
    assert not started, f"Caddy was started with a {label} in DMS_TLS_HOST"
    assert proc.returncode == 1
    assert "DMS_TLS_HOST" in proc.stderr and "refused:" in proc.stderr
    # The named reason is fixed text. The value is never echoed (log injection).
    assert len(proc.stderr.strip().splitlines()) == 1
    for piece in ("INJECTED", "second.example", "respond", "other.example", "$HOME"):
        assert piece not in proc.stderr


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("host", ["", None], ids=["empty", "unset"])
def test_empty_or_unset_host_is_refused_by_name(shell: str, host, tmp_path: Path) -> None:
    """Compose turns empty into ``localhost`` before the container starts
    (``${DMS_TLS_HOST:-localhost}``). Reaching the guard empty means that default
    was removed, so the guard refuses instead of letting Caddy pick one."""
    proc, started = _run(shell, tmp_path, host)
    assert not started and proc.returncode == 1
    assert "DMS_TLS_HOST" in proc.stderr and "empty" in proc.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_mode_guard_still_refuses_first(shell: str, tmp_path: Path) -> None:
    proc, started = _run(shell, tmp_path, "localhost", mode="*")
    assert not started and proc.returncode == 1
    assert "DMS_TLS_MODE must be exactly" in proc.stderr


def test_guard_is_an_allowlist_and_runs_before_caddy() -> None:
    script = _guard()
    # The line that reads the variable, not a comment that mentions it.
    assert script.index('host="${DMS_TLS_HOST:-}"') < script.index("exec caddy run")
    # One negated class: anything that is not a letter, digit, dot or hyphen.
    assert "*[!A-Za-z0-9.-]*" in script
    assert script.count("exec caddy run") == 1


def test_compose_default_and_caddyfile_site_are_unchanged() -> None:
    ui = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["ui"]
    assert ui["environment"]["DMS_TLS_HOST"] == "${DMS_TLS_HOST:-localhost}"
    spliced = [
        ln.strip()
        for ln in CADDYFILE.read_text(encoding="utf-8").splitlines()
        if "DMS_TLS_HOST" in ln and not ln.lstrip().startswith("#")
    ]
    # Exactly two splice points, both behind the entry guard: the site address and
    # default_sni (which lets a client that sends no SNI, as one typing an IP
    # address does, be served the certificate for the same host).
    assert spliced == [
        "default_sni {$DMS_TLS_HOST:localhost}",
        "https://{$DMS_TLS_HOST:localhost}:8080 {",
    ], "the host is spliced somewhere new"


def test_ip_host_is_served_without_sni() -> None:
    """An IPv4 host is admitted by the guard, so it must also be served. A TLS
    client does not send SNI for an IP address; without ``default_sni`` Caddy has
    no name to pick the certificate by and the handshake fails (tlsv1 alert
    internal error). The global block must carry it, ahead of the first site."""
    text = CADDYFILE.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "default_sni {$DMS_TLS_HOST:localhost}" in code
    assert code.index("default_sni") < code.index("https://{$DMS_TLS_HOST")


def test_smoke_proves_a_good_and_a_refused_host_live() -> None:
    code = "\n".join(
        ln
        for ln in SMOKE.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert 'pass "F1' in code and 'fail "F1' in code
    assert code.count('"DMS_TLS_HOST must be') >= 3, "the refused hosts are not tied to the guard"
    assert "INJECTED" in code, "the smoke no longer sends the second-site payload"
    assert '--noproxy "${GOOD_HOST}"' in code, "F1 would ask a workstation proxy for the host"
    assert 'pass "F2' in code and 'fail "F2' in code, "an IPv4 host is not proved live"
