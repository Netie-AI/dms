"""AGI-BUYER-WALK-01 (#236) -- pack + Studio copy + fail-closed config."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "walk_buyer_studio.py"
sys.path.insert(0, str(ROOT / "scripts"))

import walk_buyer_studio as walk  # noqa: E402


def _clean_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATHEXT", "HOME", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_self_check_ok() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-check"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-check ok" in proc.stdout
    assert "Not #178 COMPLETE" in proc.stdout
    assert "VERDICT: PASS" in proc.stdout


def test_unset_env_is_config_not_pass() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CONFIG" in proc.stdout
    assert "STUDIO_ORIGIN" in proc.stdout
    assert "DMS_API_BASE" in proc.stdout
    assert "127.0.0.1:8090" in proc.stdout
    assert "VERDICT: PASS" not in proc.stdout


def test_judges_ontology_or_honest_abstain() -> None:
    onto = {
        "badge": "L2_VALIDATED",
        "abstained": False,
        "plan_source": "ontology_plan",
        "answer_id": "ans_1",
        "audit_id": "aud_1",
        "rows": [{"sku": "SKU-ALPHA"}],
    }
    assert walk.judge_ask(onto) == "ONTOLOGY"
    assert walk.judge_ask({"badge": "ABSTAIN", "abstained": True}) == "ABSTAIN"
    assert (
        walk.judge_ask({"badge": "L0_CERTIFIED", "abstained": False}) == "FAIL_NOT_ONTOLOGY"
    )
    assert (
        walk.judge_ask(
            {
                "badge": "L2_VALIDATED",
                "abstained": False,
                "plan_source": "bind_plan",
            }
        )
        == "FAIL_NOT_ONTOLOGY"
    )
    assert (
        walk.judge_ask({**onto, "demo_fallback_used": True}) == "FAIL_FALLBACK"
    )
    assert walk.judge_refuse({"badge": "ABSTAIN", "abstained": True}) == "ABSTAIN"
    assert walk.judge_refuse(onto) == "WRONG"


def test_xlsx_detects_real_workbook_only() -> None:
    assert walk.xlsx_is_real(b"not a zip") is False
    assert walk.xlsx_is_real(b"PK\x03\x04fake") is False


def test_no_invented_claims_in_pack_and_studio() -> None:
    pack = (ROOT / "tests" / "fixtures" / "buyer_walk" / "questions.yaml").read_text(
        encoding="utf-8"
    )
    studio = (ROOT / "apps" / "ui" / "src" / "pages" / "StudioPage.tsx").read_text(
        encoding="utf-8"
    )
    assert walk.invented_claim_hits(pack) == []
    assert walk.invented_claim_hits(studio) == []
    assert walk.invented_claim_hits("EPIC-INSIGHTS-UX is COMPLETE") == ["bare COMPLETE"]
    assert walk.invented_claim_hits("ARR: $4M") == ["invented ARR"]
    assert walk.invented_claim_hits("see dashboard.png") == ["invented chart/screenshot"]


def test_walk_script_does_not_touch_ontology_compile() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "generative_ask" not in text
    assert "Ontology.compile" not in text
    assert "bakeoff_freeroute" not in text
    assert "from dms_executor" not in text
    assert "import ontology" not in text
