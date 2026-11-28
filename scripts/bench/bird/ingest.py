"""One Space per database, attached through the SQLite connector.

``POST /v1/spaces`` then ``POST /v1/studio/sources/sql`` with ``kind=sqlite``.
The database field is a path relative to ``DMS_SQLITE_SOURCE_ROOT``. A 500 on
space create is ``space_create_failed`` (a new tenant needs a tenant row
before this route answers). This module does not create that row.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bench.bird.gold import list_databases, relative_database

Post = Callable[[str, dict[str, Any]], tuple[int, Any]]


class IngestError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def space_body(name: str) -> dict[str, str]:
    return {"name": name}


def sqlite_source_body(database: str, space_id: str) -> dict[str, Any]:
    return {
        "kind": "sqlite",
        "host": "",
        "database": database,
        "user": "",
        "password": "",
        "space_id": space_id,
    }


def _space_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise IngestError("space_create_failed")
    space = payload.get("space")
    if isinstance(space, dict) and isinstance(space.get("id"), str) and space["id"].strip():
        return space["id"].strip()
    if isinstance(payload.get("id"), str) and str(payload["id"]).strip():
        return str(payload["id"]).strip()
    raise IngestError("space_create_failed")


def ingest_one(
    post: Post,
    *,
    name: str,
    database: str,
) -> dict[str, str]:
    """Create the Space and attach the file. ``database`` is relative to the root."""
    status, payload = post("/v1/spaces", space_body(name))
    if status != 201:
        raise IngestError("space_create_failed")
    space_id = _space_id(payload)
    attached, _body = post("/v1/studio/sources/sql", sqlite_source_body(database, space_id))
    if attached >= 400 or attached == 0:
        raise IngestError("sqlite_attach_failed")
    return {"db_id": name, "space_id": space_id, "database": database}


def ingest_root(post: Post, db_root: Path) -> dict[str, dict[str, str]]:
    """One Space per database file under ``db_root``."""
    out: dict[str, dict[str, str]] = {}
    for name, path in list_databases(db_root):
        database = relative_database(db_root, path)
        out[name] = ingest_one(post, name=name, database=database)
    return out


def write_spaces(path: Path, spaces: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spaces, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_spaces(path: Path) -> dict[str, str]:
    """db_id -> space_id. A missing file is an empty map."""
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if isinstance(value, str):
            out[str(key)] = value
        elif isinstance(value, dict) and isinstance(value.get("space_id"), str):
            out[str(key)] = value["space_id"]
    return out
