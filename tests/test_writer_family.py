"""DR-0006: the review leg clears only on an opposite-family AGREE on the exact head.

Each case below is a way the gate could wrongly let a head through. A gate
nobody has seen refuse is not evidence, so most cases assert a BLOCK.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_writer_family as cwf  # noqa: E402

HEAD = "97ca273913942274e7bd8af568c3ef4a25bc7435"
OLD = "4941237800000000000000000000000000000000"
GROK_BODY = "Writer-Model: grok-4.7 (reasoning_effort high, fast false)\n\nbody"
CLAUDE_BODY = "Writer: Claude family (Claude Code), seat taken per DR-0006\n\nbody"


def _c(body: str, when: str) -> dict[str, str]:
    return {"body": body, "created_at": when}


def _check(
    body: str, comments: list[dict[str, str]], commits: list[str] | None = None
) -> cwf.Result:
    return cwf.check(head=HEAD, body=body, commit_messages=commits or [], comments=comments)


def test_claude_agree_clears_a_grok_writer() -> None:
    res = _check(
        GROK_BODY, [_c(f"AGREE (Claude family, second-model review) @ {HEAD[:8]}", "2026-10-15T01")]
    )
    assert res.clearable and res.writer == "grok" and res.agree and res.agree.family == "claude"


def test_grok_agree_clears_a_claude_writer() -> None:
    res = _check(
        CLAUDE_BODY, [_c(f"AGREE (Grok family, grok-4.7 high) @ {HEAD[:8]}", "2026-10-15T01")]
    )
    assert res.clearable and res.writer == "claude"


def test_same_family_agree_is_refused_both_ways() -> None:
    grok_on_grok = _check(
        GROK_BODY,
        [_c(f"AGREE (different-model Grok: grok-4.6 reviewing grok-4.7) @ {HEAD[:8]}", "t1")],
    )
    claude_on_claude = _check(
        CLAUDE_BODY, [_c(f"AGREE (Claude family, second-model review) @ {HEAD[:8]}", "t1")]
    )
    assert not grok_on_grok.clearable and "same as the grok writer" in grok_on_grok.reasons[0]
    assert (
        not claude_on_claude.clearable
        and "same as the claude writer" in claude_on_claude.reasons[0]
    )


def test_reviewer_family_ignores_the_writer_named_after_reviewing() -> None:
    line = f"AGREE (different-model Grok: grok-4.6 reviewing an Opus writer) @ {HEAD[:8]}"
    assert cwf.reviewer_family(line) == "grok"
    res = _check(CLAUDE_BODY, [_c(line, "t1")])
    assert res.clearable


def test_agree_on_an_older_head_does_not_carry_over() -> None:
    res = _check(GROK_BODY, [_c(f"AGREE (Claude family, second-model review) @ {OLD[:8]}", "t1")])
    assert not res.clearable and "no AGREE, DISAGREE or VOID names head" in res.reasons[0]


def test_a_later_disagree_or_void_on_the_same_head_cancels_the_agree() -> None:
    agree = _c(f"AGREE (Claude family, second-model review) @ {HEAD[:8]}", "2026-10-15T01")
    for later in ("DISAGREE (Claude family, second-model review)", "VOID: the AGREE above"):
        res = _check(GROK_BODY, [agree, _c(f"{later} @ {HEAD[:8]}", "2026-10-15T02")])
        assert not res.clearable, later


def test_an_agree_that_names_no_family_or_two_is_refused() -> None:
    bare = _check(GROK_BODY, [_c(f"AGREE @ {HEAD[:8]}", "t1")])
    both = _check(GROK_BODY, [_c(f"AGREE (Claude and Grok both looked) @ {HEAD[:8]}", "t1")])
    assert not bare.clearable and "does not name its model family" in bare.reasons[0]
    assert not both.clearable


def test_unknown_writer_cannot_be_cleared() -> None:
    res = _check(
        "no writer line here",
        [_c(f"AGREE (Claude family, second-model review) @ {HEAD[:8]}", "t1")],
    )
    assert (
        not res.clearable
        and res.writer is None
        and "opposite family is undefined" in res.reasons[0]
    )


def test_trailers_decide_when_there_is_no_writer_line() -> None:
    claude_commit = "fix: x\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    human_commit = "fix: y\n\nCo-authored-by: Jian Hong <jian-hong@users.noreply.github.com>"
    fam, _ = cwf.writer_family("", [claude_commit, human_commit])
    assert fam == "claude"
    mixed, why = cwf.writer_family("", [claude_commit, "fix: z\n\nCo-Authored-By: grok-4.7 <x@y>"])
    assert mixed is None and "several families" in why


def test_cli_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "head": HEAD,
                "body": GROK_BODY,
                "commit_messages": [],
                "comments": [_c(f"AGREE (Claude family, second-model review) @ {HEAD[:8]}", "t1")],
            }
        ),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"head": HEAD, "body": GROK_BODY, "commit_messages": [], "comments": []}),
        encoding="utf-8",
    )
    assert cwf.main(["--json", str(good)]) == 0
    assert cwf.main(["--json", str(bad)]) == 1
    assert cwf.main(["--json", str(tmp_path / "missing.json")]) == 2
