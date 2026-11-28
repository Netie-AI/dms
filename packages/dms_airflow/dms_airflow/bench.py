"""WRONG check for a Space benchmark report.

``wrong`` above 0 fails the task. A missing or non-integer ``wrong`` also
fails: that is not a pass. A served CLI envelope is a different map and
does not use this function.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DmsBenchmarkError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fail_if_wrong(report: Mapping[str, Any]) -> None:
    wrong = report.get("wrong")
    # bool is an int subclass. A JSON true is not a count.
    if isinstance(wrong, bool) or not isinstance(wrong, int):
        raise DmsBenchmarkError("benchmark_wrong_untyped")
    if wrong > 0:
        raise DmsBenchmarkError("benchmark_wrong")
