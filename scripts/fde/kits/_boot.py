"""Put the repo packages on sys.path for a script entry."""

from __future__ import annotations

import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def boot() -> Path:
    root = repo_root()
    for rel in (
        "packages/executor",
        "packages/core",
        "packages/cortex_client",
        "packages/ledger",
        "apps/api",
    ):
        path = str(root / rel)
        if path not in sys.path:
            sys.path.insert(0, path)
    parent = str(Path(__file__).resolve().parents[1])
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return root
