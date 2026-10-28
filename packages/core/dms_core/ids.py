"""Letters-only ids.

``mint_id(prefix)`` is the one id minter. #419's ``mint_clarify_id`` calls
this with the prefix ``clr``. The body is lowercase ``a-z`` from ``secrets``.
28 letters is ``ceil(128 / log2(26))`` bits, so the body has at least 128
bits of entropy. No digits, no ``@``, no separators: a digit run is what
``mask_unknown_keys`` rewrites.
"""

from __future__ import annotations

import math
import secrets

_LETTERS = "abcdefghijklmnopqrstuvwxyz"
# 28 * log2(26) > 128.
_BODY_LEN = math.ceil(128 / math.log2(len(_LETTERS)))


def mint_id(prefix: str) -> str:
    """Return ``prefix`` plus a letters-only body of at least 128 bits."""
    if not prefix or any(ch not in _LETTERS for ch in prefix):
        raise ValueError("prefix must be lowercase letters")
    body = "".join(secrets.choice(_LETTERS) for _ in range(_BODY_LEN))
    return prefix + body
