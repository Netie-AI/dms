"""SCORE-BIRD-01: pack honesty + fail-closed CLI. Not a live Studio ask."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_bird import (  # noqa: E402
    BIRD_SPACE,
    DEFAULT_PACK,
    EXIT_BLOCKED,
    EXIT_CONFIG,
    EXIT_FAIL,
    EXIT_PASS,
    case_expect,
    exact_match_env,
    honesty_ok,
    leftover_remaining,
    live_url,
    load_bird,
    self_check,
    table_landed,
)
from score_curated import judge  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "score_bird.py"


def _clean_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATHEXT", "HOME", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _pack_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("DMS_API_BASE", "BIRD_SCORE_URL", "DMS_URL"):
        env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "apps" / "api"),
            str(ROOT / "packages" / "core"),
            str(ROOT / "packages" / "cortex_client"),
            str(ROOT / "packages" / "executor"),
            str(ROOT / "packages" / "ledger"),
            str(ROOT / "scripts"),
        ]
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["DMS_SCORE_DIR"] = str(ROOT / ".tmp")
    return env


def test_self_check_passes() -> None:
    assert self_check() == EXIT_PASS


def test_cli_self_check() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-check"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == EXIT_PASS, proc.stdout + proc.stderr
    assert "target=75" in proc.stdout
    assert "leftover=" in proc.stdout
    assert "99.95" not in proc.stdout
    assert "not complete" in proc.stdout.lower()


def test_live_unset_is_config_not_pass() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--live"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert proc.returncode == EXIT_CONFIG, proc.stdout + proc.stderr
    assert "CONFIG" in proc.stdout
    assert "127.0.0.1:8090" in proc.stdout
    assert "VERDICT: PASS" not in proc.stdout


def test_wildcard_url_is_refused() -> None:
    try:
        live_url({"DMS_API_BASE": "http://0.0.0.0:8090"}, None)
    except ValueError as exc:
        assert "0.0.0.0" in str(exc)
    else:
        raise AssertionError("wildcard must CONFIG")


def test_honesty_allows_growing_bronze() -> None:
    pack = load_bird(DEFAULT_PACK)
    assert honesty_ok(pack["honesty"]) == []
    assert pack["spaces"]["bird"] == BIRD_SPACE
    assert "99.95" not in DEFAULT_PACK.read_text(encoding="utf-8")
    growing = dict(pack["honesty"])
    growing["attached_tables"] = ["gender", "schools", "races"]
    assert honesty_ok(growing) == []
    missing = dict(pack["honesty"])
    missing["attached_tables"] = ["schools"]
    assert honesty_ok(missing)


def test_pack_has_gender_hits_and_leftover_traps() -> None:
    pack = load_bird()
    by_id = {c["id"]: c for c in pack["questions"]}
    assert by_id["gender_row_count"]["attached"] == "gender"
    assert by_id["trap_75_tables"]["leftover"] == "full_extract"
    assert by_id["trap_demo_spend"]["expect"] == "refuse"
    assert leftover_remaining(1) == 74
    assert leftover_remaining(75) == 0
    assert leftover_remaining(80) == 0


def test_green_leftover_trap_is_wrong() -> None:
    assert (
        judge(
            {"expect": "refuse"},
            {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "WRONG"
    )


def test_gender_abstain_is_not_wrong() -> None:
    assert (
        judge(
            {"expect": "answered", "min_rows": 1},
            {"badge": "ABSTAIN", "abstained": True, "rows": []},
        )
        == "ABSTAIN"
    )


def test_exact_match_misses_bird_and_demo_spend() -> None:
    env = exact_match_env("How many rows are in the gender table?", BIRD_SPACE)
    assert env.get("abstained") is True
    spend = exact_match_env(
        "What is our total spend by supplier country?", BIRD_SPACE
    )
    assert spend.get("abstained") is True
    assert judge({"expect": "refuse"}, spend) != "WRONG"


def test_ab_without_live_is_blocked_not_pass() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--ab"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_pack_env(),
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == EXIT_BLOCKED, out
    assert "BLOCKED" in out
    assert "VERDICT: PASS" not in out
    assert "exact_match" in out
    assert "target=75" in out
    assert "leftover=" in out


def test_landed_leftover_trap_skips_without_inventing_oracle() -> None:
    trap = {
        "id": "trap_california_schools",
        "expect": "refuse",
        "leftover": "multi_table",
        "needs_table": "schools",
    }
    assert case_expect(trap, ["bronze.public_gender"]) == "refuse"
    assert case_expect(trap, ["bronze.public_schools"]) is None
    assert table_landed("gender", ["bronze.public_gender"])
    assert table_landed("schools", ["bronze.public_schools"])
    assert not table_landed("schools", ["bronze.public_gender"])
    cover = {"expect": "refuse", "leftover": "full_extract"}
    assert case_expect(cover, ["bronze.public_schools"]) == "refuse"
    spend = {"expect": "refuse", "leftover": "demo_pack_bleed"}
    assert case_expect(spend, ["bronze.public_gender", "bronze.public_schools"]) == (
        "refuse"
    )


def test_no_args_is_config() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert proc.returncode == EXIT_CONFIG, proc.stdout + proc.stderr
    assert proc.returncode != EXIT_FAIL
