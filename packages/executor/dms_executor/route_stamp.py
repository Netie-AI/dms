"""Route stamp written by the path that builds the envelope.

Two fields: ``served_route`` and ``ladder_rung``. The builder writes them.
``plan_origin`` and ``served_model`` are the producer's copied stamps and
this module never writes them. A path with no attribution (model SQL whose
producer did not name the model) writes no stamp, so it stays unattributed.
``freeze_route`` puts the builder's rung back and does not choose a route.

Rungs are the tokens grade52 already reads. This module does not read the
question or a lane table.
"""

from __future__ import annotations

from typing import Any

ROUTE_VERIFIED = "verified_query"
ROUTE_L1 = "l1_governed_metric"
ROUTE_COMPILE = "ontology_compile"
ROUTE_LADDER = "ladder_rung"
ROUTE_MODEL = "model"

ROUTES = (
    ROUTE_VERIFIED,
    ROUTE_L1,
    ROUTE_COMPILE,
    ROUTE_LADDER,
    ROUTE_MODEL,
)

# Rungs freeze_route writes beside a closed route.
RUNGS = frozenset({"l0", "l1", "compile", "ladder", "generate_sql"})

# Stages the builders pass to stamp_abstain, including a route string used as
# the stage (sql_refusal_envelope, reserved_as_of_abstain). A new builder
# stage belongs here. Anything else is not a route, so the scrub drops it.
ABSTAIN_STAGES = frozenset(
    {
        "abstain",
        "bronze",
        "cascade",
        "connector",
        "cortex_ask",
        "demo",
        "demo_pack",
        "envelope",
        "extract_loop",
        "followup",
        "generated",
        "generative",
        "governed_metric",
        "insights_budget",
        "openvault",
        "reconfirm_direct",
        "reconfirm_no",
        "reserved_as_of",
        "schema_build",
        "serve_gap",
        "served_sql",
        "serving_lease",
        "unspecified",
        "user_sql",
    }
)

# ladder_rung, and whether the route needs a non-empty served_model.
_PROFILES: dict[str, tuple[str, bool]] = {
    ROUTE_VERIFIED: ("l0", False),
    ROUTE_L1: ("l1", False),
    ROUTE_COMPILE: ("compile", False),
    ROUTE_LADDER: ("ladder", False),
    ROUTE_MODEL: ("generate_sql", True),
}

# Customer route and served_route use different tokens for one path.
_ROUTE_CLASS = {
    "generated": "ai",
    "model": "ai",
    "generate_sql": "ai",
    "model_sql": "ai",
    "ai": "ai",
    "ontology_compile": "compile",
    "compile": "compile",
    "ontology_ranking": "compile",
    "ontology_plan": "compile",
    "ladder_rung": "ladder",
    "ladder": "ladder",
    "l1": "l1",
    "l1_governed_metric": "l1",
    "governed_metric": "l1",
    "verified_query": "l0",
    "l0": "l0",
    "l0_certified": "l0",
}

# Producer plan_origin tokens that name model SQL.
_AI_TOKENS = frozenset({"generate_sql", "model_sql", "ai"})


def _text(env: dict[str, Any], key: str) -> str:
    if key not in env:
        return ""
    raw = env.get(key)
    if raw is None:
        return ""
    return str(raw).strip()


def _ran_sql(env: dict[str, Any]) -> bool:
    sql_used = env.get("sql_used")
    return isinstance(sql_used, str) and bool(sql_used.strip())


def abstain_route(stage: str) -> str:
    """``abstain:<stage>``. The stage is the builder's stage, not a question word."""
    token = "".join(ch for ch in str(stage or "").strip().lower() if ch.isalnum() or ch == "_")
    return "abstain:" + (token or "unspecified")


def is_abstain_route(route: str) -> bool:
    if not route.startswith("abstain:"):
        return False
    stage = route[len("abstain:") :]
    return bool(stage) and stage == "".join(
        ch for ch in stage if ch.isalnum() or ch == "_"
    )


def kept_stamp(value: object) -> bool:
    """True for a closed route, its rung, or ``abstain:<known stage>``.

    ``abstain:shipments`` is a table name in the stamp field. The scrub
    drops it. ``freeze_route`` does not.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text in ROUTES or text in RUNGS:
        return True
    if not is_abstain_route(text):
        return False
    return text[len("abstain:") :] in ABSTAIN_STAGES


def stamp_abstain(
    env: dict[str, Any] | None,
    *,
    stage: str,
) -> dict[str, Any] | None:
    """No-SQL abstain route. Not compile, L1, or ai.

    Writes ``served_route`` and ``ladder_rung`` only. The customer reason
    (``abstain_reason``, assumptions, text) is not touched here.
    """
    if not isinstance(env, dict):
        return env
    route = abstain_route(stage)
    env["served_route"] = route
    env["ladder_rung"] = route
    return env


def route_class(token: object) -> str:
    """Class of a customer route or a served_route token. Empty when unknown."""
    return _ROUTE_CLASS.get(str(token or "").strip().lower(), "")


def stamp_route(
    env: dict[str, Any] | None,
    route: str,
) -> dict[str, Any] | None:
    """Write this path's stamp. Call at the build site.

    A no-SQL abstain keeps its ``abstain:<stage>`` stamp: nothing was served.
    """
    if not isinstance(env, dict):
        return env
    if env.get("abstained") is True and not _ran_sql(env):
        return env
    rung, _needs_model = _PROFILES[route]
    env["served_route"] = route
    env["ladder_rung"] = rung
    # A rule path built these rows. "generated" would claim the model did.
    if route != ROUTE_MODEL and env.get("route") == "generated":
        env["route"] = rung
    return env


def freeze_route(env: dict[str, Any] | None) -> dict[str, Any] | None:
    """Put back the rung the builder already wrote. Does not pick a route."""
    if not isinstance(env, dict):
        return env
    route = _text(env, "served_route")
    if is_abstain_route(route):
        env["ladder_rung"] = route
        return env
    if route not in _PROFILES:
        return env
    env["ladder_rung"] = _PROFILES[route][0]
    return env


def route_of(env: dict[str, Any] | None) -> str:
    """The one route this envelope claims, or unattributed, or contradictory.

    Reads the builder's stamp. No ``served_route`` and no ``ladder_rung`` is
    unattributed; half a stamp is contradictory. ``served_model`` is a copied
    setup field, so a model name beside a compile route does not change it
    (grade52 counts that compile too). The model route needs a model name,
    and a producer ``plan_origin`` there must name model SQL.
    """
    if not isinstance(env, dict):
        return "unattributed"
    route = _text(env, "served_route")
    rung = _text(env, "ladder_rung").lower()
    if not route:
        return "contradictory" if rung else "unattributed"
    if is_abstain_route(route):
        return route if rung == route else "contradictory"
    profile = _PROFILES.get(route)
    if profile is None:
        return "contradictory"
    expected_rung, needs_model = profile
    if rung != expected_rung:
        return "contradictory"
    if needs_model:
        if not _text(env, "served_model"):
            return "contradictory"
        origin = _text(env, "plan_origin").lower()
        if origin and origin not in _AI_TOKENS:
            return "contradictory"
    return route
