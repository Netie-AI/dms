"""DUCKDB-PIN-01: a serving file DMS writes must open on Cortex's DuckDB.

Cortex pins duckdb below 2 and reads storage versions 64 through 68.
DuckDB 2.0.0 writes version 69, and Cortex then fails the ask with
IOException. The pin is ``duckdb>=1.0,<2`` (issue #517).

This fails when a requirement admits 2.x, and when the installed library
is 2.x (that library writes a storage version Cortex cannot read).
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import duckdb
from dms_executor.demo_warehouse import connect_file, connect_serving
from packaging.requirements import Requirement
from packaging.version import Version

_REPO = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "docs",
    "handoff",
    "tests",
}
_REQUIRED = (
    Path("pyproject.toml"),
    Path("packages/executor/pyproject.toml"),
    Path("apps/api/Dockerfile"),
)
# Cortex 1.x: "we can only read versions between 64 and 68."
_CORTEX_STORAGE_MIN = 64
_CORTEX_STORAGE_MAX = 68


def _install_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if name in {"pyproject.toml", "setup.py", "setup.cfg"}:
                found.append(path)
            elif name.startswith(("requirements", "constraints")) and name.endswith(".txt"):
                found.append(path)
            elif name == "Dockerfile" or name.startswith("Dockerfile."):
                found.append(path)
    return found


def _duckdb_requirements(text: str) -> list[Requirement]:
    out: list[Requirement] = []
    for quote in ("'", '"'):
        start = 0
        while True:
            left = text.find(quote, start)
            if left < 0:
                break
            right = text.find(quote, left + 1)
            if right < 0:
                break
            token = text[left + 1 : right].strip()
            start = right + 1
            if token.lower().startswith("duckdb"):
                out.append(Requirement(token))
    return out


def _admits_2(req: Requirement) -> bool:
    return req.specifier.contains(Version("2.0.0"))


def test_bound_check_rejects_an_open_upper_range() -> None:
    """The checker itself: ``>=1.0`` admits 2.x and ``>=1.0,<2`` does not."""
    assert _admits_2(Requirement("duckdb>=1.0"))
    assert _admits_2(Requirement("duckdb>=2"))
    assert not _admits_2(Requirement("duckdb>=1.0,<2"))
    assert Requirement("duckdb>=1.0,<2").specifier.contains(Version("1.5.6"))


def test_every_duckdb_requirement_excludes_2() -> None:
    seen: dict[Path, list[Requirement]] = {}
    for path in _install_files(_REPO):
        reqs = _duckdb_requirements(path.read_text(encoding="utf-8"))
        if reqs:
            seen[path.relative_to(_REPO)] = reqs
    missing = [str(path) for path in _REQUIRED if path not in seen]
    assert not missing, f"DuckDB pin missing from {missing}"
    open_upper = [
        f"{path}: {req}"
        for path, reqs in sorted(seen.items())
        for req in reqs
        if _admits_2(req)
    ]
    assert not open_upper, (
        "DuckDB requirement allows 2.x. That storage version is 69, which "
        f"Cortex (duckdb<2) cannot open: {open_upper}"
    )


def _storage_version(path: Path) -> int:
    blob = path.read_bytes()
    magic = blob.find(b"DUCK")
    assert magic >= 8, "serving file has no DuckDB header"
    return struct.unpack_from("<Q", blob, magic + 4)[0]


def test_executor_serving_file_opens_on_cortex_duckdb(tmp_path: Path) -> None:
    """Write with the executor and read it back. Version 69 must not appear.

    Removing the ``<2`` pin and installing DuckDB 2.x fails this test: the
    installed major is 2, and the file's storage version is outside 64..68.
    """
    assert Version(duckdb.__version__).major < 2, duckdb.__version__
    path = tmp_path / "serving.duckdb"
    writer = connect_file(path)
    try:
        writer.execute("CREATE TABLE pin_probe(n INTEGER)")
        writer.execute("INSERT INTO pin_probe VALUES (7)")
    finally:
        writer.close()
    version = _storage_version(path)
    assert _CORTEX_STORAGE_MIN <= version <= _CORTEX_STORAGE_MAX, (
        f"storage version {version} is outside {_CORTEX_STORAGE_MIN}..{_CORTEX_STORAGE_MAX} "
        "(DuckDB 2.0 writes 69, which Cortex cannot read)"
    )
    reader = connect_serving(path)
    try:
        row = reader.execute("SELECT n FROM pin_probe").fetchone()
    finally:
        reader.close()
    assert row is not None and row[0] == 7
