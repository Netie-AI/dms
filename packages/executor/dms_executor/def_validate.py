"""Check AI-generated SQL against the approved ontology store.

``DMS_DEF_VALIDATE`` defaults to 0. When it is 1, generated SQL is parsed
with sqlglot (the vendored auto-ontology extractor) and checked for
declared tables, columns, join edges, and measure expressions.

Swap: this module calls ``ao_vendor`` ``extract_tables_and_columns``. If
that package is removed, the join-edge check has to move to another
sqlglot qualify pass. Do not replace it with a word list.

Pass executes. An undeclared join, a measure expression that redefines an
approved measure or that the approved links do not justify, or a match
on a revoked item returns the #464 reconfirm envelope and shows the
definition used. A PII-tagged column abstains. A table the grant does
not include abstains ``ungranted``. A granted table or column that is
not in the approved ontology reconfirms ``not_in_ontology``. Nothing
here classifies the question text.
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
    miss = _ontology_miss(tree, declared, granted)
    if miss is not None:
        qual = miss.split(":", 1)[1]
        return _reconfirm(
            miss,
            qual,
            question=question,
            space_id=space_id,
            session_id=session_id,
            cited=_cited_objects(meta, tables),
        )
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
    measure = _measure_verdict(tree, meta, onto)
    derived: list[str] = []
    if isinstance(measure, tuple):
        code, definition, cited = measure
        return _reconfirm(
            code,
            definition,
            question=question,
            space_id=space_id,
            session_id=session_id,
            cited=cited,
        )
    if isinstance(measure, list):
        derived = measure
    cited_rows = _cited_objects(meta, tables)
    cited_rows.extend(_matched_measures(tree, meta))
    cited_rows.extend(_matched_links(pairs, edges))
    prov = _provenance(cited_rows or meta)
    if derived:
        prov["derived"] = derived
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


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias.casefold() for cte in tree.find_all(exp.CTE) if cte.alias}


def _owning_select(node: exp.Expression) -> exp.Select | None:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            return parent
        parent = parent.parent
    return None


def _stops(node: exp.Expression, stop: exp.Expression, kinds: tuple[type, ...]) -> bool:
    parent = node.parent
    while parent is not None and parent is not stop:
        if isinstance(parent, kinds):
            return True
        parent = parent.parent
    return False


def _from_and_joins(select: exp.Select) -> list[exp.Expression]:
    """FROM plus JOIN nodes. sqlglot 27 stores FROM under ``from``."""
    roots: list[exp.Expression] = []
    from_ = select.args.get("from")
    if from_ is None:
        from_ = select.args.get("from_")
    if from_ is not None:
        roots.append(from_)
    roots.extend(select.args.get("joins") or [])
    return roots


def _scope(select: exp.Select, ctes: set[str]) -> list[str]:
    """Real relations in this SELECT's FROM and JOIN, aliases included.

    A second alias of the same relation is kept. Callers treat that as drift:
    a self-join can fan out, and the tree does not prove it does not.
    """
    instances: list[str] = []
    for root in _from_and_joins(select):
        for table in root.find_all(exp.Table):
            if _stops(table, root, (exp.Subquery, exp.Select)):
                continue
            bare = _bare_relation(table.name)
            if not bare or bare in ctes:
                continue
            instances.append(bare)
    return instances


def _alias_map(select: exp.Select, ctes: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for root in _from_and_joins(select):
        for table in root.find_all(exp.Table):
            if _stops(table, root, (exp.Subquery, exp.Select)):
                continue
            bare = _bare_relation(table.name)
            if not bare or bare in ctes:
                continue
            found[bare] = bare
            alias = (table.alias or "").casefold()
            if alias:
                found[alias] = bare
    return found


def _resolve_column(
    col: exp.Column, select: exp.Select, ctes: set[str]
) -> tuple[str, str] | None:
    """(relation, column) for one column in this SELECT.

    ponytail: a column projected from a subquery is not traced to the source
    column. That aggregate stays drift. Upgrade: follow the subquery list
    when it is one approved column.
    """
    if isinstance(col.this, exp.Star):
        return None
    name = (col.name or "").casefold()
    if not name:
        return None
    aliases = _alias_map(select, ctes)
    qual = (col.table or "").casefold()
    if qual:
        rel = aliases.get(qual)
        if not rel:
            return None
        return rel, name
    uniq = list(dict.fromkeys(_scope(select, ctes)))
    if len(uniq) == 1:
        return uniq[0], name
    return None


def _unwrap(node: exp.Expression | None) -> exp.Expression | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _single_arg_column(agg: exp.AggFunc) -> exp.Column | None:
    if not isinstance(agg, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
        return None
    if agg.expressions:
        return None
    node = _unwrap(agg.this)
    if isinstance(node, exp.Distinct):
        exprs = [item for item in (node.expressions or []) if isinstance(item, exp.Expression)]
        if len(exprs) != 1:
            return None
        node = _unwrap(exprs[0])
    if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
        return node
    return None


def _argument_ids(tree: exp.Expression) -> set[int]:
    found: set[int] = set()
    for agg in tree.find_all(exp.AggFunc):
        col = _single_arg_column(agg)
        if col is not None:
            found.add(id(col))
    return found


def _is_count_star(agg: exp.AggFunc) -> bool:
    if not isinstance(agg, exp.Count) or agg.expressions:
        return False
    return isinstance(_unwrap(agg.this), exp.Star)


def _parent_map(onto: Ontology) -> dict[str, set[str]]:
    """Child relation -> parent relations, from approved links only.

    ``from_object`` is the child. ``one_to_one`` walks both ways.
    """
    parents: dict[str, set[str]] = {}
    for link in onto.links.values():
        child = onto.objects.get(link.from_object)
        parent = onto.objects.get(link.to_object)
        if child is None or parent is None:
            continue
        cr = _bare_relation(child.relation)
        pr = _bare_relation(parent.relation)
        if not cr or not pr:
            continue
        parents.setdefault(cr, set()).add(pr)
        if link.one_to_one:
            parents.setdefault(pr, set()).add(cr)
    return parents


def _ancestors(start: str, parents: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(parents.get(start, ()))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(parents.get(cur, ()))
    return seen


def _finest(instances: list[str], parents: dict[str, set[str]]) -> list[str]:
    uniq = list(dict.fromkeys(instances))
    anc = {table: _ancestors(table, parents) for table in uniq}
    return [
        table
        for table in uniq
        if not any(table in anc[other] for other in uniq if other != table)
    ]


def _output_dims(
    select: exp.Select, ctes: set[str], arg_ids: set[int]
) -> set[str] | None:
    """Tables of the non-aggregated select list and GROUP BY. None if unresolved."""
    group = select.args.get("group")
    projections = {id(item) for item in (select.expressions or [])}
    dims: set[str] = set()
    for col in select.find_all(exp.Column):
        if _owning_select(col) is not select or isinstance(col.this, exp.Star):
            continue
        if id(col) in arg_ids:
            continue
        parent: exp.Expression | None = col
        in_grain = False
        while parent is not None and parent is not select:
            if id(parent) in projections or parent is group:
                in_grain = True
                break
            parent = parent.parent
        if not in_grain:
            continue
        resolved = _resolve_column(col, select, ctes)
        if resolved is None:
            return None
        dims.add(resolved[0])
    return dims


def _grain_ok(
    agg_table: str,
    instances: list[str],
    dims: set[str],
    parents: dict[str, set[str]],
) -> bool:
    if len(instances) != len(set(instances)):
        return False
    allowed = {agg_table, *_ancestors(agg_table, parents)}
    return set(instances) <= allowed and dims <= allowed


def _nested_agg(agg: exp.AggFunc) -> bool:
    for node in agg.find_all(exp.AggFunc):
        if node is not agg:
            return True
    parent = agg.parent
    while parent is not None:
        if isinstance(parent, exp.AggFunc):
            return True
        parent = parent.parent
    return False


def _agg_fn(agg: exp.AggFunc) -> str | None:
    if isinstance(agg, exp.Count):
        return "count"
    if isinstance(agg, exp.Sum):
        return "sum"
    if isinstance(agg, exp.Avg):
        return "avg"
    if isinstance(agg, exp.Min):
        return "min"
    if isinstance(agg, exp.Max):
        return "max"
    return None


def _structural_provenance(
    agg: exp.AggFunc,
    *,
    declared: dict[str, set[str]],
    parents: dict[str, set[str]],
    ctes: set[str],
    arg_ids: set[int],
) -> str | None:
    """``derived:<agg>(<table>.<col>)`` when the tree shape is one approved column.

    COUNT(*) uses ``*``. The grain is the aggregated object, or a coarser
    object reached only by walking approved links child to parent. A nested
    aggregate, a self-join, or a join to a child is not that shape.
    """
    if agg.args.get("over") is not None or _nested_agg(agg):
        return None
    fn = _agg_fn(agg)
    if fn is None:
        return None
    select = _owning_select(agg)
    if select is None:
        return None
    instances = _scope(select, ctes)
    if not instances:
        return None
    dims = _output_dims(select, ctes, arg_ids)
    if dims is None:
        return None
    if _is_count_star(agg):
        finest = _finest(instances, parents)
        if len(finest) != 1 or finest[0] not in declared:
            return None
        table = finest[0]
        if not _grain_ok(table, instances, dims, parents):
            return None
        return f"derived:count({table}.*)"
    col = _single_arg_column(agg)
    if col is None:
        return None
    resolved = _resolve_column(col, select, ctes)
    if resolved is None:
        return None
    table, name = resolved
    known = declared.get(table)
    if known is None or name not in known:
        return None
    if not _grain_ok(table, instances, dims, parents):
        return None
    return f"derived:{fn}({table}.{name})"


def _ontology_miss(
    tree: exp.Expression, declared: dict[str, set[str]], granted: set[str]
) -> str | None:
    """``not_in_ontology:<table>.<col>`` for a granted name the ontology omits.

    The single column of a plain aggregate is left for the measure check.
    ``ungranted`` stays the grant failure, decided before this runs.
    """
    ctes = _cte_names(tree)
    arg_ids = _argument_ids(tree)
    misses: list[str] = []
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star) or id(col) in arg_ids:
            continue
        select = _owning_select(col)
        if select is None:
            continue
        resolved = _resolve_column(col, select, ctes)
        if resolved is None:
            name = (col.name or "").casefold()
            if name:
                misses.append(name)
            continue
        table, name = resolved
        if table not in granted:
            continue
        known = declared.get(table)
        if known is None or name not in known:
            misses.append(f"{table}.{name}")
    if not misses:
        for table in _real_tables(tree):
            if table in granted and table not in declared:
                misses.append(table)
    if not misses:
        return None
    return "not_in_ontology:" + sorted(set(misses))[0]


def _redefined_measure(tree: exp.Expression, meta: list[dict[str, Any]]) -> str | None:
    """Normalized expression when an alias reuses an approved measure name."""
    by_name: dict[str, str] = {}
    for row in meta:
        if row.get("kind") != "measure":
            continue
        payload = _payload(row)
        name = str(payload.get("name") or "").strip().casefold()
        expr = _norm_text(str(payload.get("expression") or ""))
        if name and expr:
            by_name[name] = expr
    if not by_name:
        return None
    for select in tree.find_all(exp.Select):
        for proj in select.expressions or []:
            if not isinstance(proj, exp.Alias):
                continue
            want = by_name.get(str(proj.alias or "").casefold())
            if want is None:
                continue
            got = _norm_node(proj.this)
            if got != want:
                return got or str(proj.alias or "").casefold()
    return None


def _drift_tuple(
    drift: str,
    approved: dict[str, dict[str, Any]],
    meta: list[dict[str, Any]],
) -> tuple[str, str, list[dict[str, Any]]]:
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


def _revoked_tuple(
    row: dict[str, Any], meta: list[dict[str, Any]], *, cite_objects: bool
) -> tuple[str, str, list[dict[str, Any]]]:
    payload = _payload(row)
    definition = (
        f"revoked measure {payload.get('name')} grain {payload.get('grain')} "
        f"expression {payload.get('expression')}"
    )
    cited = [row, *_cited_for_measure(meta)] if cite_objects else [row]
    return ("revoked_measure", definition, cited)


def _measure_verdict(
    tree: exp.Expression, meta: list[dict[str, Any]], onto: Ontology
) -> tuple[str, str, list[dict[str, Any]]] | list[str] | None:
    """None when every aggregate is an approved measure expression.

    A list is a pass. Each item is ``derived:<agg>(<table>.<col>)``.
    A tuple is a reconfirm: code, definition, cited rows.
    """
    aggs = list(tree.find_all(exp.AggFunc))
    approved = _approved_measures(meta)
    redefined = _redefined_measure(tree, meta)
    if not aggs:
        if redefined:
            return _drift_tuple(redefined, approved, meta)
        return None
    revoked = _revoked_measures()
    declared = _declared_columns(onto)
    parents = _parent_map(onto)
    ctes = _cte_names(tree)
    arg_ids = _argument_ids(tree)
    revoked_hit: dict[str, Any] | None = None
    drift: str | None = None
    derived: list[str] = []
    for agg in aggs:
        key = _norm_node(agg)
        if key and key in approved:
            continue
        if key and key in revoked:
            revoked_hit = revoked[key]
            continue
        prov = _structural_provenance(
            agg, declared=declared, parents=parents, ctes=ctes, arg_ids=arg_ids
        )
        if prov:
            derived.append(prov)
            continue
        drift = key or "aggregate"
    if redefined:
        drift = redefined
    if revoked_hit is not None and drift is None:
        return _revoked_tuple(revoked_hit, meta, cite_objects=True)
    if drift is not None or revoked_hit is not None:
        if revoked_hit is not None:
            return _revoked_tuple(revoked_hit, meta, cite_objects=False)
        return _drift_tuple(drift or "aggregate", approved, meta)
    if derived:
        return sorted(set(derived))
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
