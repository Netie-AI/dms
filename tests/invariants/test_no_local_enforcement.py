"""AST: DMS must not reimplement path enforcement / predicate injection.

Cortex enforces; DMS mints. sqlglot inject / enforce_manifest outside executor fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN = [
    ROOT / "apps" / "api",
    ROOT / "packages" / "core",
    ROOT / "packages" / "cortex_client",
    ROOT / "packages" / "ledger",
]

FORBIDDEN_RE = re.compile(
    r"(inject_predicate|enforce_manifest|sqlglot\.parse)",
    re.I,
)


def test_no_enforcement_logic_outside_executor() -> None:
    offenders: list[str] = []
    for root in SCAN:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if FORBIDDEN_RE.search(text):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, "enforcement belongs in Cortex, not DMS:\n" + "\n".join(
        offenders
    )
