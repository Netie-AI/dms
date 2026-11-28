"""Curated skills shipped with DMS. They are text, not a second runner."""

from __future__ import annotations

from dms_core.skills.errors import SkillError
from dms_core.skills.validate import build_bundle

_GOOGLE = """---
name: google-ai-insights
description: >
  Cite a Google AI Overview for a general question, then infer
  from governed data already in context. The search is the question only.
license: Apache-2.0
---

# Google AI insights

The tool is google_ai_overview. It runs only when DMS_WEB_INSIGHT is the digit 1.
The fetcher receives the user's question and nothing else.
Show the result as a Google AI Overview citation with the query and the time.
Then infer from that citation and the governed data already in context.
If no overview appears, say so. Do not invent one. Do not treat it as a measured figure.
"""

_CITE = """---
name: document-citations
description: Cite the document id and chunk index DMS placed in the context. Do not invent a figure.
license: Apache-2.0
---

# Document citations

When the context lists a citation, name the doc id and the chunk index you used.
Do not state a number that is not in that excerpt or in executed SQL.
"""

_RAW = {
    "google-ai-insights": _GOOGLE,
    "document-citations": _CITE,
}


def catalog_names() -> list[str]:
    return sorted(_RAW)


def catalog_bundle(name: str) -> dict[str, object]:
    text = _RAW.get(name)
    if text is None:
        raise SkillError("catalog_unknown")
    return build_bundle(
        {"SKILL.md": text},
        commit_sha="",
        url=f"catalog:{name}",
        origin="catalog",
        skill_dir=name,
    )


def search_catalog(query: str) -> list[dict[str, object]]:
    needle = " ".join((query or "").split()).casefold()
    rows: list[dict[str, object]] = []
    for name in catalog_names():
        bundle = catalog_bundle(name)
        hay = f"{bundle['name']} {bundle['description']}".casefold()
        if needle and needle not in hay:
            continue
        rows.append(_public(bundle))
    return rows


def preview_catalog(name: str) -> dict[str, object]:
    return _public(catalog_bundle(name))


def _public(bundle: dict[str, object]) -> dict[str, object]:
    licence = bundle["licence"]
    licence_id = licence["id"] if isinstance(licence, dict) else ""
    return {
        "name": bundle["name"],
        "description": bundle["description"],
        "licence": licence_id,
        "origin": bundle["origin"],
        "instructions": bundle["instructions"],
        "files": bundle["files"],
        "slot": "google" if bundle["name"] == "google-ai-insights" else "",
    }
