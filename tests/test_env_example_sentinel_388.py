"""ENV-EXAMPLE-SENTINEL (#388): the placeholder the example files ship does not boot.

The class is "a shipped placeholder that a real boot accepts". Root
``.env.example`` shipped ``DATABASE_URL=postgresql://dms:change-me@...``; copied
unedited to ``.env`` the API started, and against a database that really had
that password it served.

What is asserted is what the operator gets: the process that would serve
(``import dms_api.app``, the uvicorn target) and the container entrypoint both
exit non-zero, with ``placeholder_secret`` and the variable's NAME on stderr,
and never its value.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dms_api import settings as settings_mod
from dms_api.settings import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]
ROOT_EXAMPLE = ROOT / ".env.example"
COMPOSE_EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"
ENTRYPOINT = ROOT / "apps" / "api" / "entrypoint.sh"

REAL_URL = "postgresql://dms:Yq7fTn2LrX9wKd4s@127.0.0.1:5432/dms"
_SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|DATABASE_URL")
#: Every variable the guard reads; cleared so the host shell cannot decide a test.
_GUARDED = ("DATABASE_URL", "CORTEX_API_KEY", "OPENVAULT_API_KEY", "DMS_OV_SERVICE_TOKEN")


def _assignments(path: Path) -> dict[str, str]:
    """Uncommented ``NAME=value`` lines, the way a copied file is read."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            out[name.strip()] = value.strip()
    return out


def _child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _GUARDED}
    paths = ("apps/api", "packages/core", "packages/cortex_client", "packages/executor")
    env["PYTHONPATH"] = os.pathsep.join(
        [
            *(str(ROOT / p) for p in (*paths, "packages/ledger", "packages/ao_vendor")),
            env.get("PYTHONPATH", ""),
        ]
    )
    env.update(extra)
    return env


def _boot(cwd: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    """Import the uvicorn target in a fresh process. It reads ``cwd/.env``."""
    return subprocess.run(
        [sys.executable, "-c", "import dms_api.app"],
        cwd=cwd,
        env=_child_env(**extra),
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in _GUARDED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_unedited_env_example_refuses_boot_by_name(tmp_path: Path) -> None:
    """The reproduction: copy the shipped file to ``.env``, edit nothing, boot."""
    shutil.copy(ROOT_EXAMPLE, tmp_path / ".env")
    shipped = _assignments(ROOT_EXAMPLE)["DATABASE_URL"]

    proc = _boot(tmp_path)

    assert proc.returncode != 0, "an unedited .env.example booted"
    assert "placeholder_secret" in proc.stderr
    assert "DATABASE_URL" in proc.stderr
    assert shipped not in proc.stderr and shipped not in proc.stdout
    password = shipped.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
    assert password not in proc.stderr and password not in proc.stdout


def test_real_value_boots(tmp_path: Path) -> None:
    proc = _boot(tmp_path, DATABASE_URL=REAL_URL)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "placeholder_secret" not in proc.stderr


def test_demo_path_without_database_url_boots(tmp_path: Path) -> None:
    """No DATABASE_URL, default demo viewer key: the path the suite relies on."""
    proc = _boot(tmp_path, DMS_ASK_MODE="demo", DMS_DEMO_FALLBACK="1")
    assert proc.returncode == 0, proc.stderr[-800:]


def test_demo_mode_does_not_excuse_a_placeholder(tmp_path: Path) -> None:
    shipped = _assignments(ROOT_EXAMPLE)["DATABASE_URL"]
    proc = _boot(tmp_path, DMS_ASK_MODE="demo", DMS_DEMO_FALLBACK="1", DATABASE_URL=shipped)
    assert proc.returncode != 0
    assert "placeholder_secret" in proc.stderr and "DATABASE_URL" in proc.stderr


def test_entrypoint_refuses_before_migrations(tmp_path: Path) -> None:
    """The container entrypoint migrates before uvicorn. It must refuse first."""
    shipped = _assignments(ROOT_EXAMPLE)["DATABASE_URL"]
    marker = tmp_path / "started"
    proc = subprocess.run(
        ["sh", str(ENTRYPOINT), "touch", str(marker)],
        cwd=tmp_path,
        env=_child_env(DATABASE_URL=shipped),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0
    assert "placeholder_secret" in proc.stderr and "DATABASE_URL" in proc.stderr
    assert shipped not in proc.stderr
    assert not marker.exists(), "the server command ran after a refused boot"
    # Refused before any connection attempt, not by a failed migration.
    assert "psycopg" not in proc.stderr and "alembic" not in proc.stderr.lower()


@pytest.mark.parametrize("path", [ROOT_EXAMPLE, COMPOSE_EXAMPLE], ids=["root", "compose"])
def test_no_shipped_example_carries_a_bootable_secret(path: Path) -> None:
    """The class guard: a secret-named line in a shipped example is empty or refused."""
    secrets = {n: v for n, v in _assignments(path).items() if _SECRET_NAME.search(n)}
    if path == ROOT_EXAMPLE:
        assert "DATABASE_URL" in secrets, "the reproduction line left the example"
    if path == COMPOSE_EXAMPLE:
        assert secrets.get("DMS_DB_PASSWORD") == "", "compose example ships a password"
    for name, value in secrets.items():
        assert value == "" or settings_mod.is_placeholder(value), (
            f"{path.name}: {name} ships a value a boot would accept"
        )


@pytest.mark.parametrize(
    "field,env_name",
    [("database_url", "DATABASE_URL"), ("cortex_api_key", "CORTEX_API_KEY")],
)
def test_each_secret_setting_refuses_by_name(clean: Path, monkeypatch, field, env_name) -> None:
    value = f"postgresql://dms:{settings_mod.PLACEHOLDER}@db:5432/dms"
    monkeypatch.setenv(env_name, value)
    with pytest.raises(settings_mod.PlaceholderSecretError) as err:
        get_settings()
    assert env_name in str(err.value)
    assert value not in str(err.value) and settings_mod.PLACEHOLDER not in str(err.value)
    assert getattr(Settings(), field) == value  # the model still loads; boot refuses


@pytest.mark.parametrize("env_name", ["OPENVAULT_API_KEY", "DMS_OV_SERVICE_TOKEN"])
def test_secrets_read_outside_settings_refuse_too(clean: Path, monkeypatch, env_name) -> None:
    monkeypatch.setenv(env_name, settings_mod.PLACEHOLDER)
    with pytest.raises(settings_mod.PlaceholderSecretError, match=env_name):
        get_settings()


def test_every_placeholder_is_named_in_one_refusal(clean: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql://dms:{settings_mod.PLACEHOLDER}@db/dms")
    monkeypatch.setenv("CORTEX_API_KEY", settings_mod.PLACEHOLDER)
    with pytest.raises(settings_mod.PlaceholderSecretError) as err:
        get_settings()
    assert "DATABASE_URL" in str(err.value) and "CORTEX_API_KEY" in str(err.value)
    assert "DMS_DB_PASSWORD" in str(err.value)  # the compose hint, for the database only


def test_compose_hint_only_for_the_database(clean: Path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_API_KEY", settings_mod.PLACEHOLDER)
    with pytest.raises(settings_mod.PlaceholderSecretError) as err:
        get_settings()
    assert "DMS_DB_PASSWORD" not in str(err.value)


@pytest.mark.parametrize(
    "value",
    [
        "change-me",
        "CHANGE_ME",
        "changeme",
        "Change Me",
        "<change-me>",
        "change%2Dme",
        "CHANGE%255FME",
        "postgresql://dms:change-me@127.0.0.1:5432/dms",
    ],
)
def test_spellings_of_the_one_placeholder(value: str) -> None:
    assert settings_mod.is_placeholder(value)


@pytest.mark.parametrize(
    "value", ["", None, REAL_URL, "dms-demo-viewer-key", "s3cr3t-Exchange-mEdium", "chang3-me-not"]
)
def test_real_values_are_not_placeholders(value) -> None:
    assert not settings_mod.is_placeholder(value)


def test_real_values_pass_get_settings(clean: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", REAL_URL)
    monkeypatch.setenv("CORTEX_API_KEY", "vk_live_7f3a9c")
    assert get_settings().database_url == REAL_URL
