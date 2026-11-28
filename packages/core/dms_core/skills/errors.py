"""Stable codes for the skills library. The text is the code, never a secret."""

from __future__ import annotations


class SkillError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
