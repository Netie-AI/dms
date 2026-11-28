"""Request-time budget check. No model call and no provider call.

OpenVault is read through ``reader``. There is no per-user cap write on that
API (``GET /api/usage`` and ``GET /api/keys/quota`` are read-only, and
``PUT /api/ship/budget`` is one install cap). A USD figure is enforced when
the readback includes ``est_cost_usd``. A USD cap with no price stops the
request instead of treating the spend as zero. Token caps use ``total_tokens``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from dms_core.governance.store import Assignment, GovStore, Spend

Reader = Callable[[str, float], "UsageRead"]


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    at: datetime
    tokens: int
    cost_usd: float | None
    model: str


@dataclass(frozen=True)
class KeyCap:
    tokens_used: int
    daily_limit: int | None


@dataclass(frozen=True)
class UsageRead:
    ok: bool
    events: tuple[UsageEvent, ...] = ()
    keys: tuple[KeyCap, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: int = 200
    code: str = ""
    message: str = ""


def _day_start(now: datetime) -> datetime:
    current = now.astimezone(UTC)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: datetime) -> datetime:
    current = now.astimezone(UTC)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _deny(status: int, code: str, message: str) -> Decision:
    return Decision(False, status, code, message)


def _feature_message(feature: str) -> str:
    label = {"ask": "Ask", "create": "Create", "connectors": "Connectors"}.get(feature, feature)
    return f"{label} is turned off for your account. A manager can enable it in Studio."


def _budget_message(window: str, kind: str, spent: float, cap: float) -> str:
    if kind == "usd":
        shown_spent = f"{spent:.2f}"
        shown_cap = f"{cap:.2f}"
        unit = "USD"
    else:
        shown_spent = str(int(spent))
        shown_cap = str(int(cap))
        unit = "tokens"
    return (
        f"Your {window} {unit} budget is {shown_cap} and {shown_spent} is already recorded. "
        "This request is stopped. A manager can raise the budget in Studio."
    )


def feature_decision(assignment: Assignment | None, feature: str) -> Decision:
    if feature not in {"ask", "create", "connectors"}:
        return _deny(400, "gov_feature", "That feature is not one a manager can assign.")
    if assignment is None:
        return Decision(True)
    allowed = {
        "ask": assignment.allow_ask,
        "create": assignment.allow_create,
        "connectors": assignment.allow_connectors,
    }[feature]
    if allowed:
        return Decision(True)
    return _deny(403, "gov_feature_off", _feature_message(feature))


def _has_cap(row: Assignment | None) -> bool:
    if row is None:
        return False
    return any(
        value is not None
        for value in (row.daily_usd, row.monthly_usd, row.daily_tokens, row.monthly_tokens)
    )


def _sum(rows: list[Spend]) -> tuple[int, float | None]:
    tokens = 0
    costs: list[float] = []
    unpriced = False
    for row in rows:
        tokens += row.tokens
        if row.cost_usd is None:
            unpriced = True
        else:
            costs.append(row.cost_usd)
    if unpriced:
        return tokens, None
    return tokens, sum(costs)


def _window_block(
    row: Assignment, tokens: int, usd: float | None, *, window: str, usd_required: bool
) -> Decision | None:
    cap_tokens = row.daily_tokens if window == "daily" else row.monthly_tokens
    cap_usd = row.daily_usd if window == "daily" else row.monthly_usd
    if cap_tokens is not None and tokens >= cap_tokens:
        return _deny(
            429,
            "gov_budget",
            _budget_message(window, "tokens", tokens, cap_tokens),
        )
    if cap_usd is None:
        return None
    if usd is None:
        if usd_required and cap_tokens is None:
            return _deny(
                429,
                "gov_usd_unpriced",
                "A USD budget is set and OpenVault did not price this usage, "
                "so the budget cannot be checked. This request is stopped "
                "rather than treating the spend as zero. Token budgets still "
                "apply when OpenVault reports tokens.",
            )
        return None
    if usd >= cap_usd:
        return _deny(429, "gov_budget", _budget_message(window, "usd", usd, cap_usd))
    return None


def _keys_exhausted(keys: tuple[KeyCap, ...]) -> bool:
    limited = [key for key in keys if key.daily_limit is not None]
    if not limited:
        return False
    return all(key.tokens_used >= int(key.daily_limit or 0) for key in limited)


def budget_decision(
    store: GovStore,
    *,
    tenant_id: str,
    user_id: str,
    assignment: Assignment | None,
    reader: Reader,
    now: datetime | None = None,
) -> Decision:
    """Pull OpenVault when a cap is set, then compare the ledger to that cap."""
    if not _has_cap(assignment):
        return Decision(True)
    assert assignment is not None
    current = now or datetime.now(UTC)
    since = _month_start(current).timestamp()
    read = reader(user_id, since)
    if read.truncated:
        return _deny(
            429,
            "gov_usage_partial",
            "OpenVault returned a partial usage ledger, so the budget cannot be checked. "
            "This request is stopped.",
        )
    if not read.ok:
        if not store.readback_ok(tenant_id, user_id):
            return _deny(
                503,
                "gov_usage_unread",
                "OpenVault usage could not be read, and a budget is set. "
                "This request is stopped until the usage readback succeeds.",
            )
    else:
        for event in read.events:
            store.add_spend(
                Spend(
                    event_id=event.event_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    at=event.at,
                    tokens=event.tokens,
                    cost_usd=event.cost_usd,
                    model=event.model[:120],
                    source="ov",
                )
            )
        store.mark_readback(tenant_id, user_id, True)
        if _keys_exhausted(read.keys):
            return _deny(
                429,
                "gov_key_cap",
                "OpenVault reports every provider key at its daily token cap. "
                "Routing stays in OpenVault. This request is stopped until that cap resets.",
            )
    rows = store.spend_for(tenant_id, user_id)
    day = _day_start(current)
    month = _month_start(current)
    day_rows = [row for row in rows if row.at >= day]
    month_rows = [row for row in rows if row.at >= month]
    for window, subset in (("daily", day_rows), ("monthly", month_rows)):
        tokens, usd = _sum(subset)
        blocked = _window_block(assignment, tokens, usd, window=window, usd_required=True)
        if blocked is not None:
            return blocked
    return Decision(True)
