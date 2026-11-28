"""UI-CHIP-TYPO-01: chips a customer can click spell category.

The misspelling lived in the empty-chat chip list. Studio's starter list was
already spelled correctly. This pins both lists. Pack traps stay in the pack.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _ROOT / "apps/ui/src/lib/fixtures.ts"
_STUDIO = _ROOT / "apps/ui/src/pages/StudioPage.tsx"

_SUGGESTED = (
    "What is our total spend by supplier country?",
    "What is total stock value by category?",
    "Top 5 selling SKUs by revenue",
    "How many SKUs do we have in inventory?",
    "Show warehouse capacity utilisation",
    "Show top 3 category sales",
)
_STARTER = (
    "What is our total spend by supplier country?",
    "What is total stock value by category?",
    "Which SKUs are below reorder level in warehouse A?",
    "Show warehouse capacity utilisation",
    "Which locations are cold storage?",
)


def _array_strings(path: Path, name: str) -> list[str]:
    src = path.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*\[(.*?)\]", src, re.S)
    assert match, f"{name} missing from {path.name}"
    return re.findall(r'"([^"]*)"', match.group(1))


def test_clickable_chips_spell_category() -> None:
    suggested = _array_strings(_FIXTURES, "SUGGESTED_QUESTIONS")
    starter = _array_strings(_STUDIO, "STARTER_QUESTIONS")
    assert tuple(suggested) == _SUGGESTED
    assert tuple(starter) == _STARTER
    for chip in (*suggested, *starter):
        assert "categoty" not in chip
