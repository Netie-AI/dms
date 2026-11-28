"""Per-tenant skill versions, grants, documents, and an append-only audit log.

ponytail: one process lock and a JSON file. Upgrade: a file lock when more
than one API worker writes the same tenant directory.
``DMS_SKILLS_DIR`` is the volume swap. Unset, the store is the process temp
dir so a flag-on run does not write into the git tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dms_core.skills.catalog import catalog_bundle
from dms_core.skills.documents import (
    chunk_text,
    embed_chunks,
    excerpt,
    extract_text,
    rank_chunks,
)
from dms_core.skills.errors import SkillError

_LOCK = threading.Lock()
_SURFACES = frozenset({"ask", "create", "agent"})
_SECRET_HINT = ("AKIA", "PRIVATE KEY", "sk-ant-", "sk-proj-", "ghp_", "AIza")


def library_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    env = os.environ.get("DMS_SKILLS_DIR", "").strip()
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "dms-skills"


class SkillLibrary:
    def __init__(self, root: Path, tenant_id: str) -> None:
        self.root = root
        self.tenant_id = _tenant(tenant_id)
        self.dir = root / self.tenant_id

    @classmethod
    def open(cls, tenant_id: str, root: Path | None = None) -> SkillLibrary:
        return cls(library_root(root), tenant_id)

    def install_bundle(self, bundle: dict[str, Any], *, actor: str) -> dict[str, Any]:
        with _LOCK:
            state = self._load()
            name = str(bundle["name"])
            version = 1 + max(
                (int(row["version"]) for row in state["versions"] if row["name"] == name),
                default=0,
            )
            record = {
                "id": str(uuid.uuid4()),
                "name": name,
                "version": version,
                "origin": str(bundle.get("origin") or ""),
                "url": str(bundle.get("url") or ""),
                "commit_sha": str(bundle.get("commit_sha") or ""),
                "content_sha256": str(bundle["content_sha256"]),
                "licence": dict(bundle["licence"]),
                "description": str(bundle["description"]),
                "instructions": str(bundle["instructions"]),
                "files": list(bundle["files"]),
                "actor": actor,
                "at": _now(),
            }
            state["versions"].append(record)
            self._save(state)
            self._audit(
                "install",
                actor,
                skill=name,
                version_id=str(record["id"]),
                pin=str(record["commit_sha"] or record["content_sha256"]),
            )
        return _public_version(record)

    def stage_fetch(self, bundle: dict[str, Any], *, actor: str) -> dict[str, Any]:
        with _LOCK:
            state = self._load()
            fetch_id = str(uuid.uuid4())
            state["fetches"].append(
                {"id": fetch_id, "bundle": bundle, "actor": actor, "at": _now()}
            )
            self._save(state)
            self._audit(
                "fetch",
                actor,
                skill=str(bundle.get("name") or ""),
                pin=str(bundle.get("commit_sha") or ""),
            )
        return {"fetch_id": fetch_id, **_public_version({**bundle, "id": fetch_id, "version": 0})}

    def install_fetch(self, fetch_id: str, *, actor: str) -> dict[str, Any]:
        with _LOCK:
            state = self._load()
            found = next((row for row in state["fetches"] if row["id"] == fetch_id), None)
            if found is None:
                raise SkillError("fetch_unknown")
            bundle = dict(found["bundle"])
            state["fetches"] = [row for row in state["fetches"] if row["id"] != fetch_id]
            self._save(state)
        return self.install_bundle(bundle, actor=actor)

    def install_catalog(self, name: str, *, actor: str) -> dict[str, Any]:
        return self.install_bundle(catalog_bundle(name), actor=actor)

    def grant(self, version_id: str, surfaces: list[str], *, actor: str) -> dict[str, Any]:
        chosen = _surfaces(surfaces)
        with _LOCK:
            state = self._load()
            if not any(row["id"] == version_id for row in state["versions"]):
                raise SkillError("version_unknown")
            state["grants"] = [row for row in state["grants"] if row["version_id"] != version_id]
            record = {
                "version_id": version_id,
                "surfaces": chosen,
                "actor": actor,
                "at": _now(),
            }
            state["grants"].append(record)
            self._save(state)
            self._audit("grant", actor, version_id=version_id, code=",".join(chosen))
        return record

    def revoke(self, version_id: str, *, actor: str) -> dict[str, Any]:
        with _LOCK:
            state = self._load()
            before = len(state["grants"])
            state["grants"] = [row for row in state["grants"] if row["version_id"] != version_id]
            if len(state["grants"]) == before:
                raise SkillError("grant_unknown")
            self._save(state)
            self._audit("revoke", actor, version_id=version_id)
        return {"version_id": version_id, "revoked": True}

    def versions(self) -> list[dict[str, Any]]:
        state = self._load()
        granted = {row["version_id"]: row["surfaces"] for row in state["grants"]}
        return [
            {**_public_version(row), "surfaces": list(granted.get(row["id"], []))}
            for row in state["versions"]
        ]

    def granted(self, surface: str) -> list[dict[str, Any]]:
        state = self._load()
        by_id = {row["id"]: row for row in state["versions"]}
        out: list[dict[str, Any]] = []
        for grant in state["grants"]:
            if surface not in grant["surfaces"]:
                continue
            row = by_id.get(grant["version_id"])
            if row is None:
                continue
            out.append(row)
        return out

    def add_document(
        self,
        *,
        filename: str,
        data: bytes,
        space_id: str,
        actor: str,
        embedder: Any = None,
    ) -> dict[str, Any]:
        text, media = extract_text(filename, data)
        chunks = chunk_text(text)
        vectors, embed = embed_chunks(chunks, embedder)
        record = {
            "id": str(uuid.uuid4()),
            "space_id": (space_id or "").strip(),
            "title": Path(filename).name[:200],
            "media": media,
            "sha256": hashlib.sha256(data).hexdigest(),
            "embed": embed,
            "chunks": [
                {"index": index, "text": chunk, "vector": vectors[index]}
                for index, chunk in enumerate(chunks)
            ],
            "surfaces": [],
            "actor": actor,
            "at": _now(),
        }
        with _LOCK:
            state = self._load()
            state["documents"].append(record)
            self._save(state)
            self._audit("document_upload", actor, skill=str(record["id"]), code=embed)
        return _public_document(record)

    def grant_document(self, doc_id: str, surfaces: list[str], *, actor: str) -> dict[str, Any]:
        chosen = _surfaces(surfaces)
        with _LOCK:
            state = self._load()
            found = next((row for row in state["documents"] if row["id"] == doc_id), None)
            if found is None:
                raise SkillError("document_unknown")
            found["surfaces"] = chosen
            self._save(state)
            self._audit("document_grant", actor, skill=doc_id, code=",".join(chosen))
        return _public_document(found)

    def documents(self, space_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._load()["documents"]
        if space_id is not None:
            rows = [row for row in rows if row["space_id"] == space_id]
        return [_public_document(row) for row in rows]

    def citations(
        self,
        question: str,
        *,
        space_id: str | None,
        embedder: Any = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        state = self._load()
        hits: list[dict[str, Any]] = []
        for doc in state["documents"]:
            if "ask" not in doc.get("surfaces", []):
                continue
            if space_id and doc["space_id"] not in {"", space_id}:
                continue
            order = rank_chunks(question, doc["chunks"], embedder)
            for index in order[:limit]:
                chunk = doc["chunks"][index]
                hits.append(
                    {
                        "doc_id": doc["id"],
                        "title": doc["title"],
                        "chunk_index": chunk["index"],
                        "excerpt": excerpt(str(chunk["text"])),
                    }
                )
        return hits[:limit]

    def audit_log(self) -> list[dict[str, str]]:
        path = self.dir / "audit.jsonl"
        if not path.is_file():
            return []
        rows: list[dict[str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def record(self, action: str, actor: str, **fields: str) -> None:
        self._audit(action, actor, **fields)

    def _audit(self, action: str, actor: str, **fields: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        row = {
            "at": _now(),
            "actor": _clean(actor),
            "action": _clean(action),
            "skill": _clean(fields.get("skill", "")),
            "version_id": _clean(fields.get("version_id", "")),
            "pin": _clean(fields.get("pin", "")),
            "code": _clean(fields.get("code", "")),
        }
        with (self.dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def _load(self) -> dict[str, Any]:
        path = self.dir / "state.json"
        if not path.is_file():
            return {"versions": [], "grants": [], "fetches": [], "documents": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("versions", "grants", "fetches", "documents"):
            data.setdefault(key, [])
        return data

    def _save(self, state: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "state.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _public_version(row: dict[str, Any]) -> dict[str, Any]:
    licence = row.get("licence") or {}
    pin = str(row.get("commit_sha") or "") or str(row.get("content_sha256") or "")
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "version": row.get("version", 0),
        "origin": row.get("origin", ""),
        "url": row.get("url", ""),
        "pin": pin,
        "commit_sha": row.get("commit_sha", ""),
        "content_sha256": row.get("content_sha256", ""),
        "licence": licence.get("id", "") if isinstance(licence, dict) else "",
        "licence_source": licence.get("source", "") if isinstance(licence, dict) else "",
        "description": row.get("description", ""),
        "instructions": row.get("instructions", ""),
        "files": row.get("files", []),
    }


def _public_document(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "space_id": row["space_id"],
        "title": row["title"],
        "media": row["media"],
        "sha256": row["sha256"],
        "embed": row["embed"],
        "chunks": len(row["chunks"]),
        "surfaces": list(row.get("surfaces") or []),
    }


def _surfaces(surfaces: list[str]) -> list[str]:
    chosen: list[str] = []
    for item in surfaces:
        name = str(item).strip()
        if name not in _SURFACES:
            raise SkillError("surface_unknown")
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        raise SkillError("surface_unknown")
    return chosen


def _tenant(tenant_id: str) -> str:
    text = (tenant_id or "").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
    if not text or any(char not in allowed for char in text):
        raise SkillError("tenant_invalid")
    return text


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: str) -> str:
    text = str(value)[:200]
    if any(hint in text for hint in _SECRET_HINT):
        return "[redacted]"
    return text
