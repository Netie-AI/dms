"""Join answers come from Ontology.compile, not from free SQL.

The model may name a measure, a group, and filters. It does not write the
join. A measure answers only after a steward confirms it. Confirming a name
does not create the measure: auto-deriving one is refused.

Single-table SQL and a short route allowlist (certified / governed metric /
catalog) still pass through. Anything else that joins two relations abstains
unless this module compiled it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from cortex_client.models import AskResponse

from dms_executor.envelope import _sql_cited_labels
from dms_executor.manifest import reject_hostile_chat_sql
from dms_executor.ontology import (
    CompiledQuery,
    Ontology,
    Refusal,
    demo_ontology,
)

logger = logging.getLogger(__name__)

_TABLE = "main._confirmed_measures"
#: Routes whose SQL was already a steward or catalog asset. Free L2 SQL is not
#: on this list, so a generated join cannot borrow a certified badge's pass.
_FREE_JOIN_ROUTES = frozenset(
    {"certified", "certified_metric", "governed_metric", "catalog"}
)
#: Exact phrases for the measures written in the demo pack. Not a parser.
_MEASURE_ALIASES: dict[str, tuple[str, ...]] = {
    "shipment_count": ("shipment count", "how many shipments"),
    "shipping_cost_myr": ("shipping cost", "shipping cost myr"),
    "stock_value_myr": ("stock value", "stock value myr"),
    "outbound_value_myr": ("outbound value", "outbound value myr"),
}


@dataclass(frozen=True)
class TypedPlan:
    """Slots the model fills. The compiler writes the SQL."""

    measure: str
    group_by: tuple[tuple[str, str], ...] = ()
    filters: tuple[tuple[str, str, str, Any], ...] = ()
    via: dict[str, str] | None = None
    limit: int | None = None


@dataclass(frozen=True)
class ResolvedJoin:
    response: AskResponse
    compiled: bool = False


_PUNCT = re.compile(r"[?!.]+$")
_JOIN_WORD = re.compile(r"\bjoin\b", re.I)
_FROM_COMMA = re.compile(r"\bfrom\b[^;]*,", re.I)
_CLAUSE = re.compile(r"\b(where|group|order|limit|having)\b", re.I)


def _fold(question: str) -> str:
    folded = " ".join((question or "").lower().replace("_", " ").replace("-", " ").split())
    return _PUNCT.sub("", folded).strip()


def match_authored_measure(question: str, measures: Collection[str]) -> str | None:
    """Exact name or authored alias. Anything else misses."""
    q = _fold(question)
    if not q:
        return None
    for name in measures:
        keys = {_fold(name), *(_fold(alias) for alias in _MEASURE_ALIASES.get(name, ()))}
        if q in keys:
            return name
    return None


def maybe_compiled_measure_ask(
    question: str,
    *,
    warehouse: Path | None,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Answer a demo-pack measure by compile(). Miss returns None.

    This runs before Cortex. A question that is not an authored name or alias
    is left for the rest of the ask path.
    """
    if warehouse is None:
        return None
    onto = demo_ontology(warehouse)
    name = match_authored_measure(question, onto.measures)
    if name is None:
        return None
    from dms_executor.envelope import build_answer_envelope

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        violations = onto.verify(con)
        if violations or not onto.verified:
            return build_answer_envelope(
                answer_id=f"ans_measure_{name}",
                text=(
                    "Abstained: ontology_unverified. verify() did not pass, "
                    "so this measure will not be compiled."
                ),
                badge="ABSTAIN",
                abstained=True,
                rows=[],
                assumptions=["ontology_unverified"],
                space_id=space_id,
                session_id=session_id,
                ask_mode="live",
            )
        compiled = compile_confirmed(onto, TypedPlan(name), set(onto.measures))
        if isinstance(compiled, Refusal):
            return build_answer_envelope(
                answer_id=f"ans_measure_{name}",
                text=f"Abstained: {compiled.reason}. {compiled.detail}",
                badge="ABSTAIN",
                abstained=True,
                rows=[],
                assumptions=[compiled.reason],
                space_id=space_id,
                session_id=session_id,
                ask_mode="live",
            )
        rows = _run(con, compiled.sql)
    finally:
        con.close()
    return build_answer_envelope(
        answer_id=f"ans_measure_{name}",
        text=f"Compiled {compiled.measure} at grain {compiled.grain}.",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        rows=rows,
        sql_used=compiled.sql,
        assumptions=["compiled_measure", *compiled.notes],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
    )


def joined_sql(sql: str | None) -> bool:
    """True when the statement joins.

    Unique relation names are not enough: ``FROM schools a JOIN schools b``
    cites one name twice and is still a join. A comma in FROM is a join too.
    """
    raw = (sql or "").strip()
    if not raw or raw.startswith("--"):
        return False
    if _JOIN_WORD.search(raw):
        return True
    if len(_sql_cited_labels(raw)) > 1:
        return True
    head = _CLAUSE.split(raw, maxsplit=1)[0]
    return _FROM_COMMA.search(head) is not None


def route_allows_free_join(route: str | None) -> bool:
    return (route or "").lower() in _FREE_JOIN_ROUTES


def parse_typed_plan(raw: Any) -> TypedPlan | Refusal | None:
    """None means the model sent no plan. A dict that is not a plan is a refusal."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return Refusal("bad_plan", "A typed plan is an object with a measure name.")
    measure = raw.get("measure")
    if not isinstance(measure, str) or not measure.strip():
        return Refusal("bad_plan", "A typed plan needs a measure name.")
    group_by: list[tuple[str, str]] = []
    for item in raw.get("group_by") or []:
        pair = _pair(item)
        if pair is None:
            return Refusal("bad_plan", "group_by entries are (object, column).")
        group_by.append(pair)
    filters: list[tuple[str, str, str, Any]] = []
    for item in raw.get("filters") or []:
        filt = _filter(item)
        if filt is None:
            return Refusal("bad_plan", "filters are (object, column, operator, value).")
        filters.append(filt)
    via_raw = raw.get("via")
    via: dict[str, str] | None
    if via_raw is None:
        via = None
    elif isinstance(via_raw, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in via_raw.items()
    ):
        via = dict(via_raw)
    else:
        return Refusal("bad_plan", "via is an object of object name to link name.")
    limit = raw.get("limit")
    if limit is not None and not isinstance(limit, int):
        return Refusal("bad_plan", "limit is a whole number.")
    return TypedPlan(
        measure.strip(),
        tuple(group_by),
        tuple(filters),
        via,
        limit,
    )


def compile_confirmed(
    onto: Ontology,
    plan: TypedPlan,
    confirmed: Collection[str],
) -> CompiledQuery | Refusal:
    """Compile only a measure a steward has already confirmed.

    The ask path also refuses an existential filter. That reading keeps a fact
    row if any linked row matches, and the caller did not ask for that reading.
    """
    if plan.measure not in set(confirmed):
        return Refusal(
            "unconfirmed_measure",
            f"{plan.measure!r} is not a confirmed measure. Cortex may suggest "
            "one. A steward confirms it before it can answer.",
        )
    compiled = onto.compile(
        plan.measure,
        group_by=plan.group_by,
        filters=plan.filters,
        via=plan.via,
        limit=plan.limit,
    )
    if isinstance(compiled, Refusal):
        return compiled
    if compiled.existential:
        return Refusal(
            "existential_filter",
            "This filter crosses a many-to-many link, so more than one reading "
            "is defensible. The ask path will not pick one.",
        )
    return compiled


def confirm_measure(path: Path, name: str, onto: Ontology) -> None:
    """Record a steward confirmation. Does not create the measure."""
    if name not in onto.measures:
        raise KeyError(
            f"measure {name!r} is not on this ontology. "
            "Confirmation does not invent one."
        )
    con = duckdb.connect(str(path))
    try:
        _ensure(con)
        con.execute(f"DELETE FROM {_TABLE} WHERE measure_name = ?", [name])
        con.execute(f"INSERT INTO {_TABLE} VALUES (?)", [name])
    finally:
        con.close()


def confirmed_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    try:
        rows = con.execute(f"SELECT measure_name FROM {_TABLE}").fetchall()
    except duckdb.Error:
        return set()
    return {str(row[0]) for row in rows}


def resolve_cortex_join(
    resp: AskResponse,
    *,
    warehouse: Path | None = None,
    onto: Ontology | None = None,
    confirmed: Collection[str] | None = None,
) -> ResolvedJoin:
    """Drop a model's join. Answer from compile() when the plan is confirmed.

    Certified routes are left alone. Single-table SQL with no plan is left
    alone. A plan that does not compile becomes an abstain, never the model's
    rows.
    """
    if resp.abstained or route_allows_free_join(resp.route):
        return ResolvedJoin(resp, compiled=False)
    plan = parse_typed_plan(resp.ontology_plan)
    joined = joined_sql(resp.sql_used)
    if plan is None and not joined:
        return ResolvedJoin(resp, compiled=False)
    if isinstance(plan, Refusal):
        return ResolvedJoin(_abstain(resp, plan.reason, plan.detail), compiled=False)
    if plan is None:
        return ResolvedJoin(
            _abstain(
                resp,
                "join_needs_compile",
                "A join is built from verified links. Free SQL may read one "
                "table. Name a confirmed measure to group across tables.",
            ),
            compiled=False,
        )
    try:
        return _compile_plan(resp, plan, warehouse=warehouse, onto=onto, confirmed=confirmed)
    except Exception:  # noqa: BLE001
        logger.exception("typed plan compile failed")
        return ResolvedJoin(
            _abstain(
                resp,
                "compile_failed",
                "The typed plan could not be compiled against this warehouse.",
            ),
            compiled=False,
        )


def _compile_plan(
    resp: AskResponse,
    plan: TypedPlan,
    *,
    warehouse: Path | None,
    onto: Ontology | None,
    confirmed: Collection[str] | None,
) -> ResolvedJoin:
    authored = False
    if onto is None:
        if warehouse is None:
            return ResolvedJoin(
                _abstain(resp, "no_ontology", "No verified ontology is loaded for this Space."),
                compiled=False,
            )
        onto = demo_ontology(warehouse)
        # The demo pack's measures are written in code. That is the steward
        # act for this seed. A name the model invents is not in this set.
        authored = True
    con: duckdb.DuckDBPyConnection | None = None
    try:
        if warehouse is not None and confirmed is None:
            con = duckdb.connect(str(warehouse), read_only=True)
            confirmed = confirmed_names(con)
        elif warehouse is not None and not onto.verified:
            con = duckdb.connect(str(warehouse), read_only=True)
        names = set(confirmed or ())
        if authored:
            names |= set(onto.measures)
        if not onto.verified:
            if con is None:
                return ResolvedJoin(
                    _abstain(
                        resp,
                        "ontology_unverified",
                        "verify() has not passed, so no join will be built.",
                    ),
                    compiled=False,
                )
            violations = onto.verify(con)
            if violations or not onto.verified:
                return ResolvedJoin(
                    _abstain(
                        resp,
                        "ontology_unverified",
                        "verify() did not pass, so no join will be built.",
                    ),
                    compiled=False,
                )
        compiled = compile_confirmed(onto, plan, names)
        if isinstance(compiled, Refusal):
            return ResolvedJoin(_abstain(resp, compiled.reason, compiled.detail), compiled=False)
        if con is None:
            if warehouse is None:
                return ResolvedJoin(
                    _abstain(resp, "no_ontology", "No warehouse to run the compiled query."),
                    compiled=False,
                )
            con = duckdb.connect(str(warehouse), read_only=True)
        rows = _run(con, compiled.sql)
    finally:
        if con is not None:
            con.close()
    updated = resp.model_copy(
        update={
            "answer": f"Compiled {compiled.measure} at grain {compiled.grain}.",
            "abstained": False,
            "badge": "governed_metric",
            "route": "compiled_join",
            "sql_used": compiled.sql,
            "rows": rows,
            "values": [],
            "assumptions": _notes(resp, compiled.notes),
        }
    )
    return ResolvedJoin(updated, compiled=True)


def _run(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    reject_hostile_chat_sql(sql)
    rel = con.execute(sql)
    cols = [d[0] for d in rel.description]
    rows: list[dict[str, Any]] = []
    for row in rel.fetchall():
        rows.append({col: _cell(val) for col, val in zip(cols, row, strict=True)})
    return rows


def _cell(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return value
    if hasattr(value, "as_integer_ratio"):
        return float(value)
    return str(value)


def _abstain(resp: AskResponse, reason: str, detail: str) -> AskResponse:
    assumptions = _as_list(resp.assumptions)
    assumptions.append(reason)
    return resp.model_copy(
        update={
            "answer": f"Abstained: {reason}. {detail}",
            "abstained": True,
            "badge": "abstain",
            "route": "abstain",
            "sql_used": None,
            "rows": [],
            "values": [],
            "assumptions": assumptions,
        }
    )


def _notes(resp: AskResponse, notes: Sequence[str]) -> list[str]:
    out = _as_list(resp.assumptions)
    out.append("compiled_join")
    out.extend(notes)
    return out


def _as_list(assumptions: str | list[str] | None) -> list[str]:
    if assumptions is None:
        return []
    if isinstance(assumptions, str):
        return [assumptions] if assumptions.strip() else []
    return [str(item) for item in assumptions]


def _ensure(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} (measure_name VARCHAR PRIMARY KEY)"
    )


def _pair(item: Any) -> tuple[str, str] | None:
    if isinstance(item, (list, tuple)) and len(item) == 2:
        if isinstance(item[0], str) and isinstance(item[1], str):
            return item[0], item[1]
        return None
    if isinstance(item, Mapping) and isinstance(item.get("object"), str):
        column = item.get("column")
        if isinstance(column, str):
            return str(item["object"]), column
    return None


def _filter(item: Any) -> tuple[str, str, str, Any] | None:
    if isinstance(item, (list, tuple)) and len(item) == 4:
        obj, column, op, value = item
        if isinstance(obj, str) and isinstance(column, str) and isinstance(op, str):
            return obj, column, op, value
        return None
    if isinstance(item, Mapping):
        obj, column, op = item.get("object"), item.get("column"), item.get("op")
        if isinstance(obj, str) and isinstance(column, str) and isinstance(op, str):
            return obj, column, op, item.get("value")
    return None


__all__ = [
    "ResolvedJoin",
    "TypedPlan",
    "compile_confirmed",
    "confirm_measure",
    "joined_sql",
    "parse_typed_plan",
    "resolve_cortex_join",
    "route_allows_free_join",
]
