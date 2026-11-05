"""DR-0006 enforcer: may this PR head be squashed on its second-model review?

The rule (DR-0006, from founder decision E on 2026-10-15, reading the merge-tier
text approved 2026-10-06): the AGREE that clears a head must come from the model
family OPPOSITE the writer's, and it must name the exact head. Claude never
clears Claude; Grok never clears Grok. A later DISAGREE or VOID on the same head
cancels an earlier AGREE. A writer whose family cannot be established cannot be
cleared, because "opposite" is then undefined.

Why opposite family and not just "a different model": on 2026-10-08/09 six
heads cleared by a Grok-family AGREE (#404, #405, #409, #419, #433, #435), on
PRs written by Grok or by Cursor agents that named no model, each carried a
blocker or major that a separate Claude run then reproduced (finding
2026-10-09_same-family-review-misses, carried on #424).

    python scripts/check_writer_family.py --pr 433        # needs gh api
    python scripts/check_writer_family.py --json pr.json  # offline, see load_bundle

Exit 0 = CLEARABLE, 1 = BLOCK (reasons printed), 2 = could not read the PR.
This checks the review leg only. CI, DMS Check and the serial order still apply.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

FAMILIES = ("claude", "grok", "composer")

# Ordered: the first family whose pattern matches wins. "claude code" and model
# names both mean the Claude family; "cursor" alone says nothing about a model.
_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claude", re.compile(r"\b(claude|opus|sonnet|haiku|fable)\b", re.I)),
    ("grok", re.compile(r"\bgrok\b", re.I)),
    ("composer", re.compile(r"\bcomposer\b", re.I)),
)

_WRITER_LINE = re.compile(r"^\s*Writer(?:-Model)?\s*:\s*(.+)$", re.I | re.M)
_TRAILER = re.compile(r"^\s*Co-Authored-By:\s*(.+)$", re.I | re.M)
_VERDICT = re.compile(r"^\W*(AGREE|DISAGREE|VOID)\b", re.I)
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def family_of(text: str) -> str | None:
    """The one family ``text`` names, or None when it names none or several."""
    fams = {fam for fam, pat in _FAMILY_PATTERNS if pat.search(text or "")}
    return fams.pop() if len(fams) == 1 else None


def reviewer_family(first_line: str) -> str | None:
    """Family of the reviewer on a verdict line.

    Lines such as "AGREE (different-model Grok: grok-4.6 reviewing an Opus
    writer)" name the writer too, so only the text before "reviewing" counts.
    A line that still names two families is ambiguous and counts as none.
    """
    head, sep, _ = first_line.partition(" reviewing ")
    return family_of(head if sep else first_line)


def writer_family(body: str, commit_messages: Iterable[str]) -> tuple[str | None, str]:
    """(family, evidence). A declared Writer line wins over trailers.

    A body that names two families on its Writer line, or trailers from two
    families with no Writer line, is ambiguous and returns None.
    """
    m = _WRITER_LINE.search(body or "")
    if m:
        fams = {f for f, p in _FAMILY_PATTERNS if p.search(m.group(1))}
        if len(fams) == 1:
            return fams.pop(), f"Writer line: {m.group(1).strip()[:80]}"
        return (
            None,
            f"Writer line names {sorted(fams) or 'no known family'}: {m.group(1).strip()[:80]}",
        )
    seen: set[str] = set()
    for msg in commit_messages:
        for t in _TRAILER.findall(msg or ""):
            seen.update(f for f, p in _FAMILY_PATTERNS if p.search(t))
    if len(seen) == 1:
        fam = seen.pop()
        return fam, f"Co-Authored-By trailers name only {fam}"
    if seen:
        return None, f"trailers name several families {sorted(seen)}"
    return None, "no Writer line and no model trailer"


@dataclass(frozen=True)
class Verdict:
    kind: str  # AGREE | DISAGREE | VOID
    family: str | None
    shas: tuple[str, ...]
    when: str
    first_line: str

    def names(self, head: str) -> bool:
        return any(head.lower().startswith(s.lower()) for s in self.shas)


def parse_verdicts(comments: Sequence[Mapping[str, Any]]) -> list[Verdict]:
    out: list[Verdict] = []
    for c in comments:
        body = str(c.get("body") or "").strip()
        if not body:
            continue
        first = body.splitlines()[0]
        m = _VERDICT.match(first)
        if not m:
            continue
        out.append(
            Verdict(
                kind=m.group(1).upper(),
                family=reviewer_family(first),
                shas=tuple(_SHA.findall(first)),
                when=str(c.get("created_at") or c.get("submitted_at") or ""),
                first_line=first[:160],
            )
        )
    out.sort(key=lambda v: v.when)
    return out


@dataclass
class Result:
    clearable: bool
    writer: str | None
    writer_evidence: str
    agree: Verdict | None = None
    reasons: list[str] = field(default_factory=list)


def check(
    *,
    head: str,
    body: str,
    commit_messages: Iterable[str],
    comments: Sequence[Mapping[str, Any]],
) -> Result:
    writer, evidence = writer_family(body, commit_messages)
    res = Result(clearable=False, writer=writer, writer_evidence=evidence)
    if writer is None:
        res.reasons.append(f"writer family unknown ({evidence}); opposite family is undefined")
        return res
    on_head = [v for v in parse_verdicts(comments) if v.names(head)]
    if not on_head:
        res.reasons.append(f"no AGREE, DISAGREE or VOID names head {head[:8]}")
        return res
    last = on_head[-1]
    if last.kind != "AGREE":
        res.reasons.append(f"latest verdict on {head[:8]} is {last.kind}: {last.first_line}")
        return res
    if last.family is None:
        res.reasons.append(f"AGREE on {head[:8]} does not name its model family: {last.first_line}")
        return res
    if last.family == writer:
        res.reasons.append(
            f"AGREE on {head[:8]} is {last.family} family, same as the {writer} writer (DR-0006)"
        )
        return res
    res.clearable = True
    res.agree = last
    return res


def load_bundle(path: str) -> dict[str, Any]:
    """Offline input: {"head": sha, "body": str, "commit_messages": [...], "comments": [...]}."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: data[k] for k in ("head", "body", "commit_messages", "comments")}


def fetch_pr(repo: str, number: int) -> dict[str, Any]:
    def api(path: str) -> Any:
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    pr = api(f"repos/{repo}/pulls/{number}")
    commits = api(f"repos/{repo}/pulls/{number}/commits?per_page=100")
    comments = api(f"repos/{repo}/issues/{number}/comments?per_page=100")
    reviews = api(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    return {
        "head": pr["head"]["sha"],
        "body": pr.get("body") or "",
        "commit_messages": [c["commit"]["message"] for c in commits],
        "comments": list(comments) + list(reviews),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pr", type=int)
    src.add_argument("--json", dest="json_path")
    ap.add_argument("--repo", default="netie-ai/dms")
    args = ap.parse_args(argv)
    try:
        bundle = fetch_pr(args.repo, args.pr) if args.pr else load_bundle(args.json_path)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"ERROR could not read the PR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    res = check(**bundle)
    head = bundle["head"][:8]
    print(f"head {head} writer={res.writer or 'unknown'} ({res.writer_evidence})")
    if res.clearable and res.agree:
        print(f"CLEARABLE review leg: {res.agree.family} AGREE @ {head} ({res.agree.when})")
        return 0
    for r in res.reasons:
        print(f"BLOCK {r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
