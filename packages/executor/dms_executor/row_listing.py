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
from decimal import Decimal
from typing import Any

#: Rows a listing shows before it is cut.
LISTING_CAP = 12


#: Significant digits a binary float is stated with (dms#540 acceptance 3).
FLOAT_DIGITS = 15


def cell_text(value: object) -> str:
    """A cell as the answer text shows it, never past 15 significant digits.

    1610511.0500000012 prints as 1610511.05. A binary float holds 15 digits
    for certain, so a figure it cannot hold that exactly (12345678901234.56)
    is shown at 15 and the answer says so (``float_precision_note``). A
    DECIMAL prints exactly.
    """
    if isinstance(value, float) and math.isfinite(value):
        big = abs(value) >= 1e15
        if big or not value.is_integer():
            short = format(value, f".{FLOAT_DIGITS}g")
            # 1.2345678901234568e16 is a 17-digit figure: stated at 15, fixed point.
            return format(Decimal(short), "f") if big else short
    return str(value)



def float_precision_note(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The precision reason when a float cell is not exactly its 15-digit text."""
    for row in rows:
        for value in row.values():
            if (
                isinstance(value, float)
                and math.isfinite(value)
                and float(format(value, f".{FLOAT_DIGITS}g")) != value
            ):
                return (
                    f"precision: a binary-float figure is shown at {FLOAT_DIGITS} "
                    "significant digits"
                )
    return None


def row_line(row: Mapping[str, Any]) -> str:
    """One listed row, exactly as the listing prints it."""
    return "  - " + ", ".join(f"{k}={cell_text(v)}" for k, v in row.items())


def found_rows_text(
    rows: Sequence[Mapping[str, Any]], *, cap: int | None = LISTING_CAP
) -> str:
    """``Found N row(s).`` and one ``k=v`` line per row. A cut listing says so."""
    shown = rows if cap is None else rows[:cap]
    text = f"Found {len(rows)} row(s)."
    if len(shown) < len(rows):
        text += f" Showing {len(shown)} of {len(rows)}."
    if shown:
        text += "\n" + "\n".join(row_line(row) for row in shown)
    return text
