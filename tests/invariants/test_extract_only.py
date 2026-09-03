"""DR-0005 stated mechanically: SQL sources are extracted, never federated.

The decision record says live federation stays declined because it moves execution to
the customer's engine and leaves ``duckdb.execute`` inside ``packages/executor`` with
nothing to guard. That is a sentence. This is the thing that goes red.

The connector's own docstring promises it never uses DuckDB's ``ATTACH`` / ``INSTALL``
scanners, so the hostile-SQL guard in ``dms_executor.manifest`` keeps rejecting those
statements on this path too. A promise in a docstring is honoured until the first
person who needs a scanner and did not read it. So the promise is asserted here, on the
source text, where a scanner import under a config branch would still be caught.

Protected path: any PR touching this file needs ``INVARIANT-CHANGE:`` in a commit body.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONNECTOR = ROOT / "packages" / "executor" / "dms_executor" / "db_connector.py"

#: Every DuckDB mechanism that would let a query run against the SOURCE database
#: rather than against rows that have landed in bronze. Any one of these turns
#: extract-only into federation.
FEDERATION_MARKERS: tuple[str, ...] = (
    "ATTACH",
    "INSTALL",
    "LOAD ",
    "sqlite_scanner",
    "postgres_scanner",
    "mysql_scanner",
    "odbc_scanner",
    "mssql_scanner",
    "duckdb.connect",
    "duckdb.sql",
)

#: A DuckDB import anywhere in the connector is the wrong layer: the connector's
#: whole job is to hand rows to ``write_bronze_rows``, which owns the DuckDB side.
FORBIDDEN_IMPORTS = re.compile(r"^\s*(import\s+duckdb|from\s+duckdb\b)", re.MULTILINE)


def _code_lines(text: str) -> list[tuple[int, str]]:
    """Source lines with comments and docstrings stripped, so a mention in prose
    explaining the ban does not trip the ban.

    A closing ``\"\"\"`` may sit at the end of a prose line, not at the start.
    Treating only a start-of-line delimiter as the close left ``in_doc`` stuck
    True, so the checker skipped the rest of the file and passed vacuously.
    """
    out: list[tuple[int, str]] = []
    in_doc = False
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if in_doc:
            if '"""' in stripped or "'''" in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            # a one-line docstring opens and closes on the same line
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
            in_doc = True
            continue
        if stripped.startswith("#"):
            continue
        code = raw.split("#", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


def _federation_offenders(text: str) -> list[str]:
    offenders: list[str] = []
    for lineno, code in _code_lines(text):
        for marker in FEDERATION_MARKERS:
            if marker in code:
                offenders.append(f"db_connector.py:{lineno} {marker!r}: {code.strip()[:80]}")
    return offenders


def test_the_connector_exists() -> None:
    """A test that cannot find its subject must fail, not pass vacuously (R-0007)."""
    assert CONNECTOR.is_file(), f"{CONNECTOR} is missing - this invariant is guarding nothing"


def test_the_connector_never_federates() -> None:
    """No scanner, no ATTACH, no INSTALL: rows come OUT of the source, queries do not go IN."""
    offenders = _federation_offenders(CONNECTOR.read_text(encoding="utf-8"))
    assert not offenders, (
        "The connector must extract, never federate (DR-0005). A DuckDB scanner or "
        "ATTACH on this path runs the customer's query in the customer's engine, and the "
        "Space boundary degrades to advisory:\n" + "\n".join(offenders)
    )


def test_the_connector_does_not_import_duckdb() -> None:
    """Landing rows is ``write_bronze_rows``'s job. The connector holds no DuckDB handle."""
    text = CONNECTOR.read_text(encoding="utf-8")
    hits = [m.group(0).strip() for m in FORBIDDEN_IMPORTS.finditer(text)]
    assert not hits, (
        "db_connector imports duckdb directly. It must hand rows to write_bronze_rows "
        f"and hold no connection of its own: {hits}"
    )


@pytest.mark.parametrize("marker", ["ATTACH", "postgres_scanner", "duckdb.connect"])
def test_the_invariant_can_fail(marker: str) -> None:
    """Guard the guard (R-0007). A connector copy with one marker injected must trip."""
    injected = CONNECTOR.read_text(encoding="utf-8") + f"\n_probe = \"{marker}\"\n"
    assert _federation_offenders(injected), (
        f"the extract-only checker missed {marker!r} in a probe line; the guard is a no-op"
    )
