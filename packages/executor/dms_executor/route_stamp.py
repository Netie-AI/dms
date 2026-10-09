"""Route stamp written by the path that builds the envelope.

One field set: ``served_route`` plus ``plan_origin``, ``ladder_rung``, and
``served_model``. The builder writes it. A later copy of setup fields must
not add a second family; ``freeze_route`` puts the builder's profile back
and does not choose a route.

Companions are the tokens grade52 (a2b39e21) already treats as one path.
This module does not read the question or a lane table.
"""

from __future__ import annotations

from typing import Any

ROUTE_VERIFIED = "verified_query"
ROUTE_L1 = "l1_governed_metric"
ROUTE_COMPILE = "ontology_compile"
ROUTE_EXEC = "exec_stub_l2"
ROUTE_LADDER = "ladder_rung"
ROUTE_MODEL = "model"

ROUTES = (
    ROUTE_VERIFIED,
    ROUTE_L1,
    ROUTE_COMPILE,
    ROUTE_EXEC,
    ROUTE_LADDER,
    ROUTE_MODEL,
)

# plan_origin, ladder_rung, whether a non-empty served_model belongs.
_PROFILES: dict[str, tuple[str, str, bool]] = {
    ROUTE_VERIFIED: ("l0", "l0", False),
    ROUTE_L1: ("l1", "l1", False),
    ROUTE_COMPILE: ("compile", "compile", False),
    ROUTE_EXEC: ("oracle", "oracle", False),
    ROUTE_LADDER: ("compile", "compile", False),
    ROUTE_MODEL: ("generate_sql", "generate_sql", True),
}

STAMP_FIELDS = ("served_route", "plan_origin", "ladder_rung", "served_model")

# Stamp tokens. Not question words and not a lane table.
_RULE_TOKENS = {
    "l0": "l0",
    "l0_certified": "l0",
    "l1": "l1",
    "l1_governed_metric": "l1",
    "compile": "compile",
    "oracle": "oracle",
}
_AI_TOKENS = frozenset({"generate_sql", "model_sql", "ai"})


def _text(env: dict[str, Any], key: str) -> str:
    if key not in env:
        return ""
    raw = env.get(key)
    if raw is None:
        return ""
    return str(raw).strip()


def _families(env: dict[str, Any]) -> set[str]:
    claims: list[str] = []
    for key in ("plan_origin", "ladder_rung"):
        token = _text(env, key).lower()
        if not token:
            continue
        if token in _RULE_TOKENS:
            claims.append(_RULE_TOKENS[token])
        elif token in _AI_TOKENS:
            claims.append("ai")
        else:
            claims.append("unknown")
    if _text(env, "served_model"):
        claims.append("ai")
    return set(claims)


def stamp_route(
    env: dict[str, Any] | None,
    route: str,
    *,
    served_model: str | None = None,
) -> dict[str, Any] | None:
    """Write this path's stamp. Call at the build site."""
    if not isinstance(env, dict):
        return env
    origin, rung, needs_model = _PROFILES[route]
    env["served_route"] = route
    env["plan_origin"] = origin
    env["ladder_rung"] = rung
    if needs_model:
        model = str(served_model or "").strip()
        if model:
            env["served_model"] = model
    return env


def freeze_route(env: dict[str, Any] | None) -> dict[str, Any] | None:
    """Put back the profile the builder already wrote. Does not pick a route."""
    if not isinstance(env, dict):
        return env
    route = _text(env, "served_route")
    if route not in _PROFILES:
        return env
    return stamp_route(env, route, served_model=_text(env, "served_model") or None)


def route_of(env: dict[str, Any] | None) -> str:
    """The one route this envelope claims, or unattributed, or contradictory.

    Reads the stamp fields only. A missing ``served_route`` is unattributed
    unless the companions disagree, which is contradictory.
    """
    if not isinstance(env, dict):
        return "unattributed"
    families = _families(env)
    if len(families) > 1 or "unknown" in families:
        return "contradictory"
    route = _text(env, "served_route")
    if not route:
        return "unattributed"
    profile = _PROFILES.get(route)
    if profile is None:
        return "contradictory"
    origin, rung, needs_model = profile
    if _text(env, "plan_origin").lower() != origin or _text(env, "ladder_rung").lower() != rung:
        return "contradictory"
    model = _text(env, "served_model")
    if needs_model != bool(model):
        return "contradictory"
    if families and families != {_family_for(route)}:
        return "contradictory"
    return route


def _family_for(route: str) -> str:
    origin, _rung, needs_model = _PROFILES[route]
    if needs_model:
        return "ai"
    return _RULE_TOKENS[origin]
