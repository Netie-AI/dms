"""Compact Create context. DMS gathers and governs. The model writes.

``DMS_CREATE_CONTEXT`` is on only when it is the digit 1. A caller can read a
chat or a project they own, or one shared to them. The pack is summaries, a
schema and ontology map, and a small result set. It is not a raw table.

``layout_blocks`` is the hook DMS-STYLE-V2-01 fills. It stays empty here.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, NoReturn

FLAG = "DMS_CREATE_CONTEXT"
_ROW_CAP = 8
_COL_CAP = 6
_CELL = 80
_TEXT = 400
_SQL = 500
_ID = re.compile(r"^[a-f0-9]{32}$")
_ACTOR = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_LOCK = threading.Lock()


def context_enabled() -> bool:
    return os.environ.get(FLAG, "").strip() == "1"


def layout_blocks(kind: str) -> list[dict[str, Any]]:
    """Layout blocks for one kind. DMS-STYLE-V2-01 returns the real blocks."""
    del kind
    return []


def save_chat(*, actor: str, chat: dict[str, Any]) -> dict[str, Any]:
    owner = _actor(actor)
    record = _record("chat", owner, chat)
    record["title"] = _short(chat.get("title"), 80) or "Chat"
    _write("chats", record)
    _audit(owner, "save_chat", str(record["id"]))
    return _public(record)


def save_project(*, actor: str, project: dict[str, Any]) -> dict[str, Any]:
    owner = _actor(actor)
    record = _record("project", owner, project)
    record["name"] = _short(project.get("name"), 80) or "Project"
    _write("projects", record)
    _audit(owner, "save_project", str(record["id"]))
    return _public(record)


def list_visible(*, actor: str) -> dict[str, list[dict[str, Any]]]:
    owner = _actor(actor)
    return {
        "chats": [_public(row) for row in _scan("chats") if _allows(row, owner)],
        "projects": [_public(row) for row in _scan("projects") if _allows(row, owner)],
    }


def share(*, actor: str, kind: str, item_id: str, with_actor: str) -> dict[str, Any]:
    owner = _actor(actor)
    target = _actor(with_actor)
    folder = _folder(kind)
    record = _owned(folder, item_id, owner)
    shared = [item for item in record.get("shared_with") or [] if isinstance(item, str)]
    if target not in shared and target != owner:
        shared.append(target)
    record["shared_with"] = shared[:20]
    _write(folder, record)
    _audit(owner, "share", str(record["id"]))
    return _public(record)


def pack_sources(
    *,
    actor: str,
    kind: str,
    granted: set[str],
    chat_ids: list[str] | None = None,
    project_id: str = "",
    questionnaire: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The provider context. Raises when a requested id is not visible."""
    owner = _actor(actor)
    chats = []
    for item_id in list(chat_ids or [])[:8]:
        chats.append(_pack_item(_visible("chats", item_id, owner), granted))
    project = None
    if project_id:
        project = _pack_item(_visible("projects", project_id, owner), granted)
    form = _questionnaire(questionnaire, granted)
    if not chats and project is None and form is None:
        return {}
    packed: dict[str, Any] = {}
    if form is not None:
        packed["questionnaire"] = form
    if chats:
        packed["chats"] = chats
    if project is not None:
        packed["project"] = project
    blocks = layout_blocks(kind)
    if blocks:
        packed["layout_blocks"] = blocks
    return packed


def _problem(code: str) -> NoReturn:
    from dms_executor.create_surface import CreateProblem

    raise CreateProblem(code)


def _actor(value: str) -> str:
    if not isinstance(value, str) or _ACTOR.fullmatch(value) is None:
        _problem("context_forbidden")
    return value


def _folder(kind: str) -> str:
    if kind == "chat":
        return "chats"
    if kind == "project":
        return "projects"
    _problem("context_forbidden")


def _record(kind: str, owner: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "owner": owner,
        "shared_with": [],
        "summary": _short(raw.get("summary"), _TEXT),
        "sql": _sql(raw.get("sql")),
        "results": _results(raw.get("results")),
        "schema": _schema(raw.get("schema")),
        "ontology": _ontology(raw.get("ontology")),
    }


def _public(record: dict[str, Any]) -> dict[str, Any]:
    title = record.get("title") or record.get("name") or ""
    return {
        "id": record.get("id"),
        "kind": record.get("kind"),
        "title": title,
        "summary": record.get("summary") or "",
    }


def _allows(record: dict[str, Any], actor: str) -> bool:
    if record.get("owner") == actor:
        return True
    shared = record.get("shared_with")
    return isinstance(shared, list) and actor in shared


def _visible(folder: str, item_id: str, actor: str) -> dict[str, Any]:
    if not isinstance(item_id, str) or _ID.fullmatch(item_id) is None:
        _problem("context_forbidden")
    record = _read(folder, item_id)
    if record is None or not _allows(record, actor):
        _problem("context_forbidden")
    return record


def _owned(folder: str, item_id: str, actor: str) -> dict[str, Any]:
    record = _visible(folder, item_id, actor)
    if record.get("owner") != actor:
        _problem("context_forbidden")
    return record


def _pack_item(record: dict[str, Any], granted: set[str]) -> dict[str, Any]:
    original = [item["name"] for item in record.get("schema") or [] if isinstance(item, dict)]
    tainted = bool(original) and any(name not in granted for name in original)
    schema = [item for item in record.get("schema") or [] if item.get("name") in granted]
    packed: dict[str, Any] = {
        "id": record.get("id"),
        "title": record.get("title") or record.get("name") or "",
        "summary": record.get("summary") or "",
        "schema": schema,
        "ontology": _granted_ontology(record.get("ontology") or [], granted),
    }
    if not tainted:
        sql = record.get("sql") or []
        results = _granted_results(record.get("results") or [], granted)
        if sql:
            packed["sql"] = sql
        if results:
            packed["results"] = results
    return packed


def _questionnaire(raw: dict[str, Any] | None, granted: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    must = raw.get("must_include") or []
    names: list[str] = []
    if isinstance(must, list):
        names = [_short(item, 80) for item in must[:12]]
        names = [item for item in names if item and item in granted]
    form = {
        "audience": _short(raw.get("audience"), 200),
        "goal": _short(raw.get("goal"), 400),
        "tone": _short(raw.get("tone"), 80),
        "length": _short(raw.get("length"), 80),
        "must_include": names,
    }
    if not any(form.values()):
        return None
    return form


def _schema(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:12]:
        if isinstance(item, str) and item.strip():
            out.append({"name": item.strip()[:80], "columns": []})
            continue
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        columns: list[str] = []
        for col in (item.get("columns") or [])[:_COL_CAP]:
            if isinstance(col, str) and col.strip():
                columns.append(col.strip()[:40])
            elif isinstance(col, dict) and isinstance(col.get("name"), str):
                columns.append(str(col["name"])[:40])
        out.append({"name": item["name"].strip()[:80], "columns": columns})
    return [item for item in out if item["name"]]


def _ontology(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[:12]:
        if isinstance(item, str) and item.strip():
            out.append({"name": item.strip()[:80]})
            continue
        if not isinstance(item, dict):
            continue
        left = _short(item.get("from"), 80)
        right = _short(item.get("to"), 80)
        if left and right:
            out.append({"from": left, "to": right})
        elif isinstance(item.get("name"), str) and item["name"].strip():
            out.append({"name": item["name"].strip()[:80]})
    return out


def _granted_ontology(items: list[dict[str, str]], granted: set[str]) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    for item in items:
        names = [item[key] for key in ("name", "from", "to") if item.get(key)]
        if names and all(name in granted for name in names):
            kept.append(item)
    return kept


def _results(raw: Any) -> list[dict[str, Any]]:
    groups = raw if isinstance(raw, list) else []
    if groups and all(not isinstance(item, dict) or "rows" not in item for item in groups):
        groups = [{"rows": groups}]
    out: list[dict[str, Any]] = []
    for group in groups[:4]:
        if not isinstance(group, dict):
            continue
        rows = _rows(group.get("rows"))
        if not rows:
            continue
        item: dict[str, Any] = {"rows": rows}
        table = group.get("table")
        if isinstance(table, str) and table.strip():
            item["table"] = table.strip()[:80]
        out.append(item)
    return out


def _rows(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for row in raw[:_ROW_CAP]:
        if not isinstance(row, dict):
            continue
        kept: dict[str, str] = {}
        for key in list(row)[:_COL_CAP]:
            if not isinstance(key, str) or key.lower() in {"raw", "blob"}:
                continue
            value = row[key]
            if isinstance(value, (dict, list)):
                continue
            kept[key[:40]] = str(value)[:_CELL]
        if kept:
            out.append(kept)
    return out


def _granted_results(groups: list[dict[str, Any]], granted: set[str]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for group in groups:
        table = group.get("table")
        if isinstance(table, str) and table not in granted:
            continue
        item: dict[str, Any] = {"rows": group.get("rows") or []}
        if isinstance(table, str):
            item["table"] = table
        if item["rows"]:
            kept.append(item)
    return kept


def _sql(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:4]:
        text = _short(item, _SQL)
        if text:
            out.append(text)
    return out


def _short(value: Any, cap: int) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())[:cap]


def _root() -> Path:
    raw = os.environ.get("DMS_CREATE_STORE", "").strip()
    if not raw:
        _problem("store_missing")
    path = Path(raw) / "context"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(folder: str, record: dict[str, Any]) -> None:
    path = _root() / folder
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{record['id']}.json"
    with _LOCK:
        target.write_text(json.dumps(record), encoding="utf-8")


def _read(folder: str, item_id: str) -> dict[str, Any] | None:
    path = _root() / folder / f"{item_id}.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _scan(folder: str) -> list[dict[str, Any]]:
    path = _root() / folder
    if not path.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for item in sorted(path.glob("*.json")):
        if _ID.fullmatch(item.stem) is None:
            continue
        try:
            loaded = json.loads(item.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            found.append(loaded)
    return found


def _audit(actor: str, action: str, item_id: str) -> None:
    path = _root() / "audit.jsonl"
    row = {"actor": actor, "action": action, "id": item_id}
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
