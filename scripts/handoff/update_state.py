"""Handoff helper for Netie Lead and Claude Code. Docs only. Never edits code or CI.

    update_state.py                       rewrite the machine block of handoff/STATE.md
    update_state.py --stdout              print the machine block, touch nothing
    update_state.py --append-log claude "what I did"
    update_state.py --log-since lead      print LOG entries after the last one by lead
(run it as python scripts/handoff/update_state.py)

Reads GitHub through the gh CLI (logged in, or GH_TOKEN set). A repo that gh cannot read is
reported as unreadable and the script still exits 0. No secret value is read or written.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "handoff" / "STATE.md"
LOG = ROOT / "handoff" / "LOG.md"
BEGIN = "<!-- MACHINE:BEGIN -->"
END = "<!-- MACHINE:END -->"
REPOS = ("Netie-AI/dms", "Netie-AI/Cortex", "Netie-AI/OpenVault")
SGT = timezone(timedelta(hours=8), "SGT")
MAX_PRS = 60


def now_sgt() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%d %H:%M SGT")


def gh_json(args: list[str]) -> object | None:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, check=False, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def ci_summary(rollup: list[dict] | None) -> str:
    if not rollup:
        return "no checks"
    ok = bad = wait = 0
    for item in rollup:
        verdict = (item.get("conclusion") or item.get("state") or "").upper()
        status = (item.get("status") or "").upper()
        if status and status != "COMPLETED":
            wait += 1
        elif verdict in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            ok += 1
        elif verdict in ("PENDING", "EXPECTED", ""):
            wait += 1
        else:
            bad += 1
    parts = [f"{ok} ok"]
    if bad:
        parts.append(f"{bad} FAIL")
    if wait:
        parts.append(f"{wait} running")
    return ", ".join(parts)


def esc(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def repo_block(repo: str) -> list[str]:
    lines = [f"### {repo}", ""]
    main = gh_json(["api", f"repos/{repo}/commits/main"])
    if not isinstance(main, dict):
        return [*lines, "unreadable with this token (no data written)", ""]
    sha = str(main.get("sha", ""))
    subject = str((main.get("commit") or {}).get("message", "")).splitlines()[0]
    lines.append(f"main: `{sha[:8]}` {esc(subject)[:100]}")
    lines.append("")
    prs = gh_json(
        [
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--limit",
            str(MAX_PRS),
            "--json",
            "number,title,isDraft,headRefName,headRefOid,labels,statusCheckRollup",
        ]
    )
    if not isinstance(prs, list):
        return [*lines, "open PRs unreadable", ""]
    lines.append(f"open PRs: {len(prs)} (cap {MAX_PRS})")
    lines.append("")
    if prs:
        lines += ["| PR | draft | head | tier | CI on head | title |", "|---|---|---|---|---|---|"]
    for pr in prs:
        tiers = [
            x["name"] for x in pr.get("labels", []) if str(x.get("name", "")).startswith("tier:")
        ]
        lines.append(
            f"| #{pr['number']} | {'y' if pr.get('isDraft') else 'n'} | "
            f"`{str(pr.get('headRefOid', ''))[:8]}` | {','.join(tiers) or '-'} | "
            f"{ci_summary(pr.get('statusCheckRollup'))} | {esc(str(pr.get('title', '')))[:80]} |"
        )
    lines.append("")
    return lines


def machine_block() -> str:
    lines = [
        BEGIN,
        f"Generated {now_sgt()} by scripts/handoff/update_state.py. Do not hand-edit.",
        "",
    ]
    for repo in REPOS:
        lines += repo_block(repo)
    lines.append(END)
    return "\n".join(lines)


def write_state() -> int:
    text = STATE.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        print(f"no machine block markers in {STATE}", file=sys.stderr)
        return 1
    block = machine_block()
    STATE.write_text(pattern.sub(lambda _m: block, text, count=1), encoding="utf-8")
    print(f"updated {STATE.relative_to(ROOT)}")
    return 0


def append_log(author: str, message: str) -> int:
    author = author.strip().lower()
    if author not in ("lead", "claude"):
        print("author must be lead or claude", file=sys.stderr)
        return 2
    line = f"- {now_sgt()} | author={author} | {esc(message)}\n"
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())
    return 0


def log_since(author: str) -> int:
    entries = [x for x in LOG.read_text(encoding="utf-8").splitlines() if x.startswith("- 20")]
    tag = f"| author={author.strip().lower()} |"
    last = max((i for i, x in enumerate(entries) if tag in x), default=-1)
    for entry in entries[last + 1 :]:
        print(entry)
    if last == len(entries) - 1:
        print(f"(no entries after the last by {author})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--append-log", nargs=2, metavar=("AUTHOR", "MESSAGE"))
    ap.add_argument("--log-since", metavar="AUTHOR")
    args = ap.parse_args()
    if args.append_log:
        return append_log(*args.append_log)
    if args.log_since:
        return log_since(args.log_since)
    if args.stdout:
        print(machine_block())
        return 0
    return write_state()


if __name__ == "__main__":
    sys.exit(main())
