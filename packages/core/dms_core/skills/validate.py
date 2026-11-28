"""SKILL.md frontmatter, secret scan, and dangerous-script scan.

The provider runs a skill. DMS only accepts a bundle that parses and scans
clean. A hit is a code plus a path. The matched secret is not returned.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from dms_core.skills.errors import SkillError

_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SCRIPT = frozenset({".py", ".js", ".mjs", ".sh", ".bash", ".ps1", ".rb", ".php", ".bat", ".cmd"})
_SECRET = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "provider_token",
        re.compile(
            r"(?:sk-ant-|sk-proj-|sk-live-|ghp_|github_pat_|xox[baprs]-|AIza[0-9A-Za-z\-_]{20,})"
        ),
    ),
    (
        "assigned_secret",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}"
        ),
    ),
)
_DANGER = (
    ("os_system", re.compile(r"\bos\.system\b")),
    ("subprocess", re.compile(r"\bsubprocess\b")),
    ("eval", re.compile(r"\beval\s*\(")),
    ("exec", re.compile(r"\bexec\s*\(")),
    ("child_process", re.compile(r"child_process")),
    ("pipe_shell", re.compile(r"\|\s*(?:ba)?sh\b")),
    ("rm_rf", re.compile(r"\brm\s+-rf\b")),
    ("invoke_expression", re.compile(r"Invoke-Expression")),
)
_LICENCE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING")
_INSTRUCTION_CAP = 8000


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """A small YAML subset: ``key: value`` and ``key: >`` / ``key: |`` blocks."""
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("frontmatter_missing")
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = index
            break
    if end is None:
        raise SkillError("frontmatter_missing")
    meta = _mapping("\n".join(lines[1:end]))
    body = "\n".join(lines[end + 1 :])
    return meta, body


def _mapping(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[0] in " \t" or ":" not in line:
            raise SkillError("frontmatter_invalid")
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            raise SkillError("frontmatter_invalid")
        if rest in {">", "|", ">-", "|-"}:
            index += 1
            block: list[str] = []
            while index < len(lines) and (
                not lines[index].strip() or lines[index][0] in " \t"
            ):
                if lines[index].strip():
                    block.append(lines[index].strip())
                index += 1
            out[key] = " ".join(block) if rest.startswith(">") else "\n".join(block)
            continue
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in {"'", '"'}:
            rest = rest[1:-1]
        out[key] = rest
        index += 1
    return out


def skill_name(meta: Mapping[str, str]) -> str:
    name = str(meta.get("name") or "").strip()
    if not _NAME.fullmatch(name):
        raise SkillError("frontmatter_name")
    return name


def skill_description(meta: Mapping[str, str]) -> str:
    text = " ".join(str(meta.get("description") or "").split())
    if not text or len(text) > 1024:
        raise SkillError("frontmatter_description")
    return text


def scan_files(files: Mapping[str, str]) -> None:
    """Reject secrets in every text file and dangerous calls in scripts."""
    for path, text in files.items():
        for kind, pattern in _SECRET:
            if pattern.search(text):
                raise SkillError(f"secret:{kind}:{path}")
        suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if suffix not in _SCRIPT:
            continue
        for kind, pattern in _DANGER:
            if pattern.search(text):
                raise SkillError(f"dangerous_script:{kind}:{path}")


def content_sha256(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def licence_of(meta: Mapping[str, str], files: Mapping[str, str]) -> dict[str, str]:
    for key in ("license", "licence"):
        raw = str(meta.get(key) or "").strip()
        if raw:
            ident = raw[:128]
            return {"id": ident, "source": "frontmatter", "sha256": _sha(ident)}
    folded = {path.casefold(): path for path in files}
    for name in _LICENCE_FILES:
        found = folded.get(name.casefold())
        if not found:
            continue
        first = " ".join(files[found].split())[:128] or "custom"
        return {"id": first, "source": found, "sha256": _sha(files[found])}
    return {"id": "unspecified", "source": "missing", "sha256": _sha("unspecified")}


def build_bundle(
    files: Mapping[str, str],
    *,
    commit_sha: str,
    url: str,
    origin: str,
    skill_dir: str | None,
) -> dict[str, object]:
    """Validate one skill directory. ``files`` keys are relative posix paths."""
    if "SKILL.md" not in files:
        raise SkillError("skill_md_missing")
    for path in files:
        if path.startswith("/") or ".." in path.split("/"):
            raise SkillError("skill_path_rejected")
    meta, _body = parse_frontmatter(str(files["SKILL.md"]))
    name = skill_name(meta)
    if skill_dir and skill_dir != name:
        raise SkillError("skill_dir_mismatch")
    description = skill_description(meta)
    scan_files(files)
    instructions = str(files["SKILL.md"])
    if len(instructions) > _INSTRUCTION_CAP:
        instructions = instructions[:_INSTRUCTION_CAP]
    listing = [
        {"path": path, "sha256": _sha(files[path])}
        for path in sorted(files)
    ]
    return {
        "name": name,
        "description": description,
        "licence": licence_of(meta, files),
        "commit_sha": commit_sha,
        "content_sha256": content_sha256(files),
        "origin": origin,
        "url": url,
        "instructions": instructions,
        "files": listing,
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
