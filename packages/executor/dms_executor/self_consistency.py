"""DMS_SELF_CONSISTENCY. Default off. The digit 1 only.

On, and only after the first checked answer fails or the ask is flagged
ambiguous, three SQL candidates are generated. The prompts differ. Each
catalog carries a sample temperature. Insights rejects unknown body fields,
so the temperature is stripped before the POST and the prompt is the
diversity the model sees.

All three are executed. The result a majority agrees on is kept. Agreement
is the SQL-vote bar: independent prompts, a granted table, equal rows.
Otherwise the ask clarifies and states no figure.

Off, callers do not enter.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

FLAG = "DMS_SELF_CONSISTENCY"
N = 3
TEMPERATURES = (0.0, 0.4, 0.8)
_QUESTION = "\nQuestion:\n"
# Three instructions. None of them inspects the question text.
_HEADS = (
    (
        "Write one SELECT that answers the question. "
        "Use only tables and columns that appear in the schema. "
        "Return the SQL. Do not write a plan."
    ),
    (
        "Write one SELECT that answers the question by grouping first "
        "and joining only tables the schema relates. "
        "Use a different aggregate form when more than one is valid. "
        "Return the SQL and nothing else."
    ),
    (
        "Write one SELECT that answers the question from the schema alone. "
        "Prefer the smallest set of tables that still answers it. "
        "Do not add a table the schema does not name. "
        "Return the SQL and nothing else."
    ),
)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_SELF_CONSISTENCY`` is the digit 1."""
    source = os.environ if env is None else env
    return str(source.get(FLAG) or "").strip() == "1"


def flagged_ambiguous(payload: dict[str, Any] | None) -> bool:
    """The model set ``ambiguous``. Prose is not scanned."""
    return isinstance(payload, dict) and payload.get("ambiguous") is True


def candidate_prompt(question: str, context: str, index: int) -> str:
    """One diverse instruction. The question tail is the same on every index."""
    head = _HEADS[index % len(_HEADS)]
    body = f"{head}{_QUESTION}{(question or '').strip()}"
    if context:
        body = f"{body}\n\n{context}"
    return body


def candidate_catalog(question: str, context: str, index: int) -> dict[str, Any]:
    """Prompt plus a temperature the Insights POST must not carry."""
    return {
        "sql_vote_prompt": candidate_prompt(question, context, index),
        "sql_vote_kind": "direct",
        "sample_temperature": TEMPERATURES[index % len(TEMPERATURES)],
    }


@dataclass
class VoteOutcome:
    status: str
    sql: str
    result: Any
    payload: dict[str, Any] | None
    reason: str
    calls: int
    executes: int
    temperatures: tuple[float, ...]
    samples: tuple[tuple[str, str], ...]


def _tables_text(sql: str, dialect: str) -> str:
    import sqlglot

    try:
        tree = sqlglot.parse_one(sql or "", read=dialect or None)
    except Exception:
        return ""
    names = sorted({table.name for table in tree.find_all(sqlglot.exp.Table) if table.name})
    return ", ".join(names)


def clarify_readings(
    candidates: list[tuple[str, str]],
    dialect: str,
) -> list[dict[str, str]]:
    """Two structural readings. The question text is not classified."""
    shown: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, (sql, _prompt) in enumerate(candidates, start=1):
        tables = _tables_text(sql, dialect)
        text = f"tables: {tables}" if tables else f"reading {index}"
        if text in seen:
            continue
        seen.add(text)
        shown.append({"id": f"r{len(shown) + 1}", "text": text, "sql_preview": ""})
        if len(shown) == 2:
            break
    while len(shown) < 2:
        shown.append(
            {
                "id": f"r{len(shown) + 1}",
                "text": f"reading {len(shown) + 1}",
                "sql_preview": "",
            }
        )
    return shown


def run_vote(
    question: str,
    context: str,
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    submit: Callable[[str], Any],
    validate: Callable[[str], str | None],
    rows_of: Callable[[Any], list[dict[str, Any]]],
    dialect: str,
    grantable: set[str],
    direct: Callable[[str], bool],
) -> VoteOutcome:
    """Execute N candidates. Majority, a direct abstain, or clarify."""
    from dms_executor.sql_vote import readings_agree, result_key

    calls = 0
    executes = 0
    ran: list[dict[str, Any]] = []
    samples: list[tuple[str, str]] = []
    direct_reason = ""
    for index in range(N):
        catalog = candidate_catalog(question, context, index)
        calls += 1
        try:
            payload = compute(catalog)
        except Exception:
            payload = None
        body = payload if isinstance(payload, dict) else None
        from dms_executor.generative_ask import query_sql_from_payload

        sql = query_sql_from_payload(body)
        if not sql:
            continue
        samples.append((sql, str(catalog["sql_vote_prompt"])))
        why = validate(sql)
        if why:
            if direct(why) and not direct_reason:
                direct_reason = why
            continue
        try:
            result = submit(sql)
        except Exception:
            continue
        executes += 1
        if getattr(result, "ok", None) is False:
            continue
        rows = rows_of(result)
        if not rows or any(not isinstance(row, dict) or not row for row in rows):
            continue
        ran.append(
            {
                "sql": sql,
                "rows": rows,
                "result": result,
                "payload": body,
                "prompt": str(catalog["sql_vote_prompt"]),
            }
        )
    groups: dict[Any, list[dict[str, Any]]] = {}
    for item in ran:
        groups.setdefault(result_key(item["rows"]), []).append(item)
    best: list[dict[str, Any]] | None = None
    for group in groups.values():
        if len(group) < 2:
            continue
        left, right = group[0], group[1]
        if not readings_agree(
            prompt_a=left["prompt"],
            prompt_b=right["prompt"],
            sql_a=left["sql"],
            sql_b=right["sql"],
            rows_a=left["rows"],
            rows_b=right["rows"],
            dialect=dialect,
            grantable=set(grantable),
        ):
            continue
        if best is None or len(group) > len(best):
            best = group
    temps = TEMPERATURES[:N]
    if best:
        pick = best[0]
        return VoteOutcome(
            status="serve",
            sql=pick["sql"],
            result=pick["result"],
            payload=pick["payload"],
            reason="",
            calls=calls,
            executes=executes,
            temperatures=temps,
            samples=tuple(samples),
        )
    if direct_reason and not ran:
        return VoteOutcome(
            status="abstain",
            sql="",
            result=None,
            payload=None,
            reason=direct_reason,
            calls=calls,
            executes=executes,
            temperatures=temps,
            samples=tuple(samples),
        )
    return VoteOutcome(
        status="clarify",
        sql="",
        result=None,
        payload=None,
        reason="clarify_ambiguous",
        calls=calls,
        executes=executes,
        temperatures=temps,
        samples=tuple(samples),
    )
