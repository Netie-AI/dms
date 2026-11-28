"""Signing failures. The message is the code only. No key material."""

from __future__ import annotations


class SignError(Exception):
    """A signing call stopped. ``code`` is safe to return to the client."""

    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)

    def __repr__(self) -> str:
        return f"SignError(code={self.code!r}, status={self.status!r})"
