"""FDE-KIT-01: launcher flag, report schema, roll-up, and secret scrub."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "fde"))

import fde_pipeline as fp  # noqa: E402
import kit  # noqa: E402
import report  # noqa: E402
from kits import KitSpec  # noqa: E402

PW = "fdekit-pw-9f3c1a"
KEY = "fdekit-key-9f3c1a"


def _ctx(tmp_path: Path) -> kit.Ctx:
    return kit.Ctx(
        engagement_dir=tmp_path,
        engagement={"name": "acme", "tenant_id": "t-1"},
        tenant=None,
        work=tmp_path / "work",
        run_dir=tmp_path / "run",
        flags={"DMS_FDE_KIT": "1"},
        dms_sha="56f55f84",
        started_at="2026-11-28T00:00:00Z",
        secrets=[PW],
    )


def _texts(folder: Path) -> str:
    parts: list[str] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:1024]:
            assert PW.encode() not in raw
            assert KEY.encode() not in raw
            continue
        parts.append(raw.decode("utf-8", errors="replace"))
    return "\n".join(parts)


def test_rollup_empty_is_skipped() -> None:
    assert report.rollup([]) == "SKIPPED"


def test_rollup_does_not_treat_a_mix_as_pass() -> None:
    assert report.rollup([{"status": "PASS"}, {"status": "SKIPPED"}]) == "SKIPPED"
    assert report.rollup([{"status": "FAIL", "severity": "low"}]) == "FAIL"
    assert report.rollup([{"status": "FAIL", "severity": "critical"}]) == "FAIL"
    assert report.rollup([{"status": "BLOCKED"}]) == "BLOCKED"
    assert report.rollup([{"status": "MANUAL"}]) == "BLOCKED"
    assert report.rollup([{"status": "BLOCKED"}, {"status": "FAIL"}]) == "FAIL"
    assert report.rollup([{"status": "PASS"}]) == "PASS"


def test_empty_kit_is_skipped_even_if_it_claims_pass(tmp_path: Path) -> None:
    def run(_ctx: kit.Ctx) -> dict:
        rep = report.new_report(
            kit="empty",
            stage="Z9",
            engagement="acme",
            tenant_id="t-1",
            dms_sha_value="56f55f84",
            started_at="2026-11-28T00:00:00Z",
        )
        rep["status"] = "PASS"
        return rep

    spec = KitSpec("empty", "Z9", run, ())
    got = kit.invoke(spec, _ctx(tmp_path))
    assert got["status"] == "SKIPPED"
    assert got["checks"] == []


def test_raising_kit_is_a_fail_check_without_the_message(tmp_path: Path) -> None:
    def run(_ctx: kit.Ctx) -> dict:
        raise RuntimeError(f"{PW} blew up")

    spec = KitSpec("boom", "Z9", run, ())
    got = kit.invoke(spec, _ctx(tmp_path))
    assert got["status"] == "FAIL"
    assert got["checks"][0]["evidence"] == {"exception": "RuntimeError"}
    blob = json.dumps(got)
    assert PW not in blob
    assert "blew up" not in blob


def test_disabled_product_flag_is_skipped_and_not_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = {"n": 0}

    def run(_ctx: kit.Ctx) -> dict:
        called["n"] += 1
        return report.new_report(
            kit="connect",
            stage="K2",
            engagement="acme",
            tenant_id="t-1",
            dms_sha_value="56f55f84",
        )

    spec = KitSpec("connect", "K2", run, (), requires_flag="DMS_CONNECT_API")
    monkeypatch.delenv("DMS_CONNECT_API", raising=False)
    got = kit.guard(spec, _ctx(tmp_path))
    assert called["n"] == 0
    assert got["status"] == "SKIPPED"
    assert got["checks"][0]["evidence"]["reason"] == "SKIPPED: DMS_CONNECT_API off"
    monkeypatch.setenv("DMS_CONNECT_API", "true")
    got = kit.guard(spec, _ctx(tmp_path))
    assert called["n"] == 0
    monkeypatch.setenv("DMS_CONNECT_API", "1")
    got = kit.guard(spec, _ctx(tmp_path))
    assert called["n"] == 1


@pytest.mark.parametrize("raw", ["true", "01", " 1 ", "1 ", "yes", "0", ""])
def test_flag_other_than_digit_1_writes_nothing(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", raw)
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 2
    assert kit.main(["run", "--engagement", str(eng), "--stage", "all"]) == 2
    err = capsys.readouterr().err
    assert "fde_kit_disabled" in err
    assert not eng.exists()


def test_flag_unset_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DMS_FDE_KIT", raising=False)
    eng = tmp_path / "acme"
    assert kit.main(["survey", "--engagement", str(eng)]) == 2
    assert "fde_kit_disabled" in capsys.readouterr().err
    assert not eng.exists()


def test_list_works_with_the_flag_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DMS_FDE_KIT", raising=False)
    assert kit.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "P1.01" in out
    assert "extract" in out
    assert "survey" in out
    assert "not registered" in out
    assert "fde_kit_disabled" not in out


def test_init_writes_templates_and_does_not_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    text = (eng / "engagement.yaml").read_text(encoding="utf-8")
    assert "password_env" in text
    assert "password:" not in text
    loaded = yaml.safe_load(text)
    assert loaded["name"] == "acme"
    assert loaded["sources"][0]["password_env"] == "FDE_SOURCE_PASSWORD"
    assert (eng / "intake.md").is_file()
    (eng / "engagement.yaml").write_text(text + "\n# keep\n", encoding="utf-8")
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    assert "# keep" in (eng / "engagement.yaml").read_text(encoding="utf-8")


def test_inline_password_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    data = yaml.safe_load((eng / "engagement.yaml").read_text(encoding="utf-8"))
    data["sources"][0]["password"] = PW
    data["sources"][0]["kind"] = "postgresql"
    data["sources"][0]["database"] = "db"
    (eng / "engagement.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert kit.main(["run", "--engagement", str(eng), "--stage", "all"]) == 2
    assert not (eng / "runs").exists()


def test_blank_engagement_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    assert kit.main(["run", "--engagement", str(eng), "--stage", "all"]) == 1
    reports = list((eng / "runs").glob("*/report.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["status"] == "SKIPPED"
    assert report.validate(payload) == []


def test_md_is_rendered_from_the_json_object() -> None:
    payload = report.new_report(
        kit="survey",
        stage="K1",
        engagement="acme",
        tenant_id="t-1",
        dms_sha_value="56f55f84",
        flags={"DMS_FDE_KIT": "1"},
        started_at="2026-11-28T00:00:00Z",
    )
    report.add_check(
        payload,
        report.make_check(
            id="K1.02",
            title="Later",
            severity="low",
            status="PASS",
            evidence={"n": 1},
            fix="",
            source="x",
        ),
    )
    report.add_check(
        payload,
        report.make_check(
            id="K1.01",
            title="Rows",
            severity="critical",
            status="FAIL",
            evidence={"rows": 1},
            fix="fix-from-json",
            source="y",
        ),
    )
    report.finish(payload)
    md = report.render_md(json.loads(json.dumps(payload)))
    assert "fix-from-json" in md
    assert md.index("K1.01") < md.index("K1.02")
    clone = json.loads(json.dumps(payload))
    clone["checks"][1]["fix"] = "other-fix"
    md2 = report.render_md(clone)
    assert "other-fix" in md2
    assert "fix-from-json" not in md2


def test_schema_rejects_a_partial_object() -> None:
    assert report.validate({"schema": "nope"})


def test_flag_off_run_after_init_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    monkeypatch.setenv("DMS_FDE_KIT", "true")
    assert kit.main(["run", "--engagement", str(eng), "--stage", "all"]) == 2
    assert not (eng / "runs").exists()


def test_unregistered_verb_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    assert kit.main(["survey", "--engagement", str(eng)]) == 1
    payload = json.loads(next((eng / "runs").glob("*/report.json")).read_text(encoding="utf-8"))
    assert payload["status"] == "SKIPPED"
    assert payload["kit"] == "survey"


def test_apply_without_approval_ref_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    assert kit.main(["govern", "--engagement", str(eng), "--apply"]) == 3
    payload = json.loads(next((eng / "runs").glob("*/report.json")).read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "approval_ref missing" in json.dumps(payload)


def test_pipeline_entry_respects_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DMS_FDE_KIT", raising=False)
    from kits import pipeline

    assert pipeline.main(["--engagement", str(tmp_path / "nope")]) == 2
    assert "fde_kit_disabled" in capsys.readouterr().err
    assert not (tmp_path / "nope").exists()


def test_stage_all_matches_pipeline_and_scrubs_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", PW)
    tenants = tmp_path / "tenants"
    assert (
        fp.main(
            ["factory", "--n", "2", "--seed", "11", "--engine", "generated", "--out", str(tenants)]
        )
        == 0
    )
    eng = tmp_path / "acme"
    assert kit.main(["init", "--engagement", str(eng)]) == 0
    data = yaml.safe_load((eng / "engagement.yaml").read_text(encoding="utf-8"))
    data["tenant_files"] = [str(path) for path in sorted(tenants.glob("*.yaml"))]
    (eng / "engagement.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (eng / "receipts").mkdir()
    (eng / "receipts" / "connect.json").write_text(
        json.dumps({"api_key": KEY, "note": f"pw {PW}"}), encoding="utf-8"
    )
    assert kit.main(["run", "--engagement", str(eng), "--stage", "all", "--expect-keys"]) == 0
    folders = [path for path in (eng / "runs").iterdir() if path.is_dir()]
    assert len(folders) == 2
    by_tenant = {}
    for folder in folders:
        blob = _texts(folder)
        assert PW not in blob
        assert KEY not in blob
        payload = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        assert report.validate(payload) == []
        assert payload["status"] == "PASS"
        assert payload["schema"] == report.SCHEMA
        md = (folder / "report.md").read_text(encoding="utf-8")
        assert payload["status"] in md
        by_tenant[folder.name.split("-", 1)[-1]] = payload
    work = tmp_path / "pipe"
    assert fp.main(["run", "--tenants", str(tenants), "--work", str(work), "--expect-keys"]) == 0
    for pipe in json.loads((work / "fleet.json").read_text(encoding="utf-8")):
        got = by_tenant[_safe_match(pipe["tenant"], by_tenant)]
        assert got["metrics"]["pipeline_status"] == pipe["status"]
        assert got["metrics"]["pipeline_failures"] == pipe["failures"]
        assert got["metrics"]["stages"]["extract"] == pipe["stages"]["extract"]["status"]
        assert got["metrics"]["stages"]["pack"] == pipe["stages"]["pack"]["status"]
        assert (pipe["failures"] == [] and pipe["status"] == "OK") is (got["status"] == "PASS")


def _safe_match(tenant: str, by_tenant: dict[str, dict]) -> str:
    if tenant in by_tenant:
        return tenant
    safe = kit._safe_name(tenant)
    assert safe in by_tenant
    return safe
