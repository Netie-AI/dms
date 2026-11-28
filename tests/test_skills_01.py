"""DMS_SKILLS. Digit 1 only. Fetch, pin, scan, grant, audit, documents."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

PIN = "a" * 40
OTHER = "b" * 40
SECRET = "sk-ant-secretvalue"
QUESTION = "supplier lead time policy"


def _skill(body: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    files = {"SKILL.md": body}
    if extra:
        files.update(extra)
    return files


def _md() -> str:
    return (
        "---\n"
        "name: lead-time\n"
        "description: Cite the granted lead-time note.\n"
        "license: Apache-2.0\n"
        "---\n"
        "Use the citation. Do not invent a figure.\n"
    )


def test_flag_is_digit_1_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.skills.flag import enabled

    monkeypatch.delenv("DMS_SKILLS", raising=False)
    assert enabled() is False
    for value in ("0", "true", "yes", "on", "01", ""):
        monkeypatch.setenv("DMS_SKILLS", value)
        assert enabled() is False
    monkeypatch.setenv("DMS_SKILLS", " 1 ")
    assert enabled() is True


def test_flag_off_is_404_and_does_not_touch_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DMS_SKILLS", raising=False)
    monkeypatch.setenv("DMS_SKILLS_DIR", str(tmp_path))
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from dms_core.create_generator import skills_for
    from dms_core.skills.context import attach_skill_context
    from fastapi.testclient import TestClient

    ctx = {"tables": ["orders"]}
    assert attach_skill_context(ctx, QUESTION) is ctx
    picked = skills_for("app")
    assert [item["name"] for item in picked["local"]] == ["app"]
    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/v1/studio/skills/catalog").status_code == 404
    assert list(tmp_path.iterdir()) == []


def test_frontmatter_and_secret_scan_do_not_echo_the_secret() -> None:
    from dms_core.skills.errors import SkillError
    from dms_core.skills.validate import build_bundle, scan_files

    with pytest.raises(SkillError) as missing:
        build_bundle(
            {"SKILL.md": "no frontmatter\n"},
            commit_sha=PIN,
            url="https://example.com/skill.git",
            origin="git",
            skill_dir=None,
        )
    assert missing.value.code == "frontmatter_missing"

    secret_file = _skill(
        "---\nname: lead-time\ndescription: A note.\n---\nkey = \"" + SECRET + "\"\n"
    )
    with pytest.raises(SkillError) as leaked:
        scan_files(secret_file)
    assert leaked.value.code == "secret:provider_token:SKILL.md"
    assert SECRET not in leaked.value.code
    assert SECRET not in str(leaked.value)

    prose = _skill(_md().replace("Do not invent", "Do not call subprocess"))
    scan_files(prose)

    danger = _skill(_md(), {"helper.py": "import subprocess\nsubprocess.run(['ls'])\n"})
    with pytest.raises(SkillError) as script:
        scan_files(danger)
    assert script.value.code == "dangerous_script:subprocess:helper.py"


def test_fetch_pins_the_sha_and_rejects_a_mismatch() -> None:
    from dms_core.skills.errors import SkillError
    from dms_core.skills.fetch import fetch_skill

    written = {"done": False}

    def runner(argv: list[str], cwd: str | None) -> str:
        if "ls-remote" in argv:
            return PIN + "\tHEAD\n"
        if "rev-parse" in argv:
            return (OTHER if written["done"] else PIN) + "\n"
        if "checkout" in argv and cwd and not written["done"]:
            Path(cwd, "SKILL.md").write_text(_md(), encoding="utf-8")
        return ""

    bundle = fetch_skill(
        "https://github.com/example/skills.git",
        ref="main",
        runner=runner,
        resolve=lambda _host: ["8.8.8.8"],
    )
    assert bundle["commit_sha"] == PIN
    assert bundle["licence"]["id"] == "Apache-2.0"
    assert bundle["licence"]["source"] == "frontmatter"

    written["done"] = True
    with pytest.raises(SkillError) as mismatch:
        fetch_skill(
            "https://github.com/example/skills.git",
            ref=PIN,
            runner=runner,
            resolve=lambda _host: ["8.8.8.8"],
        )
    assert mismatch.value.code == "pin_mismatch"


def test_private_host_and_userinfo_are_rejected() -> None:
    from dms_core.skills.errors import SkillError
    from dms_core.skills.fetch import assert_public_host, parse_public_git_url

    with pytest.raises(SkillError):
        parse_public_git_url("https://user:token@github.com/example/skills.git")
    with pytest.raises(SkillError) as local:
        assert_public_host("127.0.0.1")
    assert local.value.code == "git_host_rejected"


def test_versions_grants_tenants_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DMS_SKILLS", "1")
    from dms_core.skills.context import provider_context
    from dms_core.skills.store import SkillLibrary

    one = SkillLibrary.open("tenant-one", tmp_path)
    first = one.install_catalog("document-citations", actor="steward")
    second = one.install_catalog("document-citations", actor="steward")
    assert first["version"] == 1
    assert second["version"] == 2
    assert provider_context(surface="ask", tenant_id="tenant-one", root=tmp_path)["skills"] == []
    one.grant(second["id"], ["create"], actor="steward")
    ask = provider_context(surface="ask", tenant_id="tenant-one", root=tmp_path)
    create = provider_context(surface="create", tenant_id="tenant-one", root=tmp_path)
    assert ask["skills"] == []
    assert [item["name"] for item in create["skills"]] == ["document-citations"]
    other = SkillLibrary.open("tenant-two", tmp_path)
    assert other.versions() == []
    blob = json.dumps(one.audit_log())
    assert "install" in blob
    assert "grant" in blob
    assert SECRET not in blob
    one.record("fetch_rejected", "steward", code="secret:" + SECRET)
    assert SECRET not in json.dumps(one.audit_log())


def test_documents_chunk_cite_and_do_not_invent_vectors(tmp_path: Path) -> None:
    from dms_core.skills.documents import excerpt, rank_chunks
    from dms_core.skills.store import SkillLibrary

    library = SkillLibrary.open("tenant-one", tmp_path)
    text = ("policy " * 80).strip()
    saved = library.add_document(
        filename="policy.md",
        data=text.encode("utf-8"),
        space_id="space-1",
        actor="steward",
    )
    assert saved["embed"] == "unavailable"
    assert saved["chunks"] >= 1
    library.grant_document(saved["id"], ["ask"], actor="steward")
    cites = library.citations("policy", space_id="space-1")
    assert cites
    assert len(cites[0]["excerpt"]) <= 240
    assert len(excerpt("x" * 300)) == 240

    docx = _docx("Lead time is stated in the contract.")
    office = library.add_document(
        filename="note.docx",
        data=docx,
        space_id="space-1",
        actor="steward",
    )
    assert office["media"] == "docx"
    raw = json.loads((tmp_path / "tenant-one" / "state.json").read_text(encoding="utf-8"))
    vectors = raw["documents"][0]["chunks"][0]["vector"]
    assert vectors is None

    ranked = rank_chunks(
        "zzzz",
        [
            {"text": "aaaa", "vector": [1.0, 0.0]},
            {"text": "bbbb", "vector": [0.0, 1.0]},
        ],
        lambda _texts: [[1.0, 0.0]],
    )
    assert ranked == [0]


def test_seam_lifts_skill_context_off_the_ontology() -> None:
    from dms_executor import _insights_compute_seam

    seen: dict[str, object] = {}

    def compute_insights(question: str, **kwargs: object) -> dict[str, bool]:
        seen["question"] = question
        seen["ontology"] = kwargs.get("ontology")
        return {"ok": True}

    class Cortex:
        pass

    cortex = Cortex()
    cortex.compute_insights = compute_insights  # type: ignore[attr-defined]
    plain = "how many orders"
    _insights_compute_seam(
        cortex,
        plain,
        session_id=None,
        space_id=None,
        ontology={"tables": ["orders"]},
    )
    assert seen["question"] == plain
    onto = {"tables": ["orders"], "skill_context": "\nskill context\n"}
    _insights_compute_seam(
        cortex,
        plain,
        session_id=None,
        space_id=None,
        ontology=onto,
    )
    posted = seen["ontology"]
    assert isinstance(posted, dict)
    assert "skill_context" not in posted
    assert "skill context" in str(seen["question"])


def test_viewer_cannot_grant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DMS_SKILLS", "1")
    monkeypatch.setenv("DMS_SKILLS_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    def allow(*, action: str, actor: str | None = None, **_kwargs: object) -> ComplianceDecision:
        del actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    get_settings.cache_clear()
    app = create_app()
    monkeypatch.setattr("dms_api.routes.skills.compliance_gate", allow)
    client = TestClient(app)
    response = client.post("/v1/studio/skills/install", json={"catalog": "document-citations"})
    assert response.status_code == 403
    assert response.json()["detail"] == "steward_grant_required"


def _docx(text: str) -> bytes:
    xml = (
        "<?xml version='1.0'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()
