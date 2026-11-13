"""Every place DMS installs DuckDB must stay below DuckDB 2.

DuckDB 2.0.0 writes storage version 69. The Cortex engine pins
``duckdb>=1.1,<2`` and its 1.x cannot open that file ("we can only read
versions between 64 and 68"), so an unbounded DMS requirement breaks every ask
that reaches Cortex's execution against the serving warehouse DMS wrote.

Which DuckDB major the two products share is a founder decision. Until a
decision record changes it, this gate holds the DMS side below 2.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

REPO = Path(__file__).resolve().parents[1]

# The three sites known when the gate was written. The scan below must find a
# requirement in each, so it cannot go blind and pass on nothing.
KNOWN_SITES = (
    "pyproject.toml",
    "packages/executor/pyproject.toml",
    "apps/api/Dockerfile",
)

# Files that can install a package: build metadata, requirement lists, images,
# CI and operator scripts. Tests, docs and archives install nothing.
_INSTALL_FILE = re.compile(
    r"(^|/)(pyproject\.toml|setup\.(py|cfg)|[^/]*(requirements|constraints)[^/]*\.txt"
    r"|Dockerfile[^/]*|[^/]+\.(ya?ml|sh|ps1)|justfile)$"
)
_SKIP_DIRS = ("tests/", "docs/", "archive/")

# A requirement token: lowercase ``duckdb`` standing alone (not ``x.duckdb``,
# not ``duckdb_engine``), then its version specifier, if any.
_REQUIREMENT = re.compile(r"(?<![\w.\-/\\])duckdb(?![\w.\-/\\])\s*([<>=!~][<>=!~\w.*,\s]*)?")

_DUCKDB_2_AND_UP = ("2.0.0a1", "2.0.0", "2.0.1", "2.1.0", "3.0.0", "10.0.0")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()


def _duckdb_requirements() -> list[tuple[str, int, str]]:
    """(file, line number, specifier) for every DuckDB requirement in an install file."""
    found: list[tuple[str, int, str]] = []
    for rel in _tracked_files():
        if rel.startswith(_SKIP_DIRS) or not _INSTALL_FILE.search(rel):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for match in _REQUIREMENT.finditer(line):
                found.append((rel, lineno, (match.group(1) or "").strip().rstrip(",")))
    return found


def _allows_duckdb_2(specifier: str) -> bool:
    spec = SpecifierSet(specifier)
    return any(spec.contains(version, prereleases=True) for version in _DUCKDB_2_AND_UP)


def test_scan_finds_the_known_install_sites() -> None:
    sites = {rel for rel, _, _ in _duckdb_requirements()}
    missing = [site for site in KNOWN_SITES if site not in sites]
    assert not missing, f"no DuckDB requirement found in {missing}; the gate would pass blind"


def test_every_duckdb_requirement_is_bounded_below_2() -> None:
    unbounded = [
        f"{rel}:{lineno}: duckdb{spec or ' (no version specifier)'}"
        for rel, lineno, spec in _duckdb_requirements()
        if _allows_duckdb_2(spec)
    ]
    assert not unbounded, (
        "DuckDB requirement allows 2.x, whose storage version 69 the Cortex engine "
        "(duckdb>=1.1,<2) cannot read. Use duckdb>=1.1,<2:\n  " + "\n  ".join(unbounded)
    )


@pytest.mark.parametrize(
    ("specifier", "allows"),
    [
        ("", True),
        (">=1.0", True),
        (">=1.1,<=2", True),
        (">=1.1,<3", True),
        (">=1.1,<2", False),
        ("==1.5.6", False),
        ("~=1.4", False),
    ],
)
def test_the_bound_check_itself(specifier: str, allows: bool) -> None:
    assert _allows_duckdb_2(specifier) is allows
