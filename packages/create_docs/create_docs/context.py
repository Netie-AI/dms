"""XML context for the provider. Granted answers only.

Prompt shape follows Anthropic's XML-tag guidance: one tag per block, the
data separate from the style. This file does not call a model.
"""

from __future__ import annotations

import json
import math
from typing import Any
from xml.sax.saxutils import escape

ROW_CAP = 20
_SKILLS = frozenset({"pptx", "docx", "xlsx", "pdf"})
FORMATS = frozenset({"pptx", "docx", "pdf", "html", "xlsx"})


def skill_for(fmt: str, override: str | None) -> str:
    if override:
        if override not in _SKILLS:
            raise ValueError("skill_id")
        return override
    if fmt == "html":
        return "pdf"
    if fmt not in _SKILLS:
        raise ValueError("format")
    return fmt


def govern(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Schema summary, shown-row sums, and at most 20 result rows. No raw table."""
    out: list[dict[str, Any]] = []
    for index, answer in enumerate(answers):
        rows = [_row(row) for row in list(answer.get("rows") or [])[:ROW_CAP]]
        out.append(
            {
                "id": f"s{index}",
                "question": _text(answer.get("question"), 500),
                "text": _text(answer.get("text"), 4000),
                "sql": _text(answer.get("sql"), 8000),
                "sources": [
                    _text(s, 200) for s in list(answer.get("sources") or [])[:8] if _text(s, 200)
                ],
                "as_of": _text(answer.get("as_of"), 40),
                "space_id": _text(answer.get("space_id"), 128) or None,
                "schema": _schema(rows),
                "rows": rows,
                "shown_sums": _sums(rows),
                "row_count_shown": len(rows),
            }
        )
    return out


def build_spec(answers: list[dict[str, Any]], fmt: str, skill_id: str) -> dict[str, Any]:
    title = "Answers from this session"
    if len(answers) == 1:
        title = _text(answers[0].get("question"), 180) or "Answer"
    sections = [{"id": "title", "title": title, "kind": "title"}]
    for index, answer in enumerate(answers):
        sections.append(
            {
                "id": f"s{index}",
                "title": _text(answer.get("question"), 180) or "Answer",
                "kind": "insight",
            }
        )
    sections.append({"id": "appendix", "title": "Appendix: SQL", "kind": "appendix"})
    spec: dict[str, Any] = {
        "format": fmt,
        "skill_id": skill_id,
        "title": title,
        "sections": sections,
        "phase": "build",
    }
    if fmt == "pptx":
        spec["slide_count"] = len(sections)
    return spec


def to_xml(spec: dict[str, Any], answers: list[dict[str, Any]], style: dict[str, Any]) -> str:
    """One document. Tags keep the spec, the granted data, and the style apart."""
    parts = [
        "<create_docs>",
        f'<spec format="{escape(str(spec.get("format") or ""))}" '
        f'skill_id="{escape(str(spec.get("skill_id") or ""))}">',
        f"<title>{escape(str(spec.get('title') or ''))}</title>",
    ]
    for section in spec.get("sections") or []:
        parts.append(
            f'<section id="{escape(str(section.get("id") or ""))}" '
            f'kind="{escape(str(section.get("kind") or ""))}">'
            f"{escape(str(section.get('title') or ''))}</section>"
        )
    edit = spec.get("edit")
    if isinstance(edit, dict):
        parts.append(
            f'<edit target="{escape(str(edit.get("target") or ""))}">'
            f"{escape(str(edit.get('instruction') or ''))}</edit>"
        )
    repair = spec.get("repair")
    if repair:
        parts.append(f"<repair>{escape(json.dumps(repair))}</repair>")
    parts.append("</spec>")
    parts.append('<data_context granted_only="true">')
    for answer in answers:
        parts.append(f'<answer id="{escape(str(answer.get("id") or ""))}">')
        parts.append(f"<question>{escape(str(answer.get('question') or ''))}</question>")
        parts.append(f"<text>{escape(str(answer.get('text') or ''))}</text>")
        parts.append(f"<sql>{escape(str(answer.get('sql') or ''))}</sql>")
        parts.append(f"<as_of>{escape(str(answer.get('as_of') or ''))}</as_of>")
        for source in answer.get("sources") or []:
            parts.append(f"<source>{escape(str(source))}</source>")
        parts.append("<schema_summary>")
        for column in answer.get("schema") or []:
            parts.append(
                f'<column name="{escape(str(column.get("name") or ""))}" '
                f'type="{escape(str(column.get("type") or ""))}"/>'
            )
        parts.append("</schema_summary>")
        parts.append("<aggregates>")
        for row in answer.get("rows") or []:
            parts.append("<row>")
            for key, value in row.items():
                parts.append(f'<cell name="{escape(str(key))}">{escape(_cell_text(value))}</cell>')
            parts.append("</row>")
        for item in answer.get("shown_sums") or []:
            parts.append(
                f'<shown_sum column="{escape(str(item.get("column") or ""))}" '
                f'value="{escape(str(item.get("value") or ""))}"/>'
            )
        parts.append("</aggregates>")
        parts.append("</answer>")
    parts.append("</data_context>")
    parts.append("<style>")
    parts.append(
        f'<prompt version="{escape(str(style.get("prompt_version") or ""))}">'
        f"{escape(str(style.get('prompt_text') or ''))}</prompt>"
    )
    parts.append(f"<super_guide>{escape(str(style.get('super_guide') or ''))}</super_guide>")
    parts.append(f'<template id="{escape(str(style.get("template_id") or ""))}"/>')
    brand = style.get("brand") or {}
    parts.append(
        f'<brand name="{escape(str(brand.get("name") or ""))}" '
        f'primary="{escape(str(brand.get("primary") or ""))}" '
        f'font="{escape(str(brand.get("font") or ""))}">'
        f"{escape(str(brand.get('notes') or ''))}</brand>"
    )
    for shot in style.get("few_shots") or []:
        parts.append(
            f'<few_shot title="{escape(str(shot.get("title") or ""))}">'
            f"{escape(str(shot.get('text') or ''))}</few_shot>"
        )
    parts.append(f"<route>{escape(str(style.get('route') or 'openvault'))}</route>")
    parts.append("</style>")
    parts.append("</create_docs>")
    return "".join(parts)


def allowed_numbers(answers: list[dict[str, Any]]) -> set[str]:
    """Figures the file may cite. Structural ids and the skill name are not figures."""
    blob = json.dumps(
        [
            {
                "question": a.get("question"),
                "text": a.get("text"),
                "sql": a.get("sql"),
                "sources": a.get("sources"),
                "as_of": a.get("as_of"),
                "rows": a.get("rows"),
                "shown_sums": a.get("shown_sums"),
            }
            for a in answers
        ],
        default=str,
    )
    return _forms(blob)


def _forms(text: str) -> set[str]:
    import re

    found: set[str] = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        plain = raw.replace(",", "")
        found.add(plain)
        if "." in plain:
            trimmed = plain.rstrip("0").rstrip(".")
            if trimmed:
                found.add(trimmed)
    return found


def _schema(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    kinds: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            kind = "number" if _number(value) is not None else "text"
            prev = kinds.get(key)
            if prev is None:
                kinds[key] = kind
            elif prev != kind:
                kinds[key] = "text"
    return [{"name": key, "type": kind} for key, kind in kinds.items()]


def _sums(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            number = _number(value)
            if number is None:
                continue
            totals[key] = totals.get(key, 0.0) + number
            counts[key] = counts.get(key, 0) + 1
    out = []
    for key, total in totals.items():
        if counts.get(key, 0) < 2:
            continue
        shown: int | float
        if abs(total - round(total)) < 1e-9:
            shown = int(round(total))
        else:
            shown = round(total, 4)
        out.append({"column": key, "value": shown})
    return out


def _row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("row")
    clean: dict[str, Any] = {}
    for key, value in list(row.items())[:12]:
        name = _text(key, 80)
        if not name:
            raise ValueError("row_key")
        clean[name] = _cell(value)
    return clean


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError("row_value")
        return value
    if isinstance(value, str):
        return value[:200]
    raise ValueError("row_value")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()[:limit]
