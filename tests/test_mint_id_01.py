"""mint_id stays letters-only so mask_unknown_keys does not rewrite it."""

from __future__ import annotations

from dms_core.ids import mint_id
from dms_core.pii import mask_unknown_keys

_PREFIXES = ("clr", "tkt")
_COUNT = 10_000
# Hex digits. The 12-digit run is an NRIC-shaped value the real masker rewrites.
_HEX_WITH_DIGITS = "a900101011234f"


def test_minted_ids_survive_the_real_masker_and_hex_digits_do_not() -> None:
    seen: set[str] = set()
    for prefix in _PREFIXES:
        fresh = [mint_id(prefix) for _ in range(_COUNT)]
        assert len(fresh) == _COUNT
        masked = mask_unknown_keys({str(i): item for i, item in enumerate(fresh)})
        for i, item in enumerate(fresh):
            body = item[len(prefix) :]
            assert item.startswith(prefix)
            assert body.isalpha() and body.islower()
            assert len(body) >= 28
            assert "@" not in item
            assert item == masked[str(i)]
            seen.add(item)
    assert len(seen) == _COUNT * len(_PREFIXES)
    rewritten = mask_unknown_keys({"ticket_id": _HEX_WITH_DIGITS})
    assert rewritten["ticket_id"] != _HEX_WITH_DIGITS
