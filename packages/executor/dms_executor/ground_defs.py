"""Ground governed measure definitions into the AI prompt.

``DMS_GROUND_DEFS`` defaults to 0. On, measures on the ontology this ask is
already using (the Space's own, or the demo ontology only while the ask is
on the demo ontology) are ranked against the question by the verified-example
embedder, or by Okapi BM25 when that embedder is absent. No word list. The
top three above the score floor are written into the Insights question as
``governed definitions; use exactly``, with name, grain, the governed SQL
expression, and the as_of anchor that expression already uses. The same block
carries Space-scoped verified question-to-SQL rows from the ``DMS_VQ_RAG``
path when the caller has already retrieved them.

When one of those definitions has a predicate, the L2 check compares the
generated SQL's predicate on those columns to the governed predicate. The
comparison is the normalized sqlglot tree, not a search of the SQL text. A
mismatch returns the reconfirm envelope with ``reason_code``
``definition_mismatch`` and is not served. The token locks in
``semantic_retrieve`` stay until WORD-RULES-OUT-01.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.ontology import Measure, Ontology
from dms_executor.sql_vote import reconfirm_envelope
from dms_executor.verified_queries import rank_texts

FLAG = "DMS_GROUND_DEFS"
HEADER = "governed definitions; use exactly"
# ponytail: one floor for cosine and BM25. A live A/B that shows a real hit
# under 0.5 should split the floors. k stays 3.
_K = 3
_SCORE_FLOOR = 0.5
REASON = "definition_mismatch"


def ground_defs_enabled() -> bool:
    """True only when ``DMS_GROUND_DEFS`` is the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


@dataclass(frozen=True)
class GroundedDef:
    name: str
    grain: str
    expression: str
    as_of: str
    columns: frozenset[str]
    predicate_sql: str


def build_grounding(
    question: str,
    ontology: Ontology | None,
    *,
    embedder: Callable[[list[str]], Any] | None,
    examples: Sequence[Mapping[str, Any]] | None = None,
    grantable: set[str] | None = None,
) -> tuple[str, list[GroundedDef]]:
    """Prompt block and the definitions in it. Empty when the flag is off.

    ``examples`` are verified question-to-SQL rows the caller already
    retrieved for this Space. This function does not read another ontology.
    A measure whose grain is not in ``grantable`` is left out. ``None`` does
    not filter.
    """
    if not ground_defs_enabled():
        return "", []
    defs = _rank_measures(question, ontology, embedder, grantable)
    block = _block(defs, examples or ())
    return block, defs


def mismatch_envelope(
    sql: str,
    defs: Sequence[GroundedDef],
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Reconfirm envelope when ``sql`` does not use a grounded predicate.

    None when the flag is off, nothing with a predicate was grounded, or the
    predicate on those columns matches. The held SQL is not parked: Yes
    re-asks, it does not run the statement that missed the definition.
    """
    if not ground_defs_enabled() or not defs or _respects(sql, defs):
        return None
    top = next((item for item in defs if item.predicate_sql), None)
    definition = ""
    if top is not None:
        definition = (
            f"{top.name} grain {top.grain} expression {top.expression} as_of {top.as_of}"
        )
    env = reconfirm_envelope(
        question=question,
        why=REASON,
        closest_question=question,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["code"] = REASON
        block["reason_code"] = REASON
        if definition:
            block["definition"] = definition
    env["abstain_reason"] = REASON
    return env


def _rank_measures(
    question: str,
    ontology: Ontology | None,
    embedder: Callable[[list[str]], Any] | None,
    grantable: set[str] | None,
) -> list[GroundedDef]:
    if ontology is None or not ontology.measures:
        return []
    measures = [
        item
        for item in ontology.measures.values()
        if _visible(ontology, item, grantable)
    ]
    docs = [_doc(item) for item in measures]
    ranked = rank_texts(
        question,
        docs,
        embedder,
        limit=_K,
        floor=_SCORE_FLOOR,
    )
    out: list[GroundedDef] = []
    for _score, index in ranked:
        item = measures[index]
        columns, predicate = _predicate(item.expression)
        out.append(
            GroundedDef(
                name=item.name,
                grain=item.grain,
                expression=" ".join(item.expression.split()),
                as_of=_anchor(item.expression),
                columns=columns,
                predicate_sql=predicate,
            )
        )
    return out


def _visible(ontology: Ontology, measure: Measure, grantable: set[str] | None) -> bool:
    if grantable is None:
        return True
    allowed = {part.split(".")[-1].casefold() for part in grantable if part}
    obj = ontology.objects.get(measure.grain)
    names = {measure.grain.casefold()}
    if obj is not None:
        names.add(obj.name.casefold())
        rel = obj.relation.casefold()
        names.add(rel)
        names.add(rel.split(".")[-1])
    return bool(names & allowed)


def _doc(measure: Measure) -> str:
    return "\n".join(
        part
        for part in (
            measure.name,
            measure.grain,
            measure.description or "",
            measure.expression or "",
        )
        if part
    )


def _block(defs: Sequence[GroundedDef], examples: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for item in defs:
        lines.append(f"name: {item.name}")
        lines.append(f"grain: {item.grain}")
        lines.append(f"expression: {item.expression}")
        lines.append(f"as_of: {item.as_of}")
        lines.append("")
    for row in examples:
        if not isinstance(row, dict):
            continue
        asked = " ".join(str(row.get("question") or "").split())
        sql = " ".join(str(row.get("sql") or "").split())
        if not asked or not sql:
            continue
        lines.append(f"example_question: {asked}")
        lines.append(f"example_sql: {sql}")
        lines.append("")
    if not lines:
        return ""
    return HEADER + "\n" + "\n".join(lines).rstrip() + "\n"


def _respects(sql: str, defs: Sequence[GroundedDef]) -> bool:
    if not any(item.predicate_sql for item in defs):
        return True
    tree = _parse(sql)
    if tree is None:
        return False
    for index, item in enumerate(defs):
        if not item.predicate_sql or not item.columns:
            continue
        got = _canon_on(tree, item.columns)
        if not got:
            # The best-ranked definition is the one the question mapped to.
            # A later definition is context: using it wrong fails, skipping it
            # does not.
            if index == 0:
                return False
            continue
        if got != item.predicate_sql:
            return False
    return True


def _predicate(expression: str) -> tuple[frozenset[str], str]:
    tree = _parse(expression)
    if tree is None:
        return frozenset(), ""
    columns: set[str] = set()
    for cond in _conditions(tree):
        bare = cond.copy().transform(_unqualify)
        for piece in _conjuncts(bare):
            columns |= _columns(piece)
    if not columns:
        return frozenset(), ""
    return frozenset(columns), _canon_on(tree, frozenset(columns))


def _anchor(expression: str) -> str:
    tree = _parse(expression)
    if tree is None:
        return "none"
    if any(True for _node in tree.find_all(exp.CurrentDate)):
        return "CURRENT_DATE"
    if any(True for _node in tree.find_all(exp.CurrentTimestamp)):
        return "CURRENT_TIMESTAMP"
    for node in tree.find_all(exp.Placeholder):
        name = str(node.name or node.this or "").casefold()
        if name == "as_of":
            return "$as_of"
    return "none"


def _canon_on(tree: exp.Expression, columns: frozenset[str]) -> str:
    parts: list[str] = []
    for cond in _conditions(tree):
        bare = cond.copy().transform(_unqualify)
        for piece in _conjuncts(bare):
            if _columns(piece) & columns:
                parts.append(piece.sql(dialect=SERVING_DIALECT, normalize=True))
    kept = sorted({part for part in parts if part})
    return " AND ".join(kept)


def _conditions(tree: exp.Expression) -> list[exp.Expression]:
    found: list[exp.Expression] = []
    for node in tree.find_all(exp.Where, exp.Having, exp.Qualify):
        cond = node.this
        if isinstance(cond, exp.Expression):
            found.append(cond)
    for case in tree.find_all(exp.Case):
        for iff in case.args.get("ifs") or []:
            if isinstance(iff, exp.If) and isinstance(iff.this, exp.Expression):
                found.append(iff.this)
    return found


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        out: list[exp.Expression] = []
        left = node.this if isinstance(node.this, exp.Expression) else None
        right = node.expression if isinstance(node.expression, exp.Expression) else None
        if left is not None:
            out.extend(_conjuncts(left))
        if right is not None:
            out.extend(_conjuncts(right))
        return out or [node]
    if isinstance(node, exp.Paren) and isinstance(node.this, exp.Expression):
        return _conjuncts(node.this)
    return [node]


def _columns(node: exp.Expression) -> set[str]:
    names: set[str] = set()
    for col in node.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        name = (col.name or "").casefold()
        if name:
            names.add(name)
    return names


def _unqualify(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
        name = (node.name or "").casefold()
        if name:
            return exp.column(name)
    return node


def _parse(sql: str) -> exp.Expression | None:
    text = (sql or "").strip()
    if not text:
        return None
    try:
        return sqlglot.parse_one(text, read=SERVING_DIALECT)
    except (ParseError, TokenError, ValueError):
        return None
