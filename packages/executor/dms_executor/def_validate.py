"""Check AI-generated SQL against the approved ontology store.

``DMS_DEF_VALIDATE`` defaults to 0. When it is 1, generated SQL is parsed
with sqlglot (the vendored auto-ontology extractor) and checked for
declared tables, columns, join edges, and measure expressions.

Swap: this module calls ``ao_vendor`` ``extract_tables_and_columns``. If
that package is removed, the join-edge check has to move to another
sqlglot qualify pass. Do not replace it with a word list.

Pass executes. An undeclared join, a measure expression that is not an
approved measure, or a match on a revoked item returns the #464
reconfirm envelope and shows the definition used. A PII-tagged column
or a column the ontology does not grant abstains. Nothing here classifies
the question text.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.ontology import Ontology, _bare_relation


def def_validate_enabled() -> bool:
    """True only when ``DMS_DEF_VALIDATE`` is the digit 1. Unset is off."""
    return os.environ.get("DMS_DEF_VALIDATE", "0").strip() == "1"


@dataclass(frozen=True)
class DefVerdict:
    outcome: str
    reason: str
    envelope: dict[str, Any] | None
    provenance: dict[str, Any]


def validate_generated_sql(
    sql: str,
    *,
    grantable: set[str],
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> DefVerdict | None:
    """None when the flag is off. The caller executes only on ``pass``."""
    if not def_validate_enabled():
        return None
    onto = Ontology.from_store()
    meta = _meta(onto)
    provenance = _provenance(meta)
    parsed = _parse(sql)
    if parsed is None:
        return _stop(
            "abstain",
            "sql_not_analyzable",
            _abstain_env(
                "sql_not_analyzable",
                question=question,
                space_id=space_id,
                session_id=session_id,
                provenance=provenance,
            ),
            provenance,
        )
    tree, columns, pairs, saw_join = parsed
    tables = _real_tables(tree)
    granted = {_bare_relation(name) for name in grantable}
    declared = _declared_columns(onto)
    pii = _pii_hit(meta, columns, tables, tree)
    if pii is not None:
        return _stop(
            "abstain",
            "pii",
            _abstain_env(
                "pii",
                question=question,
                space_id=space_id,
                session_id=session_id,
                provenance=_provenance([*meta, *([pii] if pii.get("id") else [])]),
            ),
            _provenance(meta),
        )
    if _select_star(tree):
        for table in tables:
            if _relation_pii(meta, table) is not None:
                hit = _relation_pii(meta, table)
                return _stop(
                    "abstain",
                    "pii",
                    _abstain_env(
                        "pii",
                        question=question,
                        space_id=space_id,
                        session_id=session_id,
                        provenance=_provenance(meta),
                    ),
                    _provenance(meta if hit is None else [hit]),
                )
        return _stop(
            "abstain",
            "ungranted",
            _abstain_env(
                "ungranted",
                question=question,
                space_id=space_id,
                session_id=session_id,
                provenance=provenance,
            ),
            provenance,
        )
    for table in tables:
        if table not in granted:
            return _ungranted(question, space_id, session_id, provenance)
    for table, cols in columns.items():
        known = declared.get(table) or set()
        if table not in declared or any(col not in known for col in cols):
            return _ungranted(question, space_id, session_id, provenance)
    edges = _edges(onto, meta)
    if saw_join:
        if not pairs:
            definition = "join " + ", ".join(tables)
            return _reconfirm(
                "undeclared_join",
                definition,
                question=question,
                space_id=space_id,
                session_id=session_id,
                cited=_cited_objects(meta, tables),
            )
        for left_t, left_c, right_t, right_c in pairs:
            key = _edge_key((left_t, left_c), (right_t, right_c))
            if key not in edges:
                definition = f"{left_t}.{left_c} = {right_t}.{right_c}"
                return _reconfirm(
                    "undeclared_join",
                    definition,
                    question=question,
                    space_id=space_id,
                    session_id=session_id,
                    cited=_cited_objects(meta, tables),
                )
    measure = _measure_verdict(tree, meta)
    if measure is not None:
        code, definition, cited = measure
        return _reconfirm(
            code,
            definition,
            question=question,
            space_id=space_id,
            session_id=session_id,
            cited=cited,
        )
    cited = _cited_objects(meta, tables)
    cited.extend(_matched_measures(tree, meta))
    cited.extend(_matched_links(pairs, edges))
    prov = _provenance(cited or meta)
    return DefVerdict("pass", "", None, prov)


def _stop(
    outcome: str,
    reason: str,
    envelope: dict[str, Any],
    provenance: dict[str, Any],
) -> DefVerdict:
    return DefVerdict(outcome, reason, envelope, provenance)


def _ungranted(
    question: str,
    space_id: str | None,
    session_id: str | None,
    provenance: dict[str, Any],
) -> DefVerdict:
    return _stop(
        "abstain",
        "ungranted",
        _abstain_env(
            "ungranted",
            question=question,
            space_id=space_id,
            session_id=session_id,
            provenance=provenance,
        ),
        provenance,
    )


def _reconfirm(
    code: str,
    definition: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    cited: list[dict[str, Any]],
) -> DefVerdict:
    prov = _provenance(cited)
    envelope = _reconfirm_env(
        code,
        definition,
        question=question,
        space_id=space_id,
        session_id=session_id,
        provenance=prov,
    )
    return DefVerdict("reconfirm", "def_validate", envelope, prov)


def _meta(onto: Ontology) -> list[dict[str, Any]]:
    raw = onto.__dict__.get("_item_meta") or []
    return [row for row in raw if isinstance(row, dict)]


def _declared_columns(onto: Ontology) -> dict[str, set[str]]:
    raw = onto.__dict__.get("_relation_columns") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): set(value) for key, value in raw.items() if isinstance(value, set)}


def _provenance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ids = sorted({str(row["id"]) for row in rows if isinstance(row.get("id"), str) and row["id"]})
    versions = sorted(
        {
            str(row["version_id"])
            for row in rows
            if isinstance(row.get("version_id"), str) and row["version_id"]
        }
    )
    version_id = versions[0] if len(versions) == 1 else ",".join(versions)
    return {"version_id": version_id, "item_ids": ids}


def _cited_objects(meta: list[dict[str, Any]], tables: list[str]) -> list[dict[str, Any]]:
    want = set(tables)
    found: list[dict[str, Any]] = []
    for row in meta:
        if row.get("kind") != "object":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if _bare_relation(str(payload.get("relation") or "")) in want:
            found.append(row)
    return found


def _parse(
    sql: str,
) -> tuple[exp.Expression, dict[str, set[str]], list[tuple[str, str, str, str]], bool] | None:
    try:
        from ao_vendor.catalog.parsers.sqlglot_extractor import (
            SQLSyntaxError,
            extract_tables_and_columns,
        )
    except ImportError:
        return None
    try:
        extracted = extract_tables_and_columns(sql, [SERVING_DIALECT], {})
        tree = sqlglot.parse_one(sql, read=SERVING_DIALECT)
    except (SQLSyntaxError, ParseError, TokenError, ValueError):
        return None
    if tree is None:
        return None
    columns: dict[str, set[str]] = {}
    for key, match in extracted.tables.items():
        bare = _bare_relation(key)
        if not bare:
            continue
        columns.setdefault(bare, set()).update(col.casefold() for col in match.columns)
    tables = _real_tables(tree)
    if len(tables) == 1:
        only = tables[0]
        for col in tree.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue
            name = (col.name or "").casefold()
            if name:
                columns.setdefault(only, set()).add(name)
    pairs = [
        (
            _bare_relation(pair.left_table),
            pair.left_column.casefold(),
            _bare_relation(pair.right_table),
            pair.right_column.casefold(),
        )
        for pair in extracted.joins
    ]
    saw_join = bool(list(tree.find_all(exp.Join))) or (len(tables) > 1 and bool(pairs))
    if len(tables) > 1 and not pairs:
        saw_join = True
    return tree, columns, pairs, saw_join


def _real_tables(tree: exp.Expression) -> list[str]:
    ctes = {cte.alias.casefold() for cte in tree.find_all(exp.CTE) if cte.alias}
    names: list[str] = []
    seen: set[str] = set()
    for table in tree.find_all(exp.Table):
        bare = _bare_relation(table.name)
        if not bare or bare in ctes or bare in seen:
            continue
        seen.add(bare)
        names.append(bare)
    return names


def _select_star(tree: exp.Expression) -> bool:
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.AggFunc):
            return True
    return False


def _pii_column(row: dict[str, Any]) -> tuple[str, str] | None:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    relation = _bare_relation(str(payload.get("relation") or payload.get("table") or ""))
    column = str(payload.get("column") or "").strip().casefold()
    name = str(payload.get("name") or "")
    if not column and "." in name:
        left, right = name.split(".", 1)
        relation = _bare_relation(left)
        column = right.strip().casefold()
    if not column:
        return None
    return relation, column


def _pii_hit(
    meta: list[dict[str, Any]],
    columns: dict[str, set[str]],
    tables: list[str],
    tree: exp.Expression,
) -> dict[str, Any] | None:
    if _select_star(tree):
        for table in tables:
            hit = _relation_pii(meta, table)
            if hit is not None:
                return hit
    for row in meta:
        if row.get("kind") != "pii":
            continue
        parsed = _pii_column(row)
        if parsed is None:
            continue
        relation, column = parsed
        for table, cols in columns.items():
            if column in cols and (not relation or relation == table):
                return row
    return None


def _relation_pii(meta: list[dict[str, Any]], table: str) -> dict[str, Any] | None:
    for row in meta:
        if row.get("kind") != "pii":
            continue
        parsed = _pii_column(row)
        if parsed is None:
            continue
        relation, _column = parsed
        if not relation or relation == table:
            return row
    return None


def _edge_key(
    left: tuple[str, str], right: tuple[str, str]
) -> tuple[tuple[str, str], tuple[str, str]]:
    a, b = sorted((left, right))
    return (a, b)


def _edges(
    onto: Ontology, meta: list[dict[str, Any]]
) -> dict[tuple[tuple[str, str], tuple[str, str]], dict[str, Any]]:
    by_name = {
        str(row.get("payload", {}).get("name")): row
        for row in meta
        if row.get("kind") == "link" and isinstance(row.get("payload"), dict)
    }
    found: dict[tuple[tuple[str, str], tuple[str, str]], dict[str, Any]] = {}
    for link in onto.links.values():
        left = onto.objects.get(link.from_object)
        right = onto.objects.get(link.to_object)
        if left is None or right is None:
            continue
        item = by_name.get(link.name) or {
            "id": "",
            "version_id": "",
            "kind": "link",
            "payload": {"name": link.name},
        }
        lt = _bare_relation(left.relation)
        rt = _bare_relation(right.relation)
        for src, dst in zip(link.from_columns, link.to_columns, strict=False):
            key = _edge_key((lt, src.casefold()), (rt, dst.casefold()))
            found[key] = item
    return found


def _norm_node(node: exp.Expression) -> str | None:
    try:
        cloned = node.copy()
        for col in cloned.find_all(exp.Column):
            col.set("table", None)
            if col.name:
                col.set("this", exp.to_identifier(col.name.casefold()))
        return cloned.sql(dialect=SERVING_DIALECT, normalize=True).casefold()
    except (ParseError, TokenError, ValueError):
        return None


def _norm_text(text: str) -> str | None:
    try:
        tree = sqlglot.parse_one(text, read=SERVING_DIALECT)
    except (ParseError, TokenError, ValueError):
        return None
    if tree is None:
        return None
    return _norm_node(tree)


def _agg_keys(tree: exp.Expression) -> list[str]:
    keys: list[str] = []
    for agg in tree.find_all(exp.AggFunc):
        key = _norm_node(agg)
        if key:
            keys.append(key)
    return keys


def _measure_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != "measure":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        key = _norm_text(str(payload.get("expression") or ""))
        if key:
            found[key] = row
    return found


def _approved_measures(meta: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _measure_index([row for row in meta if row.get("kind") == "measure"])


def _revoked_measures() -> dict[str, dict[str, Any]]:
    from dms_core.control_plane.onto_data import revoked_items

    return _measure_index([row for row in revoked_items() if row.get("kind") == "measure"])


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    return raw if isinstance(raw, dict) else {}


def _measure_verdict(
    tree: exp.Expression, meta: list[dict[str, Any]]
) -> tuple[str, str, list[dict[str, Any]]] | None:
    keys = _agg_keys(tree)
    if not keys:
        return None
    approved = _approved_measures(meta)
    revoked = _revoked_measures()
    revoked_hit: dict[str, Any] | None = None
    drift: str | None = None
    for key in keys:
        if key in approved:
            continue
        if key in revoked:
            revoked_hit = revoked[key]
            continue
        drift = key
    if revoked_hit is not None and drift is None:
        payload = _payload(revoked_hit)
        definition = (
            f"revoked measure {payload.get('name')} grain {payload.get('grain')} "
            f"expression {payload.get('expression')}"
        )
        return ("revoked_measure", definition, [revoked_hit, *_cited_for_measure(meta)])
    if drift is not None or revoked_hit is not None:
        if revoked_hit is not None:
            payload = _payload(revoked_hit)
            definition = (
                f"revoked measure {payload.get('name')} grain {payload.get('grain')} "
                f"expression {payload.get('expression')}"
            )
            return ("revoked_measure", definition, [revoked_hit])
        approved_text = (
            "; ".join(
                f"measure {row.get('payload', {}).get('name')} "
                f"expression {row.get('payload', {}).get('expression')}"
                for row in approved.values()
            )
            or "none"
        )
        return (
            "measure_drift",
            f"{drift}; approved: {approved_text}",
            list(approved.values()) or list(meta),
        )
    return None


def _cited_for_measure(meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in meta if row.get("kind") == "object"]


def _matched_measures(tree: exp.Expression, meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    approved = _approved_measures(meta)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in _agg_keys(tree):
        row = approved.get(key)
        if row is None:
            continue
        item_id = str(row.get("id") or "")
        if item_id and item_id not in seen:
            seen.add(item_id)
            found.append(row)
    return found


def _matched_links(
    pairs: list[tuple[str, str, str, str]],
    edges: dict[tuple[tuple[str, str], tuple[str, str]], dict[str, Any]],
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for left_t, left_c, right_t, right_c in pairs:
        row = edges.get(_edge_key((left_t, left_c), (right_t, right_c)))
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "")
        if item_id and item_id not in seen:
            seen.add(item_id)
            found.append(row)
    return found


def _abstain_env(
    reason: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    env = build_abstain(
        reason=reason,
        question=question,
        stage="generative",
        answer_id="ans_def_validate",
        text=(
            "I cannot certify that query against the approved ontology, so I am not executing it."
        ),
        rows=[],
        sql_used=None,
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    env["ontology_provenance"] = provenance
    return env


def _reconfirm_env(
    code: str,
    definition: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    from dms_executor.sql_vote import reconfirm_envelope

    why = f"{code}: {definition}"
    env = reconfirm_envelope(
        question=question,
        why=why,
        closest_question=question,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["definition"] = definition
        block["code"] = code
    env["text"] = (
        f"{code}: {definition} Closest answerable question: {question}. Yes runs that question."
    )
    env["ontology_provenance"] = provenance
    return env
