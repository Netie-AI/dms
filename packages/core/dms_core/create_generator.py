"""Shared generator adapter for the Create harness and the doc/slides writer.

DMS does not pick a model. ``DMS_CREATE_GENERATOR`` selects ``claude_api``
(OpenVault, role from ``DMS_CREATE_OV_ROLE``, default ``reason``) or
``cursor_api`` (Cursor Cloud Agents HTTP). The Cursor key is
``CURSOR_API_KEY`` or the secrets port. It is never returned.

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
import time
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
# Prove-box OpenVault allowlist. A name outside this set is not sent.
_OV_ROLES = frozenset({"sql", "tool", "reason", "summarize", "grain", "ontology"})
_secret_store: Any = None
# Thousands groups, a leading currency mark, a K/M/B suffix, and a trailing percent
# are one number. The suffix is optional so 1,606,773 still matches as one token.
_NUM = re.compile(
    r"(?<![\w.])"
    r"(?:[$£€¥]|RM)?"
    r"-?"
    r"(?P<body>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suffix>[KkMmBb])?"
    r"(?P<pct>%)?"
    r"(?![\w%]|\.\d)"
)
_PERCENT_WORD = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s+percent\b", re.I)
_SCALES = {"k": 1e3, "m": 1e6, "b": 1e9}
_SHARE_TOLERANCE = 1.0
_SCHEMA_EXAMPLE = {
    "kind": "app",
    "title": "Short title",
    "parts": [
        {
            "id": "part-1",
            "title": "Section title",
            "body": "One sentence.",
            "sql": "SELECT column FROM table",
            "charts": [
                {
                    "label": "Comparison",
                    "sql": "SELECT column FROM table",
                    "type": "bar",
                    "x": "column",
                    "series": ["value"],
                    "labels": True,
                }
            ],
        }
    ],
}
_MODEL_NOTE = "Served model is not the requested provider."
_OV_TIMEOUT_S = 240.0
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
    model_warning: str | None = None
    seconds: float | None = None
    cursor_target: dict[str, Any] | None = None


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


def ov_role() -> str:
    """OpenVault role. ``DMS_CREATE_OV_ROLE`` overrides the default ``reason``."""
    raw = os.environ.get("DMS_CREATE_OV_ROLE", "reason").strip() or "reason"
    if raw not in _OV_ROLES:
        raise GeneratorError("ov_role_unknown")
    return raw


def bind_secret_store(store: Any) -> None:
    """Install the secrets port used when ``CURSOR_API_KEY`` is unset."""
    global _secret_store
    _secret_store = store


def ov_tools_enabled() -> bool:
    """Anthropic tools passthrough. Off unless the value is the digit 1."""
    return os.environ.get("DMS_CREATE_OV_TOOLS", "0").strip() == "1"


def request_max_tokens() -> int:
    """Tokens sent on the OpenVault body. Default 8000. This does not stop a run."""
    raw = os.environ.get("DMS_CREATE_MAX_TOKENS", "8000").strip() or "8000"
    try:
        value = int(raw)
    except ValueError:
        return 8000
    if value < 1:
        return 8000
    return value


def transport_timeout() -> float:
    """OpenVault HTTP timeout. Live calls ran 18 to 24 seconds under the old 30."""
    return _OV_TIMEOUT_S


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
            _tag("data", _shown_numbers(data_context.get("data"))),
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
    """The plain Claude ask: JSON schema plus the same XML context. No tools."""
    xml = _system_and_xml(spec, data_context, style, prompt_version)[1]
    system, cleaned = _plain_pair(xml, spec)
    return system + "\n" + cleaned


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
        plain, cleaned = _plain_pair(xml, spec)
        body = {
            "prompt": {"text": plain + "\n" + cleaned},
            "source": {"repository": repo, "ref": ref},
            "target": {"autoCreatePr": False},
        }
        _reject_model_keys(body)
        return body, cleaned
    # Caller ``role`` is the task name. The wire role is the OpenVault allowlist.
    role = ov_role()
    cap = request_max_tokens()
    prefill = str(library.get("prefill") or "")
    if ov_tools_enabled():
        tools = client_tools(enabled)
        messages = [{"role": "user", "content": xml}]
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
            "max_tokens": cap,
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
    # Plain contract. OpenVault rejects ``tools`` and ignores a top-level system.
    # The library string still says call return_artifact. This path does not send it.
    plain, cleaned = _plain_pair(xml, spec)
    messages = [
        {"role": "system", "content": plain},
        {"role": "user", "content": cleaned},
    ]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    body = {"role": role, "messages": messages, "max_tokens": cap}
    _reject_model_keys(body)
    return body, cleaned


def complete(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    *,
    api_key: str,
    role: str = "generate",
    enabled: Mapping[str, bool] | None = None,
    prompt_version: str = "1",
    transport: Transport | None = None,
    cursor_transport: Transport | None = None,
) -> ProviderTurn:
    """One provider call. The model plans. DMS does not."""
    provider = selected_provider()
    cursor_key = _cursor_key() if provider == "cursor_api" else ""
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
        if cursor_key in prompt:
            raise GeneratorError("cursor_key_leaked")
        raw = (cursor_transport or default_cursor)(body, cursor_key)
    else:
        raw = (transport or default_transport)(body, api_key)
    artifact, calls, usage = _parse(raw)
    cost = _cost(raw, usage)
    tokens = _int(usage.get("total_tokens"))
    estimate = None
    if isinstance(artifact, dict):
        estimate = _num(artifact.get("estimated_cost"))
    served = _served_name(raw, usage)
    model = served
    if model is None:
        found = usage.get("model")
        model = found.strip() if isinstance(found, str) and found.strip() else None
    return ProviderTurn(
        artifact=artifact,
        tool_calls=calls,
        cost=cost,
        tokens=tokens,
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
        model=model,
        prompt=prompt,
        provider=provider,
        estimated_cost=estimate,
        model_warning=_provider_mismatch(provider, served),
        cursor_target=_cursor_target(raw) if provider == "cursor_api" else None,
    )


def generate(
    spec: Mapping[str, Any],
    data_context: Mapping[str, Any],
    style: Mapping[str, Any],
    *,
    api_key: str,
    role: str = "generate",
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
    ratios = _ratio_points(artifact)
    diffs = _artifact_diffs(artifact)
    if _unsupported(prose, allowed_numbers, ratios, diffs) or _chart_untraced(artifact):
        issues.append("unsupported_claim")
    if _share_mismatch(artifact):
        issues.append("share_mismatch")
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
            timeout=transport_timeout(),
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


_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CURSOR_DONE = frozenset({"FINISHED", "FAILED", "ERROR", "CANCELLED", "EXPIRED"})


def default_cursor(body: dict[str, Any], api_key: str) -> Any:
    """Launch one agent, poll until it finishes, then return that result.

    A second call is a second launch. Repair must not call this again.
    """
    import httpx

    base = os.environ.get("CURSOR_AGENTS_URL", "https://api.cursor.com").strip().rstrip("/")
    timeout = transport_timeout()
    auth = (api_key, "")
    payload = _cursor_json(httpx, "post", base + "/v0/agents", auth, timeout, json=body)
    agent_id = payload.get("id")
    if not isinstance(agent_id, str) or _AGENT_ID.fullmatch(agent_id) is None:
        return payload
    deadline = time.monotonic() + timeout
    status = ""
    while True:
        payload = _cursor_json(httpx, "get", base + "/v0/agents/" + agent_id, auth, timeout)
        status = str(payload.get("status") or "").upper()
        if status in _CURSOR_DONE:
            break
        if not status or time.monotonic() >= deadline:
            raise GeneratorError("cursor_timeout")
        time.sleep(1.0)
    if status != "FINISHED":
        raise GeneratorError("cursor_failed")
    return _collect_cursor(httpx, base, auth, payload, timeout)


def _cursor_json(
    httpx: Any,
    method: str,
    url: str,
    auth: tuple[str, str],
    timeout: float,
    **kwargs: Any,
) -> dict[str, Any]:
    """One Cursor HTTP call. The error code never carries the key or the body."""
    try:
        response = getattr(httpx, method)(url, auth=auth, timeout=timeout, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise GeneratorError("cursor_unauthorized") from None
        raise GeneratorError(f"cursor_http_{status}") from None
    except httpx.HTTPError:
        raise GeneratorError("cursor_unreachable") from None
    except ValueError:
        raise GeneratorError("model_unreadable") from None
    if not isinstance(payload, dict):
        raise GeneratorError("model_unreadable")
    return payload


def _collect_cursor(
    httpx: Any,
    base: str,
    auth: tuple[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Prefer the artifact in the summary. Otherwise read the finished transcript."""
    summary = payload.get("summary") if isinstance(payload.get("summary"), str) else ""
    found = _as_artifact(summary)
    text = ""
    if found is None and isinstance(payload.get("id"), str):
        try:
            convo = _cursor_json(
                httpx,
                "get",
                base + "/v0/agents/" + payload["id"] + "/conversation",
                auth,
                timeout,
            )
        except GeneratorError:
            convo = {}
        text = _last_assistant(convo)
        found = _as_artifact(text)
    out = dict(payload)
    if found is not None:
        out["content"] = found
    elif text:
        out["content"] = text
    return out


def _last_assistant(payload: Mapping[str, Any]) -> str:
    messages = payload.get("messages") or payload.get("conversation") or []
    if not isinstance(messages, list):
        return ""
    texts: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("role") or "")
        if kind not in {"assistant_message", "assistant"}:
            continue
        text = item.get("text") if isinstance(item.get("text"), str) else item.get("content")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    return texts[-1] if texts else ""


def _cursor_target(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    found = raw.get("target")
    target: dict[str, Any] = found if isinstance(found, dict) else {}
    agent_id = raw.get("id") if isinstance(raw.get("id"), str) else None
    branch = target.get("branchName") or target.get("branch")
    pr_url = target.get("prUrl") or target.get("pr_url")
    status = raw.get("status") if isinstance(raw.get("status"), str) else None
    if not any((agent_id, branch, pr_url, status)):
        return None
    return {"id": agent_id, "status": status, "branch": branch, "pr_url": pr_url}


def _cursor_key() -> str:
    """Env first, then the secrets port. The value is not logged."""
    raw = os.environ.get("CURSOR_API_KEY", "").strip()
    if not raw and _secret_store is not None:
        try:
            found = _secret_store.get("CURSOR_API_KEY")
        except Exception:
            raise GeneratorError("cursor_key_missing") from None
        raw = found.strip() if isinstance(found, str) else ""
    if len(raw) < 8:
        raise GeneratorError("cursor_key_missing")
    return raw


def _unwrap_artifact(content: dict[str, Any]) -> dict[str, Any]:
    """Accept `{action, artifact}` and a summary string. Leave any other object."""
    for key in ("artifact", "result", "output", "summary"):
        found = _as_artifact(content.get(key))
        if found is not None:
            return found
    return content


def _as_artifact(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = _json_in(value)
    if isinstance(value, dict) and ("kind" in value or "parts" in value):
        return value
    return None


def _json_in(text: str) -> Any:
    stripped = _strip_fence(text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return None
        try:
            found, offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(found, dict) and ("kind" in found or "parts" in found):
            return found
        index = start + max(offset, 1)


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
    if isinstance(content, dict) and content.get("type") == "tool_use":
        content = [content]
    elif isinstance(content, dict) and "cursor_key" in content and "kind" not in content:
        return content, [], usage
    elif isinstance(content, dict):
        # A wrapped `{artifact: {...}}` is the artifact. A shape miss stays a dict.
        return _public(_unwrap_artifact(content)), [], usage
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
        cap = schema.get("maxItems")
        if isinstance(cap, int) and not isinstance(cap, bool) and len(value) > cap:
            issues.append(f"{path}:maxItems")
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


def _plain_pair(xml: str, spec: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """Schema plus the context. Tool names stay on the tools-flag path only."""
    return _plain_system(spec), _drop_tool_words(xml)


def _plain_system(spec: Mapping[str, Any] | None = None) -> str:
    deck = spec.get("deck") if isinstance(spec, Mapping) else None
    figures = (
        "Every cited figure must be copied from a result row "
        "or an aggregate already in the data.\n"
        "Write every number with thousands separators and no trailing .0.\n"
    )
    if isinstance(deck, dict) and deck.get("step") == "outline":
        return (
            "Reply with one JSON object and no other text.\n"
            'The object is an outline: {"slides":[{"id":"s1","title":"Short title"}]}.\n'
            "No figures. No parts.\n"
        )
    schema = tool_registry()[_OUTPUT_TOOL]["input_schema"]
    body = (
        "Reply with one JSON object and no other text.\n"
        "The object is the artifact itself. Do not wrap it.\n"
        + figures
        + "JSON schema:\n"
        + json.dumps(schema, indent=2)
        + "\nExample:\n"
        + json.dumps(_SCHEMA_EXAMPLE, indent=2)
    )
    if isinstance(deck, dict) and deck.get("step") == "slides":
        body += "\nOnly the slides named in the task. One part each.\n"
    return body


def _drop_tool_words(text: str) -> str:
    text = re.sub(r"call return_artifact", "reply with the JSON object", text, flags=re.I)
    text = re.sub(r"return_artifact", "the JSON object", text, flags=re.I)
    text = re.sub(r"use run_sql", "use the SQL already in the context", text, flags=re.I)
    text = re.sub(r"run_sql", "the SQL results", text, flags=re.I)
    return text


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
        return not _unsupported(
            prose, allowed_numbers, _ratio_points(artifact), _artifact_diffs(artifact)
        )
    if check_id == "clean":
        if redact_text(prose) != prose:
            return False
        masked = mask_payload(text=prose).get("text") or ""
        return "DMSMASK_" not in str(masked)
    return False


def _shown_numbers(value: Any) -> Any:
    """Figures in the prompt use thousands separators and no trailing .0."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        if abs(value) >= 1000 and not 1900 <= value <= 2100:
            return f"{value:,}"
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return _shown_numbers(int(value))
        return f"{value:,.2f}"
    if isinstance(value, list):
        return [_shown_numbers(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _shown_numbers(item) for key, item in value.items()}
    return value


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
            for chart in part.get("charts") or []:
                if isinstance(chart, dict):
                    chunks.append(str(chart.get("label") or ""))
    return "\n".join(chunks)


def _unsupported(
    prose: str,
    allowed: set[str],
    ratios: Sequence[float] | None = None,
    derived: Sequence[float] | None = None,
) -> bool:
    near = list(ratios or [])
    pool = _allowed_floats(allowed)
    pool.extend(derived or [])
    for match in _NUM.finditer(prose):
        if _is_list_marker(prose, match):
            continue
        forms = _match_forms(match.group("body"), bool(match.group("pct")), match.group("suffix"))
        if not forms.isdisjoint(allowed):
            continue
        if match.group("suffix") and _suffix_near(match, pool):
            continue
        if match.group("pct") and _percent_near(match, near):
            continue
        if not match.group("pct") and not match.group("suffix") and _plain_near(match, pool):
            continue
        return True
    return False


def _is_list_marker(text: str, match: re.Match[str]) -> bool:
    """A leading `2.` or `2)` is a list marker, not a figure."""
    if match.group("pct") or match.group("suffix"):
        return False
    body = match.group("body")
    if "," in body or "." in body:
        return False
    raw = match.group(0)
    if raw[:1] in "$£€¥" or raw.startswith(("RM", "-")):
        return False
    end = match.end()
    if end >= len(text) or text[end] not in ".)":
        return False
    if text[end] == "." and end + 1 < len(text) and text[end + 1].isdigit():
        return False
    line = text.rfind("\n", 0, match.start()) + 1
    return text[line : match.start()].strip() == ""


def _plain_near(match: re.Match[str], values: Sequence[float]) -> bool:
    """16,392 matches a computed difference that rounds to that precision."""
    plain = match.group("body").replace(",", "")
    decimals = len(plain.split(".", 1)[1]) if "." in plain else 0
    try:
        shown = round(float(plain), decimals)
    except ValueError:
        return False
    return any(round(candidate, decimals) == shown for candidate in values)


def _number_tokens(value: Any) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        return {text, str(value)}
    return set().union(*_trace_forms(str(value))) if _NUM.search(str(value)) else set()


def _trace_forms(text: str) -> list[set[str]]:
    found: list[set[str]] = []
    for match in _NUM.finditer(text):
        found.append(
            _match_forms(match.group("body"), bool(match.group("pct")), match.group("suffix"))
        )
    return found


def _match_forms(body: str, percent: bool, suffix: str | None = None) -> set[str]:
    plain = body.replace(",", "")
    forms = {plain, _bare(plain)}
    scaled = _scaled(plain, suffix)
    if scaled is None:
        return forms
    shown = _num_text(scaled)
    forms.update({shown, _bare(shown)})
    if not percent:
        return forms
    frac = _num_text(scaled / 100.0)
    forms.update({frac, _bare(frac)})
    return forms


def _scaled(plain: str, suffix: str | None) -> float | None:
    try:
        number = float(plain)
    except ValueError:
        return None
    if suffix:
        number *= _SCALES[suffix.casefold()]
    return number


def _allowed_floats(allowed: set[str]) -> list[float]:
    found: list[float] = []
    for token in allowed:
        try:
            found.append(float(token))
        except ValueError:
            continue
    return found


def _suffix_near(match: re.Match[str], values: Sequence[float]) -> bool:
    """$9.9M matches a value that rounds to 9.9 at the printed precision."""
    suffix = match.group("suffix")
    if not suffix:
        return False
    plain = match.group("body").replace(",", "")
    decimals = len(plain.split(".", 1)[1]) if "." in plain else 0
    try:
        shown = round(float(plain), decimals)
    except ValueError:
        return False
    scale = _SCALES[suffix.casefold()]
    return any(round(candidate / scale, decimals) == shown for candidate in values)


def _percent_near(match: re.Match[str], points: Sequence[float]) -> bool:
    """77% is supported when a multi-row ratio rounds to it within one point."""
    try:
        shown = float(match.group("body").replace(",", ""))
    except ValueError:
        return False
    return any(abs(shown - point) <= _SHARE_TOLERANCE for point in points)


def _num_text(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return format(value, "f").rstrip("0").rstrip(".")


def _chart_untraced(artifact: Mapping[str, Any]) -> bool:
    """A number in a chart label must appear in that chart's own rows."""
    for part in artifact.get("parts") or []:
        if not isinstance(part, dict):
            continue
        for chart in part.get("charts") or []:
            if not isinstance(chart, dict):
                continue
            allowed: set[str] = set()
            for row in chart.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                for value in row.values():
                    allowed.update(_number_tokens(value))
            ratios = [share * 100.0 for share in _shares_of(chart.get("rows"))]
            diffs = _column_diffs(chart.get("rows"))
            if _unsupported(str(chart.get("label") or ""), allowed, ratios, diffs):
                return True
    return False


def _share_mismatch(artifact: Mapping[str, Any]) -> bool:
    """A percent is checked against the chart or SQL that sentence names.

    A one-row result is not a share. A sentence that names no chart is not
    checked against every chart in the artifact.
    """
    for part in artifact.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if _part_share_mismatch(part):
            return True
    return False


def _part_share_mismatch(part: Mapping[str, Any]) -> bool:
    texts = [str(part.get("title") or ""), str(part.get("body") or "")]
    for chart in part.get("charts") or []:
        if isinstance(chart, dict):
            texts.append(str(chart.get("label") or ""))
    for sentence in _sentences("\n".join(texts)):
        claims = _claimed_percents(sentence)
        if not claims:
            continue
        shares = _shares_for_sentence(sentence, part)
        if not shares:
            continue
        for claim in claims:
            if not any(abs(claim - (share * 100.0)) <= _SHARE_TOLERANCE for share in shares):
                return True
    return False


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _shares_for_sentence(sentence: str, part: Mapping[str, Any]) -> list[float]:
    folded = sentence.casefold()
    charts = [
        chart
        for chart in part.get("charts") or []
        if isinstance(chart, dict) and _shares_of(chart.get("rows"))
    ]
    named = [chart for chart in charts if _refers(folded, chart)]
    if named:
        shares: list[float] = []
        for chart in named:
            shares.extend(_shares_of(chart.get("rows")))
        return shares
    if len(charts) == 1:
        return _shares_of(charts[0].get("rows"))
    if charts:
        return []
    return _shares_of(part.get("rows"))


def _refers(sentence: str, chart: Mapping[str, Any]) -> bool:
    label = str(chart.get("label") or "").strip()
    if len(label) >= 3 and label.casefold() in sentence:
        return True
    for row in chart.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if isinstance(value, str) and len(value.strip()) >= 3 and value.casefold() in sentence:
                return True
    return False


def _artifact_diffs(artifact: Mapping[str, Any]) -> list[float]:
    found: list[float] = []
    for part in artifact.get("parts") or []:
        if not isinstance(part, dict):
            continue
        found.extend(_column_diffs(part.get("rows")))
        for chart in part.get("charts") or []:
            if isinstance(chart, dict):
                found.extend(_column_diffs(chart.get("rows")))
    return found


def _column_diffs(rows: Any) -> list[float]:
    """Differences computed from one result. A year column is not a measure."""
    if not isinstance(rows, list):
        return []
    columns: dict[str, list[float]] = {}
    diffs: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nums: list[float] = []
        for key, value in row.items():
            number = _as_float(value)
            if number is None or _is_year(number):
                continue
            columns.setdefault(str(key), []).append(number)
            nums.append(number)
        for index, left in enumerate(nums):
            for right in nums[index + 1 :]:
                diffs.append(abs(left - right))
    for series in columns.values():
        if len(series) < 2:
            continue
        # ponytail: full pairs up to 80 values. Beyond that, only the spread.
        ordered = series if len(series) <= 80 else [min(series), max(series)]
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                diffs.append(abs(left - right))
    return diffs


def _is_year(number: float) -> bool:
    return 1900 <= number <= 2100 and float(number).is_integer()


def _ratio_points(artifact: Mapping[str, Any]) -> list[float]:
    points: list[float] = []
    for part in artifact.get("parts") or []:
        if not isinstance(part, dict):
            continue
        points.extend(share * 100.0 for share in _shares_of(part.get("rows")))
        for chart in part.get("charts") or []:
            if isinstance(chart, dict):
                points.extend(share * 100.0 for share in _shares_of(chart.get("rows")))
    return points


def _shares_of(rows: Any) -> list[float]:
    if not isinstance(rows, list) or len(rows) < 2:
        return []
    totals: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            number = _as_float(value)
            if number is None or number < 0:
                continue
            totals[str(key)] = totals.get(str(key), 0.0) + number
    if not totals:
        return []
    key = _measure_key(rows, totals)
    if key is None:
        return []
    whole = totals[key]
    if whole <= 0:
        return []
    parts: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        number = _as_float(row.get(key))
        if number is None or number < 0:
            continue
        parts.append(number / whole)
    return parts


def _measure_key(rows: list[Any], totals: dict[str, float]) -> str | None:
    """The numeric column that is the measure. A year column is not a share."""
    best: tuple[float, str] | None = None
    for key, total in totals.items():
        numbers: list[float] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            number = _as_float(row.get(key))
            if number is not None and number >= 0:
                numbers.append(number)
        if not numbers:
            continue
        if all(1900 <= number <= 2100 and number.is_integer() for number in numbers):
            continue
        if best is None or total > best[0]:
            best = (total, key)
    return None if best is None else best[1]


def _claimed_percents(prose: str) -> list[float]:
    claimed: list[float] = []
    for match in _NUM.finditer(prose):
        if not match.group("pct"):
            continue
        try:
            claimed.append(float(match.group("body").replace(",", "")))
        except ValueError:
            continue
    for match in _PERCENT_WORD.finditer(prose):
        try:
            claimed.append(float(match.group(1).replace(",", "")))
        except ValueError:
            continue
    return claimed


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = next(_NUM.finditer(value), None)
        if match is None:
            return None
        scaled = _scaled(match.group("body").replace(",", ""), match.group("suffix"))
        return scaled
    return None


def _served_name(raw: Any, usage: Mapping[str, Any]) -> str | None:
    sources: list[Mapping[str, Any]] = []
    if isinstance(raw, Mapping):
        sources.append(raw)
    sources.append(usage)
    for source in sources:
        value = source.get("served_model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _provider_mismatch(provider: str, served: str | None) -> str | None:
    """Warn when OpenVault served a different family. Absent means no warning."""
    if not served:
        return None
    name = served.casefold()
    if provider == "claude_api":
        return None if "claude" in name else _MODEL_NOTE
    if provider == "cursor_api":
        return None if "cursor" in name else _MODEL_NOTE
    return None


def _cost(raw: Any, usage: Mapping[str, Any]) -> float | None:
    """OpenVault sends ``est_cost_usd`` on the top level. ``usage.cost`` still counts."""
    if isinstance(raw, dict):
        top = _num(raw.get("est_cost_usd"))
        if top is not None:
            return top
    return _num(usage.get("cost"))


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
