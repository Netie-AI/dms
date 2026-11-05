# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model-bound sample values pass through one guard (dms#272 PII-01).

Not upstream. The caller binds a guard for the compile, the same way
``ov_client.bind_client`` binds the client. Every prompt builder that writes
sample values asks :func:`model_sample_values` for them. The guard returns
``(model_values, personal)``: placeholders or shapes for a personal-data
column, a short slice for any other column.

With no guard bound, a prompt gets no sample values at all. A guard that
raises is treated the same way. Local SQL (the semantic-FK membership probe)
does not go through this: it runs on the caller connection and never leaves.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any

from ao_vendor.utils.sample_values import parse_sample_values

#: ``(table, column, values) -> (model_values, personal)``.
SampleGuard = Callable[[str, str, list[Any]], tuple[list[Any], bool]]

_GUARD: ContextVar[SampleGuard | None] = ContextVar("ao_sample_guard", default=None)


def bind_sample_guard(guard: SampleGuard | None) -> Token[SampleGuard | None]:
    return _GUARD.set(guard)


def reset_sample_guard(token: Token[SampleGuard | None]) -> None:
    _GUARD.reset(token)


def guard_samples(table: str, column: str, raw: Any) -> tuple[list[Any] | None, bool]:
    """Return ``(model_values, personal)``. ``None`` input stays ``None``.

    Fails closed: no bound guard, or a guard error, sends no values and
    counts the column as personal.
    """
    values = parse_sample_values(raw)
    if values is None:
        return None, False
    guard = _GUARD.get()
    if guard is None:
        return [], True
    try:
        safe, personal = guard(str(table or ""), str(column or ""), list(values))
    except Exception:
        return [], True
    return list(safe), bool(personal)


def model_sample_values(table: str, column: str, raw: Any) -> list[Any] | None:
    """Sample values a prompt may carry for ``table.column``."""
    return guard_samples(table, column, raw)[0]
