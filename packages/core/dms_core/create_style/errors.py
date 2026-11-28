"""Errors for Create style. The message is a code, not a sentence we composed."""

from __future__ import annotations


class CreateError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
