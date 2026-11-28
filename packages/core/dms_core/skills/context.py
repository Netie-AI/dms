"""Provider context for Ask, Create, and agents.

DMS sends the granted skill instructions, the pin, and the licence.
It does not run the skill. Document citations are short excerpts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dms_core.skills.flag import enabled
from dms_core.skills.store import SkillLibrary
from dms_core.skills.web_insight import enabled as web_insight_enabled
from dms_core.skills.web_insight import tool_spec as web_insight_tool

_SURFACES = frozenset({"ask", "create", "agent"})


def provider_context(
    *,
    surface: str,
    tenant_id: str = "",
    root: Path | None = None,
    question: str = "",
    space_id: str | None = None,
    embedder: Any = None,
) -> dict[str, Any]:
    """Granted skills for one surface. Empty when both flags are off."""
    if surface not in _SURFACES:
        return {"surface": surface, "skills": [], "citations": [], "tools": []}
    skills: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    if enabled():
        library = SkillLibrary.open(tenant_id or _tenant(), root)
        skills = [_skill(row) for row in library.granted(surface)]
        if surface == "ask" and question.strip():
            citations = library.citations(
                question, space_id=space_id, embedder=embedder, limit=3
            )
        tools = [
            {
                "name": f"skill.{item['name']}",
                "description": str(item["description"])[:240],
                "pin": item["pin"],
                "licence": item["licence"],
                "ran_by": "provider",
            }
            for item in skills
        ]
    if web_insight_enabled():
        spec = web_insight_tool()
        tools.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "ran_by": "provider",
                "label": "Google AI Overview",
            }
        )
    return {"surface": surface, "skills": skills, "citations": citations, "tools": tools}


def skill_prompt(
    question: str,
    *,
    space_id: str | None = None,
    tenant_id: str = "",
    root: Path | None = None,
    embedder: Any = None,
) -> str:
    """Ask block. Empty when the flag is off, before the store is opened."""
    if not enabled():
        return ""
    ctx = provider_context(
        surface="ask",
        tenant_id=tenant_id,
        root=root,
        question=question,
        space_id=space_id,
        embedder=embedder,
    )
    if not ctx["skills"] and not ctx["citations"]:
        return ""
    lines = ["\nskill context; the provider runs these. DMS does not."]
    for skill in ctx["skills"]:
        lines.append(
            f"skill: {skill['name']} pin: {skill['pin']} licence: {skill['licence']}"
        )
        lines.append(str(skill["instructions"]))
    for cite in ctx["citations"]:
        lines.append(
            "citation: {doc_id} {title} chunk {chunk_index}: {excerpt}".format(**cite)
        )
    return "\n".join(lines) + "\n"


def attach_skill_context(
    ctx: dict[str, Any],
    question: str,
    *,
    space_id: str | None = None,
    tenant_id: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Same object when the flag is off or nothing is granted."""
    if not enabled():
        return ctx
    block = skill_prompt(question, space_id=space_id, tenant_id=tenant_id, root=root)
    if not block:
        return ctx
    out = dict(ctx)
    out["skill_context"] = block
    return out


def with_granted_library(picked: dict[str, Any], *, surface: str = "create") -> dict[str, Any]:
    """Create's skill dict. Same object when the flag is off or nothing is granted."""
    if not enabled():
        return picked
    extra = provider_context(surface=surface)["skills"]
    if not extra:
        return picked
    local = [item for item in (picked.get("local") or []) if isinstance(item, dict)]
    have = {str(item.get("name") or "") for item in local}
    added = False
    for skill in extra:
        name = str(skill["name"])
        if name in have:
            continue
        local.append({"name": name, "text": str(skill["instructions"])})
        have.add(name)
        added = True
    if not added:
        return picked
    out = dict(picked)
    out["local"] = local
    return out


def _skill(row: dict[str, Any]) -> dict[str, Any]:
    licence = row.get("licence") or {}
    pin = str(row.get("commit_sha") or "") or str(row.get("content_sha256") or "")
    return {
        "name": row.get("name", ""),
        "description": row.get("description", ""),
        "pin": pin,
        "licence": licence.get("id", "") if isinstance(licence, dict) else "",
        "instructions": row.get("instructions", ""),
        "version": row.get("version", 0),
        "id": row.get("id", ""),
    }


def _tenant() -> str:
    return os.environ.get("DMS_TENANT_ID", "").strip() or "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
