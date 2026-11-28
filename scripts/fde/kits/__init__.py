"""Kit registry. Pipeline stages register here. Later kits add one module."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Ctx = Any
Runner = Callable[[Ctx], dict[str, Any]]


@dataclass(frozen=True)
class KitSpec:
    name: str
    stage: str
    run: Runner
    catalogue: tuple[tuple[str, str, str], ...]
    requires_flag: str = ""


_order: list[str] = []
_by_name: dict[str, KitSpec] = {}
_loaded = False


def register(spec: KitSpec) -> None:
    if spec.name not in _by_name:
        _order.append(spec.name)
    _by_name[spec.name] = spec


def load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from kits import pipeline

    pipeline.register_kits()
    folder = Path(__file__).parent
    for path in sorted(folder.glob("*.py")):
        if path.stem in {"__init__", "pipeline"}:
            continue
        importlib.import_module(f"kits.{path.stem}")


def ordered() -> list[KitSpec]:
    load()
    return [_by_name[name] for name in _order]


def get(name: str) -> KitSpec | None:
    load()
    return _by_name.get(name)
