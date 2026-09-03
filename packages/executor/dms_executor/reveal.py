"""Open Explorer on a filesystem origin_uri (REVEAL-01 / AirGPT reveal pattern).

DMS-local only — never call AirGPT HTTP. Paths must resolve under allowlisted
roots (warehouse dir + optional DMS_REVEAL_ROOTS).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def is_filesystem_uri(path: str) -> bool:
    p = (path or "").strip()
    if not p or p.startswith(("http://", "https://", "duckdb://", "s3://", "azure://")):
        return False
    if p.startswith("\\\\") or p.startswith("/"):
        return True
    return len(p) >= 3 and p[1] == ":" and p[2] in "\\/"


def allowlisted_roots() -> list[Path]:
    from dms_executor.demo_warehouse import warehouse_path

    wh = warehouse_path().resolve()
    roots = [wh.parent]
    if wh.exists() and wh.is_dir():
        roots.append(wh)
    raw = os.environ.get("DMS_REVEAL_ROOTS", "")
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            roots.append(Path(piece).expanduser().resolve())
        except OSError:
            continue
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _under_roots(resolved: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def resolve_allowlisted_file(path: str | Path) -> dict[str, Any]:
    """Resolve a caller-supplied path to a file under allowlisted_roots.

    Empty path is the caller's problem. Relative paths are resolved against cwd
    then checked, so ``..`` cannot walk out of the warehouse tree.
    """
    raw = str(path or "").strip()
    if not raw:
        return {"ok": False, "error": "path_required"}
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError:
        return {"ok": False, "error": "unresolvable_path"}
    if not _under_roots(resolved, allowlisted_roots()):
        return {"ok": False, "error": "path_not_allowlisted", "path": str(resolved)}
    if not resolved.is_file():
        return {"ok": False, "error": "not_a_file", "path": str(resolved)}
    return {"ok": True, "path": resolved}


def reveal_path(path: str, *, open_explorer: bool = True) -> dict[str, Any]:
    """Reveal path in Explorer if allowlisted. Never shell-opens outside roots."""
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "path_required"}
    if not is_filesystem_uri(raw):
        return {"ok": False, "error": "not_filesystem_path"}

    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError:
        return {"ok": False, "error": "unresolvable_path"}

    if not _under_roots(resolved, allowlisted_roots()):
        return {"ok": False, "error": "path_not_allowlisted"}

    if not resolved.exists():
        return {"ok": False, "error": f"not_found: {resolved}", "path": str(resolved)}

    target = str(resolved)
    if not open_explorer:
        return {"ok": True, "path": target, "action": "dry_run"}

    if os.name == "nt":
        try:
            # shell=True + quoted /select, — list argv form often opens wrong folders
            subprocess.Popen(f'explorer /select,"{target}"', shell=True)
            return {"ok": True, "path": target, "opened": target, "action": "explorer_select"}
        except OSError as exc:
            return {"ok": False, "error": str(exc)[:200], "path": target}

    # xdg-open and `open` have no "select this file" equivalent, so the best
    # they can do is surface the containing folder. That is a fine fallback, but
    # it must not change what ``path`` means: every other branch returns the
    # target that was asked for, and a caller reading ``path`` to label the UI
    # would otherwise show the folder on Linux and the file on Windows. What the
    # OS actually surfaced goes in ``opened``.
    folder = str(resolved if resolved.is_dir() else resolved.parent)
    try:
        import sys

        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return {"ok": True, "path": target, "opened": folder, "action": "xdg_or_open"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200], "path": target, "opened": folder}
