"""Pins that the extract-lab follow-up must not regress.

No Docker. These fail when a declared dep or bootstrap migrate step is deleted,
or when AW_IMAGE quietly defaults to a 2022 tag that cannot read 2025 .bak files.
"""

from __future__ import annotations

import importlib.util
import io
import re
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _toml() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _dep_name(raw: str) -> str:
    return re.split(r"[<>=!\[]", raw.strip().strip("\"'"), 1)[0].strip().lower()


def _load_aw():
    spec = importlib.util.spec_from_file_location(
        "load_adventureworks", ROOT / "scripts" / "load_adventureworks.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pyarrow_is_a_declared_runtime_dep() -> None:
    names = [_dep_name(d) for d in _toml()["project"]["dependencies"]]
    assert "pyarrow" in names
    extract = (ROOT / "scripts" / "load_adventureworks.py").read_text(encoding="utf-8")
    assert "to_parquet" in extract


def test_start_dms_stack_and_compose_bootstrap_run_alembic() -> None:
    start = (ROOT / "scripts" / "windows" / "Start-DMSStack.ps1").read_text(encoding="utf-8")
    assert "alembic upgrade head" in start
    entry = (ROOT / "apps" / "api" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "run_migrations" in entry
    dockerfile = (ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "entrypoint.sh" in dockerfile
    lifespan = (ROOT / "apps" / "api" / "dms_api" / "app.py").read_text(encoding="utf-8")
    assert "run_migrations" in lifespan


def test_aw_image_defaults_to_2025_and_rejects_2022() -> None:
    aw = _load_aw()
    assert aw.AW_IMAGE_DEFAULT == "mcr.microsoft.com/mssql/server:2025-latest"
    assert "2025-latest" in aw.IMAGE
    assert aw.image_cannot_restore_2025("mcr.microsoft.com/mssql/server:2022-latest")
    assert aw.image_cannot_restore_2025("mcr.microsoft.com/mssql/server:2022-CU19-ubuntu-22.04")
    assert aw.image_cannot_restore_2025("mcr.microsoft.com/mssql/server:2019-latest")
    assert not aw.image_cannot_restore_2025(aw.AW_IMAGE_DEFAULT)
    with pytest.raises(aw.StageFailed, match="998"):
        aw.assert_image_can_restore_2025("mcr.microsoft.com/mssql/server:2022-latest")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "2025-latest" in readme
    source = (ROOT / "scripts" / "load_adventureworks.py").read_text(encoding="utf-8")
    assert "2025-latest" in source
    assert "version 998" in source
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exited, redirect_stdout(buf):
        aw.main(["--help"])
    assert exited.value.code == 0
    assert "2025-latest" in buf.getvalue()
