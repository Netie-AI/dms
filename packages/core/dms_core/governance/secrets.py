"""Secret shapes from gitleaks, not a local word list.

The regex, keyword prefilter, and Shannon entropy floor are copied from
gitleaks ``config/gitleaks.toml`` at ``5f6ec56cfa6f8c67648d863582c7ae2f206d5c1c``.
Rules whose regex Python cannot compile are absent from the JSON (26 of 222).
A hit records the rule id and the span. The matched text is not returned.
"""

from __future__ import annotations

import json
import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).with_name("gitleaks_rules.json")
_HIT_CAP = 40


@dataclass(frozen=True)
class SecretHit:
    """A rule id and where it matched. No secret text."""

    rule_id: str
    start: int
    end: int


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    regex: re.Pattern[str]
    keywords: tuple[str, ...]
    entropy: float | None


def _shannon(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


@lru_cache(maxsize=1)
def _rules() -> tuple[_Rule, ...]:
    raw = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    out: list[_Rule] = []
    for item in raw["rules"]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            compiled = re.compile(item["regex"])
        entropy = item.get("entropy")
        out.append(
            _Rule(
                rule_id=str(item["id"]),
                regex=compiled,
                keywords=tuple(str(k).lower() for k in item.get("keywords") or ()),
                entropy=None if entropy is None else float(entropy),
            )
        )
    return tuple(out)


def secret_hits(text: str) -> tuple[SecretHit, ...]:
    """Rule ids only. The capture is used for the entropy floor, then dropped."""
    if not text:
        return ()
    # ponytail: first 1_000_000 chars. A secret past that is ``unscanned_tail``.
    # Upgrade: chunk the file the way gitleaks walks a git blob.
    window = text[:1_000_000]
    lowered = window.lower()
    found: list[SecretHit] = []
    seen: set[str] = set()
    for rule in _rules():
        if rule.keywords and not any(word in lowered for word in rule.keywords):
            continue
        for match in rule.regex.finditer(window):
            secret = match.group(1) if match.lastindex else match.group(0)
            if rule.entropy is not None and _shannon(secret) < rule.entropy:
                continue
            if rule.rule_id in seen:
                break
            seen.add(rule.rule_id)
            found.append(SecretHit(rule.rule_id, match.start(), match.end()))
            break
        if len(found) >= _HIT_CAP:
            break
    if len(text) > len(window):
        found.append(SecretHit("unscanned_tail", len(window), len(window)))
    return tuple(found)


def redact_spans(text: str, hits: tuple[SecretHit, ...]) -> str:
    """Replace matched spans with a fixed token. The span text is discarded."""
    spans = [(hit.start, hit.end) for hit in hits if hit.end > hit.start]
    if not spans:
        return text
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out: list[str] = []
    cursor = 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append("[redacted]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)
