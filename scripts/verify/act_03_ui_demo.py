"""ACT-03 proof: the governed-action UI says "Applied" only with proof, and is absent when off.

Usage, from a worktree ``$W`` of this branch::

    python scripts/verify/act_03_ui_demo.py [--skip-ci] [--skip-build]

Runs, in ``apps/ui``:

1. ``npm ci`` (skip with ``--skip-ci`` when node_modules is already the lockfile);
2. ``npm run test -- <the five ACT-03 vitest files> --reporter=verbose`` with
   ``ACT03_PROOF=1``, which makes those tests print the strings they render;
3. ``npm run build`` (``tsc --noEmit`` + ``vite build``);
4. the three pre-existing suites the ticket requires to pass unedited.

It then checks the printed strings: the healthy receipt sentence, no "Applied"
on the unverified and unproven receipts, and the flag-off page equal to
origin/main's markup with no Governed actions section. Exits non-zero on any
mismatch and prints ``ACT-03 PROOF OK @<sha>``.

UI only: no API, no Postgres, no warehouse file, no network beyond npm ci.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "apps" / "ui"
TEST_FILES = (
    "src/lib/actions.test.ts",
    "src/lib/actionReceipt.test.ts",
    "src/components/ActionReceiptPanel.test.ts",
    "src/components/ActionCard.test.ts",
    "src/pages/AmendPage.test.ts",
)
UNEDITED = (
    "src/lib/promoteReceipt.test.ts",
    "src/components/PromoteReceiptPanel.test.ts",
    "src/lib/splitInsights.test.ts",
)
EXPECTED = (
    "[ACT03] healthy receipt sentence: Reorder level for SKU-BETA at WH-B: 400 (source) -> 450. "
    "Verified by re-read. Receipt e1 recorded in the ledger. Source system unchanged.",
    "[ACT03] healthy receipt defect: null",
    "[ACT03] chain_unproven panel contains 'Applied': false",
    "[ACT03] verified:false panel contains 'Applied': false",
    "[ACT03] object_changed refusal contains 'Applied': false",
    "[ACT03] flag null contains 'Governed actions': false",
    "[ACT03] flag off contains 'Governed actions': false",
    "[ACT03] flag off markup === section stubbed out: true",
    "[ACT03] flag off markup === origin/main 58fcaa5 markup: true",
    "[ACT03] flag on contains 'Governed actions': true",
    "[ACT03] flag on action row: Apply in Governed actions",
)


def _npm() -> str:
    found = shutil.which("npm") or shutil.which("npm.cmd")
    if not found:
        raise SystemExit("npm not found on PATH")
    return found


def _run(args: list[str], env: dict[str, str] | None = None) -> str:
    print(f"$ npm {' '.join(args)}", flush=True)
    proc = subprocess.run(
        [_npm(), *args],
        cwd=UI,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout + proc.stderr)
    if proc.returncode != 0:
        print(out)
        raise SystemExit(f"npm {' '.join(args)} exited {proc.returncode}")
    return out


def _summary(out: str) -> str:
    lines = [ln.strip() for ln in out.splitlines()]
    return " | ".join(ln for ln in lines if ln.startswith(("Test Files", "Tests ")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-ci", action="store_true")
    ap.add_argument("--skip-build", action="store_true")
    ns = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"git HEAD      {sha}")

    if not ns.skip_ci:
        _run(["ci"])

    env = {**os.environ, "ACT03_PROOF": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}
    out = _run(["run", "test", "--", *TEST_FILES, "--reporter=verbose"], env=env)
    printed = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[ACT03]")]
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith(("✓", "×", "[ACT03]")) or "Test Files" in s or s.startswith("Tests "):
            print(f"  {s}")

    failures = [want for want in EXPECTED if want not in printed]
    for want in failures:
        print(f"MISSING  {want}")

    if not ns.skip_build:
        build = _run(["run", "build"])
        built = [ln.strip() for ln in build.splitlines() if "built in" in ln]
        print(f"  build: {built[-1] if built else 'ok'}")

    unedited = _run(["run", "test", "--", *UNEDITED], env={**os.environ, "NO_COLOR": "1"})
    print(f"  unedited suites: {_summary(unedited)}")
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "diff",
            "--quiet",
            "origin/main",
            "--",
            *(f"apps/ui/{f}" for f in UNEDITED),
        ],
    )
    if diff.returncode != 0:
        failures.append("an unedited suite differs from origin/main")
        print("MISSING  the three existing suites equal origin/main")

    if failures:
        print(f"ACT-03 PROOF FAILED @{sha}: {len(failures)} mismatches")
        return 1
    print(f"ACT-03 PROOF OK @{sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
