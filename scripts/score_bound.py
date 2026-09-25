"""Rule-of-three error bound for a zero-wrong scorer summary (A1-01, NETIE.md rule 7).

A bare "WRONG=0" is not a claim. Zero wrong in n answered bounds the true
confident-wrong rate at about 300/n pct (R-0010, 95 pct), so below n=300 the
scorer may not imply "under one percent". Every scorer that prints a zero must
print n and the bound on that line or the next; ``zero_wrong_summary_bounded``
is the check that makes that a gate (R-0007: it can fail).

Reporting only. Nothing here decides OK/LAYER/ABSTAIN/WRONG.
"""

from __future__ import annotations

import re

RULE_OF_THREE = 300.0

# A line that reports a zero-wrong verdict, in either scorer's wording.
_ZERO_WRONG = re.compile(r"WRONG=0\b|\b0 confidently wrong\b")
_HAS_N = re.compile(r"\banswered=\d+")
_HAS_BOUND = re.compile(r"\bbound (about \d+(\.\d+)? pct|n/a)")


def bound_pct(answered: int) -> float | None:
    """300/n for n>0; None when nothing was answered (never 0 pct)."""
    return RULE_OF_THREE / answered if answered > 0 else None


def bound_line(answered: int) -> str:
    """The n-and-bound sentence that must follow a zero-wrong verdict."""
    pct = bound_pct(answered)
    if pct is None:
        return "answered=0 bound n/a (nothing answered)"
    return f"answered={answered} bound about {pct:.2f} pct (rule of three, 95 pct)"


def zero_wrong_summary_bounded(lines: list[str]) -> bool:
    """True iff every zero-wrong line carries answered= and a bound, on it or the next.

    A summary with no zero-wrong line is vacuously bounded; a zero-wrong line
    with a bare zero is not, and that is the failure this gate exists to raise.
    """
    for i, line in enumerate(lines):
        if not _ZERO_WRONG.search(line):
            continue
        window = line + "\n" + (lines[i + 1] if i + 1 < len(lines) else "")
        if not (_HAS_N.search(window) and _HAS_BOUND.search(window)):
            return False
    return True
