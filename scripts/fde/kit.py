"""FDE field kit launcher.

    python scripts/fde/kit.py init   --engagement acme/
    python scripts/fde/kit.py list
    python scripts/fde/kit.py run    --engagement acme/ --stage all

``DMS_FDE_KIT`` must be the digit ``1``. Any other value, including unset,
``true``, ``01``, and `` 1 ``, refuses every verb except ``list`` with exit 2
and writes nothing. ``list`` works with the flag off.

Exit codes: 0 every required check PASS, 1 any FAIL or a run that is only
SKIPPED, 2 config or the flag refusal, 3 BLOCKED (a person still has to act).
A check that cannot run is SKIPPED. Nothing defaults to PASS.

Model routing stays in OpenVault. This launcher does not name a model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_FDE = Path(__file__).resolve().parent
if str(_FDE) not in sys.path:
    sys.path.insert(0, str(_FDE))

import fde_pipeline as fp  # noqa: E402
import report  # noqa: E402
from kits import KitSpec, get, ordered  # noqa: E402

FLAG = "DMS_FDE_KIT"
EXIT_PASS, EXIT_FAIL, EXIT_CONFIG, EXIT_BLOCKED = 0, 1, 2, 3
TEMPLATES = _FDE / "templates"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

# CLI verbs from the field-kit plan. Registered kits replace the "not registered" row.
VERBS = (
    "survey",
    "connect",
    "ontology",
    "scan",
    "accuracy",
    "govern",
    "budget",
    "inspect",
    "collect",
    "upgrade",
    "accept",
    "pitch",
)
VERB_STAGE = {
    "survey": "K1",
    "connect": "K2",
    "ontology": "K3",
    "scan": "K4",
    "accuracy": "K5",
    "govern": "K6",
    "budget": "K7",
    "inspect": "K8",
    "collect": "K8",
    "upgrade": "K9",
    "accept": "K10",
    "pitch": "K11",
}


@dataclass
class Ctx:
    engagement_dir: Path
    engagement: dict[str, Any]
    tenant: Any
    work: Path
    run_dir: Path
    flags: dict[str, str]
    dms_sha: str
    started_at: str
    state: dict[str, Any] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)
    max_questions: int = 25
    expect_keys: bool = False
    kit_name: str = ""
    kit_stage: str = ""

    @property
    def engagement_name(self) -> str:
        name = str(self.engagement.get("name") or "").strip()
        return name or self.engagement_dir.name

    @property
    def tenant_id(self) -> str:
        return str(self.engagement.get("tenant_id") or "")


def flag_on(environ: dict[str, str] | None = None) -> bool:
    """True only when the value is the single digit 1. Spaces do not count."""
    env = os.environ if environ is None else environ
    return env.get(FLAG) == "1"


def _refuse() -> int:
    print("fde_kit_disabled", file=sys.stderr)
    return EXIT_CONFIG


def _safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", name).strip("._")
    return (cleaned or "tenant")[:80]


def _password_key(data: Any, where: str) -> str | None:
    if isinstance(data, dict):
        if "password" in data and str(data.get("password") or "").strip():
            return where
        for key, value in data.items():
            hit = _password_key(value, f"{where}.{key}")
            if hit:
                return hit
    elif isinstance(data, list):
        for i, value in enumerate(data):
            hit = _password_key(value, f"{where}[{i}]")
            if hit:
                return hit
    return None


def _password_env_names(engagement: dict[str, Any], tenant: Any) -> list[str]:
    names: list[str] = []
    for src in engagement.get("sources") or []:
        if isinstance(src, dict) and src.get("password_env"):
            names.append(str(src["password_env"]))
    if tenant is not None and tenant.source.get("password_env"):
        names.append(str(tenant.source["password_env"]))
    return names


def flag_snapshot(engagement: dict[str, Any]) -> dict[str, str]:
    names = [FLAG]
    for name in engagement.get("flags_on") or []:
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return {name: "1" if os.environ.get(name) == "1" else "off" for name in names}


def load_engagement(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: engagement file must be a mapping")
    hit = _password_key(data, str(path))
    if hit:
        raise SystemExit(f"{hit}: put the password in an env var and name it in password_env")
    return data


def resolve_tenants(engagement: dict[str, Any], folder: Path) -> list[Any]:
    files = engagement.get("tenant_files") or []
    if files:
        specs = []
        for item in files:
            path = Path(str(item))
            if not path.is_absolute():
                path = folder / path
            specs.append(fp.TenantSpec.load(path))
        return specs
    specs = []
    name = str(engagement.get("name") or folder.name)
    for i, src in enumerate(engagement.get("sources") or []):
        if not isinstance(src, dict):
            continue
        kind = str(src.get("kind") or "").strip()
        database = str(src.get("database") or "").strip()
        if not kind or not database:
            continue
        source = {k: v for k, v in src.items() if k != "name"}
        specs.append(
            fp.TenantSpec(
                tenant=str(src.get("name") or f"{name}-{i}"),
                source=source,
                glossary={str(k): str(v) for k, v in (engagement.get("glossary") or {}).items()},
                generated=src.get("generated") or engagement.get("generated"),
            )
        )
    return specs


def invoke(spec: KitSpec, ctx: Ctx) -> dict[str, Any]:
    """Run one kit. An exception becomes a FAIL check and does not crash the process."""
    ctx.kit_name, ctx.kit_stage = spec.name, spec.stage
    try:
        produced = spec.run(ctx)
    except (Exception, SystemExit) as exc:
        produced = report.new_report(
            kit=spec.name,
            stage=spec.stage,
            engagement=ctx.engagement_name,
            tenant_id=ctx.tenant_id,
            dms_sha_value=ctx.dms_sha,
            flags=ctx.flags,
            started_at=ctx.started_at,
        )
        report.add_check(
            produced,
            report.make_check(
                id=f"{spec.stage}.EXC",
                title=f"{spec.name} raised",
                severity="high",
                status="FAIL",
                evidence={"exception": type(exc).__name__},
                fix=(
                    f"The kit raised {type(exc).__name__}. "
                    "The message is not stored. Rerun after the cause is fixed."
                ),
                source=f"scripts/fde/kits/{spec.name}.py",
            ),
        )
    if not isinstance(produced, dict):
        produced = report.new_report(
            kit=spec.name,
            stage=spec.stage,
            engagement=ctx.engagement_name,
            tenant_id=ctx.tenant_id,
            dms_sha_value=ctx.dms_sha,
            flags=ctx.flags,
            started_at=ctx.started_at,
        )
    return report.finish(produced)


def guard(spec: KitSpec, ctx: Ctx) -> dict[str, Any]:
    """A product flag that is not the digit 1 is SKIPPED and the kit is not called."""
    flag = spec.requires_flag
    if flag and os.environ.get(flag) != "1":
        reason = f"SKIPPED: {flag} off"
        rep = report.new_report(
            kit=spec.name,
            stage=spec.stage,
            engagement=ctx.engagement_name,
            tenant_id=ctx.tenant_id,
            dms_sha_value=ctx.dms_sha,
            flags=ctx.flags,
            started_at=ctx.started_at,
        )
        report.add_check(
            rep,
            report.make_check(
                id=f"{spec.stage}.00",
                title=reason,
                severity="info",
                status="SKIPPED",
                evidence={"reason": reason},
                fix=f"Set {flag} to the digit 1 to run this kit.",
                source="scripts/fde/kit.py",
            ),
        )
        return report.finish(rep)
    return invoke(spec, ctx)


def _select(stage: str) -> list[KitSpec] | None:
    kits = ordered()
    if stage.strip() == "all":
        return kits
    want = {part.strip() for part in stage.split(",") if part.strip()}
    known = {kit.name for kit in kits} | {kit.stage for kit in kits}
    if not want or not want <= known:
        return None
    return [kit for kit in kits if kit.name in want or kit.stage in want]


def _apply_only(kits: list[KitSpec], only: str) -> list[KitSpec] | None:
    """Keep kits named by ``--only``. None means a name matched nothing."""
    if not str(only or "").strip():
        return kits
    want = {part.strip() for part in str(only).split(",") if part.strip()}
    chosen = []
    for spec in kits:
        ids = {item[0] for item in spec.catalogue}
        if spec.name in want or spec.stage in want or ids & want:
            chosen.append(spec)
    if not chosen:
        return None
    return chosen


def _align_eval(ctx: Ctx, summary: dict[str, Any]) -> None:
    """Call the pipeline eval so the summary status matches ``fde_pipeline``."""
    ext = ctx.state.get("extract")
    prof = ctx.state.get("profile")
    onto = ctx.state.get("ontology")
    pack = ctx.state.get("pack")
    summary["metrics"]["stages"] = {
        "extract": (ext or {}).get("status"),
        "profile": (prof or {}).get("status"),
        "ontology": (onto or {}).get("status"),
        "pack": (pack or {}).get("status"),
    }
    if not all((ext, prof, onto, pack)):
        summary["metrics"]["pipeline_status"] = "ERROR"
        summary["metrics"]["pipeline_failures"] = ["stage did not finish"]
        report.add_check(
            summary,
            report.make_check(
                id="P5.01",
                title="Pipeline eval",
                severity="high",
                status="FAIL",
                evidence={"exception": "stage did not finish"},
                fix="Rerun after the failed stage is fixed. The exception class is on that check.",
                source="scripts/fde/fde_pipeline.py:stage_eval_offline",
            ),
        )
        return
    try:
        ev = fp.stage_eval_offline(ctx.tenant, ext, onto, pack, prof, ctx.work)
    except (Exception, SystemExit) as exc:
        summary["metrics"]["pipeline_status"] = "ERROR"
        summary["metrics"]["pipeline_failures"] = [type(exc).__name__]
        report.add_check(
            summary,
            report.make_check(
                id="P5.01",
                title="Pipeline eval",
                severity="high",
                status="FAIL",
                evidence={"exception": type(exc).__name__},
                fix="The eval stage raised. The message is not stored.",
                source="scripts/fde/fde_pipeline.py:stage_eval_offline",
            ),
        )
        return
    ev["pack_status"] = pack.get("status")
    failures = fp.eval_failures(ev, expect_keys=ctx.expect_keys)
    summary["metrics"]["pipeline_status"] = "OK"
    summary["metrics"]["pipeline_failures"] = failures
    fidelity = ev.get("fidelity") or {}
    kept = ("status", "why", "gold_ok", "mismatch")
    summary["metrics"]["fidelity"] = {key: fidelity.get(key) for key in kept if key in fidelity}
    if failures:
        report.add_check(
            summary,
            report.make_check(
                id="P5.01",
                title="Pipeline eval",
                severity="critical",
                status="FAIL",
                evidence={"failures": failures},
                fix="The pipeline eval reported failures. Fix those before calling the run done.",
                source="scripts/fde/fde_pipeline.py:eval_failures",
            ),
        )


def _run_tenant(ctx: Ctx, kits: list[KitSpec]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for spec in kits:
        part = guard(spec, ctx)
        report.write_report(ctx.run_dir / spec.name, part, ctx.secrets)
        parts.append(part)
    summary = report.new_report(
        kit="all" if len(kits) != 1 else kits[0].name,
        stage="all" if len(kits) != 1 else kits[0].stage,
        engagement=ctx.engagement_name,
        tenant_id=ctx.tenant_id,
        dms_sha_value=ctx.dms_sha,
        flags=ctx.flags,
        started_at=ctx.started_at,
    )
    for part in parts:
        for check in part.get("checks") or []:
            summary["checks"].append(check)
    names = {spec.name for spec in kits}
    if {"extract", "profile", "ontology", "pack"} <= names:
        _align_eval(ctx, summary)
    summary["artifacts"] = report.ingest_receipts(
        ctx.engagement_dir / "receipts", ctx.run_dir / "receipts", ctx.secrets
    )
    report.finish(summary)
    report.write_report(ctx.run_dir, summary, ctx.secrets)
    report.scrub_tree(ctx.run_dir, ctx.secrets)
    # Scrub rewrites the JSON. Re-read so the printed status matches the file.
    import json

    return json.loads((ctx.run_dir / "report.json").read_text(encoding="utf-8"))


def exit_for(statuses: list[str]) -> int:
    if not statuses:
        return EXIT_FAIL
    if any(s == "FAIL" for s in statuses):
        return EXIT_FAIL
    if any(s == "BLOCKED" for s in statuses):
        return EXIT_BLOCKED
    if all(s == "PASS" for s in statuses):
        return EXIT_PASS
    return EXIT_FAIL


def cmd_list() -> int:
    print(f"{'stage':<6} {'kit':<12} {'severity':<10} {'id':<8} title")
    seen = set()
    for spec in ordered():
        seen.add(spec.name)
        if not spec.catalogue:
            print(f"{spec.stage:<6} {spec.name:<12} {'':<10} {'':<8} (no checks)")
            continue
        for check_id, title, severity in spec.catalogue:
            print(f"{spec.stage:<6} {spec.name:<12} {severity:<10} {check_id:<8} {title}")
    for verb in VERBS:
        if verb in seen:
            continue
        print(f"{VERB_STAGE[verb]:<6} {verb:<12} {'':<10} {'':<8} not registered")
    return EXIT_PASS


def cmd_init(dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    eng_path = dest / "engagement.yaml"
    if not eng_path.exists():
        raw = (TEMPLATES / "engagement.yaml").read_text(encoding="utf-8")
        name = json.dumps(dest.name)
        raw = raw.replace('name: ""', f"name: {name}", 1)
        eng_path.write_text(raw, encoding="utf-8")
        print(f"[INIT] {eng_path}")
    intake = dest / "intake.md"
    if not intake.exists():
        intake.write_text((TEMPLATES / "intake.md").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[INIT] {intake}")
    return EXIT_PASS


def _blocked_apply(folder: Path, started: str) -> int:
    """``govern --apply`` with no founder approval ref. Writes a BLOCKED report only."""
    engagement = load_engagement(folder / "engagement.yaml")
    secrets = report.secret_values(_password_env_names(engagement, None))
    rep = report.new_report(
        kit="govern",
        stage="K6",
        engagement=str(engagement.get("name") or folder.name),
        tenant_id=str(engagement.get("tenant_id") or ""),
        flags=flag_snapshot(engagement),
        started_at=started,
    )
    report.add_check(
        rep,
        report.make_check(
            id="K6.00",
            title="Apply needs a founder approval ref",
            severity="critical",
            status="BLOCKED",
            evidence={"reason": "approval_ref missing"},
            fix="Pass --approval-ref with the founder approval, or run without --apply.",
            source="scripts/fde/kit.py",
        ),
    )
    report.finish(rep)
    run_dir = folder / "runs" / f"{_stamp()}-govern"
    report.write_report(run_dir, rep, secrets)
    report.scrub_tree(run_dir, secrets)
    print(f"[BLOCKED] {run_dir}")
    return EXIT_BLOCKED


def _stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_dir(folder: Path, label: str) -> Path:
    stamp = _stamp()
    dest = folder / "runs" / f"{stamp}-{_safe_name(label)}"
    if dest.exists():
        dest = folder / "runs" / f"{stamp}-{os.getpid()}-{_safe_name(label)}"
    return dest


def cmd_run(folder: Path, args: argparse.Namespace) -> int:
    engagement = load_engagement(folder / "engagement.yaml")
    kits = _select(args.stage)
    if kits is None:
        print(f"[CONFIG] unknown stage {args.stage}", file=sys.stderr)
        return EXIT_CONFIG
    if not kits:
        print("[CONFIG] no kits registered", file=sys.stderr)
        return EXIT_CONFIG
    kits = _apply_only(kits, getattr(args, "only", "") or "")
    if kits is None:
        print("[CONFIG] --only matched no kit", file=sys.stderr)
        return EXIT_CONFIG
    try:
        tenants = resolve_tenants(engagement, folder)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG
    started = report.utc_now()
    sha = report.dms_sha()
    flags = flag_snapshot(engagement)
    if not tenants:
        secrets = report.secret_values(_password_env_names(engagement, None))
        rep = report.new_report(
            kit="all",
            stage="all",
            engagement=str(engagement.get("name") or folder.name),
            tenant_id=str(engagement.get("tenant_id") or ""),
            dms_sha_value=sha,
            flags=flags,
            started_at=started,
        )
        report.add_check(
            rep,
            report.make_check(
                id="P0.01",
                title="Source configured",
                severity="high",
                status="SKIPPED",
                evidence={"reason": "no source configured"},
                fix="Set tenant_files or a source kind and database in engagement.yaml.",
                source="scripts/fde/kit.py",
            ),
        )
        report.finish(rep)
        run_dir = _run_dir(folder, "all")
        report.write_report(run_dir, rep, secrets)
        print(f"[SKIPPED] {run_dir}")
        return exit_for([rep["status"]])
    statuses = []
    for tenant in tenants:
        secrets = report.secret_values(_password_env_names(engagement, tenant))
        run_dir = _run_dir(folder, tenant.tenant)
        ctx = Ctx(
            engagement_dir=folder,
            engagement=engagement,
            tenant=tenant,
            work=run_dir / "work",
            run_dir=run_dir,
            flags=flags,
            dms_sha=sha,
            started_at=started,
            secrets=secrets,
            max_questions=int(getattr(args, "max_questions", 25) or 25),
            expect_keys=bool(getattr(args, "expect_keys", False)),
        )
        ctx.work.mkdir(parents=True, exist_ok=True)
        rep = _run_tenant(ctx, kits)
        statuses.append(str(rep.get("status")))
        print(f"[{rep.get('status')}] {tenant.tenant} {run_dir}")
    return exit_for(statuses)


def cmd_verb(verb: str, folder: Path, args: argparse.Namespace) -> int:
    spec = get(verb)
    started = report.utc_now()
    if verb == "govern" and getattr(args, "apply", False):
        if not str(getattr(args, "approval_ref", "") or "").strip():
            return _blocked_apply(folder, started)
    if spec is None:
        engagement = load_engagement(folder / "engagement.yaml")
        secrets = report.secret_values(_password_env_names(engagement, None))
        stage = VERB_STAGE.get(verb, verb)
        rep = report.new_report(
            kit=verb,
            stage=stage,
            engagement=str(engagement.get("name") or folder.name),
            tenant_id=str(engagement.get("tenant_id") or ""),
            flags=flag_snapshot(engagement),
            started_at=started,
        )
        report.add_check(
            rep,
            report.make_check(
                id=f"{stage}.00",
                title=f"{verb} is not registered",
                severity="info",
                status="SKIPPED",
                evidence={"reason": "kit module not registered"},
                fix=f"Add scripts/fde/kits/{verb}.py and register it.",
                source="scripts/fde/kit.py",
            ),
        )
        report.finish(rep)
        run_dir = _run_dir(folder, verb)
        report.write_report(run_dir, rep, secrets)
        report.scrub_tree(run_dir, secrets)
        print(f"[SKIPPED] {run_dir}")
        return exit_for([rep["status"]])
    ns = argparse.Namespace(
        stage=spec.name,
        expect_keys=False,
        max_questions=25,
    )
    return cmd_run(folder, ns)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="kits and check items; works with the flag off")
    init = sub.add_parser("init", help="write engagement.yaml and intake.md")
    init.add_argument("--engagement", required=True)
    run = sub.add_parser("run", help="run registered kits")
    run.add_argument("--engagement", required=True)
    run.add_argument("--stage", default="all")
    run.add_argument("--expect-keys", action="store_true")
    run.add_argument("--max-questions", type=int, default=25)
    run.add_argument("--only", default="")
    for verb in VERBS:
        parser = sub.add_parser(verb, help=f"{verb} kit")
        parser.add_argument("--engagement", required=True)
        parser.add_argument("--only", default="")
        if verb == "govern":
            parser.add_argument("--dry-run", action="store_true")
            parser.add_argument("--apply", action="store_true")
            parser.add_argument("--approval-ref", default="")
        if verb == "upgrade":
            parser.add_argument("--phase", choices=("precheck", "verify"))
            parser.add_argument("--target", default="")
        if verb == "accuracy":
            parser.add_argument("--split", default="")
        if verb == "pitch":
            parser.add_argument("--storyline", default="")
    return ap


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "list":
        return cmd_list()
    if not flag_on():
        return _refuse()
    folder = Path(args.engagement)
    if args.cmd == "init":
        return cmd_init(folder)
    if not folder.is_dir() or not (folder / "engagement.yaml").is_file():
        print(f"[CONFIG] {folder} has no engagement.yaml", file=sys.stderr)
        return EXIT_CONFIG
    if args.cmd == "run":
        return cmd_run(folder, args)
    return cmd_verb(args.cmd, folder, args)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except SystemExit as exc:
        # Config refusals raise SystemExit with a string. argparse uses an int.
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return EXIT_CONFIG
        raise


if __name__ == "__main__":
    raise SystemExit(main())
