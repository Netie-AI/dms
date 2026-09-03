"""DR-0005 stated mechanically: SQL sources are extracted, never federated.

The decision record says live federation stays declined because it moves execution to
the customer's engine and leaves ``duckdb.execute`` inside ``packages/executor`` with
nothing to guard. That is a sentence. This is the thing that goes red.

Verify-agent on PR #111 defeated the first draft 12 ways out of 18: lowercase markers,
a triple-quoted SQL string on a continuation line, a ``#`` inside a string, and
``importlib.import_module("duckdb")`` / ``__import__("duckdb")`` / ``"ATT" + "ACH"`` all
slipped a text-and-regex checker. So this one walks the AST instead:

- every string constant that is NOT a docstring is scanned, case-insensitively, on word
  boundaries - SQL lives in strings, and DuckDB's SQL is case-insensitive;
- ``import``/``from ... import`` of duckdb, and ``importlib.import_module`` /
  ``__import__`` with a string that names it, are offenders regardless of nesting;
- adjacent string constants joined with ``+`` are checked as one string;
- ``duckdb.connect`` / ``duckdb.sql`` attribute chains are offenders in the connector.

Two marker sets, because the first draft of the package-wide scan tripped on three
legitimate strings (R-0005): the demo alert text "Load near capacity", the hostile-SQL
guard's own error message naming "attach", and ``warehouse_identity.py``'s ``ATTACH`` of
the LOCAL serving DuckDB file - which is the bronze-to-serving sync, not federation.

- **Connector-only:** ``ATTACH`` / ``INSTALL`` / ``LOAD`` / ``*_scanner`` in any string,
  plus any DuckDB handle. The connector attaches nothing and holds nothing.
- **Package-wide:** ``INSTALL`` and ``*_scanner`` only. Those are the tells of reaching
  OUT to another database engine; attaching a local .duckdb file is not.

Protected path: any PR touching this file needs ``INVARIANT-CHANGE:`` in a commit body.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "packages" / "executor" / "dms_executor"
CONNECTOR = EXECUTOR / "db_connector.py"

#: In the connector, ANY of these in a string is federation. Nothing there may attach.
CONNECTOR_SQL = re.compile(r"\b(?:ATTACH|INSTALL|LOAD)\b|\b\w+_scanner\b", re.IGNORECASE)
#: Package-wide, only the markers that mean another database ENGINE. A local ATTACH of a
#: .duckdb file is the serving sync and is allowed; LOAD and ATTACH also appear in prose.
REACH_OUT = re.compile(r"\bINSTALL\b|\b\w+_scanner\b", re.IGNORECASE)
#: Holding a DuckDB handle at all. Forbidden in the connector only.
HANDLE_MARKERS = ("duckdb.connect", "duckdb.sql")


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The Constant nodes that are docstrings, so prose explaining the ban does not trip it."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    out.add(id(body[0].value))
    return out


def _joined_string(node: ast.AST) -> str | None:
    """A str Constant, or a chain of them joined with +, as one string. Else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _joined_string(node.left), _joined_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _offenders(
    text: str, *, filename: str, sql: re.Pattern[str] | None, handles: bool
) -> list[str]:
    tree = ast.parse(text, filename=filename)
    docstrings = _docstring_ids(tree)
    out: list[str] = []

    def hit(node: ast.AST, what: str) -> None:
        out.append(f"{filename}:{getattr(node, 'lineno', '?')} {what}")

    for node in ast.walk(tree):
        # imports of duckdb, by any spelling
        if handles and isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "duckdb" or a.name.startswith("duckdb."):
                    hit(node, f"import {a.name}")
        if handles and isinstance(node, ast.ImportFrom):
            if (node.module or "") == "duckdb" or (node.module or "").startswith("duckdb."):
                hit(node, f"from {node.module} import ...")
        if isinstance(node, ast.Call):
            callee = _dotted(node.func)
            if handles and callee in ("importlib.import_module", "__import__"):
                for arg in node.args[:1]:
                    s = _joined_string(arg)
                    if s and "duckdb" in s:
                        hit(node, f"{callee}({s!r})")
            if handles and callee in HANDLE_MARKERS:
                hit(node, callee)
        # every non-docstring string, including + joined pieces
        if sql and isinstance(node, (ast.Constant, ast.BinOp)) and id(node) not in docstrings:
            s = _joined_string(node)
            if s is not None:
                m = sql.search(s)
                if m:
                    hit(node, f"{m.group(0)!r} in string {s.strip()[:60]!r}")
    return out


def _federation_offenders(text: str) -> list[str]:
    """The connector must neither reach out (scanners) nor hold a handle (duckdb.*)."""
    return _offenders(text, filename="db_connector.py", sql=CONNECTOR_SQL, handles=True)


def test_the_connector_exists() -> None:
    """A test that cannot find its subject must fail, not pass vacuously (R-0007)."""
    assert CONNECTOR.is_file(), f"{CONNECTOR} is missing - this invariant is guarding nothing"


def test_the_connector_never_federates() -> None:
    offenders = _federation_offenders(CONNECTOR.read_text(encoding="utf-8"))
    assert not offenders, (
        "The connector must extract, never federate (DR-0005). A scanner or a DuckDB "
        "handle on this path runs the customer's query in the customer's engine and the "
        "Space boundary degrades to advisory:\n" + "\n".join(offenders)
    )


def test_nothing_in_the_executor_reaches_out_to_another_database() -> None:
    """INSTALL and *_scanner are forbidden package-wide; only the connector is handle-free.

    ATTACH is deliberately NOT in this set: warehouse_identity.py attaches the local
    serving .duckdb to sync bronze into it, and that is the product working, not a leak.
    """
    offenders: list[str] = []
    for py in sorted(EXECUTOR.glob("*.py")):
        offenders += _offenders(
            py.read_text(encoding="utf-8"), filename=py.name, sql=REACH_OUT, handles=False
        )
    assert not offenders, (
        "INSTALL / *_scanner in the executor - reaching out to another engine:\n"
        + "\n".join(offenders)
    )


def test_a_local_duckdb_attach_in_the_sync_is_not_federation() -> None:
    """R-0005, package level: the serving sync ATTACHes a local file and must stay green."""
    sync = EXECUTOR / "warehouse_identity.py"
    assert sync.is_file()
    text = sync.read_text(encoding="utf-8")
    assert "ATTACH" in text, "this test assumes the serving sync still uses ATTACH"
    assert _offenders(text, filename=sync.name, sql=REACH_OUT, handles=False) == []


def test_prose_about_the_ban_does_not_trip_it() -> None:
    """R-0005 - the module docstring names ATTACH and INSTALL on purpose."""
    text = CONNECTOR.read_text(encoding="utf-8")
    assert "ATTACH" in text, "this test assumes the docstring still explains the ban"
    assert _federation_offenders(text) == []


_PROBES = {
    "uppercase ATTACH in a string": 'x = "ATTACH DATABASE remote AS r"',
    "lowercase attach in a string": 'x = "attach database remote as r"',
    "scanner name": 'x = "INSTALL postgres_scanner"',
    "triple-quoted SQL on a continuation line": 'x = (\n    """\n    LOAD httpfs\n    """\n)',
    "hash inside a string before the marker": 'x = "# note ATTACH remote"',
    "string concatenation": 'x = "ATT" + "ACH remote"',
    "import duckdb": "import duckdb",
    "from duckdb import": "from duckdb import connect",
    "importlib": 'import importlib\nm = importlib.import_module("duckdb")',
    "dunder import": 'm = __import__("duckdb")',
    "duckdb.connect": "con = duckdb.connect(':memory:')",
}


@pytest.mark.parametrize("label", sorted(_PROBES))
def test_the_invariant_can_fail(label: str) -> None:
    """Guard the guard (R-0007). Each shape defeated the previous checker or is the base case."""
    injected = CONNECTOR.read_text(encoding="utf-8") + "\n" + _PROBES[label] + "\n"
    assert _federation_offenders(injected), f"the checker missed: {label}"


def test_the_probe_shapes_are_not_already_in_the_connector() -> None:
    """If a probe string is literally present, the probe proves nothing (R-0007)."""
    text = CONNECTOR.read_text(encoding="utf-8")
    for label, snippet in _PROBES.items():
        assert snippet not in text, f"probe {label!r} is already in the connector"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
