"""Plan a doc or a deck, then call the shared ``generate``.

The model designs the artifact. The template and the style guide are context
on that call. This module does not judge, does not store a brand kit, and
does not draw charts.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from create_docs.adapter import governance

Transport = Callable[[dict[str, Any], str], Any]
_KINDS = frozenset({"slides", "doc"})
_BRAND = ("colours", "fonts", "logo", "tone")


def new_run(answers: list[dict[str, Any]], style: dict[str, Any]) -> dict[str, Any]:
    if not answers:
        raise ValueError("no_answers")
    if len(answers) > 12:
        raise ValueError("too_many_answers")
    gov = governance()
    kind = str(style.get("kind") or "slides")
    if kind not in _KINDS:
        raise ValueError("kind")
    template = str(style.get("template") or "plain").strip() or "plain"
    version = str(style.get("prompt_version") or "1").strip() or "1"
    try:
        template_body = gov.template_text(template)
        library = gov.prompt_library(version)
        skills = gov.skills_for(kind, workbook=bool(style.get("workbook")))
    except gov.GeneratorError as exc:
        raise ValueError(exc.code) from None
    guides = library.get("guides") if isinstance(library.get("guides"), dict) else {}
    guide = str(style.get("guide") or "").strip() or str(guides.get(kind) or "")
    brand = {key: str((style.get("brand") or {}).get(key) or "").strip()[:80] for key in _BRAND}
    governed = [_answer(item, index) for index, item in enumerate(answers)]
    rows = [row for item in governed for row in item["rows"]]
    spec = {
        "kind": kind,
        "goal": governed[0]["question"] if len(governed) == 1 else "Answers from this session",
        "audience": "reader",
        "workbook": bool(style.get("workbook")),
    }
    data_context = {
        "schema": [
            {"name": item["source"] or item["id"], "columns": item["columns"]} for item in governed
        ],
        "data": [
            {"name": item["source"] or item["id"], "rows": item["rows"], "sql": item["sql"]}
            for item in governed
        ],
        "examples": _shots(style.get("few_shots")),
        "choices": {"template": template},
        "skills": skills,
    }
    sent_style = {
        "prompt": str(style.get("prompt_text") or "").strip()[:2000],
        "template": template_body,
        "brand": brand,
        "guide": guide[:4000],
    }
    return {
        "run_id": uuid.uuid4().hex,
        "spec": spec,
        "style": sent_style,
        "template": template,
        "prompt_version": version,
        "tools": style.get("tools") if isinstance(style.get("tools"), dict) else None,
        "data_context": data_context,
        "inputs": answers,
        "answers": governed,
        "allowed": gov.allowed_from(rows, {}),
        "steps": 0,
        "repaired": False,
        "stopped": None,
        "spend": {"prompt_tokens": None, "completion_tokens": None, "usd": None},
        "versions": [],
        "generated": False,
    }


def apply_style(run: dict[str, Any], style: dict[str, Any]) -> None:
    if run["generated"]:
        raise ValueError("already_generated")
    fresh = new_run(run["inputs"], style)
    fresh["run_id"] = run["run_id"]
    run.clear()
    run.update(fresh)


def produce(
    run: dict[str, Any],
    *,
    api_key: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    version = _call(run, dict(run["spec"]), api_key, transport)
    if version is None or not version["issues"] or run["stopped"]:
        run["generated"] = True
        return public_view(run)
    run["repaired"] = True
    repair = dict(run["spec"])
    repair["judge"] = {"issues": version["issues"]}
    _call(run, repair, api_key, transport)
    run["generated"] = True
    return public_view(run)


def edit(
    run: dict[str, Any],
    target: str,
    instruction: str,
    *,
    api_key: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    if not run["generated"]:
        raise ValueError("generate_first")
    text = instruction.strip()
    if not text:
        raise ValueError("instruction")
    prior = _latest_artifact(run)
    if prior is None:
        raise ValueError("no_artifact")
    known = {str(part.get("id")) for part in prior.get("parts") or [] if isinstance(part, dict)}
    if target not in known:
        raise ValueError("target")
    spec = dict(run["spec"])
    spec["edit"] = {"section_id": target, "instruction": text[:400], "prior": prior}
    _call(run, spec, api_key, transport, prior=prior, section_id=target)
    return public_view(run)


def public_view(run: dict[str, Any]) -> dict[str, Any]:
    versions = []
    for version in run["versions"]:
        artifact = version["artifact"]
        versions.append(
            {
                "id": version["id"],
                "artifact": artifact,
                "charts": _charts(artifact),
                "issues": version["issues"],
                "cost": version["cost"],
                "sign": version["sign"],
                "edit": version.get("edit"),
            }
        )
    latest = versions[-1]["artifact"] if versions else None
    parts = latest.get("parts") if isinstance(latest, dict) else []
    return {
        "run_id": run["run_id"],
        "spec": run["spec"],
        "style": {
            "prompt": run["style"].get("prompt") or "",
            "template": run["template"],
            "template_text": run["style"].get("template") or "",
            "brand": run["style"].get("brand") or {},
            "guide": run["style"].get("guide") or "",
        },
        "skills": (run["data_context"].get("skills") or {}).get("anthropic") or [],
        "steps": run["steps"],
        "spend": run["spend"],
        "stopped": run["stopped"],
        "generated": run["generated"],
        "versions": versions,
        "sections": [
            {"id": part.get("id"), "title": part.get("title")}
            for part in parts or []
            if isinstance(part, dict)
        ],
        "charts": _charts(latest) if isinstance(latest, dict) else [],
    }


def _call(
    run: dict[str, Any],
    spec: dict[str, Any],
    api_key: str,
    transport: Transport | None,
    *,
    prior: dict[str, Any] | None = None,
    section_id: str | None = None,
) -> dict[str, Any] | None:
    if run["stopped"]:
        return None
    gov = governance()
    try:
        turn = gov.generate(
            spec,
            run["data_context"],
            run["style"],
            api_key=api_key,
            prompt_version=run["prompt_version"],
            enabled=run["tools"],
            transport=transport,
        )
    except gov.GeneratorError as exc:
        run["steps"] += 1
        run["stopped"] = exc.code
        return None
    run["steps"] += 1
    _add_cost(run, turn)
    artifact = turn.artifact if isinstance(turn.artifact, dict) else None
    if section_id and prior is not None and isinstance(artifact, dict):
        artifact = _splice(gov, prior, section_id, artifact)
    if not isinstance(artifact, dict):
        run["stopped"] = "model_unreadable"
        return None
    issues = gov.judge_artifact(
        artifact,
        kind=str(run["spec"]["kind"]),
        brand=run["style"].get("brand") or {},
        allowed_numbers=run["allowed"],
    )
    label_issues = gov.validate_call("sign_request", {"label": run["run_id"]})
    sign = {
        "tool": "sign_request",
        "digest": gov.prompt_digest(turn.prompt),
        "issues": label_issues,
    }
    version = {
        "id": f"v{len(run['versions']) + 1}",
        "artifact": artifact,
        "issues": issues,
        "cost": {
            "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens,
            "usd": turn.cost,
        },
        "sign": sign,
        "edit": spec.get("edit"),
    }
    run["versions"].append(version)
    return version


def _splice(
    gov: Any,
    prior: dict[str, Any],
    section_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    part = next(
        (
            item
            for item in artifact.get("parts") or []
            if isinstance(item, dict) and item.get("id") == section_id
        ),
        None,
    )
    if not isinstance(part, dict):
        return artifact
    try:
        return gov.splice_section(prior, section_id, part)
    except gov.GeneratorError:
        return artifact


def _charts(artifact: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(artifact, dict):
        return []
    charts = artifact.get("charts")
    if not isinstance(charts, list):
        return []
    return [item for item in charts if isinstance(item, dict)]


def _latest_artifact(run: dict[str, Any]) -> dict[str, Any] | None:
    if not run["versions"]:
        return None
    artifact = run["versions"][-1]["artifact"]
    return artifact if isinstance(artifact, dict) else None


def _add_cost(run: dict[str, Any], turn: Any) -> None:
    spend = run["spend"]
    pairs = (
        ("prompt_tokens", turn.prompt_tokens),
        ("completion_tokens", turn.completion_tokens),
        ("usd", turn.cost),
    )
    for key, incoming in pairs:
        if incoming is None:
            spend[key] = None
            continue
        if spend.get(key) is None and run["steps"] > 1:
            continue
        spend[key] = (spend.get(key) or 0) + incoming


def _answer(answer: dict[str, Any], index: int) -> dict[str, Any]:
    rows = []
    for row in list(answer.get("rows") or [])[:20]:
        if not isinstance(row, dict):
            raise ValueError("row")
        rows.append({str(key)[:80]: value for key, value in list(row.items())[:12]})
    columns = sorted({key for row in rows for key in row})
    sources = [str(item) for item in list(answer.get("sources") or [])[:4] if str(item).strip()]
    return {
        "id": f"s{index}",
        "question": str(answer.get("question") or "").strip()[:180],
        "text": str(answer.get("text") or "").strip()[:4000],
        "sql": str(answer.get("sql") or "").strip()[:8000],
        "source": sources[0] if sources else "",
        "rows": rows,
        "columns": columns,
    }


def _shots(raw: Any) -> list[dict[str, str]]:
    shots = []
    for item in list(raw or [])[:4]:
        if not isinstance(item, dict):
            raise ValueError("few_shot")
        shots.append(
            {
                "title": str(item.get("title") or "").strip()[:120],
                "text": str(item.get("text") or "").strip()[:1000],
            }
        )
    return shots
