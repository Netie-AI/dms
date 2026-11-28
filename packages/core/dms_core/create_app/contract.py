"""App bundle contract ``dms.app/1``.

The provider returns this object. DMS checks it and writes ``dms_data.py``.
The backend imports that client. It does not receive a database URL, a
credential, or raw table rows. SQL in ``queries`` is aggregated by the
provider and executed later under the caller's grants.

A Palantir-style ontology action uses connector ``ontology``. Nothing in
this contract calls a Palantir API. The slot is reserved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

CONTRACT = "dms.app/1"
RESERVED_FILES = frozenset({"dms_data.py", "dms_app_guard.py"})
CONNECTORS = frozenset({"writeback", "salesforce", "ontology"})
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_FILE_MAX = 200_000
_SECRET_MARKS = (
    "postgres://",
    "mysql://",
    "mongodb://",
    "BEGIN PRIVATE",
    "CURSOR_API_KEY",
    "DATABASE_URL",
    "AWS_SECRET",
    "aws_secret_access_key",
    "password=",
    "api_key=",
)
_FORBIDDEN = (
    "import duckdb",
    "import psycopg",
    "import sqlite3",
    "import sqlalchemy",
    "import boto3",
    "import subprocess",
    "from duckdb",
    "from psycopg",
    "os.system(",
)

CLIENT_PY = '''"""Scoped DMS data client. The harness writes this file. The app imports it."""
import json
import os
import urllib.request


def query(sql):
    return _post("/query", {"sql": sql})


def dry_run(action_id, payload=None):
    return _post("/actions/dry-run", {"action_id": action_id, "payload": payload or {}})


def _post(path, body):
    base = os.environ["DMS_APP_DATA_URL"].rstrip("/")
    token = os.environ["DMS_APP_TOKEN"]
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json", "X-Dms-App-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode())
'''

CONTRACT_SCHEMA: dict[str, Any] = {
    "contract": CONTRACT,
    "name": "lowercase-slug",
    "frontend": [{"path": "index.html", "content": "html"}],
    "backend": [{"path": "server.py", "content": "python that imports dms_data"}],
    "queries": [{"id": "identifier", "sql": "one aggregated select"}],
    "actions": [
        {
            "id": "identifier",
            "connector": "writeback | salesforce | ontology",
            "label": "short label",
            "payload": {},
        }
    ],
    "rules": [
        "No rows field, no credentials, no connection string.",
        "Backend reaches data only through dms_data.",
        "ontology is a slot. DMS does not call a Palantir API.",
    ],
}


def contract_document() -> dict[str, Any]:
    """The documented bundle shape. Returned to Studio and to the provider."""
    return dict(CONTRACT_SCHEMA)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _safe_path(path: str) -> bool:
    if not isinstance(path, str) or not path or path.startswith(("/", "\\")):
        return False
    if "\\" in path or path.strip() != path:
        return False
    parts = Path(path).parts
    return ".." not in parts and all(part not in ("", ".", "..") for part in parts)


def _files(raw: Any, errors: list[str], label: str) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        errors.append(f"{label}_missing")
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            errors.append(f"{label}_invalid")
            continue
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not _safe_path(path):
            errors.append("path_escape")
            continue
        if path in RESERVED_FILES:
            errors.append("client_reserved")
            continue
        if not isinstance(content, str) or len(content) > _FILE_MAX:
            errors.append(f"{label}_invalid")
            continue
        out.append({"path": path, "content": content})
    return out


def validate_bundle(raw: Any) -> list[str]:
    """Codes for a bundle that is not ``dms.app/1``. Empty means it can run."""
    if not isinstance(raw, dict):
        return ["bundle_not_object"]
    errors: list[str] = []
    if raw.get("contract") != CONTRACT:
        errors.append("contract_mismatch")
    if any(key in raw for key in ("rows", "credentials", "connection_string")):
        errors.append("rows_or_secrets_forbidden")
    name = raw.get("name")
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        errors.append("name_invalid")
    front = _files(raw.get("frontend"), errors, "frontend")
    back = _files(raw.get("backend"), errors, "backend")
    if not any(item["path"] == "index.html" for item in front):
        errors.append("frontend_index_missing")
    server = next((item["content"] for item in back if item["path"] == "server.py"), "")
    if not server:
        errors.append("backend_entry_missing")
    elif "dms_data" not in server:
        errors.append("backend_must_use_client")
    for item in (*front, *back):
        text = item["content"]
        if any(mark in text for mark in _SECRET_MARKS):
            errors.append("secret_in_bundle")
        if any(mark in text for mark in _FORBIDDEN):
            errors.append("forbidden_import")
    queries = raw.get("queries")
    if not isinstance(queries, list) or not queries:
        errors.append("queries_missing")
    else:
        for query in queries:
            if not isinstance(query, dict):
                errors.append("query_invalid")
                continue
            qid = query.get("id")
            sql = query.get("sql")
            if not isinstance(qid, str) or _IDENT.fullmatch(qid) is None:
                errors.append("query_invalid")
            if not isinstance(sql, str) or not sql.strip():
                errors.append("query_sql_missing")
    actions = raw.get("actions")
    if not isinstance(actions, list):
        errors.append("actions_missing")
    else:
        for action in actions:
            if not isinstance(action, dict):
                errors.append("action_invalid")
                continue
            if action.get("connector") not in CONNECTORS:
                errors.append("action_connector_unknown")
            aid = action.get("id")
            if not isinstance(aid, str) or _IDENT.fullmatch(aid) is None:
                errors.append("action_invalid")
            if not isinstance(action.get("label"), str) or not action["label"].strip():
                errors.append("action_invalid")
            if "payload" in action and not isinstance(action.get("payload"), dict):
                errors.append("action_invalid")
    return _unique(errors)


def compact_map(
    catalog: list[Any],
    grants: set[str],
    links: list[Any] | None = None,
) -> dict[str, Any]:
    """Names and types inside the grant. Cell values and samples are dropped."""
    tables: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name not in grants:
            dropped.append(name)
            continue
        columns: list[dict[str, str]] = []
        raw_cols = item.get("columns")
        if isinstance(raw_cols, list):
            for col in raw_cols:
                if not isinstance(col, dict) or not isinstance(col.get("name"), str):
                    continue
                typ = col.get("type")
                columns.append({"name": col["name"], "type": typ if isinstance(typ, str) else ""})
        tables.append({"name": name, "columns": columns})
    kept_links: list[dict[str, str]] = []
    for link in links or []:
        if not isinstance(link, dict):
            continue
        left = link.get("from") if isinstance(link.get("from"), str) else link.get("left")
        right = link.get("to") if isinstance(link.get("to"), str) else link.get("right")
        if not isinstance(left, str) or not isinstance(right, str):
            continue
        if left not in grants or right not in grants:
            continue
        kept_links.append({"from": left, "to": right})
    return {"tables": tables, "links": kept_links, "dropped": dropped}


def materialize(bundle: dict[str, Any], dest: Path) -> Path:
    """Write the provider files and the generated data client."""
    errors = validate_bundle(bundle)
    if errors:
        raise ValueError(errors[0])
    if dest.exists():
        for child in dest.iterdir():
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                _rmtree(child)
    dest.mkdir(parents=True, exist_ok=True)
    for group in ("frontend", "backend"):
        files = bundle.get(group)
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or "")
            if not _safe_path(rel):
                raise ValueError("path_escape")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(item.get("content") or ""), encoding="utf-8")
    (dest / "dms_data.py").write_text(CLIENT_PY, encoding="utf-8")
    return dest


def _rmtree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    path.rmdir()
