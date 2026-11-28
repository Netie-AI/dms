"""Plan, one provider call, one judge repair, section edits.

Versions stay. A passing PDF is hashed and handed to ``sign_request``.
The provider writes every byte of the file. This module does not.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from create_docs.adapter import ProviderStop, resolve_generate, resolve_sign
from create_docs.context import FORMATS, allowed_numbers, build_spec, govern, skill_for, to_xml
from create_docs.judge import judge

MAX_STEPS = 8
_MEDIA = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "html": "text/html",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
DEFAULT_PROMPT = (
    "Use the Anthropic skill named in the spec. "
    "Write only figures that appear in data_context. "
    "One insight per slide or section. "
    "Put the source and the as_of timestamp on each figure. "
    "Put the SQL in the appendix. "
    "Do not name a model."
)


def new_run(
    answers: list[dict[str, Any]],
    style: dict[str, Any],
    *,
    max_steps: int = MAX_STEPS,
    max_usd: float | None = None,
) -> dict[str, Any]:
    if not answers:
        raise ValueError("no_answers")
    if len(answers) > 12:
        raise ValueError("too_many_answers")
    fmt = str(style.get("format") or "pptx")
    if fmt not in FORMATS:
        raise ValueError("format")
    steps = int(max_steps)
    if steps < 1 or steps > MAX_STEPS:
        raise ValueError("max_steps")
    if max_usd is not None and (max_usd < 0 or max_usd > 25):
        raise ValueError("max_usd")
    clean_style = _style(style, fmt)
    governed = govern(answers)
    spec = build_spec(governed, fmt, clean_style["skill_id"])
    data_context = {
        "xml": to_xml(spec, governed, clean_style),
        "answers": governed,
        "granted_only": True,
    }
    return {
        "run_id": uuid.uuid4().hex,
        "spec": spec,
        "style": clean_style,
        "data_context": data_context,
        "allowed": allowed_numbers(governed),
        "budget": {"max_steps": steps, "max_usd": max_usd},
        "steps": 0,
        "spend": {"prompt_tokens": None, "completion_tokens": None, "usd": None},
        "stopped": None,
        "versions": [],
        "generated": False,
    }


def apply_style(run: dict[str, Any], style: dict[str, Any]) -> None:
    """User edits before the first provider call. The XML is rebuilt."""
    if run["generated"]:
        raise ValueError("already_generated")
    fmt = str(style.get("format") or run["spec"]["format"])
    clean = _style(style, fmt)
    governed = run["data_context"]["answers"]
    spec = build_spec(governed, fmt, clean["skill_id"])
    run["style"] = clean
    run["spec"] = spec
    run["data_context"] = {
        "xml": to_xml(spec, governed, clean),
        "answers": governed,
        "granted_only": True,
    }


def produce(run: dict[str, Any]) -> dict[str, Any]:
    """Build once. If the judge fails, one repair call. Both versions are kept."""
    version = _call(run, dict(run["spec"]))
    if version is None or version["judge"]["ok"] or run["stopped"]:
        run["generated"] = True
        return public_view(run)
    repair = dict(run["spec"])
    repair["repair"] = version["judge"]["problems"]
    repair["edit"] = None
    _call(run, repair)
    run["generated"] = True
    return public_view(run)


def edit(run: dict[str, Any], target: str, instruction: str) -> dict[str, Any]:
    """One slide or paragraph. The provider returns a new file. The judge runs again."""
    if not run["generated"]:
        raise ValueError("generate_first")
    known = {str(section.get("id")) for section in run["spec"].get("sections") or []}
    if target not in known:
        raise ValueError("target")
    text = instruction.strip()
    if not text:
        raise ValueError("instruction")
    spec = dict(run["spec"])
    spec["edit"] = {"target": target, "instruction": text[:400]}
    spec["repair"] = None
    spec["phase"] = "edit"
    _call(run, spec)
    return public_view(run)


def public_view(run: dict[str, Any]) -> dict[str, Any]:
    versions = []
    for version in run["versions"]:
        versions.append(
            {
                "id": version["id"],
                "filename": version["filename"],
                "media_type": version["media_type"],
                "pdf_sha256": version["pdf_sha256"],
                "artifact_sha256": version["artifact_sha256"],
                "judge": version["judge"],
                "cost": version["cost"],
                "sign": version["sign"],
                "edit": version.get("edit"),
                "repair": version.get("repair"),
            }
        )
    return {
        "run_id": run["run_id"],
        "spec": run["spec"],
        "style": run["style"],
        "xml": run["data_context"]["xml"],
        "budget": run["budget"],
        "steps": run["steps"],
        "spend": run["spend"],
        "stopped": run["stopped"],
        "generated": run["generated"],
        "versions": versions,
        "sections": run["spec"].get("sections") or [],
    }


def file_bytes(
    run: dict[str, Any], *, which: str, version_id: str | None
) -> tuple[bytes, str, str]:
    version = _version(run, version_id)
    if version is None:
        raise ValueError("no_file")
    if which == "pdf":
        return version["pdf"], "application/pdf", "netie-answers.pdf"
    return version["content"], version["media_type"], version["filename"]


def _version(run: dict[str, Any], version_id: str | None) -> dict[str, Any] | None:
    versions = run["versions"]
    if not versions:
        return None
    if version_id is None:
        return versions[-1]
    for version in versions:
        if version["id"] == version_id:
            return version
    return None


def _call(run: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    if run["stopped"]:
        return None
    if run["steps"] >= int(run["budget"]["max_steps"]):
        run["stopped"] = "max_steps"
        return None
    if _over_usd(run):
        run["stopped"] = "budget_usd"
        return None
    data_context = dict(run["data_context"])
    data_context["xml"] = to_xml(spec, data_context["answers"], run["style"])
    try:
        raw = resolve_generate()(spec, data_context, run["style"])
    except ProviderStop as exc:
        run["steps"] += 1
        run["stopped"] = exc.code
        return None
    run["steps"] += 1
    content, pdf, media, filename, cost = _artifact(raw, spec)
    _add_cost(run, cost)
    verdict = judge(content, media_type=media, spec=spec, allowed=run["allowed"])
    pdf_verdict = judge(
        pdf, media_type="application/pdf", spec=_pdf_spec(spec), allowed=run["allowed"]
    )
    if not pdf_verdict["ok"]:
        verdict = {
            "ok": False,
            "problems": [
                *verdict["problems"],
                *[f"pdf:{item}" for item in pdf_verdict["problems"]],
            ],
            "text_chars": verdict["text_chars"],
        }
    pdf_sha = hashlib.sha256(pdf).hexdigest()
    sign = _sign(run, pdf_sha) if verdict["ok"] else {"status": "withheld", "sha256": pdf_sha}
    version = {
        "id": f"v{len(run['versions']) + 1}",
        "content": content,
        "pdf": pdf,
        "media_type": media,
        "filename": filename,
        "pdf_sha256": pdf_sha,
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
        "judge": verdict,
        "cost": cost,
        "sign": sign,
        "edit": spec.get("edit"),
        "repair": spec.get("repair"),
    }
    run["versions"].append(version)
    if _over_usd(run):
        run["stopped"] = "budget_usd"
    return version


def _pdf_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The signable PDF must carry the same section titles and no extra figures."""
    return {"format": "pdf", "sections": spec.get("sections") or []}


def _sign(run: dict[str, Any], pdf_sha: str) -> dict[str, Any]:
    payload = {
        "tool": "sign_request",
        "sha256": pdf_sha,
        "filename": "netie-answers.pdf",
        "media_type": "application/pdf",
        "run_id": run["run_id"],
        "version": f"v{len(run['versions']) + 1}",
    }
    fn = resolve_sign()
    if fn is None:
        return {"status": "adapter_missing", "sha256": pdf_sha}
    try:
        result = fn(payload)
    except ProviderStop as exc:
        run["stopped"] = exc.code
        return {"status": exc.code, "sha256": pdf_sha}
    if not isinstance(result, dict):
        return {"status": "sign_unreadable", "sha256": pdf_sha}
    result.setdefault("sha256", pdf_sha)
    return result


def _artifact(raw: Any, spec: dict[str, Any]) -> tuple[bytes, bytes, str, str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ProviderStop("adapter_unreadable")
    content = _bytes(raw.get("content") if "content" in raw else raw.get("artifact"))
    pdf = _bytes(raw.get("pdf"))
    if content is None or pdf is None:
        raise ProviderStop("adapter_unreadable")
    fmt = str(spec.get("format") or "pptx")
    media = str(raw.get("media_type") or _MEDIA[fmt])
    filename = str(raw.get("filename") or f"netie-answers.{fmt}")
    cost_raw = raw.get("cost")
    cost = cost_raw if isinstance(cost_raw, dict) else {}
    return content, pdf, media, filename, _cost(cost)


def _bytes(value: Any) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        import base64

        try:
            return base64.b64decode(value, validate=True)
        except ValueError:
            return None
    return None


def _cost(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_tokens": _opt_int(raw.get("prompt_tokens")),
        "completion_tokens": _opt_int(raw.get("completion_tokens")),
        "usd": _opt_float(raw.get("usd")),
    }


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _opt_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


def _add_cost(run: dict[str, Any], cost: dict[str, Any]) -> None:
    """Sum only when every call reported the figure. A gap stays null."""
    spend = run["spend"]
    gaps: set[str] = run.setdefault("_cost_gaps", set())
    for key in ("prompt_tokens", "completion_tokens", "usd"):
        if key in gaps:
            continue
        incoming = cost.get(key)
        if incoming is None:
            gaps.add(key)
            spend[key] = None
            continue
        spend[key] = (spend.get(key) or 0) + incoming


def _over_usd(run: dict[str, Any]) -> bool:
    ceiling = run["budget"].get("max_usd")
    spent = run["spend"].get("usd")
    if ceiling is None or spent is None:
        return False
    return float(spent) > float(ceiling)


def _style(style: dict[str, Any], fmt: str) -> dict[str, Any]:
    route = str(style.get("route") or "openvault")
    if route not in {"openvault", "cursor"}:
        raise ValueError("route")
    shots = []
    for shot in list(style.get("few_shots") or [])[:4]:
        if not isinstance(shot, dict):
            raise ValueError("few_shot")
        shots.append(
            {
                "title": str(shot.get("title") or "").strip()[:120],
                "text": str(shot.get("text") or "").strip()[:1000],
            }
        )
    brand_raw = style.get("brand")
    brand: dict[str, Any] = brand_raw if isinstance(brand_raw, dict) else {}
    prompt = str(style.get("prompt_text") or "").strip()
    return {
        "format": fmt,
        "skill_id": skill_for(fmt, str(style.get("skill_id") or "").strip() or None),
        "prompt_version": str(style.get("prompt_version") or "draft").strip()[:40] or "draft",
        "prompt_text": (prompt or DEFAULT_PROMPT)[:2000],
        "super_guide": str(style.get("super_guide") or "").strip()[:4000],
        "template_id": str(style.get("template_id") or "").strip()[:80],
        "brand": {
            "name": str(brand.get("name") or "").strip()[:80],
            "primary": str(brand.get("primary") or "").strip()[:16],
            "font": str(brand.get("font") or "").strip()[:40],
            "notes": str(brand.get("notes") or "").strip()[:400],
        },
        "few_shots": shots,
        "route": route,
    }
