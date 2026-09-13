"""DEMO-HOST-02 fail-closed config -- no live GCP/IAP, no 127.0.0.1:8090 default."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_studio_host_online.py"


def _clean_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATHEXT", "HOME", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_self_check_rejects_unset_and_wildcard() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-check"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-check ok" in proc.stdout


def test_unset_env_is_config_not_pass() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CONFIG" in proc.stdout
    assert "STUDIO_ORIGIN" in proc.stdout
    assert "127.0.0.1:8090" in proc.stdout
    assert "VERDICT: PASS" not in proc.stdout
