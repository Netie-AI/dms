"""Shared generator adapter for the Create harness and the doc/slides writer.

DMS does not pick a model. ``DMS_CREATE_GENERATOR`` selects ``claude_api``
(OpenVault role) or ``cursor_api`` (Cursor Cloud Agents HTTP). The Cursor
key comes from OpenVault and is never returned.

Docs followed:
- https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview
- https://platform.claude.com/docs/en/build-with-claude/skills-guide
- https://www.anthropic.com/research/building-effective-agents
- https://cursor.com/docs/cloud-agent/api/v0
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms_core.freeroute import redact_text
from dms_core.pii import mask_payload

_ROOT = Path(__file__).resolve().parent
_TOOLS_PATH = _ROOT / "create_tools.json"
_PROMPTS = _ROOT / "create_prompts"
_SCORE = _ROOT / "create_score.json"
_SKILLS = _ROOT / "create_skills"
_FORMAT_SKILLS: dict[str, dict[str, list[str]]] = {
    "app": {"anthropic": [], "local": ["app"]},
    "slides": {"anthropic": ["pptx"], "local": ["slides"]},
    "doc": {"anthropic": ["docx", "pdf"], "local": ["doc"]},
}
_CLIENT_TOOLS = frozenset(
    {
        "run_sql",
        "fetch_ontology",
        "chart",
        "render_doc",
        "sign_request",
        "save_artifact",
        "edit_section",
    }
)
_OUTPUT_TOOL = "return_artifact"
_TEMPLATES = frozenset({"plain", "brief", "bold"})
_NUM = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w]|\.\d)")
_MODEL_KEYS = frozenset(
    {"model", "model_id", "model_name", "provider", "served_model", "served_provider", "strict"}
)
Transport = Callable[[dict[str, Any], str], Any]


class GeneratorError(RuntimeError):
    """A provider call failed. ``code`` never contains a key."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class ProviderTurn:
    artifact: dict[str, Any] | None
    tool_calls: list[dict[str, Any]]
    cost: float | None
    tokens: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str | None
    prompt: str
    provider: str
    estimated_cost: float | None


def tool_registry() -> dict[str, dict[str, Any]]:
    loaded = json.loads(_TOOLS_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GeneratorError("tools_unreadable")
    return loaded


def prompt_library(version: str = "1") -> dict[str, Any]:
    path = _PROMPTS / f"v{version}.json"
    if not path.is_file():
        raise GeneratorError("prompt_unknown")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GeneratorError("prompt_unknown")
    return loaded


def selected_provider() -> str:
    raw = os.environ.get("DMS_CREATE_GENERATOR", "claude_api").strip()
    if raw not in {"claude_api", "cursor_api"}:
        raise GeneratorError("generator_unknown")
    return raw


def local_skill(name: str) -> str:
    path = _SKILLS / name / "SKILL.md"
    if not path.is_file():
        raise GeneratorError("skill_missing")
    return path.read_text(encoding="utf-8")


def template_text(name: str) -> str:
    if name not in _TEMPLATES:
        raise GeneratorError("template_unknown")
    return (_SKILLS / "templates" / f"{name}.md").read_text(encoding="utf-8")


def skills_for(kind: str, *, workbook: bool = False) -> dict[str, Any]:
    """Anthropic skill ids plus the local SKILL.md picked for this format."""
    picked = _FORMAT_SKILLS.get(kind)
    if picked is None:
        raise GeneratorError("kind_required")
    anthropic = list(picked["anthropic"])
    if workbook and "xlsx" not in anthropic:
        anthropic.append("xlsx")
    local = [{"name": item, "text": local_skill(item)} for item in picked["local"]]
    return {"anthropic": anthropic, "local": local}


def validate_call(name: str, arguments: Mapping[str, Any]) -> list[str]:
    registry = tool_registry()
    spec = registry.get(name)
    if not isinstance(spec, dict):
        return ["tool_unknown"]
    schema = spec.get("input_schema")
    if not isinstance(schema, dict):
        return ["tool_unknown"]
    return _check(schema, arguments, name)


def client_tools(enabled: Mapping[str, bool] | None) -> list[dict[str, Any]]:
    """Tools the tenant left on, plus the output tool. Schemas stay in JSON."""
    registry = tool_registry()
    allowed = enabled or {}
    out: list[dict[str, Any]] = []
    for name, spec in registry.items():
        if name == _OUTPUT_TOOL:
            out.append(spec)
            continue
        if name not in _CLIENT_TOOLS:
            continue
        if allowed.get(name, True) is False:
            continue
        out.append(spec)
    return out


def render_context(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
) -> str:
    """XML-tagged context. Long data sits above the task, as the guide says."""
    examples = data_context.get("examples") or []
    return "\n".join(
        [
            _tag("schema", data_context.get("schema")),
            _tag("data", data_context.get("data")),
            _tag("choices", {"spec": dict(spec), "choices": data_context.get("choices")}),
            _tag("style", style),
            _tag("examples", examples),
            _tag("skills", data_context.get("skills")),
            _tag("judge", spec.get("judge")),
        ]
    )


def direct_prompt(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    *,
    prompt_version: str = "1",
) -> str:
    """The plain Claude ask: system prompt plus the same XML context. No tools."""
    system, xml = _system_and_xml(spec, data_context, style, prompt_version)
    return system + "\n" + xml


def request_body(
    *,
    provider: str,
    role: str,
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    enabled: Mapping[str, bool] | None,
    prompt_version: str,
) -> tuple[dict[str, Any], str]:
    library = prompt_library(prompt_version)
    system, xml = _system_and_xml(spec, data_context, style, prompt_version)
    if provider == "cursor_api":
        repo = os.environ.get("DMS_CREATE_CURSOR_REPO", "").strip()
        ref = os.environ.get("DMS_CREATE_CURSOR_REF", "main").strip() or "main"
        if not repo:
            raise GeneratorError("cursor_repo_missing")
        body = {
            "prompt": {"text": system + "\n" + xml},
            "source": {"repository": repo, "ref": ref},
            "target": {"autoCreatePr": False},
        }
        _reject_model_keys(body)
        return body, xml
    tools = client_tools(enabled)
    messages: list[dict[str, Any]] = [{"role": "user", "content": xml}]
    prefill = str(library.get("prefill") or "")
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    kind = str(spec.get("kind") or "")
    picked = skills_for(kind, workbook=bool(spec.get("workbook")))
    body = {
        "role": role,
        "system": system,
        "messages": messages,
        "tools": tools,
        "tool_choice": {"type": "auto"},
        "thinking": {"type": "adaptive"},
    }
    if picked["anthropic"]:
        body["container"] = {
            "skills": [
                {"type": "anthropic", "skill_id": item, "version": "latest"}
                for item in picked["anthropic"]
            ]
        }
        body["tools"] = [
            *tools,
            {"type": "code_execution_20250825", "name": "code_execution"},
        ]
    _reject_model_keys(body)
    return body, xml


def complete(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    *,
    api_key: str,
    role: str = "create_generate",
    enabled: Mapping[str, bool] | None = None,
    prompt_version: str = "1",
    transport: Transport | None = None,
    cursor_transport: Transport | None = None,
) -> ProviderTurn:
    """One provider call. The model plans. DMS does not."""
    provider = selected_provider()
    body, prompt = request_body(
        provider=provider,
        role=role,
        spec=spec,
        data_context=data_context,
        style=style,
        enabled=enabled,
        prompt_version=prompt_version,
    )
    if provider == "cursor_api":
        key = _cursor_key(api_key, transport or default_transport)
        if key and key in prompt:
            raise GeneratorError("cursor_key_leaked")
        raw = (cursor_transport or default_cursor)(body, key)
    else:
        raw = (transport or default_transport)(body, api_key)
    artifact, calls, usage = _parse(raw)
    cost = _num(usage.get("cost"))
    tokens = _int(usage.get("total_tokens"))
    estimate = None
    if isinstance(artifact, dict):
        estimate = _num(artifact.get("estimated_cost"))
    model = usage.get("model")
    return ProviderTurn(
        artifact=artifact,
        tool_calls=calls,
        cost=cost,
        tokens=tokens,
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
        model=model if isinstance(model, str) else None,
        prompt=prompt,
        provider=provider,
        estimated_cost=estimate,
    )


def generate(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    *,
    api_key: str,
    role: str = "create_generate",
    enabled: Mapping[str, bool] | None = None,
    prompt_version: str = "1",
    transport: Transport | None = None,
    cursor_transport: Transport | None = None,
) -> ProviderTurn:
    """``generate(spec, data_context, style)`` plus the caller key. One turn."""
    turn = complete(
        spec,
        data_context,
        style,
        api_key=api_key,
        role=role,
        enabled=enabled,
        prompt_version=prompt_version,
        transport=transport,
        cursor_transport=cursor_transport,
    )
    if turn.artifact is None and not turn.tool_calls:
        raise GeneratorError("model_unreadable")
    return turn


def splice_section(
    artifact: Mapping[str, Any], section_id: str, replacement: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply one section. Every other section stays byte-for-byte."""
    new = json.loads(json.dumps(artifact))
    parts = new.get("parts")
    if not isinstance(parts, list):
        raise GeneratorError("section_missing")
    for index, part in enumerate(parts):
        if isinstance(part, dict) and part.get("id") == section_id:
            merged = dict(part)
            merged.update(replacement)
            merged["id"] = section_id
            parts[index] = merged
            new["parts"] = parts
            return new
    raise GeneratorError("section_missing")


def judge_artifact(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    brand: Mapping[str, str],
    allowed_numbers: set[str],
) -> list[str]:
    """Schema, brand, traced numbers, secrets, and PII. No phrase list."""
    issues = validate_call(_OUTPUT_TOOL, artifact)
    if str(artifact.get("kind") or "") != kind:
        issues.append("kind_mismatch")
    style_raw = artifact.get("style")
    style: Mapping[str, Any] = style_raw if isinstance(style_raw, dict) else {}
    for key in ("colours", "fonts", "logo", "tone"):
        wanted = brand.get(key) or ""
        if wanted and str(style.get(key) or "") != wanted:
            issues.append(f"brand_{key}")
    prose = _prose(artifact)
    if _unsupported(prose, allowed_numbers):
        issues.append("unsupported_claim")
    if redact_text(prose) != prose:
        issues.append("secret_present")
    masked = mask_payload(text=prose).get("text") or ""
    if "DMSMASK_" in str(masked):
        issues.append("pii_present")
    return issues


def direct_reading(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Same words with the SQL and the row citations removed."""
    copy = json.loads(json.dumps(artifact))
    for part in copy.get("parts") or []:
        if isinstance(part, dict):
            part.pop("sql", None)
            part.pop("rows", None)
            part.pop("citations", None)
    return copy


def score_artifact(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    brand: Mapping[str, str],
    allowed_numbers: set[str],
) -> dict[str, Any]:
    """Points from ``create_score.json``. Unknown checks earn nothing."""
    loaded = json.loads(_SCORE.read_text(encoding="utf-8"))
    checks = loaded.get("checks") if isinstance(loaded, dict) else None
    prose = _prose(artifact)
    earned = 0
    detail: list[dict[str, Any]] = []
    for item in checks or []:
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("id") or "")
        points = item.get("points")
        if isinstance(points, bool) or not isinstance(points, int):
            continue
        ok = _score_check(
            check_id,
            artifact,
            kind=kind,
            brand=brand,
            allowed_numbers=allowed_numbers,
            prose=prose,
        )
        gained = points if ok else 0
        earned += gained
        detail.append({"id": check_id, "ok": ok, "points": gained})
    return {"points": earned, "checks": detail}


def holds_direct_bar(create_points: int, direct_points: int) -> bool:
    return create_points >= direct_points


def allowed_from(rows: Sequence[Mapping[str, Any]], overview: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        for value in row.values():
            found.update(_number_tokens(value))
    for table in overview.get("data") or []:
        if not isinstance(table, dict):
            continue
        for stat in table.get("stats") or []:
            if isinstance(stat, dict):
                for value in stat.values():
                    found.update(_number_tokens(value))
        for group in table.get("groups") or []:
            if not isinstance(group, dict):
                continue
            for item in group.get("values") or []:
                if isinstance(item, dict):
                    for value in item.values():
                        found.update(_number_tokens(value))
    return found


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def default_transport(body: dict[str, Any], api_key: str) -> Any:
    """POST to OpenVault. The error code never carries the key or the body."""
    import httpx

    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    if not base:
        raise GeneratorError("openvault_url_missing")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = httpx.post(
            base + "/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise GeneratorError("ov_unauthorized") from None
        if status == 402:
            raise GeneratorError("budget_exceeded") from None
        raise GeneratorError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise GeneratorError("ov_unreachable") from None
    try:
        return response.json()
    except ValueError:
        raise GeneratorError("model_unreadable") from None


def default_cursor(body: dict[str, Any], api_key: str) -> Any:
    """POST /v0/agents. Basic auth, no model field. The key is not logged."""
    import httpx

    base = os.environ.get("CURSOR_AGENTS_URL", "https://api.cursor.com").strip().rstrip("/")
    try:
        response = httpx.post(
            base + "/v0/agents",
            auth=(api_key, ""),
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise GeneratorError("cursor_unauthorized") from None
        raise GeneratorError(f"cursor_http_{status}") from None
    except httpx.HTTPError:
        raise GeneratorError("cursor_unreachable") from None
    try:
        return response.json()
    except ValueError:
        raise GeneratorError("model_unreadable") from None


def _cursor_key(ov_key: str, transport: Transport) -> str:
    body = {
        "role": "create_cursor_key",
        "messages": [{"role": "user", "content": "{}"}],
        "schema": "CursorKey",
    }
    raw = transport(body, ov_key)
    content, _calls, _usage = _parse(raw)
    key = content.get("cursor_key") if isinstance(content, dict) else None
    if not isinstance(key, str) or len(key) < 8:
        raise GeneratorError("cursor_key_missing")
    return key


def _parse(raw: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    usage: dict[str, Any] = {}
    content = raw
    if isinstance(raw, dict) and "content" in raw:
        content = raw.get("content")
        found = raw.get("usage")
        if isinstance(found, dict):
            usage = found
    if isinstance(raw, dict) and isinstance(raw.get("choices"), list) and raw["choices"]:
        message = raw["choices"][0].get("message") or {}
        content = message.get("content")
        found = raw.get("usage")
        if isinstance(found, dict):
            usage = found
    if isinstance(content, str):
        try:
            content = json.loads(_strip_fence(content))
        except json.JSONDecodeError:
            return None, [], usage
    if isinstance(content, dict) and "cursor_key" in content and "kind" not in content:
        return content, [], usage
    if isinstance(content, dict) and content.get("kind"):
        return _public(content), [], usage
    calls: list[dict[str, Any]] = []
    artifact = None
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name") == _OUTPUT_TOOL:
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                artifact = _public(tool_input)
        elif block.get("type") == "tool_use" and block.get("name") == "edit_section":
            calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "name": "edit_section",
                    "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                }
            )
        elif block.get("type") == "tool_use":
            calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                }
            )
    return artifact, calls, usage


def _check(schema: Mapping[str, Any], value: Any, path: str) -> list[str]:
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path}:type"]
        issues: list[str] = []
        for key in schema.get("required") or []:
            if key not in value:
                issues.append(f"missing:{key}")
        props = schema.get("properties") or {}
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in value and isinstance(sub, dict):
                    issues.extend(_check(sub, value[key], key))
        return issues
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}:type"]
        item = schema.get("items")
        issues = []
        if isinstance(item, dict):
            for index, entry in enumerate(value):
                issues.extend(_check(item, entry, f"{path}[{index}]"))
        return issues
    if kind == "string":
        if not isinstance(value, str):
            return [f"{path}:type"]
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            return [f"{path}:enum"]
        return []
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path}:type"]
        return []
    return []


def _system_and_xml(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    prompt_version: str,
) -> tuple[str, str]:
    library = prompt_library(prompt_version)
    system = str(library.get("system") or "")
    xml = redact_text(render_context(spec, data_context, style))
    return system, xml


def _score_check(
    check_id: str,
    artifact: Mapping[str, Any],
    *,
    kind: str,
    brand: Mapping[str, str],
    allowed_numbers: set[str],
    prose: str,
) -> bool:
    if check_id == "schema":
        return not validate_call(_OUTPUT_TOOL, artifact)
    if check_id == "kind":
        return str(artifact.get("kind") or "") == kind
    if check_id == "parts":
        parts = artifact.get("parts")
        return isinstance(parts, list) and any(
            isinstance(part, dict) and part.get("title") for part in parts
        )
    if check_id == "brand":
        style_raw = artifact.get("style")
        style: Mapping[str, Any] = style_raw if isinstance(style_raw, dict) else {}
        for key in ("colours", "fonts", "logo", "tone"):
            wanted = brand.get(key) or ""
            if wanted and str(style.get(key) or "") != wanted:
                return False
        return True
    if check_id == "traced":
        return not _unsupported(prose, allowed_numbers)
    if check_id == "clean":
        if redact_text(prose) != prose:
            return False
        masked = mask_payload(text=prose).get("text") or ""
        return "DMSMASK_" not in str(masked)
    return False


def _tag(name: str, value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    escaped = text.replace("&", "&amp;").replace("<", "&lt;")
    return f"<{name}>\n{escaped}\n</{name}>"


def _prose(artifact: Mapping[str, Any]) -> str:
    chunks = [str(artifact.get("title") or "")]
    for part in artifact.get("parts") or []:
        if isinstance(part, dict):
            chunks.append(str(part.get("title") or ""))
            chunks.append(str(part.get("body") or ""))
    return "\n".join(chunks)


def _unsupported(prose: str, allowed: set[str]) -> bool:
    for token in _NUM.findall(prose):
        if token not in allowed and _bare(token) not in allowed:
            return True
    return False


def _number_tokens(value: Any) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        return {text, str(value)}
    return set(_NUM.findall(str(value)))


def _bare(token: str) -> str:
    if "." in token:
        head, tail = token.split(".", 1)
        if set(tail) == {"0"}:
            return head
    return token


def _public(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in _MODEL_KEYS}


def _reject_model_keys(body: Mapping[str, Any]) -> None:
    if _MODEL_KEYS & set(body):
        raise GeneratorError("model_key_forbidden")


def _strip_fence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    return raw.strip()


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
