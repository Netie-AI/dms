"""General escalation ladder for a generative miss. Default off.

``DMS_ASK_LADDER`` off: the ask path is unchanged.

On, a generative-stage give-up climbs four rungs, in order, inside the
existing insights cap. One call stays unused so reconfirm can still suggest
a question. DMS never names a model. The last rung sends ``route_hint``
``escalate`` and OpenVault picks the route.

Outright stop, no climb: ungranted table, PII, destructive or hostile SQL.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

_ON = frozenset({"1", "true", "yes", "on"})
ASK_LADDER_ENV = "DMS_ASK_LADDER"
RUNGS = ("repair", "context", "plan", "escalate")
_CLIMB_STAGES = frozenset({"generative", "ai_call"})
_QUESTION = "\nQuestion:\n"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VALUE = re.compile(r"^[A-Za-z0-9_./:-]{1,40}$")
_CONTEXT_CAP = 4000
_SAMPLE_TABLES = 4
_SAMPLE_COLUMNS = 4
# Tokens that stop the climb even when a prefix such as ``validate:`` wraps them.
_DIRECT_TOKENS = frozenset(
    {
        "ungranted",
        "hostile_sql",
        "pii",
        "sql_relation_not_granted",
        "statement_not_allowed",
        "path_not_allowed",
        "multi_statement",
        "reserved_param",
        "warehouse_missing",
        "insights_unauthorized",
        "insights_timeout",
        "insights_call_cap",
        "sql_dialect_unknown",
        "sql_relation_unresolved",
    }
)
_PROSE = {
    "question is too vague or time-unbounded to ground": "vague_question",
    "predictive: history is not a forecast": "predictive",
    "year 2099 is not a certified period; all-time history is not that year": "year_not_certified",
    "uncertified paraphrase: not a generative certify boundary": "uncertified_paraphrase",
    "compute abstained (unsure)": "compute_unsure",
    "query_sql was empty": "query_sql_empty",
    "insights generate did not return a typed plan or SQL": "no_plan",
    "retrieve bind abstained (unsure)": "compute_unsure",
    "query_plan was not typed": "no_plan",
}
_REPAIR = (
    "The previous SELECT failed the checker. "
    "Write one SELECT that fixes that failure. "
    "Use only granted tables and columns."
)
_CONTEXT = (
    "The previous SELECT failed the checker. "
    "Use the ontology joins, column samples, and value hints below. "
    "Write one SELECT. Use only granted tables and columns."
)
_ESCALATE = (
    "The earlier readings failed the checker. "
    "Write one SELECT that answers the question. "
    "Use only granted tables and columns."
)
_LEASE = frozenset(
    {
        "ServingLeaseCap",
        "ServingLeaseQueueFull",
        "ServingWaitCancelled",
        "ServingDeadlineExceeded",
        "ServingDeadlineReserve",
        "OpenVaultTokenError",
    }
)

_CLIMB: ContextVar[ClimbSession | None] = ContextVar("dms_ask_ladder", default=None)


def ask_ladder_enabled(env: os._Environ[str] | None = None) -> bool:
    """``DMS_ASK_LADDER``: off unless set to a true token."""
    source = os.environ if env is None else env
    return source.get(ASK_LADDER_ENV, "").strip().lower() in _ON


def bind_climb(session: ClimbSession | None) -> Token[ClimbSession | None]:
    return _CLIMB.set(session)


def reset_climb(token: Token[ClimbSession | None]) -> None:
    _CLIMB.reset(token)


def current_climb() -> ClimbSession | None:
    return _CLIMB.get()


def named_code(reason: str) -> str:
    """A pipeline-failure token for a generative give-up. Prose maps here."""
    text = " ".join(str(reason or "").split())
    if text in _PROSE:
        return _PROSE[text]
    if text.startswith("query_plan was not typed"):
        return "no_plan"
    from dms_executor.pipeline_failure import _known_codes, _reason_code

    code = _reason_code(text)
    if code != "unspecified":
        return code
    known = _known_codes()
    found = [part.strip() for part in text.casefold().split(":") if part.strip() in known]
    if found:
        return ":".join(found)
    return "no_sql" if not text else "unspecified"


def is_direct_abstain(reason: str) -> bool:
    """Ungranted, PII, or destructive SQL. A ``validate:`` prefix still counts."""
    from dms_executor.pipeline_failure import reconfirm_disposition

    text = str(reason or "").strip()
    if not text:
        return False
    if reconfirm_disposition(text) == "direct":
        return True
    tokens = {part.strip() for part in text.casefold().split(":") if part.strip()}
    if tokens & _DIRECT_TOKENS:
        return True
    return any(reconfirm_disposition(token) == "direct" for token in tokens)


def richer_context_block(
    question: str,
    sql: str | None,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    ontology: Any,
    space_id: str | None,
) -> str:
    """Ontology joins, masked column samples, and retrieved verified examples.

    A sampling or retrieval failure is an empty block, not an ask failure.
    """
    parts: list[str] = []
    try:
        from dms_executor.schema_retry import _declared_joins

        lines = []
        for link in _declared_joins(ontology)[:8]:
            lines.append(
                f"{link['from']}.{link['from_column']} = {link['to']}.{link['to_column']}"
            )
        if lines:
            parts.append("ontology_joins:\n" + "\n".join(lines))
    except Exception:
        pass
    try:
        samples = _column_samples(warehouse, grantable, dialect)
        if samples:
            parts.append("column_samples:\n" + "\n".join(samples))
            parts.append("value_hints:\n" + "\n".join(samples))
    except Exception:
        pass
    try:
        from dms_executor.verified_queries import examples_text, retrieve_verified_examples

        rows = retrieve_verified_examples(
            question,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            force=True,
        )
        text = examples_text(rows).replace("Question:", "verified_example_question:")
        if text.strip():
            parts.append("verified_examples:\n" + text)
    except Exception:
        pass
    blob = "\n".join(parts).replace(_QUESTION, "\n")
    if sql:
        blob = blob.replace(str(sql), "")
    return blob[:_CONTEXT_CAP]


def _column_samples(
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
) -> list[str]:
    if warehouse is None or not Path(warehouse).is_file():
        return []
    from dms_executor.bronze import quote_ident
    from dms_executor.demo_warehouse import connect_serving
    from dms_executor.schema_retry import _catalog_column_rows, _column_granted
    from dms_executor.sql_loop import mask_feedback_text

    picked: list[tuple[str, str]] = []
    tables: list[str] = []
    per_table: dict[str, int] = {}
    for table, column, _typ, _flag in _catalog_column_rows(warehouse):
        if not _IDENT.fullmatch(table) or not _IDENT.fullmatch(column):
            continue
        try:
            granted = _column_granted(table, column, grantable, dialect)
        except Exception:
            continue
        if not granted:
            continue
        if table not in per_table:
            if len(tables) >= _SAMPLE_TABLES:
                continue
            tables.append(table)
            per_table[table] = 0
        if per_table[table] >= _SAMPLE_COLUMNS:
            continue
        per_table[table] += 1
        picked.append((table, column))
    if not picked:
        return []
    con = connect_serving(Path(warehouse))
    out: list[str] = []
    try:
        for table, column in picked:
            sql = (
                f"SELECT DISTINCT {quote_ident(column)} FROM {quote_ident(table)} "
                f"WHERE {quote_ident(column)} IS NOT NULL LIMIT 3"
            )
            try:
                cursor = con.execute(sql)
                fetched = cursor.fetchall()
            except Exception:
                continue
            vals: list[str] = []
            for row in fetched:
                if not row:
                    continue
                text = str(row[0])
                if not _VALUE.fullmatch(text):
                    continue
                masked = mask_feedback_text(text)
                if masked != text:
                    continue
                vals.append(text)
            if vals:
                out.append(f"{table}.{column}: {', '.join(vals)}")
    finally:
        con.close()
    return out


def _clean(text: str) -> str:
    from dms_executor.sql_loop import mask_feedback_text

    return mask_feedback_text(text or "").replace(_QUESTION, "\n")


def _ended(body: str, question: str) -> str:
    return f"{body.rstrip()}{_QUESTION}{question or ''}"


def _env_reason(env: dict[str, Any] | None) -> str:
    if not isinstance(env, dict):
        return "checker"
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    prefix = "GEN-01: "
    for item in env.get("assumptions") or []:
        text = str(item)
        if text.startswith(prefix):
            reason = text[len(prefix):].strip()
            if reason:
                return reason
    return "checker"


class ClimbSession:
    """One ask's ladder. ``reserve`` leaves the last insights call for reconfirm."""

    def __init__(
        self,
        *,
        compute: Callable[[dict[str, Any]], dict[str, Any] | None],
        budget: Any,
        ctx: dict[str, Any],
        question: str,
        warehouse: Path | None,
        dialect: str,
        grantable: set[str],
        space_id: str | None,
        session_id: str | None,
        ontology: Any,
        attempts: list[dict[str, Any]],
        check: Callable[[str], str | None],
        submit: Callable[[str, dict[str, Any]], dict[str, Any]],
        reserve: bool = True,
    ) -> None:
        self.compute = compute
        self.budget = budget
        self.ctx = ctx
        self.question = question
        self.warehouse = warehouse
        self.dialect = dialect
        self.grantable = grantable
        self.space_id = space_id
        self.session_id = session_id
        self.ontology = ontology
        self.attempts = attempts
        self.check = check
        self.submit = submit
        self.reserve = reserve
        self.climbing = False
        self.finalizing = False
        self.direct = ""
        self.tried: list[str] = []
        self.index = 0
        self.last_reason = ""

    def notes(self) -> tuple[str, ...]:
        return tuple(f"ladder:{name}" for name in self.tried)

    def exhausted_reason(self, original: str) -> str:
        if self.direct:
            return self.direct
        code = named_code(self.last_reason or original)
        if self.tried:
            return f"ladder_exhausted:{code}"
        return named_code(original)

    def on_abstain(self, reason: str, sql: str | None, stage: str) -> dict[str, Any] | None:
        if self.climbing or stage not in _CLIMB_STAGES:
            return None
        if is_direct_abstain(reason):
            self.direct = named_code(reason)
            self.last_reason = reason
            return None
        self.climbing = True
        try:
            return self._climb(reason, sql)
        finally:
            self.climbing = False

    def finish_miss(self) -> dict[str, Any] | None:
        """The opening call returned no SQL. Climb, or return None to fall through."""
        if self.direct:
            return None
        self.climbing = True
        try:
            return self._climb(self.last_reason or "no_sql", None)
        finally:
            self.climbing = False

    def _room(self) -> bool:
        # ponytail: one reserved call. The cap is still DMS_INSIGHTS_CALL_CAP
        # (ceiling 4). A fifth rung needs a higher ceiling, not a new client.
        reserve = 1 if self.reserve else 0
        return self.budget.used < self.budget.cap - reserve and self.budget.allow()

    def _next_rung(self) -> str | None:
        if self.index >= len(RUNGS):
            return None
        rung = RUNGS[self.index]
        self.index += 1
        return rung

    def _prompt(self, rung: str, reason: str, sql: str | None) -> str:
        from dms_executor.sql_vote import ontology_json, plan_prompt

        feedback = _clean(reason)
        previous = _clean(sql or "")
        tail = f"\n\nprevious_sql:\n{previous}\n\nfeedback:\n{feedback}"
        if rung == "plan":
            text = plan_prompt(self.question, ontology_json(self.ctx))
        elif rung == "context":
            extra = richer_context_block(
                self.question,
                sql,
                warehouse=self.warehouse,
                grantable=self.grantable,
                dialect=self.dialect,
                ontology=self.ontology,
                space_id=self.space_id,
            )
            text = _ended(_CONTEXT + tail + ("\n" + extra if extra else ""), self.question)
        else:
            instruction = _ESCALATE if rung == "escalate" else _REPAIR
            text = _ended(instruction + tail, self.question)
        from dms_executor.generate_retry import append_granted_schema

        return append_granted_schema(text, self.warehouse, self.grantable, self.dialect)

    def _record(
        self,
        rung: str,
        payload: dict[str, Any] | None,
        sql: str | None,
        outcome: str,
    ) -> None:
        from dms_executor.sql_loop import loop_entry

        entry = loop_entry(
            prompt="",
            payload=payload,
            sql=sql,
            outcome=outcome,
            dialect=self.dialect,
        )
        entry["rung"] = f"rung{min(len(self.tried), 9)}"
        self.attempts.append(entry)

    def _climb(self, reason: str, sql: str | None) -> dict[str, Any] | None:
        from dms_executor.generative_ask import query_sql_from_payload
        from dms_executor.sql_loop import EMPTY_UNVERIFIED
        from dms_executor.sql_vote import ontology_json, reading_prompt, vote_plan_from_payload

        if reason and reason != "no_sql":
            self.last_reason = reason
        elif not self.last_reason:
            self.last_reason = reason or "no_sql"
        if is_direct_abstain(reason):
            self.direct = named_code(reason)
            return None
        rung = self._next_rung()
        if rung is None or not self._room() or not self.budget.take():
            return None
        self.tried.append(rung)
        prompt = self._prompt(rung, reason, sql)
        ctx = dict(self.ctx)
        ctx["sql_vote_prompt"] = prompt
        ctx["sql_vote_kind"] = rung
        ctx.pop("sql_loop_feedback", None)
        ctx.pop("route_hint", None)
        if rung == "escalate":
            ctx["route_hint"] = "escalate"
        try:
            payload = self.compute(ctx)
        except Exception as exc:
            if type(exc).__name__ in _LEASE:
                raise
            self._record(rung, None, sql, f"ladder:{rung}")
            return self._climb("no_sql", sql)
        got = query_sql_from_payload(payload if isinstance(payload, dict) else None)
        plan_payload = payload if isinstance(payload, dict) else None
        if rung == "plan" and not got and isinstance(plan_payload, dict):
            plan = vote_plan_from_payload(plan_payload)
            if plan is None:
                raw = plan_payload.get("query_plan")
                plan = raw if isinstance(raw, dict) else None
            if isinstance(plan, dict) and self._room() and self.budget.take():
                self.tried.append("plan_solve")
                solve = reading_prompt(
                    self.question,
                    reading=2,
                    plan=plan,
                    ontology=ontology_json(self.ctx),
                )
                ctx2 = dict(ctx)
                ctx2["sql_vote_prompt"] = solve
                ctx2["sql_vote_kind"] = "plan_solve"
                ctx2.pop("route_hint", None)
                try:
                    payload = self.compute(ctx2)
                except Exception as exc:
                    if type(exc).__name__ in _LEASE:
                        raise
                    payload = None
                got = query_sql_from_payload(payload if isinstance(payload, dict) else None)
                plan_payload = payload if isinstance(payload, dict) else plan_payload
        self._record(rung, plan_payload, got, f"ladder:{rung}")
        if not got:
            return self._climb("no_sql", sql)
        why = self.check(got)
        if why:
            if is_direct_abstain(why):
                self.direct = named_code(why)
                return None
            return self._climb(why, got)
        env = self.submit(got, plan_payload or {})
        if not isinstance(env, dict) or env.get("abstained") or not (env.get("rows") or []):
            if isinstance(env, dict) and env.get("abstained"):
                nxt = _env_reason(env)
            else:
                nxt = EMPTY_UNVERIFIED
            if is_direct_abstain(nxt):
                self.direct = named_code(nxt)
                return None
            return self._climb(nxt, got)
        assumptions = list(env.get("assumptions") or [])
        for line in self.notes():
            if line not in assumptions:
                assumptions.append(line)
        env["assumptions"] = assumptions
        return env
