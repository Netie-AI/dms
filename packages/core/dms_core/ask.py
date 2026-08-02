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
        #: Narrow the session manifest to these warehouse tables, so a question
        #: grounded in chosen files is enforced by the engine rather than
        #: suggested to the model. Empty/None means the whole Space.
        tables: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class AskServiceError(Exception):
    """Stable failure from ask/bind — HTTP layer maps ``code``."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class GroundingRefused(Exception):
    """A grounding selection named a source that cannot be granted.

    Raised instead of quietly narrowing to the grantable subset, or - worse -
    falling back to everything, which granted the whole demo warehouse while
    the UI still read "Grounded in 1 file".

    Lives here rather than in the executor because the HTTP layer has to render
    it and may not import the executor (.importlinter).
    """

    code = "grounding_not_grantable"

    def __init__(self, *, ungrantable: list[str], grantable: list[str]) -> None:
        self.ungrantable = list(ungrantable)
        self.grantable = list(grantable)
        named = ", ".join(self.ungrantable)
        is_are = "it is" if len(self.ungrantable) == 1 else "they are"
        super().__init__(
            f"Cannot ground this question in {named}: {is_are} not readable from "
            f"here. Pick a different file, or ask in a Space that has access."
        )

    @property
    def message(self) -> str:
        return str(self)
