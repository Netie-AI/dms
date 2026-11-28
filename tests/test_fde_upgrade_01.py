"""FDE-UPGRADE-01. Precheck and verify write a report and do not migrate a customer database.

The live scratch round-trip is opt-in (`DMS_FDE_UPGRADE_LIVE=1`). CI runs the
rest of this file with the suite.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPGRADE_PATH = ROOT / "scripts" / "fde" / "kits" / "upgrade.py"
PASSWORD = "planted-db-password-9f3a7c"
API_KEY = "planted-api-key-9f3a7c"
SCRATCH_PASS = "planted-scratch-pass-9f3a7c"
SCRATCH_URL = f"postgresql://user:{SCRATCH_PASS}@127.0.0.1:5432/fde_upgrade_scratch"

GOOD = """\
revision = "0001_base"
down_revision = None

def upgrade():
    value = 1

def downgrade():
    value = 1
"""

EMPTY_DOWN = """\
revision = "0002_planted"
down_revision = "0001_base"

def upgrade():
    value = 1

def downgrade():
    pass
"""

DUP_A = """\
revision = "0002_same"
down_revision = "0001_base"

def upgrade():
    value = 1

def downgrade():
    value = 1
"""

DUP_B = """\
revision = "0002_same"
down_revision = "0001_base"

def upgrade():
    value = 2

def downgrade():
    value = 2
"""

REV_0010 = """\
revision = "0010_connections"
down_revision = "0009_action_writeback"

def upgrade():
    value = 1

def downgrade():
    value = 1
"""

REV_0009 = """\
revision = "0009_action_writeback"
down_revision = None

def upgrade():
    value = 1

def downgrade():
    value = 1
"""

REV_ONBOARD = """\
revision = "0010_onboard_item_kinds"
down_revision = "0009_action_writeback"

def upgrade():
    value = 1

def downgrade():
    value = 1
"""

NEXT_REV = """\
revision = "0002_next"
down_revision = "0001_base"

def upgrade():
    value = 1

def downgrade():
    value = 1
"""


def _load():
    spec = importlib.util.spec_from_file_location("fde_upgrade_01", UPGRADE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fde_upgrade_01"] = module
    spec.loader.exec_module(module)
    return module


upgrade = _load()


def _git(cwd: Path, *args: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "fde"
    env["GIT_AUTHOR_EMAIL"] = "fde@example.com"
    env["GIT_COMMITTER_NAME"] = "fde"
    env["GIT_COMMITTER_EMAIL"] = "fde@example.com"
    subprocess.check_call(["git", *args], cwd=cwd, env=env, stdout=subprocess.DEVNULL)


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    _write(repo, files)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _init(repo: Path, files: dict[str, str]) -> str:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    return _commit(repo, files, "init")


def _engagement(path: Path, body: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "engagement.yaml").write_text(body, encoding="utf-8")


def _base_yaml(**extra: str) -> str:
    lines = [
        "tenant_id: acme",
        "flags_on: []",
        "sources:",
        "  - kind: postgresql",
        "    host: db.example",
        "    port: 5432",
        "    database: acme",
        "    user: fde_ro",
        "    password_env: ACME_DB_PASSWORD",
        "upgrade:",
        "  backup_dump: backups/acme.dump",
        "  warehouse_copy: backups/acme.duckdb",
    ]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def _report(eng: Path) -> tuple[Path, dict]:
    runs = sorted((eng / "runs").glob("*-upgrade"))
    assert len(runs) == 1
    doc = json.loads((runs[0] / "report.json").read_text(encoding="utf-8"))
    return runs[0], doc


def _by_id(doc: dict, cid: str) -> dict:
    for check in doc["checks"]:
        if check["id"] == cid:
            return check
    raise AssertionError(cid)


def _ok_executor(plan, *, url, secrets, sources, trace, databases):
    assert urlsplit(url).path.endswith("/fde_upgrade_scratch")
    assert urlsplit(url).hostname == "127.0.0.1"
    databases.append(upgrade.database_name(url))
    out = []
    for step in plan:
        assert upgrade.argv_allowed(step["argv"])
        out.append(
            {
                "code": 0,
                "stdout": "",
                "stderr": "",
                "kind": step["kind"],
                "database": upgrade.database_name(url) if step["kind"] == "alembic" else "",
                "cleanup": bool(step.get("cleanup")),
            }
        )
    return out


def _backups(eng: Path) -> None:
    folder = eng / "backups"
    folder.mkdir(parents=True)
    (folder / "acme.dump").write_bytes(b"PGDMP")
    (folder / "acme.duckdb").write_bytes(b"duck")


def _run(monkeypatch, eng: Path, repo: Path, *args: str, executor=None) -> int:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    ctx_repo = repo
    argv = ["--engagement", str(eng), "--repo", str(ctx_repo), *args]
    if executor is None:
        return upgrade.main(argv)
    # main() builds the context. A scratch success path needs the double, so
    # call run() with the same flags main() would.
    parsed = upgrade._parser().parse_args(argv)
    ctx = upgrade.KitContext(
        engagement_dir=Path(parsed.engagement),
        phase=parsed.phase,
        target=parsed.target,
        current=parsed.current,
        repo=Path(parsed.repo),
        watch_refs=tuple(parsed.watch_ref),
        watch_dirs=tuple(parsed.watch_dir),
        scan_open_prs=bool(parsed.scan_open_prs),
        executor=executor,
    )
    doc = upgrade.run(ctx)
    return upgrade.exit_for(doc["status"])


@pytest.mark.parametrize("raw", ["", "0", "true", "01", " 1 ", "1 ", "1\n", "yes"])
def test_flag_off_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    if raw == "":
        monkeypatch.delenv("DMS_FDE_KIT", raising=False)
    else:
        monkeypatch.setenv("DMS_FDE_KIT", raw)
    code = upgrade.main(["--engagement", str(eng), "--phase", "precheck", "--target", "deadbeef"])
    assert code == 2
    assert not (eng / "runs").exists()


def test_missing_target_and_password_key_write_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    assert upgrade.main(["--engagement", str(eng), "--phase", "precheck"]) == 2
    assert not (eng / "runs").exists()
    _engagement(eng, "tenant_id: acme\nsources:\n  - password: nope\n")
    code = upgrade.main(["--engagement", str(eng), "--phase", "precheck", "--target", "HEAD"])
    assert code == 2
    assert not (eng / "runs").exists()


def test_empty_downgrade_is_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    first = _init(
        repo,
        {
            "alembic/versions/0001_base.py": GOOD,
            "flags.py": "import os\nos.environ.get('DMS_KEEP')\n",
        },
    )
    second = _commit(repo, {"alembic/versions/0002_planted.py": EMPTY_DOWN}, "bad")
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    code = _run(
        monkeypatch,
        eng,
        repo,
        "--phase",
        "precheck",
        "--current",
        first,
        "--target",
        second,
    )
    run, doc = _report(eng)
    assert code == 1
    assert doc["status"] == "FAIL"
    assert doc["schema"] == "netie.fde.report/1"
    bad = _by_id(doc, "K9.03")
    assert bad["status"] == "FAIL"
    assert "0002_planted" in json.dumps(bad["evidence"])
    assert "not a working downgrade" in (run / "report.md").read_text(encoding="utf-8")
    assert (run / "report.md").read_text(encoding="utf-8") == upgrade.report_api().render_md(doc)


def test_duplicate_revision_is_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    first = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    second = _commit(
        repo,
        {
            "alembic/versions/0002_a.py": DUP_A,
            "alembic/versions/0002_b.py": DUP_B,
        },
        "dup",
    )
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    _run(monkeypatch, eng, repo, "--phase", "precheck", "--current", first, "--target", second)
    _, doc = _report(eng)
    dup = _by_id(doc, "K9.05")
    assert dup["status"] == "FAIL"
    assert "0002_same" in json.dumps(dup["evidence"])
    assert doc["status"] == "FAIL"


def test_revision_number_clash_across_a_watched_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(
        repo,
        {
            "alembic/versions/0009_action_writeback.py": REV_0009,
            "alembic/versions/0010_connections.py": REV_0010,
        },
    )
    watch = tmp_path / "pr543" / "alembic" / "versions"
    watch.mkdir(parents=True)
    (watch / "0010_onboard_item_kinds.py").write_text(REV_ONBOARD, encoding="utf-8")
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    _run(
        monkeypatch,
        eng,
        repo,
        "--phase",
        "precheck",
        "--current",
        sha,
        "--target",
        sha,
        "--watch-dir",
        str(watch.parent.parent),
    )
    _, doc = _report(eng)
    clash = _by_id(doc, "K9.09")
    assert clash["status"] == "FAIL"
    blob = json.dumps(clash["evidence"])
    assert "0010_connections" in blob
    assert "0010_onboard_item_kinds" in blob
    assert doc["status"] == "FAIL"


def test_open_pr_scan_not_requested_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    code = _run(monkeypatch, eng, repo, "--phase", "precheck", "--current", sha, "--target", sha)
    _, doc = _report(eng)
    assert _by_id(doc, "K9.09")["status"] == "SKIPPED"
    assert doc["status"] != "PASS"
    assert code == 1


def test_flag_removed_fails_and_a_declared_rename_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    first = _init(
        repo,
        {
            "alembic/versions/0001_base.py": GOOD,
            "flags.py": "import os\nos.environ.get('DMS_OLD')\nos.environ.get('DMS_KEEP')\n",
        },
    )
    second = _commit(
        repo,
        {"flags.py": "import os\nos.environ.get('DMS_NEW')\nos.environ.get('DMS_KEEP')\n"},
        "rename",
    )
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    _run(monkeypatch, eng, repo, "--phase", "precheck", "--current", first, "--target", second)
    _, doc = _report(eng)
    flags = _by_id(doc, "K9.06")
    assert flags["status"] == "FAIL"
    assert flags["evidence"]["removed"] == ["DMS_OLD"]
    assert "DMS_NEW" in flags["evidence"]["added"]

    eng2 = tmp_path / "acme2"
    _engagement(
        eng2,
        "tenant_id: acme\nflags_on: []\nupgrade:\n"
        "  flag_renames:\n    - {from: DMS_OLD, to: DMS_NEW}\n",
    )
    _backups(eng2)
    _run(monkeypatch, eng2, repo, "--phase", "precheck", "--current", first, "--target", second)
    _, renamed = _report(eng2)
    ok = _by_id(renamed, "K9.06")
    assert ok["status"] == "PASS"
    assert ok["evidence"]["renamed"] == [{"from": "DMS_OLD", "to": "DMS_NEW"}]
    assert ok["evidence"]["removed"] == []


def test_backup_missing_or_stale_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _run(monkeypatch, eng, repo, "--phase", "precheck", "--current", sha, "--target", sha)
    _, doc = _report(eng)
    assert _by_id(doc, "K9.07")["status"] == "FAIL"
    assert "pg_dump" in _by_id(doc, "K9.07")["evidence"]["missing"]

    _backups(eng)
    old = eng / "backups" / "acme.dump"
    stamp = old.stat().st_mtime - (48 * 3600)
    os.utime(old, (stamp, stamp))
    eng2 = tmp_path / "acme-stale"
    _engagement(eng2, _base_yaml())
    folder = eng2 / "backups"
    folder.mkdir()
    dump = folder / "acme.dump"
    warehouse = folder / "acme.duckdb"
    dump.write_bytes(b"PGDMP")
    warehouse.write_bytes(b"duck")
    os.utime(dump, (stamp, stamp))
    _run(monkeypatch, eng2, repo, "--phase", "precheck", "--current", sha, "--target", sha)
    _, stale = _report(eng2)
    assert _by_id(stale, "K9.07")["status"] == "FAIL"


def test_customer_url_never_shells_and_plan_is_scratch_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def boom(argv, **_kwargs):
        calls.append(list(argv))
        raise AssertionError(argv)

    monkeypatch.setattr(upgrade.subprocess, "run", boom)
    with pytest.raises(upgrade.CommandRefused) as caught:
        upgrade.execute_plan(
            [
                {
                    "kind": "alembic",
                    "argv": [
                        sys.executable,
                        "-m",
                        "alembic",
                        "-c",
                        "alembic.ini",
                        "upgrade",
                        "head",
                    ],
                    "cwd": str(tmp_path),
                    "expect": "",
                    "cleanup": False,
                }
            ],
            url="postgresql://user:secret@10.1.1.9:5432/customers",
            secrets=[],
            sources=[{"database": "customers"}],
            trace=[],
            databases=[],
        )
    assert caught.value.code == "not_loopback"
    assert calls == []

    with pytest.raises(upgrade.CommandRefused) as refused:
        upgrade.execute_plan(
            [
                {
                    "kind": "alembic",
                    "argv": ["helm", "upgrade", "dms"],
                    "cwd": str(tmp_path),
                    "expect": "",
                    "cleanup": False,
                }
            ],
            url=SCRATCH_URL,
            secrets=[SCRATCH_PASS],
            sources=[],
            trace=[],
            databases=[],
        )
    assert refused.value.code == "refused_command"
    assert calls == []
    assert (
        upgrade.scratch_refusal("postgresql://u:p@127.0.0.1:8090/fde_upgrade_scratch", [])
        == "port_8090"
    )
    assert (
        upgrade.scratch_refusal(SCRATCH_URL, [{"database": "fde_upgrade_scratch"}])
        == "matches_engagement_source"
    )


def test_execute_plan_puts_only_the_scratch_url_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD, "alembic.ini": "x\n"})
    seen: list[tuple[list[str], str]] = []

    def fake_run(argv, **kwargs):
        env = kwargs.get("env") or {}
        seen.append((list(argv), env.get("DATABASE_URL", "")))

        class Result:
            returncode = 0
            stdout = ""
            stderr = env.get("DATABASE_URL", "")

        return Result()

    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)
    plan = upgrade.build_roundtrip_plan(repo, sha, sha, "0001_base", tmp_path / "wt")
    for step in plan:
        assert upgrade.argv_allowed(step["argv"])
        assert "helm" not in step["argv"]
        assert "docker" not in step["argv"]
        assert "systemctl" not in step["argv"]
    results = upgrade.execute_plan_marked(
        plan,
        url=SCRATCH_URL,
        secrets=[SCRATCH_PASS, SCRATCH_URL],
        sources=[],
        trace=[],
        databases=[],
    )
    assert seen
    for argv, env_url in seen:
        assert upgrade.argv_allowed(argv)
        if "alembic" in argv:
            assert env_url.endswith("/fde_upgrade_scratch")
            assert "customers" not in env_url
    blob = json.dumps(results)
    assert SCRATCH_PASS not in blob
    assert SCRATCH_URL not in blob


def test_clean_precheck_can_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    sha = _init(
        repo,
        {
            "alembic/versions/0001_base.py": GOOD,
            "flags.py": "import os\nos.environ.get('DMS_KEEP')\n",
        },
    )
    watch = tmp_path / "next"
    _write(watch, {"alembic/versions/0002_next.py": NEXT_REV})
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    monkeypatch.setenv("FDE_UPGRADE_SCRATCH_URL", SCRATCH_URL)
    code = _run(
        monkeypatch,
        eng,
        repo,
        "--phase",
        "precheck",
        "--current",
        sha,
        "--target",
        sha,
        "--watch-dir",
        str(watch),
        executor=_ok_executor,
    )
    run, doc = _report(eng)
    failed = [c["id"] for c in doc["checks"] if c["status"] != "PASS"]
    assert code == 0 and doc["status"] == "PASS", failed
    assert doc["kit"] == "upgrade"
    assert doc["stage"] == "K9"
    assert doc["flags"]["DMS_FDE_KIT"] == "1"
    assert doc["cost"] == {"ov_calls": 0, "usd": 0.0, "source": "none"}
    assert [key for key in doc if key] == list(upgrade.report_api().DOCUMENT_KEYS)
    assert _by_id(doc, "K9.04")["status"] == "PASS"
    assert _by_id(doc, "K9.04")["evidence"]["database"] == "fde_upgrade_scratch"
    stacked = json.dumps(_by_id(doc, "K9.09")["evidence"]["stacked"])
    assert "0002_next" in stacked
    text = (run / "report.json").read_text(encoding="utf-8")
    assert SCRATCH_PASS not in text
    assert SCRATCH_URL not in text
    assert (run / "report.md").read_text(encoding="utf-8") == upgrade.report_api().render_md(doc)


def test_secrets_do_not_land_in_the_run_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    receipts = eng / "receipts"
    receipts.mkdir()
    (receipts / "connect.json").write_text(
        json.dumps({"api_key": API_KEY, "note": f"see {API_KEY} and {PASSWORD}"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ACME_DB_PASSWORD", PASSWORD)
    _run(monkeypatch, eng, repo, "--phase", "precheck", "--current", sha, "--target", sha)
    run, _doc = _report(eng)
    for path in run.rglob("*"):
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        assert PASSWORD not in body
        assert API_KEY not in body
        assert SCRATCH_PASS not in body
        assert SCRATCH_URL not in body


def test_verify_fails_when_heldout_wrong_rises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(
        eng,
        "tenant_id: acme\nflags_on: []\nupgrade:\n"
        "  accepted_report: accepted.json\n"
        "  heldout_report: heldout.json\n",
    )
    (eng / "accepted.json").write_text(json.dumps({"metrics": {"wrong": 0}}), encoding="utf-8")
    (eng / "heldout.json").write_text(json.dumps({"metrics": {"wrong": 1}}), encoding="utf-8")
    code = _run(monkeypatch, eng, repo, "--phase", "verify", "--target", sha)
    _, doc = _report(eng)
    assert code == 1
    assert doc["status"] == "FAIL"
    wrong = _by_id(doc, "K9.12")
    assert wrong["status"] == "FAIL"
    assert wrong["evidence"] == {"accepted_wrong": 0, "heldout_wrong": 1}
    assert _by_id(doc, "K9.10")["status"] == "SKIPPED"
    assert _by_id(doc, "K9.11")["status"] == "SKIPPED"
    assert "inspect.py is not in this tree" in _by_id(doc, "K9.10")["evidence"]["reason"]


def test_verify_does_not_pass_when_wrong_holds_but_the_rerun_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(
        eng,
        "tenant_id: acme\nflags_on: []\nupgrade:\n"
        "  accepted_report: accepted.json\n"
        "  heldout_report: heldout.json\n",
    )
    (eng / "accepted.json").write_text(json.dumps({"metrics": {"wrong": 1}}), encoding="utf-8")
    (eng / "heldout.json").write_text(json.dumps({"metrics": {"wrong": 1}}), encoding="utf-8")
    code = _run(monkeypatch, eng, repo, "--phase", "verify", "--target", sha)
    _, doc = _report(eng)
    assert _by_id(doc, "K9.12")["status"] == "PASS"
    assert doc["status"] == "SKIPPED"
    assert code == 1


def test_verify_blocks_without_an_accepted_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(eng, "tenant_id: acme\nflags_on: []\n")
    code = _run(monkeypatch, eng, repo, "--phase", "verify", "--target", sha)
    _, doc = _report(eng)
    assert _by_id(doc, "K9.12")["status"] == "BLOCKED"
    assert doc["status"] == "BLOCKED"
    assert code == 3


def test_main_tree_revisions_are_unique_and_downgrades_work() -> None:
    trace: list[str] = []
    revs = upgrade.revisions_at(ROOT, "HEAD", trace)
    ids = [rev.revision for rev in revs]
    assert len(ids) >= 11
    assert len(ids) == len(set(ids))
    assert all(rev.downgrade_ok for rev in revs)
    assert len(upgrade.heads(revs)) == 1
    issues = upgrade.issues_within(revs)
    assert issues["duplicates"] == []
    assert issues["number_clashes"] == []
    assert issues["branches"] == []
    for shown in trace:
        assert upgrade._trace_allowed(shown)


def test_pass_downgrade_and_missing_downgrade_are_not_working() -> None:
    planted = upgrade.parse_revision_source(EMPTY_DOWN, "0002_planted.py")
    assert planted is not None
    assert planted.downgrade_ok is False
    missing = upgrade.parse_revision_source(
        'revision = "0003_gone"\ndown_revision = None\n\ndef upgrade():\n    value = 1\n',
        "0003_gone.py",
    )
    assert missing is not None
    assert missing.downgrade_ok is False
    good = upgrade.parse_revision_source(GOOD, "0001_base.py")
    assert good is not None and good.downgrade_ok is True


def test_render_lists_failures_first() -> None:
    api = upgrade.report_api()
    assert api.SCHEMA == "netie.fde.report/1"
    assert api.rollup([]) == "SKIPPED"
    doc = {
        "kit": "upgrade",
        "stage": "K9",
        "status": "FAIL",
        "engagement": "acme",
        "tenant_id": "acme",
        "dms_sha": "56f55f84",
        "started_at": "2026-11-28T00:00:00Z",
        "finished_at": "2026-11-28T00:00:01Z",
        "checks": [
            {
                "id": "K9.07",
                "title": "Backup evidence",
                "severity": "high",
                "status": "PASS",
                "evidence": {},
                "fix": "",
                "source": "scripts/fde/kits/upgrade.py",
            },
            {
                "id": "K9.03",
                "title": "Pending revisions have a downgrade",
                "severity": "critical",
                "status": "FAIL",
                "evidence": {"revision": "0002_planted"},
                "fix": "Add a downgrade.",
                "source": "alembic/versions/0002_planted.py",
            },
        ],
    }
    text = api.render_md(doc)
    assert text.index("K9.03") < text.index("K9.07")
    assert "Add a downgrade." in text


def test_a_raising_executor_is_a_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    sha = _init(repo, {"alembic/versions/0001_base.py": GOOD})
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    watch = tmp_path / "same"
    _write(watch, {"alembic/versions/0001_base.py": GOOD})
    monkeypatch.setenv("FDE_UPGRADE_SCRATCH_URL", SCRATCH_URL)

    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    code = _run(
        monkeypatch,
        eng,
        repo,
        "--phase",
        "precheck",
        "--current",
        sha,
        "--target",
        sha,
        "--watch-dir",
        str(watch),
        executor=explode,
    )
    _, doc = _report(eng)
    assert code == 1
    assert _by_id(doc, "K9.04")["status"] == "FAIL"
    assert _by_id(doc, "K9.04")["evidence"]["error"] == "RuntimeError"
    assert "boom" not in json.dumps(doc)


@pytest.mark.skipif(
    os.environ.get("DMS_FDE_UPGRADE_LIVE") != "1", reason="scratch Postgres is opt-in"
)
def test_live_scratch_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ["FDE_UPGRADE_SCRATCH_URL"]
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    monkeypatch.setenv("FDE_UPGRADE_SCRATCH_URL", url)
    eng = tmp_path / "acme"
    _engagement(eng, _base_yaml())
    _backups(eng)
    code = upgrade.main(
        [
            "--engagement",
            str(eng),
            "--repo",
            str(ROOT),
            "--phase",
            "precheck",
            "--current",
            "c2b6bc2",
            "--target",
            "56f55f84",
        ]
    )
    _run_dir, doc = _report(eng)
    scratch = _by_id(doc, "K9.04")
    assert scratch["status"] == "PASS", scratch["evidence"]
    pending = [item["revision"] for item in _by_id(doc, "K9.02")["evidence"]["pending"]]
    assert "0010_connections" in pending
    assert "0011_space_bench" in pending
    assert code == 1
    assert doc["status"] == "SKIPPED"
