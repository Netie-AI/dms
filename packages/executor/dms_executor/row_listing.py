"""The one writer of an answer's row listing (FIG-FIDELITY-01, dms#508).

Five renderers each printed ``str(float)`` and cut the list at twelve without
saying so. A sum of two 2-dp floats printed that way carries float noise, and
the noise looked like a phone number to the masker. One writer, so the text
rules hold on every listing door: a number prints without float noise, and a
cut listing says how many rows it shows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

#: Rows a listing shows before it is cut.
LISTING_CAP = 12


def cell_text(value: object) -> str:
    """A cell as the answer text shows it. 15 significant digits drop float noise."""
    if isinstance(value, float) and math.isfinite(value) and not value.is_integer():
        return format(value, ".15g")
    return str(value)


def found_rows_text(
    rows: Sequence[Mapping[str, Any]], *, cap: int | None = LISTING_CAP
) -> str:
    """``Found N row(s).`` and one ``k=v`` line per row. A cut listing says so."""
    shown = rows if cap is None else rows[:cap]
    text = f"Found {len(rows)} row(s)."
    if len(shown) < len(rows):
        text += f" Showing {len(shown)} of {len(rows)}."
    if shown:
        text += "\n" + "\n".join(
            "  - " + ", ".join(f"{k}={cell_text(v)}" for k, v in row.items()) for row in shown
        )
    return text
