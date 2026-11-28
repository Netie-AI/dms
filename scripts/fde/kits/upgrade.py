"""FDE-UPGRADE-01: upgrade precheck, verify, and the rollback runbook.

The kit writes a ``netie.fde.report/1`` document. It does not deploy, restart,
or migrate a customer database. Alembic runs only against a loopback scratch
database whose name contains ``scratch`` or ``fde_upgrade``.

T1 registers this module with one line once ``scripts/fde/kit.py`` exists.
Until then:

    DMS_FDE_KIT=1 python scripts/fde/kits/upgrade.py --engagement DIR --phase precheck --target SHA

``DMS_FDE_KIT`` must be the single character ``1``. Any other value exits 2
with ``fde_kit_disabled`` and writes nothing.

ponytail: flag diff sees literal ``DMS_*`` strings in environ reads only. A
flag held in a variable is invisible. Upgrade path: resolve a same-function
assignment before the environ call. Open-PR scan caps at 100 heads so a
noisy repo cannot turn the precheck into an unbounded fetch.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
KIT = "upgrade"
STAGE = "K9"
SOURCE = "scripts/fde/kits/upgrade.py"
OPEN_PR_CAP = 100
DEFAULT_BACKUP_HOURS = 24
SCRATCH_ENV = "FDE_UPGRADE_SCRATCH_URL"

FLAG_READ = re.compile(
    r"""(?:os\.)?environ\.get\(\s*['"](DMS_[A-Z][A-Z0-9_]*)['"]"""
    r"""|os\.getenv\(\s*['"](DMS_[A-Z][A-Z0-9_]*)['"]"""
    r"""|(?:os\.)?environ\[\s*['"](DMS_[A-Z][A-Z0-9_]*)['"]\s*\]"""
)
SECRET_KEY = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|authorization|bearer|private_key)$",
    re.IGNORECASE,
)
ENV_SECRET = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|_KEY)$")
URL_USERINFO = re.compile(r"(postgres(?:ql)?://[^:\s/]+:)([^@\s/]+)@")
BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
REV_NUMBER = re.compile(r"^(\d+)")
FORBIDDEN = frozenset(
    {
        "helm",
        "kubectl",
        "systemctl",
        "docker",
        "docker-compose",
        "podman",
        "reboot",
        "shutdown",
        "service",
    }
)
ALLOWED_GIT = frozenset(
    {"worktree", "show", "ls-tree", "rev-parse", "grep", "fetch", "merge-base", "cat-file"}
)
_KEEP = frozenset(
    {
        "PASS",
        "FAIL",
        "BLOCKED",
        "SKIPPED",
        "MANUAL",
        "critical",
        "high",
        "medium",
        "low",
        "info",
        "none",
        "off",
    }
)

Executor = Callable[..., list[dict[str, Any]]]


class ConfigError(Exception):
    token = "fde_kit_config"


class CommandRefused(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class Revision:
    revision: str
    down: str | tuple[str, ...] | None
    file: str
    downgrade_ok: bool

    def parents(self) -> tuple[str, ...]:
        if self.down is None:
            return ()
        if isinstance(self.down, str):
            return (self.down,)
        return tuple(self.down)


@dataclass
class KitContext:
    engagement_dir: Path
    phase: str
    target: str
    current: str = ""
    repo: Path = ROOT
    watch_refs: tuple[str, ...] = ()
    watch_dirs: tuple[str, ...] = ()
    scan_open_prs: bool = False
    repo_slug: str = "Netie-AI/dms"
    executor: Executor | None = None
    now: datetime | None = None
    trace: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)


@dataclass
class Loaded:
    data: dict[str, Any]
    tenant_id: str
    sources: list[dict[str, Any]]
    flags_on: list[str]
    upgrade: dict[str, Any]


def kit_enabled() -> bool:
    """On only when the variable is the digit 1. No strip. Unset is off."""
    return os.environ.get("DMS_FDE_KIT") == "1"


def load_report_api() -> Any:
    """Prefer T1's ``scripts/fde/report.py`` when it is this schema."""
    candidate = Path(__file__).resolve().parents[1] / "report.py"
    if candidate.is_file():
        spec = importlib.util.spec_from_file_location("fde_report_t1", candidate)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if (
                getattr(module, "SCHEMA", None) == "netie.fde.report/1"
                and callable(getattr(module, "render_md", None))
                and callable(getattr(module, "rollup", None))
                and callable(getattr(module, "document", None))
            ):
                return module
    spec = importlib.util.spec_from_file_location(
        "fde_report_v1", Path(__file__).resolve().parent / "report_v1.py"
    )
    if spec is None or spec.loader is None:
        raise ConfigError()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REPORT: Any | None = None


def report_api() -> Any:
    global _REPORT
    if _REPORT is None:
        _REPORT = load_report_api()
    return _REPORT


def _check(
    cid: str,
    title: str,
    severity: str,
    status: str,
    evidence: dict[str, Any],
    fix: str,
    source: str = SOURCE,
) -> dict[str, Any]:
    return {
        "id": cid,
        "title": title,
        "severity": severity,
        "status": status,
        "evidence": evidence,
        "fix": fix,
        "source": source,
    }


def scrub_text(text: str, secrets: Sequence[str]) -> str:
    out = text
    ordered = sorted(
        {s for s in secrets if isinstance(s, str) and len(s) >= 4 and s not in _KEEP},
        key=len,
        reverse=True,
    )
    for secret in ordered:
        out = out.replace(secret, "[redacted]")
    out = URL_USERINFO.sub(r"\1[redacted]@", out)
    out = BEARER.sub("Bearer [redacted]", out)
    return out


def scrub_obj(obj: Any, secrets: Sequence[str]) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj, secrets)
    if isinstance(obj, list):
        return [scrub_obj(item, secrets) for item in obj]
    if isinstance(obj, dict):
        return {str(key): scrub_obj(value, secrets) for key, value in obj.items()}
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return scrub_text(str(obj), secrets)


def _sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("sources")
    if raw is None and isinstance(data.get("source"), dict):
        raw = [data["source"]]
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError()
    sources: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError()
        for key in item:
            if str(key).lower() in {"password", "passwd", "secret"}:
                raise ConfigError()
        sources.append(item)
    return sources


def load_engagement(directory: Path) -> Loaded:
    file = directory / "engagement.yaml"
    if not file.is_file():
        raise ConfigError()
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError() from exc
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError() from exc
    if not isinstance(data, dict):
        raise ConfigError()
    tenant = data.get("tenant_id")
    if not isinstance(tenant, str) or not tenant.strip():
        raise ConfigError()
    upgrade = data.get("upgrade") or {}
    if not isinstance(upgrade, dict):
        raise ConfigError()
    flags = data.get("flags_on") or []
    if not isinstance(flags, list) or not all(isinstance(item, str) for item in flags):
        raise ConfigError()
    hours = upgrade.get("backup_max_age_hours", DEFAULT_BACKUP_HOURS)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
        raise ConfigError()
    return Loaded(
        data=data,
        tenant_id=tenant.strip(),
        sources=_sources(data),
        flags_on=list(flags),
        upgrade=upgrade,
    )


def add_url_secrets(loaded: Loaded, secrets: list[str]) -> None:
    """The scratch URL and its password are secrets even though the env name is not."""
    env_name = str(loaded.upgrade.get("scratch_url_env") or SCRATCH_ENV)
    url = os.environ.get(env_name, "")
    if len(url) >= 4:
        secrets.append(url)
    try:
        password = urlsplit(url).password or ""
    except ValueError:
        password = ""
    if len(password) >= 4:
        secrets.append(password)


def collect_secrets(loaded: Loaded) -> list[str]:
    names = {str(src.get("password_env") or "") for src in loaded.sources}
    names.add(str(loaded.upgrade.get("scratch_url_env") or SCRATCH_ENV))
    for name in os.environ:
        if ENV_SECRET.search(name):
            names.add(name)
    values: list[str] = []
    for name in names:
        if not name:
            continue
        value = os.environ.get(name, "")
        if len(value) >= 4:
            values.append(value)
    return values


def argv_allowed(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    names = {Path(arg).name for arg in argv}
    if names & FORBIDDEN:
        return False
    base = Path(argv[0]).name
    if base == "git":
        return len(argv) > 1 and argv[1] in ALLOWED_GIT
    if base.startswith("python"):
        return "-m" in argv and "alembic" in argv
    return False


def _run(
    argv: list[str], cwd: Path, trace: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # `gh pr list` is the read-only open-PR catalogue. Anything else named gh
    # is refused. stderr is kept only when the caller scrubs it; fetch discards
    # its own stderr because a credential helper can echo a URL.
    gh_list = bool(argv) and Path(argv[0]).name == "gh" and argv[1:3] == ["pr", "list"]
    if not gh_list and not argv_allowed(argv):
        raise CommandRefused("refused_command")
    trace.append("gh pr list" if gh_list else " ".join(argv[:4]))
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def resolve_sha(repo: Path, name: str, trace: list[str]) -> str | None:
    if not name:
        return None
    proc = _run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{name}^{{commit}}"],
        repo,
        trace,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def short_sha(sha: str) -> str:
    return sha[:8]


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    return ast.literal_eval(node)


def _assigned(tree: ast.AST, name: str) -> ast.AST | None:
    found: ast.AST | None = None
    for node in tree.body:  # type: ignore[attr-defined]
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            found = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    found = node.value
    return found


def _effective(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return False
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return False
    if isinstance(stmt, ast.Raise):
        return False
    if isinstance(stmt, ast.Return) and stmt.value is None:
        return False
    return True


def _downgrade_ok(tree: ast.AST) -> bool:
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            return any(_effective(stmt) for stmt in node.body)
    return False


def parse_revision_source(source: str, file: str) -> Revision | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Revision(revision="", down=None, file=file, downgrade_ok=False)
    try:
        revision = _literal(_assigned(tree, "revision"))
        down = _literal(_assigned(tree, "down_revision"))
    except (ValueError, SyntaxError):
        return Revision(revision="", down=None, file=file, downgrade_ok=False)
    if revision is None and _assigned(tree, "revision") is None:
        return None
    if not isinstance(revision, str) or not revision:
        return Revision(revision="", down=None, file=file, downgrade_ok=False)
    if down is not None and not isinstance(down, (str, tuple)):
        return Revision(revision="", down=None, file=file, downgrade_ok=False)
    if isinstance(down, tuple) and not all(isinstance(item, str) for item in down):
        return Revision(revision="", down=None, file=file, downgrade_ok=False)
    return Revision(revision=revision, down=down, file=file, downgrade_ok=_downgrade_ok(tree))


def revisions_from_texts(pairs: Sequence[tuple[str, str]]) -> list[Revision]:
    found: list[Revision] = []
    for file, source in pairs:
        parsed = parse_revision_source(source, file)
        if parsed is not None:
            found.append(parsed)
    return found


def revisions_at(repo: Path, sha: str, trace: list[str]) -> list[Revision]:
    listing = _run(["git", "ls-tree", "-r", "--name-only", sha, "alembic/versions"], repo, trace)
    if listing.returncode != 0:
        raise CommandRefused("git_ls_tree")
    pairs: list[tuple[str, str]] = []
    for path in listing.stdout.splitlines():
        if not path.endswith(".py") or "/__pycache__/" in path:
            continue
        blob = _run(["git", "show", f"{sha}:{path}"], repo, trace)
        if blob.returncode != 0:
            pairs.append((path, "???"))
            continue
        pairs.append((path, blob.stdout))
    return revisions_from_texts(pairs)


def revisions_in_dir(directory: Path) -> list[Revision]:
    versions = directory / "alembic" / "versions"
    if not versions.is_dir():
        versions = directory
    pairs: list[tuple[str, str]] = []
    if not versions.is_dir():
        return []
    for path in sorted(versions.glob("*.py")):
        if path.name == "__init__.py":
            continue
        pairs.append((str(path), path.read_text(encoding="utf-8")))
    return revisions_from_texts(pairs)


def revision_number(revision: str) -> str:
    match = REV_NUMBER.match(revision)
    return match.group(1) if match else revision


def heads(revs: Sequence[Revision]) -> list[Revision]:
    parents: set[str] = set()
    for rev in revs:
        parents.update(rev.parents())
    return [rev for rev in revs if rev.revision and rev.revision not in parents]


def issues_within(revs: Sequence[Revision]) -> dict[str, list[dict[str, Any]]]:
    by_id: dict[str, list[str]] = {}
    for rev in revs:
        if not rev.revision:
            continue
        by_id.setdefault(rev.revision, []).append(rev.file)
    duplicates = [
        {"revision": revision, "files": files}
        for revision, files in sorted(by_id.items())
        if len(files) > 1
    ]
    by_num: dict[str, set[str]] = {}
    for rev in revs:
        if rev.revision:
            by_num.setdefault(revision_number(rev.revision), set()).add(rev.revision)
    clashes = [
        {"number": number, "ids": sorted(ids)}
        for number, ids in sorted(by_num.items())
        if len(ids) > 1
    ]
    children: dict[str, set[str]] = {}
    for rev in revs:
        if not rev.revision:
            continue
        for parent in rev.parents():
            children.setdefault(parent, set()).add(rev.revision)
    branches = [
        {"parent": parent, "children": sorted(kids)}
        for parent, kids in sorted(children.items())
        if len(kids) > 1
    ]
    unreadable = sorted({rev.file for rev in revs if not rev.revision})
    return {
        "duplicates": duplicates,
        "number_clashes": clashes,
        "branches": branches,
        "unreadable": [{"file": file} for file in unreadable],
    }


def _dump(item: dict[str, Any]) -> str:
    return json.dumps(item, sort_keys=True)


def issues_across(
    base: Sequence[Revision], extras: Sequence[tuple[str, Sequence[Revision]]]
) -> dict[str, Any]:
    combined: dict[str, Revision] = {rev.revision: rev for rev in base if rev.revision}
    stacked: list[dict[str, Any]] = []
    tree_internal: list[dict[str, Any]] = []
    for label, revs in extras:
        local = issues_within(revs)
        if (
            local["duplicates"]
            or local["number_clashes"]
            or local["branches"]
            or local["unreadable"]
        ):
            tree_internal.append({"tree": label, **local})
        extra_ids = []
        for rev in revs:
            if rev.revision and rev.revision not in combined:
                combined[rev.revision] = rev
                extra_ids.append(rev.revision)
        if extra_ids:
            stacked.append({"tree": label, "revisions": extra_ids})
    overlay = issues_within(list(combined.values()))
    base_issues = issues_within(base)

    def fresh(kind: str) -> list[dict[str, Any]]:
        known = {_dump(item) for item in base_issues[kind]}
        return [item for item in overlay[kind] if _dump(item) not in known]

    return {
        "number_clashes": fresh("number_clashes"),
        "branches": fresh("branches"),
        "stacked": stacked,
        "tree_internal": tree_internal,
    }


def flags_at(repo: Path, sha: str, trace: list[str]) -> set[str]:
    proc = _run(
        ["git", "grep", "-I", "-h", "-n", "-E", r"DMS_[A-Z][A-Z0-9_]*", sha, "--", "*.py"],
        repo,
        trace,
    )
    if proc.returncode not in (0, 1):
        raise CommandRefused("git_grep")
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        content = line.split(":", 1)[1] if ":" in line else line
        for match in FLAG_READ.finditer(content):
            found.add(next(group for group in match.groups() if group))
    return found


def _ancestor(repo: Path, older: str, newer: str, trace: list[str]) -> bool:
    proc = _run(["git", "merge-base", "--is-ancestor", older, newer], repo, trace)
    return proc.returncode == 0


def scratch_refusal(url: str, sources: Sequence[dict[str, Any]]) -> str | None:
    """Return a stable code, or None when the URL is a loopback scratch database.

    The code never contains the URL. Port 8090 is the API port and is refused
    even on loopback. The database name has to carry a scratch marker and must
    not be an engagement source.
    """
    if not url:
        return "unset"
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "bad_url"
    if parsed.scheme.lower() not in {"postgresql", "postgres"}:
        return "bad_url"
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return "not_loopback"
    port = parsed.port or 5432
    if port == 8090:
        return "port_8090"
    name = parsed.path.lstrip("/").split("?", 1)[0]
    if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name):
        return "bad_name"
    lowered = name.lower()
    if "scratch" not in lowered and "fde_upgrade" not in lowered:
        return "not_scratch_name"
    for source in sources:
        source_name = str(source.get("database") or "")
        if source_name and source_name.lower() == lowered:
            return "matches_engagement_source"
    return None


def database_name(url: str) -> str:
    try:
        return urlsplit(url).path.lstrip("/").split("?", 1)[0]
    except ValueError:
        return ""


def build_roundtrip_plan(
    repo: Path,
    current: str,
    target: str,
    downgrade_to: str,
    work_root: Path,
) -> list[dict[str, Any]]:
    """Commands for a scratch round-trip. The URL is not in argv."""
    current_tree = work_root / "current"
    target_tree = work_root / "target"
    steps: list[dict[str, Any]] = []

    def add(kind: str, argv: list[str], cwd: Path, expect: str = "", cleanup: bool = False) -> None:
        steps.append(
            {"kind": kind, "argv": argv, "cwd": str(cwd), "expect": expect, "cleanup": cleanup}
        )

    def alembic(cwd: Path, *args: str, expect: str = "") -> None:
        add("alembic", [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args], cwd, expect)

    add("git", ["git", "worktree", "add", "--detach", str(current_tree), current], repo)
    alembic(current_tree, "upgrade", "head")
    alembic(current_tree, "current", expect="" if downgrade_to == "base" else downgrade_to)
    if target != current:
        add("git", ["git", "worktree", "add", "--detach", str(target_tree), target], repo)
        alembic(target_tree, "upgrade", "head")
        alembic(target_tree, "downgrade", downgrade_to)
        alembic(target_tree, "current", expect="" if downgrade_to == "base" else downgrade_to)
        add("git", ["git", "worktree", "remove", "--force", str(target_tree)], repo, cleanup=True)
    else:
        alembic(current_tree, "downgrade", "base")
        alembic(current_tree, "current", expect="")
    add("git", ["git", "worktree", "remove", "--force", str(current_tree)], repo, cleanup=True)
    return steps


def execute_plan(
    plan: Sequence[dict[str, Any]],
    *,
    url: str,
    secrets: Sequence[str],
    sources: Sequence[dict[str, Any]],
    trace: list[str],
    databases: list[str],
) -> list[dict[str, Any]]:
    """Run the plan. Refuses before any subprocess when the URL is not scratch."""
    code = scratch_refusal(url, sources)
    if code:
        raise CommandRefused(code)
    name = database_name(url)
    secret_values = list(secrets)
    secret_values.append(url)
    try:
        password = urlsplit(url).password or ""
    except ValueError:
        password = ""
    if password:
        secret_values.append(password)
    child_env = os.environ.copy()
    child_env["DATABASE_URL"] = url
    results: list[dict[str, Any]] = []
    failed = False
    try:
        for step in plan:
            if step.get("cleanup"):
                continue
            if not argv_allowed(step["argv"]):
                raise CommandRefused("refused_command")
            env = child_env if step["kind"] == "alembic" else os.environ.copy()
            proc = _run(step["argv"], Path(step["cwd"]), trace, env=env)
            if step["kind"] == "alembic":
                databases.append(name)
            results.append(
                {
                    "code": proc.returncode,
                    "stdout": scrub_text(proc.stdout or "", secret_values)[:2000],
                    "stderr": scrub_text(proc.stderr or "", secret_values)[:2000],
                    "expect": step.get("expect") or "",
                    "kind": step["kind"],
                    "database": name if step["kind"] == "alembic" else "",
                    "cleanup": False,
                }
            )
            if proc.returncode != 0:
                failed = True
                break
    finally:
        for step in plan:
            if not step.get("cleanup"):
                continue
            if not argv_allowed(step["argv"]):
                results.append(
                    {
                        "code": 1,
                        "stdout": "",
                        "stderr": "refused_command",
                        "expect": "",
                        "kind": "git",
                        "database": "",
                        "cleanup": True,
                    }
                )
                continue
            proc = _run(step["argv"], Path(step["cwd"]), trace, env=os.environ.copy())
            results.append(
                {
                    "code": proc.returncode,
                    "stdout": "",
                    "stderr": scrub_text(proc.stderr or "", secret_values)[:300],
                    "expect": "",
                    "kind": "git",
                    "database": "",
                    "cleanup": True,
                }
            )
    if failed:
        return results
    return results


def _roundtrip_ok(results: Sequence[dict[str, Any]], known_ids: Sequence[str]) -> tuple[bool, str]:
    for item in results:
        if item["code"] != 0:
            return False, "worktree_cleanup" if item.get("cleanup") else "command_exit"
        if item.get("kind") == "alembic" and not item.get("cleanup"):
            database = str(item.get("database") or "")
            if "scratch" not in database.lower() and "fde_upgrade" not in database.lower():
                return False, "database"
    currents = [item for item in results if item.get("kind") == "alembic" and _is_current(item)]
    if not currents:
        return False, "no_current"
    last = currents[-1]
    blob = f"{last.get('stdout', '')}\n{last.get('stderr', '')}"
    expect = str(last.get("expect") or "")
    if expect:
        if expect not in blob:
            return False, "revision_mismatch"
        return True, ""
    for revision in known_ids:
        if revision and revision in blob:
            return False, "revision_still_present"
    return True, ""


def _is_current(item: dict[str, Any]) -> bool:
    # The expect field is set only on `alembic current` steps. Upgrade and
    # downgrade steps leave it empty too, so the command shape is on stdout's
    # sibling: callers set a marker key.
    return bool(item.get("is_current"))


def execute_plan_marked(
    plan: Sequence[dict[str, Any]],
    *,
    url: str,
    secrets: Sequence[str],
    sources: Sequence[dict[str, Any]],
    trace: list[str],
    databases: list[str],
) -> list[dict[str, Any]]:
    results = execute_plan(
        plan, url=url, secrets=secrets, sources=sources, trace=trace, databases=databases
    )
    # `alembic current` is the only alembic step whose argv ends with current.
    # execute_plan does not keep argv. Re-walk the non-cleanup plan steps.
    produced = [item for item in results if not item.get("cleanup")]
    steps = [step for step in plan if not step.get("cleanup")]
    for item, step in zip(produced, steps, strict=False):
        argv = step.get("argv") or []
        item["is_current"] = step["kind"] == "alembic" and argv[-1:] == ["current"]
        item["expect"] = step.get("expect") or ""
    return results


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _flag_map(flags_on: Sequence[str]) -> dict[str, str]:
    flags = {"DMS_FDE_KIT": "1"}
    for name in flags_on:
        if ENV_SECRET.search(name):
            continue
        flags[name] = "1" if os.environ.get(name) == "1" else "off"
    return flags


def _resolve_path(directory: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        path = directory / path
    return path


def check_backup(directory: Path, upgrade: dict[str, Any], now: datetime) -> dict[str, Any]:
    hours = float(upgrade.get("backup_max_age_hours", DEFAULT_BACKUP_HOURS))
    dump = _resolve_path(directory, upgrade.get("backup_dump"))
    warehouse = _resolve_path(directory, upgrade.get("warehouse_copy"))
    missing = []
    if dump is None or not dump.is_file():
        missing.append("pg_dump")
    if warehouse is None or not warehouse.is_file():
        missing.append("warehouse")
    fix = (
        "Take a pg_dump and copy the warehouse, then set upgrade.backup_dump and "
        "upgrade.warehouse_copy. This kit does not create either file."
    )
    if missing:
        return _check(
            "K9.07",
            "Backup evidence",
            "high",
            "FAIL",
            {"missing": missing, "max_age_hours": hours},
            fix,
        )
    stale: list[dict[str, Any]] = []
    ages: dict[str, float] = {}
    assert dump is not None and warehouse is not None
    for label, path in (("pg_dump", dump), ("warehouse", warehouse)):
        stat = path.stat()
        age_h = (now.timestamp() - stat.st_mtime) / 3600
        ages[label] = round(age_h, 3)
        if stat.st_size <= 0 or age_h > hours:
            stale.append({"file": label, "age_hours": round(age_h, 3), "bytes": stat.st_size})
    if stale:
        return _check(
            "K9.07",
            "Backup evidence",
            "high",
            "FAIL",
            {"stale": stale, "max_age_hours": hours},
            fix,
        )
    return _check(
        "K9.07",
        "Backup evidence",
        "high",
        "PASS",
        {"age_hours": ages, "max_age_hours": hours},
        "",
    )


def _declared_renames(upgrade: dict[str, Any]) -> list[tuple[str, str]]:
    raw = upgrade.get("flag_renames") or []
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        old = item.get("from")
        new = item.get("to")
        if isinstance(old, str) and isinstance(new, str) and old and new:
            pairs.append((old, new))
    return pairs


def check_flags(
    repo: Path,
    current: str | None,
    target: str | None,
    upgrade: dict[str, Any],
    trace: list[str],
) -> dict[str, Any]:
    title = "Flags removed or renamed"
    if not current or not target:
        return _check(
            "K9.06",
            title,
            "high",
            "SKIPPED",
            {"reason": "shas_not_resolved"},
            "Resolve both shas, then re-run precheck.",
        )
    try:
        old = flags_at(repo, current, trace)
        new = flags_at(repo, target, trace)
    except CommandRefused as exc:
        return _check(
            "K9.06",
            title,
            "high",
            "FAIL",
            {"error": exc.code},
            "Flag scan could not read one of the trees.",
        )
    removed = set(old) - set(new)
    added = set(new) - set(old)
    renamed: list[dict[str, str]] = []
    for src, dst in _declared_renames(upgrade):
        if src in removed and dst in added:
            removed.discard(src)
            added.discard(dst)
            renamed.append({"from": src, "to": dst})
    evidence = {
        "removed": sorted(removed),
        "added": sorted(added),
        "renamed": renamed,
    }
    if removed:
        return _check(
            "K9.06",
            title,
            "high",
            "FAIL",
            evidence,
            "A DMS_* environment read disappeared between these shas. Declare "
            "upgrade.flag_renames when it was renamed, or keep the old name until "
            "the tenant sheet drops it.",
        )
    return _check("K9.06", title, "high", "PASS", evidence, "")


def _bad_downgrades(pending: Sequence[Revision]) -> list[dict[str, Any]]:
    bad = []
    for rev in pending:
        if not rev.downgrade_ok or not rev.revision:
            bad.append({"revision": rev.revision or "", "file": rev.file, "downgrade_ok": False})
    return bad


def _pending(current: Sequence[Revision], target: Sequence[Revision]) -> list[Revision]:
    have = {rev.revision for rev in current if rev.revision}
    return [rev for rev in target if rev.revision not in have or not rev.revision]


def scan_open_prs(
    repo: Path, slug: str, trace: list[str]
) -> tuple[list[tuple[str, list[Revision]]], list[dict[str, str]]]:
    """Read open PR heads. stderr is discarded: it can carry a credential URL."""
    listed = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            slug,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,headRefOid",
        ],
        repo,
        trace,
    )
    if listed.returncode != 0:
        return [], [{"error": "gh_list_failed"}]
    try:
        payload = json.loads(listed.stdout or "[]")
    except json.JSONDecodeError:
        return [], [{"error": "gh_list_failed"}]
    if not isinstance(payload, list):
        return [], [{"error": "gh_list_failed"}]
    extras: list[tuple[str, list[Revision]]] = []
    errors: list[dict[str, str]] = []
    truncated = len(payload) > OPEN_PR_CAP
    for item in payload[:OPEN_PR_CAP]:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        ref = f"refs/fde-upgrade/pr-{number}"
        fetched = subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", f"pull/{number}/head:{ref}"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        trace.append(f"git fetch pull/{number}/head")
        if fetched.returncode != 0:
            errors.append({"pr": str(number), "error": "fetch_failed"})
            continue
        sha = resolve_sha(repo, ref, trace)
        if not sha:
            errors.append({"pr": str(number), "error": "fetch_failed"})
            continue
        try:
            extras.append((f"pr-{number}", revisions_at(repo, sha, trace)))
        except CommandRefused:
            errors.append({"pr": str(number), "error": "unreadable"})
    if truncated:
        errors.append({"error": "truncated"})
    return extras, errors


def _jsonable_revs(revs: Sequence[Revision]) -> list[dict[str, Any]]:
    return [
        {
            "revision": rev.revision,
            "file": rev.file,
            "down": list(rev.parents()),
            "downgrade_ok": rev.downgrade_ok,
        }
        for rev in revs
    ]


def _host_check(trace: list[str], databases: Sequence[str]) -> dict[str, Any]:
    refused = [item for item in trace if not _trace_allowed(item)]
    bad_db = [
        name
        for name in databases
        if "scratch" not in name.lower() and "fde_upgrade" not in name.lower()
    ]
    evidence = {
        "commands": trace[:40],
        "commands_truncated": len(trace) > 40,
        "databases": list(databases),
        "refused": refused,
    }
    if refused or bad_db:
        return _check(
            "K9.08",
            "No deploy and no customer migration",
            "critical",
            "FAIL",
            evidence,
            "The kit may run git reads and alembic only against the scratch database. "
            "It must not run helm, compose, docker, or a restart.",
        )
    return _check(
        "K9.08",
        "No deploy and no customer migration",
        "critical",
        "PASS",
        evidence,
        "",
    )


def _trace_allowed(shown: str) -> bool:
    parts = shown.split()
    if not parts:
        return False
    if parts[0] == "gh" and parts[1:3] == ["pr", "list"]:
        return True
    if parts[0] == "git" and len(parts) > 1 and parts[1] in ALLOWED_GIT:
        return True
    if Path(parts[0]).name.startswith("python") and "alembic" in shown:
        return True
    return False


def _load_sibling(name: str) -> Any | None:
    path = Path(__file__).resolve().parent / f"{name}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"fde_kit_{name}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _child_status(result: Any) -> str:
    if isinstance(result, dict):
        status = result.get("status")
    elif hasattr(result, "to_dict"):
        data = result.to_dict()
        status = data.get("status") if isinstance(data, dict) else None
    else:
        status = getattr(result, "status", None)
    if status in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
        return str(status)
    return ""


def _child_metrics(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        metrics = result.get("metrics")
        return metrics if isinstance(metrics, dict) else {}
    if hasattr(result, "to_dict"):
        data = result.to_dict()
        if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
            return data["metrics"]
    return {}


def wrong_of(doc: dict[str, Any] | None) -> int | None:
    """Integer WRONG count from a report document. Prose is ignored."""
    if not isinstance(doc, dict):
        return None
    metrics = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else {}
    candidates: list[Any] = [
        metrics.get("wrong"),
        metrics.get("WRONG"),
        metrics.get("heldout_wrong"),
        doc.get("wrong"),
    ]
    held = metrics.get("heldout")
    if isinstance(held, dict):
        candidates.append(held.get("wrong"))
    for value in candidates:
        if type(value) is int and value >= 0:
            return value
    return None


def _read_report(directory: Path, value: Any) -> dict[str, Any] | None:
    path = _resolve_path(directory, value)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _redact_receipts(directory: Path, secrets: list[str]) -> list[tuple[str, str]]:
    folder = directory / "receipts"
    if not folder.is_dir():
        return []
    copied: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            copied.append((path.name, scrub_text(raw, secrets)))
            continue
        found: list[str] = []

        def walk(obj: Any) -> Any:
            if isinstance(obj, dict):
                out: dict[str, Any] = {}
                for key, value in obj.items():
                    if (
                        isinstance(key, str)
                        and SECRET_KEY.search(key)
                        and isinstance(value, str)
                        and len(value) >= 4
                    ):
                        found.append(value)
                        out[key] = "[redacted]"
                    else:
                        out[str(key)] = walk(value)
                return out
            if isinstance(obj, list):
                return [walk(item) for item in obj]
            return obj

        redacted = walk(payload)
        secrets.extend(found)
        text = json.dumps(redacted, indent=2)
        copied.append((path.name, scrub_text(text, secrets)))
    return copied


def _safe_git_check(
    fn: Callable[[], dict[str, Any]], cid: str, title: str, severity: str
) -> dict[str, Any]:
    try:
        return fn()
    except CommandRefused as exc:
        return _check(
            cid, title, severity, "FAIL", {"error": exc.code}, "The check refused a command."
        )
    except Exception as exc:
        return _check(
            cid,
            title,
            severity,
            "FAIL",
            {"error": type(exc).__name__},
            "The check raised. Evidence carries the exception class only.",
        )


def precheck(
    ctx: KitContext, loaded: Loaded, secrets: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    repo = ctx.repo
    current = resolve_sha(repo, ctx.current or "HEAD", ctx.trace)
    target = resolve_sha(repo, ctx.target, ctx.trace)
    metrics: dict[str, Any] = {
        "current_sha": short_sha(current) if current else "",
        "target_sha": short_sha(target) if target else "",
        "pending": [],
        "heldout_wrong": None,
        "accepted_wrong": None,
    }
    checks: list[dict[str, Any]] = []

    def sha_check() -> dict[str, Any]:
        if not current or not target:
            return _check(
                "K9.01",
                "Current and target revisions",
                "high",
                "BLOCKED",
                {
                    "current_resolved": bool(current),
                    "target_resolved": bool(target),
                },
                "Fetch the missing sha into this clone. This kit does not deploy.",
            )
        ancestor = _ancestor(repo, current, target, ctx.trace)
        status = "PASS" if ancestor else "FAIL"
        fix = (
            ""
            if ancestor
            else (
                "The target is not a descendant of current. Pass the deployed sha as "
                "--current and a descendant as --target."
            )
        )
        return _check(
            "K9.01",
            "Current and target revisions",
            "high",
            status,
            {
                "current": short_sha(current),
                "target": short_sha(target),
                "same_sha": current == target,
                "ancestor": ancestor,
            },
            fix,
        )

    checks.append(_safe_git_check(sha_check, "K9.01", "Current and target revisions", "high"))

    current_revs: list[Revision] = []
    target_revs: list[Revision] = []
    read_error = ""
    if current and target:
        try:
            current_revs = revisions_at(repo, current, ctx.trace)
            target_revs = revisions_at(repo, target, ctx.trace)
        except CommandRefused as exc:
            read_error = exc.code
        except Exception as exc:
            read_error = type(exc).__name__

    pending = _pending(current_revs, target_revs) if not read_error and current and target else []
    metrics["pending"] = [rev.revision or rev.file for rev in pending]
    target_heads = heads(target_revs) if target_revs else []
    current_heads = heads(current_revs) if current_revs else []

    if read_error or not current or not target:
        checks.append(
            _check(
                "K9.02",
                "Pending alembic revisions",
                "high",
                "SKIPPED",
                {"reason": read_error or "shas_not_resolved"},
                "Resolve both trees, then re-run precheck.",
            )
        )
    elif len(target_heads) != 1:
        checks.append(
            _check(
                "K9.02",
                "Pending alembic revisions",
                "high",
                "FAIL",
                {"heads": [rev.revision for rev in target_heads], "pending": metrics["pending"]},
                "The target tree has no single alembic head. A branch in alembic/versions "
                "has to be merged or removed before an upgrade.",
            )
        )
    else:
        checks.append(
            _check(
                "K9.02",
                "Pending alembic revisions",
                "high",
                "PASS",
                {
                    "pending": _jsonable_revs(pending),
                    "head": target_heads[0].revision,
                    "current_head": current_heads[0].revision if len(current_heads) == 1 else "",
                },
                "",
            )
        )

    if read_error or not current or not target:
        checks.append(
            _check(
                "K9.03",
                "Pending revisions have a downgrade",
                "critical",
                "SKIPPED",
                {"reason": read_error or "shas_not_resolved"},
                "Resolve both trees, then re-run precheck.",
            )
        )
    else:
        bad = _bad_downgrades(pending)
        if bad:
            checks.append(
                _check(
                    "K9.03",
                    "Pending revisions have a downgrade",
                    "critical",
                    "FAIL",
                    {"revisions": bad},
                    "Add a downgrade() that reverses the upgrade. An empty body, pass, "
                    "a bare return, or a raise is not a working downgrade.",
                    source=bad[0]["file"] or SOURCE,
                )
            )
        else:
            checks.append(
                _check(
                    "K9.03",
                    "Pending revisions have a downgrade",
                    "critical",
                    "PASS",
                    {"pending": len(pending), "downgrade_ok": True},
                    "",
                )
            )

    local_issues = (
        issues_within(target_revs)
        if target_revs
        else {
            "duplicates": [],
            "number_clashes": [],
            "branches": [],
            "unreadable": [],
        }
    )
    if read_error or not target:
        checks.append(
            _check(
                "K9.05",
                "Duplicate revision ids",
                "critical",
                "SKIPPED",
                {"reason": read_error or "shas_not_resolved"},
                "Resolve the target tree, then re-run precheck.",
            )
        )
    elif (
        local_issues["duplicates"]
        or local_issues["number_clashes"]
        or local_issues["branches"]
        or local_issues["unreadable"]
    ):
        checks.append(
            _check(
                "K9.05",
                "Duplicate revision ids",
                "critical",
                "FAIL",
                local_issues,
                "Give every revision a unique id and a unique leading number. "
                "Two children of the same parent are a branch and block the upgrade.",
            )
        )
    else:
        checks.append(
            _check(
                "K9.05",
                "Duplicate revision ids",
                "critical",
                "PASS",
                {"revisions": len(target_revs)},
                "",
            )
        )

    checks.append(check_flags(repo, current, target, loaded.upgrade, ctx.trace))
    now = ctx.now or datetime.now(UTC)
    checks.append(check_backup(ctx.engagement_dir, loaded.upgrade, now))
    checks.append(_scratch_check(ctx, loaded, secrets, current, target, current_revs, target_revs))
    checks.append(_cross_tree_check(ctx, target_revs))
    return checks, metrics


def _scratch_check(
    ctx: KitContext,
    loaded: Loaded,
    secrets: list[str],
    current: str | None,
    target: str | None,
    current_revs: Sequence[Revision],
    target_revs: Sequence[Revision],
) -> dict[str, Any]:
    title = "Scratch upgrade and downgrade"
    env_name = str(loaded.upgrade.get("scratch_url_env") or SCRATCH_ENV)
    url = os.environ.get(env_name, "")
    code = scratch_refusal(url, loaded.sources)
    if code == "unset":
        return _check(
            "K9.04",
            title,
            "critical",
            "SKIPPED",
            {"reason": "scratch URL is not set", "env": env_name},
            "Set the environment variable named by upgrade.scratch_url_env to a "
            "loopback scratch database. This kit does not migrate the engagement host.",
        )
    if code:
        return _check(
            "K9.04",
            title,
            "critical",
            "FAIL",
            {"reason": code},
            "Point the scratch URL at loopback and a database whose name contains "
            "scratch or fde_upgrade, and which is not an engagement source. "
            "Port 8090 is refused.",
        )
    if not current or not target:
        return _check(
            "K9.04",
            title,
            "critical",
            "SKIPPED",
            {"reason": "shas_not_resolved"},
            "Resolve both shas before the scratch round-trip.",
        )
    current_heads = heads(list(current_revs))
    target_heads = heads(list(target_revs))
    if current_revs and len(current_heads) != 1:
        return _check(
            "K9.04",
            title,
            "critical",
            "FAIL",
            {"reason": "current_heads", "heads": [rev.revision for rev in current_heads]},
            "The current tree needs one alembic head before a scratch round-trip.",
        )
    if target_revs and len(target_heads) != 1:
        return _check(
            "K9.04",
            title,
            "critical",
            "FAIL",
            {"reason": "target_heads", "heads": [rev.revision for rev in target_heads]},
            "The target tree needs one alembic head before a scratch round-trip.",
        )
    downgrade_to = current_heads[0].revision if current_heads else "base"
    work_root = Path(tempfile.mkdtemp(prefix="fde-upgrade-"))
    plan = build_roundtrip_plan(ctx.repo, current, target, downgrade_to, work_root)
    for step in plan:
        if not argv_allowed(step["argv"]):
            return _check(
                "K9.04",
                title,
                "critical",
                "FAIL",
                {"reason": "refused_command"},
                "The round-trip plan contained a command this kit does not run.",
            )
    runner = ctx.executor or execute_plan_marked
    try:
        results = runner(
            plan,
            url=url,
            secrets=secrets,
            sources=loaded.sources,
            trace=ctx.trace,
            databases=ctx.databases,
        )
    except CommandRefused as exc:
        return _check(
            "K9.04",
            title,
            "critical",
            "FAIL",
            {"reason": exc.code},
            "The scratch round-trip was refused before it touched a database.",
        )
    except Exception as exc:
        return _check(
            "K9.04",
            title,
            "critical",
            "FAIL",
            {"error": type(exc).__name__},
            "The scratch round-trip raised. Evidence carries the exception class only.",
        )
    known = [rev.revision for rev in list(current_revs) + list(target_revs)]
    # A test double may already mark is_current. If not, mark from the plan.
    if results and "is_current" not in results[0]:
        produced = [item for item in results if not item.get("cleanup")]
        steps = [step for step in plan if not step.get("cleanup")]
        for item, step in zip(produced, steps, strict=False):
            argv = step.get("argv") or []
            item["is_current"] = step["kind"] == "alembic" and argv[-1:] == ["current"]
            item["expect"] = step.get("expect") or ""
    ok, why = _roundtrip_ok(results, known)
    evidence = {
        "database": database_name(url),
        "downgrade_to": downgrade_to,
        "reason": why,
        "alembic_codes": [item["code"] for item in results if item.get("kind") == "alembic"],
    }
    if ok:
        return _check("K9.04", title, "critical", "PASS", evidence, "")
    return _check(
        "K9.04",
        title,
        "critical",
        "FAIL",
        evidence,
        "The scratch database did not return to the current revision. Do not upgrade "
        "the customer database. See docs/fde/UPGRADE.md.",
    )


def _cross_tree_check(ctx: KitContext, target_revs: Sequence[Revision]) -> dict[str, Any]:
    title = "Revision clashes across trees"
    if not ctx.scan_open_prs and not ctx.watch_dirs and not ctx.watch_refs:
        return _check(
            "K9.09",
            title,
            "critical",
            "SKIPPED",
            {"reason": "not_requested"},
            "Re-run with --scan-open-prs, or pass --watch-dir / --watch-ref for each "
            "open PR tree. A revision-number clash is invisible until you do.",
        )
    extras: list[tuple[str, list[Revision]]] = []
    errors: list[dict[str, str]] = []
    for raw in ctx.watch_dirs:
        directory = Path(raw)
        if not directory.is_dir():
            errors.append({"dir": raw, "error": "missing"})
            continue
        extras.append((f"dir:{directory.name}", revisions_in_dir(directory)))
    for ref in ctx.watch_refs:
        sha = resolve_sha(ctx.repo, ref, ctx.trace)
        if not sha:
            errors.append({"ref": ref, "error": "not_in_clone"})
            continue
        try:
            extras.append((short_sha(sha), revisions_at(ctx.repo, sha, ctx.trace)))
        except CommandRefused as exc:
            errors.append({"ref": ref, "error": exc.code})
    if ctx.scan_open_prs:
        try:
            scanned, scan_errors = scan_open_prs(ctx.repo, ctx.repo_slug, ctx.trace)
        except Exception as exc:
            scanned = []
            scan_errors = [{"error": type(exc).__name__}]
        extras.extend(scanned)
        errors.extend(scan_errors)
    found = issues_across(target_revs, extras)
    clash = bool(found["number_clashes"] or found["branches"] or found["tree_internal"])
    evidence = {
        "number_clashes": found["number_clashes"],
        "branches": found["branches"],
        "stacked": found["stacked"],
        "tree_internal": found["tree_internal"],
        "errors": errors,
    }
    if clash:
        return _check(
            "K9.09",
            title,
            "critical",
            "FAIL",
            evidence,
            "An open tree reuses a revision number or adds a second child of a "
            "revision that already has one. Renumber it so it chains after the current head.",
        )
    if errors:
        return _check(
            "K9.09",
            title,
            "critical",
            "SKIPPED",
            evidence,
            "A watched tree could not be read. Re-run when the fetch works. "
            "Do not treat this as PASS.",
        )
    return _check("K9.09", title, "critical", "PASS", evidence, "")


def verify(ctx: KitContext, loaded: Loaded) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "current_sha": "",
        "target_sha": "",
        "pending": [],
        "heldout_wrong": None,
        "accepted_wrong": None,
    }
    current = resolve_sha(ctx.repo, ctx.current or "HEAD", ctx.trace)
    target = resolve_sha(ctx.repo, ctx.target, ctx.trace)
    if current:
        metrics["current_sha"] = short_sha(current)
    if target:
        metrics["target_sha"] = short_sha(target)

    inspect_mod = None
    accuracy_mod = None
    inspect_error = ""
    accuracy_error = ""
    try:
        inspect_mod = _load_sibling("inspect")
    except Exception as exc:
        inspect_error = type(exc).__name__
    try:
        accuracy_mod = _load_sibling("accuracy")
    except Exception as exc:
        accuracy_error = type(exc).__name__

    inspect_result: Any = None
    if inspect_error:
        checks.append(
            _check(
                "K9.10",
                "Inspect re-run",
                "high",
                "FAIL",
                {"error": inspect_error},
                "scripts/fde/kits/inspect.py raised while loading.",
            )
        )
    elif inspect_mod is None or not hasattr(inspect_mod, "run"):
        checks.append(
            _check(
                "K9.10",
                "Inspect re-run",
                "high",
                "SKIPPED",
                {"reason": "scripts/fde/kits/inspect.py is not in this tree"},
                "Land T5 or pass a completed inspect report after that kit exists. "
                "A missing inspect is not PASS.",
            )
        )
    else:
        try:
            inspect_result = inspect_mod.run(ctx)
        except Exception as exc:
            checks.append(
                _check(
                    "K9.10",
                    "Inspect re-run",
                    "high",
                    "FAIL",
                    {"error": type(exc).__name__},
                    "Inspect raised. Evidence carries the exception class only.",
                )
            )
        else:
            status = _child_status(inspect_result)
            if not status:
                status = "FAIL"
            checks.append(
                _check(
                    "K9.10",
                    "Inspect re-run",
                    "high",
                    status,
                    {"child_status": status},
                    ""
                    if status == "PASS"
                    else "Inspect did not pass. Fix those checks before upgrade.",
                )
            )

    accuracy_result: Any = None
    if accuracy_error:
        checks.append(
            _check(
                "K9.11",
                "Held-out re-run",
                "critical",
                "FAIL",
                {"error": accuracy_error},
                "scripts/fde/kits/accuracy.py raised while loading.",
            )
        )
    elif accuracy_mod is None or not hasattr(accuracy_mod, "run"):
        checks.append(
            _check(
                "K9.11",
                "Held-out re-run",
                "critical",
                "SKIPPED",
                {"reason": "scripts/fde/kits/accuracy.py is not in this tree"},
                "Land T4 or compare the supplied held-out report. A missing re-run is not PASS.",
            )
        )
    else:
        try:
            accuracy_result = accuracy_mod.run(ctx)
        except Exception as exc:
            checks.append(
                _check(
                    "K9.11",
                    "Held-out re-run",
                    "critical",
                    "FAIL",
                    {"error": type(exc).__name__},
                    "Held-out accuracy raised. Evidence carries the exception class only.",
                )
            )
        else:
            status = _child_status(accuracy_result)
            if not status:
                status = "FAIL"
            checks.append(
                _check(
                    "K9.11",
                    "Held-out re-run",
                    "critical",
                    status,
                    {"child_status": status},
                    "" if status == "PASS" else "Held-out accuracy did not pass.",
                )
            )

    accepted = _read_report(ctx.engagement_dir, loaded.upgrade.get("accepted_report"))
    heldout_file = _read_report(ctx.engagement_dir, loaded.upgrade.get("heldout_report"))
    accepted_wrong = wrong_of(accepted)
    new_doc: dict[str, Any] | None = None
    if isinstance(accuracy_result, dict):
        new_doc = accuracy_result
    elif accuracy_result is not None and hasattr(accuracy_result, "to_dict"):
        data = accuracy_result.to_dict()
        new_doc = data if isinstance(data, dict) else None
    if new_doc is None:
        new_doc = heldout_file
    new_wrong = wrong_of(new_doc)
    if new_doc is not None and new_wrong is None:
        new_wrong = (
            wrong_of({"metrics": _child_metrics(accuracy_result)}) if accuracy_result else None
        )
    metrics["accepted_wrong"] = accepted_wrong
    metrics["heldout_wrong"] = new_wrong
    if accepted_wrong is None or new_wrong is None:
        checks.append(
            _check(
                "K9.12",
                "Held-out WRONG against the accepted report",
                "critical",
                "BLOCKED",
                {"accepted_wrong": accepted_wrong, "heldout_wrong": new_wrong},
                "Point upgrade.accepted_report and upgrade.heldout_report at report "
                "JSON files with an integer metrics.wrong. A missing count blocks the upgrade.",
            )
        )
    elif new_wrong > accepted_wrong:
        checks.append(
            _check(
                "K9.12",
                "Held-out WRONG against the accepted report",
                "critical",
                "FAIL",
                {"accepted_wrong": accepted_wrong, "heldout_wrong": new_wrong},
                "Held-out WRONG rose above the accepted report. Do not upgrade. "
                "Roll back with docs/fde/UPGRADE.md.",
            )
        )
    else:
        checks.append(
            _check(
                "K9.12",
                "Held-out WRONG against the accepted report",
                "critical",
                "PASS",
                {"accepted_wrong": accepted_wrong, "heldout_wrong": new_wrong},
                "",
            )
        )
    return checks, metrics


def _write(directory: Path, doc: dict[str, Any], receipts: list[tuple[str, str]]) -> Path:
    moment = datetime.now(UTC)
    dest = directory / "runs" / f"{_run_stamp(moment)}-upgrade"
    if dest.exists():
        dest = directory / "runs" / f"{_run_stamp(moment)}-{os.getpid()}-upgrade"
    dest.mkdir(parents=True)
    if receipts:
        folder = dest / "receipts"
        folder.mkdir()
        for name, text in receipts:
            (folder / name).write_text(text, encoding="utf-8")
            doc["artifacts"].append(f"receipts/{name}")
    text = json.dumps(doc, indent=2) + "\n"
    (dest / "report.json").write_text(text, encoding="utf-8")
    rendered = report_api().render_md(json.loads(text))
    (dest / "report.md").write_text(rendered, encoding="utf-8")
    print(dest)
    return dest


def run(ctx: KitContext) -> dict[str, Any]:
    if ctx.phase not in {"precheck", "verify"}:
        raise ConfigError()
    if not ctx.target:
        raise ConfigError()
    loaded = load_engagement(ctx.engagement_dir)
    secrets = collect_secrets(loaded)
    add_url_secrets(loaded, secrets)
    started = ctx.now or datetime.now(UTC)
    if ctx.phase == "precheck":
        checks, metrics = precheck(ctx, loaded, secrets)
    else:
        checks, metrics = verify(ctx, loaded)
    checks.append(_host_check(ctx.trace, ctx.databases))
    finished = datetime.now(UTC)
    head = resolve_sha(ctx.repo, "HEAD", ctx.trace)
    api = report_api()
    status = api.rollup(checks)
    receipts = _redact_receipts(ctx.engagement_dir, secrets)
    doc = api.document(
        kit=KIT,
        stage=STAGE,
        engagement=ctx.engagement_dir.name,
        tenant_id=loaded.tenant_id,
        dms_sha=short_sha(head) if head else "unknown",
        started_at=_stamp(started),
        finished_at=_stamp(finished),
        flags=_flag_map(loaded.flags_on),
        status=status,
        checks=checks,
        metrics=metrics,
        artifacts=[],
        cost={"ov_calls": 0, "usd": 0.0, "source": "none"},
    )
    doc = scrub_obj(doc, secrets)
    # Re-validate after scrub in case a secret was a status word. Status words
    # are in the keep set, so this is a straight copy check.
    doc = api.document(
        **{
            key: doc[key]
            for key in (
                "kit",
                "stage",
                "engagement",
                "tenant_id",
                "dms_sha",
                "started_at",
                "finished_at",
                "flags",
                "status",
                "checks",
                "metrics",
                "artifacts",
                "cost",
            )
        }
    )
    _write(ctx.engagement_dir, doc, receipts)
    return doc


def exit_for(status: str) -> int:
    if status == "PASS":
        return 0
    if status == "BLOCKED":
        return 3
    if status in {"FAIL", "SKIPPED"}:
        return 1
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fde-upgrade", description="FDE upgrade precheck and verify"
    )
    parser.add_argument("--engagement", required=True)
    parser.add_argument("--phase", required=True, choices=("precheck", "verify"))
    parser.add_argument("--target", default="")
    parser.add_argument("--current", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--watch-ref", action="append", default=[])
    parser.add_argument("--watch-dir", action="append", default=[])
    parser.add_argument("--scan-open-prs", action="store_true")
    parser.add_argument("--repo-slug", default="Netie-AI/dms")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if not kit_enabled():
        print("fde_kit_disabled", file=sys.stderr)
        return 2
    ctx = KitContext(
        engagement_dir=Path(args.engagement),
        phase=args.phase,
        target=args.target,
        current=args.current,
        repo=Path(args.repo) if args.repo else ROOT,
        watch_refs=tuple(args.watch_ref),
        watch_dirs=tuple(args.watch_dir),
        scan_open_prs=bool(args.scan_open_prs),
        repo_slug=args.repo_slug,
    )
    try:
        doc = run(ctx)
    except ConfigError:
        print("fde_kit_config", file=sys.stderr)
        return 2
    return exit_for(doc["status"])


if __name__ == "__main__":
    sys.exit(main())
