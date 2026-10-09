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
        #: GEN-02 A/B, HTTP-gated by GEN-03: product = certified-first then
        #: Cortex ask; exact = pack/VQ only; generative = skip pack, no plan
        #: source on live_ask (bind_plan and POST /dms/query are off). None = product.
        ask_path: str | None = None,
        #: ASK-CLARIFY-01 follow-up. Ignored unless DMS_ASK_CLARIFY is on.
        clarify_id: str | None = None,
        option_id: str | None = None,
        clarify_text: str | None = None,
        #: ASK-RECONFIRM-01. Ignored unless DMS_ASK_RECONFIRM is on.
        confirm_id: str | None = None,
        confirm_choice: str | None = None,
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


# One lane list. BRONZE-GRANT-01 (#333, bronze_sheet_ask) and the pin both
# import this object. No second copy.
# A name is listed only when that handler cannot reach cortex_client and
# cannot reach L2. rules and curated both submit through CortexClient, and
# contract /ask can call a model via attempt_l2, so they are not listed.
# Their recorded zero is scored from rows only when the Platform scan
# cortex_l2_scan.json says cortex_l2 is off. See the pin.
NO_MODEL_LANES: frozenset[str] = frozenset()
MODEL_LANES = frozenset({"generative"})
_ROUTE_LANE = {
    "verified_query": "rules",
    "governed_metric": "curated",
    "generated": "generative",
    "bronze_sheet": "bronze",
    "followup": "followup",
}


def lane_for_route(route: object) -> str | None:
    """Lane name for an ask route. None when this route is not on the list."""
    if not isinstance(route, str) or route == "":
        return None
    return _ROUTE_LANE.get(route)
