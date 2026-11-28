"""Engagement folder loader. Passwords stay in the named env var."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

_SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "bearer",
        "passwd",
        "pwd",
    }
)


class EngageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                return True
            if _secret_key(item):
                return True
        return False
    if isinstance(value, list):
        return any(_secret_key(item) for item in value)
    return False


def load(path: Path) -> tuple[Path, dict[str, Any]]:
    directory = path if path.is_dir() else path.parent
    file = path / "engagement.yaml" if path.is_dir() else path
    if not file.is_file():
        raise EngageError("engagement_missing")
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise EngageError("engagement_invalid")
    if _secret_key(data):
        raise EngageError("password_in_file")
    return directory, data


def sources_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(data.get("sources"), list):
        return [item for item in data["sources"] if isinstance(item, dict)]
    source = data.get("source")
    if isinstance(source, dict):
        return [source]
    return []


def identity(directory: Path, data: dict[str, Any]) -> tuple[str, str]:
    name = str(data.get("name") or data.get("tenant") or directory.name)
    tenant = str(data.get("tenant_id") or data.get("tenant") or "")
    return name, tenant


def secret_values(data: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for source in sources_of(data):
        env = str(source.get("password_env") or "FDE_SOURCE_PASSWORD")
        value = os.environ.get(env, "")
        if len(value) >= 8:
            found.append(value)
    return found


def password_for(source: dict[str, Any]) -> str:
    env = str(source.get("password_env") or "FDE_SOURCE_PASSWORD")
    return os.environ.get(env, "")


def cli(run: Callable[..., dict[str, Any]], argv: list[str] | None = None) -> int:
    """Flag gate first. A refused run prints ``fde_kit_disabled`` and writes nothing."""
    from kits._schema import KitDisabled, exit_code, kit_enabled

    parser = argparse.ArgumentParser()
    parser.add_argument("--engagement", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    if not kit_enabled():
        print("fde_kit_disabled", file=sys.stderr)
        return 2
    try:
        directory, data = load(Path(args.engagement))
    except EngageError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    only = {part.strip() for part in str(args.only).split(",") if part.strip()}
    try:
        report = run(directory, data, only=only)
    except KitDisabled:
        print("fde_kit_disabled", file=sys.stderr)
        return 2
    except EngageError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    return exit_code(str(report.get("status", "")))
