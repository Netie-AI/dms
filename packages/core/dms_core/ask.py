"""Ask / serving facades used by the API — implementations live in dms_executor."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AskServicePort(Protocol):
    """Demo or live ask. Injected on app.state; API must not import dms_executor."""

    def demo_ask(self, question: str, *, space_id: str | None = None) -> dict[str, Any]: ...

    def live_ask(
        self,
        question: str,
        *,
        space_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class AskServiceError(Exception):
    """Stable failure from ask/bind — HTTP layer maps ``code``."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)
