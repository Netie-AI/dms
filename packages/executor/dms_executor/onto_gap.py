"""Propose a missing measure after a reconfirm. Flag default off.

``DMS_ONTO_GAP_PROPOSE`` does nothing unless it is ``1`` and ``DMS_ONTO_PG``
is ``1``. Off, ask envelopes, prompts, and routes are unchanged.

On, a reconfirm whose reason code starts with ``unknown_measure`` or
``ungrounded_measure``, or model SQL whose aggregate is not one of the
ontology's measures, is logged. The model call is not on the ask. A
background thread, or ``scripts/onto/propose_gaps.py --since``, drains the
log. The OpenVault client sends role ``ontology`` and does not name a
model. A 422 is ``gap_propose_unavailable`` and today's reconfirm stands.

A valid proposal is a new ontology version, status ``proposed``, served
false, stored through the same ``store_proposal`` path as AO compile.
Steward approval is ``onto_steward`` / ``approve_version``. Yes on the
reconfirm does not approve. After approval, GROUND-DEFS ranks the measure
with the existing embedder or BM25. No keyword map is added.

ponytail: the gap log is a JSONL file and the in-flight envelope dict is
process-local. Upgrade: a table keyed by ask id if a restart must rewrite
an envelope that already left the process.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from dms_executor.demo_warehouse import SERVING_DIALECT, connect_serving
from dms_executor.ontology import Ontology

logger = logging.getLogger(__name__)

FLAG = "DMS_ONTO_GAP_PROPOSE"
PG_FLAG = "DMS_ONTO_PG"
ASYNC_FLAG = "DMS_ONTO_GAP_ASYNC"
LOG_ENV = "DMS_ONTO_GAP_LOG"
ROLE = "ontology"
SCHEMA_NAME = "MeasureGapProposal"
# Not the settings default actor. store_proposal does not approve.
_WRITER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
_GAP_HEADS = frozenset({"unknown_measure", "ungrounded_measure"})
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FK = ("source_table", "source_column", "target_table", "target_column")
_OPS = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    "<>": exp.NEQ,
    "<": exp.LT,
    ">": exp.GT,
    "<=": exp.LTE,
    ">=": exp.GTE,
}
_SKIP_PROVENANCE = frozenset({"question", "text", "rows", "samples", "sql"})

_LOCK = threading.Lock()
_ENVS: dict[str, dict[str, Any]] = {}
_BUSY: set[str] = set()
_CTX: ContextVar[dict[str, Any] | None] = ContextVar("dms_onto_gap_ctx", default=None)


class MeasureGapProposal(BaseModel):
    """One proposed measure, or ``none`` when the model declines."""

    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    grain: str | None = None
    expression: str | None = None
    filters: list[Any] | None = None
    time_column: str | None = None
    unit: str | None = None
    description: str | None = None
    assumptions: list[str] | None = None
    none: str | None = None


def gap_propose_enabled() -> bool:
    """True only when both flags are the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1" and os.environ.get(PG_FLAG, "0").strip() == "1"


def question_hash(question: str) -> str:
    """Hash of the normalised question. The text is not the hash input's echo."""
    try:
        from dms_executor.demo_ask import normalize_ask_question

        normed = normalize_ask_question(question) or question
    except Exception:
        normed = question
    flat = " ".join(str(normed or "").casefold().split())
    return hashlib.sha256(flat.encode()).hexdigest()


def reset_gap_state() -> None:
    """Drop in-process envelopes. Tests use a fresh log path of their own."""
    with _LOCK:
        _ENVS.clear()
        _BUSY.clear()
    _CTX.set(None)


def bind_gap_context(
    *,
    question: str,
    warehouse: str,
    grantable: set[str] | Sequence[str] | None,
    ontology: Ontology | None,
    space_id: str | None,
    session_id: str | None,
) -> Token[dict[str, Any] | None]:
    """Remember the ask that just built an envelope. The model call is later."""
    names = sorted({str(name) for name in (grantable or []) if str(name).strip()})
    payload: dict[str, Any] = {
        "question": question,
        "question_hash": question_hash(question),
        "warehouse": warehouse,
        "grantable": names,
        "ontology": ontology,
        "space_id": space_id,
        "session_id": session_id,
    }
    return _CTX.set(payload)


def ask_ontology() -> Ontology | None:
    ctx = _CTX.get()
    if not isinstance(ctx, dict):
        return None
    onto = ctx.get("ontology")
    return onto if isinstance(onto, Ontology) else None


def gap_log_path() -> Path:
    raw = os.environ.get(LOG_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "dms-onto-gaps.jsonl"


def undeclared_aggregate_reason(sql: str, ontology: Ontology | None) -> str | None:
    """``unknown_measure:undeclared_aggregate`` when model SQL invents an aggregate.

    None when the flag is off, the SQL has no aggregate, or every aggregate
    matches a measure expression on ``ontology``. Comparison is the normalised
    sqlglot tree, not a scan of the question.
    """
    if not gap_propose_enabled():
        return None
    aggs = _outer_agg_keys(sql)
    if not aggs:
        return None
    known: set[str] = set()
    if ontology is not None:
        for measure in ontology.measures.values():
            known |= _measure_keys(measure.expression)
    if all(key in known for key in aggs):
        return None
    return "unknown_measure:undeclared_aggregate"


def note_reconfirm(
    env: dict[str, Any],
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> None:
    """Log a measure gap after the reconfirm envelope exists. Does not call a model.

    Flag off: return without reading the envelope. The ask then returns the
    same object it already built.
    """
    if not gap_propose_enabled() or not isinstance(env, dict):
        return
    reason = _gap_reason(env)
    if reason is None:
        return
    ctx = _context_for(question)
    gap_id = uuid.uuid4().hex
    record: dict[str, Any] = {
        "gap_id": gap_id,
        "ts": _now(),
        "status": "pending",
        "ask_id": _ask_id(env),
        "question_hash": question_hash(question),
        "question": question,
        "reason_code": reason,
        "space_id": space_id if isinstance(space_id, str) else ctx.get("space_id"),
        "session_id": session_id if isinstance(session_id, str) else ctx.get("session_id"),
        "warehouse": str(ctx.get("warehouse") or ""),
        "grantable": list(ctx.get("grantable") or []),
    }
    _append(record)
    with _LOCK:
        _ENVS[gap_id] = env
    if os.environ.get(ASYNC_FLAG, "1").strip() == "1":
        threading.Thread(target=_safe_drain, args=(gap_id,), name="onto-gap", daemon=True).start()


def drain_since(*, since: str, client: Any = None) -> list[dict[str, Any]]:
    """Propose every pending gap at or after ``since``. ``since`` of ``0`` drains all."""
    if not gap_propose_enabled():
        return []
    return _drain_records(_pending(since), client)


def _safe_drain(gap_id: str) -> None:
    try:
        _drain_records(_pending_ids([gap_id]), None)
    except Exception as exc:
        logger.error("onto_gap drain failed: %s", type(exc).__name__)


def _drain_records(records: list[dict[str, Any]], client: Any) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    owned = client is None
    caller = client
    for record in records:
        gap_id = str(record.get("gap_id") or "")
        if not gap_id or not _claim(gap_id):
            continue
        try:
            if caller is None:
                caller = _new_client()
            outcome = _propose_one(record, caller)
        except Exception as exc:
            outcome = _outcome(gap_id, "dropped", drop_reason=type(exc).__name__)
        _append(outcome)
        outcomes.append(outcome)
        _release(gap_id)
        if owned:
            caller = None
    return outcomes


def _propose_one(record: Mapping[str, Any], client: Any) -> dict[str, Any]:
    gap_id = str(record.get("gap_id") or "")
    question = str(record.get("question") or "")
    try:
        proposal, unavailable = _complete(client, record)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == "ao_ov_http_422" or str(code).endswith("_422"):
            return _outcome(
                gap_id,
                "gap_propose_unavailable",
                drop_reason="gap_propose_unavailable",
            )
        if isinstance(code, str) and code:
            return _outcome(gap_id, "dropped", drop_reason=code)
        return _outcome(gap_id, "dropped", drop_reason=type(exc).__name__)
    if unavailable:
        return _outcome(gap_id, "gap_propose_unavailable", drop_reason="gap_propose_unavailable")
    if proposal is None:
        return _outcome(gap_id, "dropped", drop_reason="output_invalid")
    if isinstance(proposal.none, str) and proposal.none.strip():
        return _outcome(gap_id, "dropped", drop_reason="model_none")
    built = _validate(proposal, record)
    if isinstance(built, str):
        return _outcome(gap_id, "dropped", drop_reason=built)
    stored = _store(built, record, question)
    if isinstance(stored, str):
        return _outcome(gap_id, "dropped", drop_reason=stored)
    name = str(built["name"])
    code = f"measure_proposed:{name}"
    _annotate(_ENVS.get(gap_id), code)
    return _outcome(
        gap_id,
        "done",
        reason_code=code,
        version_id=str(stored.get("version_id") or ""),
    )


def _complete(client: Any, record: Mapping[str, Any]) -> tuple[MeasureGapProposal | None, bool]:
    from ao_vendor.llm.messages import HumanMessage, SystemMessage
    from ao_vendor.llm.ov_client import AoLlmError

    catalog = _catalog(str(record.get("warehouse") or ""), list(record.get("grantable") or []))
    if catalog is None:
        raise AoLlmError("schema_unavailable")
    measures = _known_measures(record)
    body = {
        "question": str(record.get("question") or ""),
        "schema": {
            "tables": catalog["tables"],
            "declared_fks": catalog["declared_fks"],
        },
        "measures": measures,
    }
    messages = [
        SystemMessage(
            "Return one JSON object for a single aggregate measure. "
            "Use only columns from the granted schema. "
            "expression is SQL over those columns. "
            "If no measure should be added, set none to a short reason. "
            "Do not name a model or a provider."
        ),
        HumanMessage(json.dumps(body, sort_keys=True, default=str)),
    ]
    try:
        parsed = client.complete_structured(messages, MeasureGapProposal)
    except AoLlmError as exc:
        if exc.code == "ao_ov_http_422" or str(exc.code).endswith("_422"):
            return None, True
        raise
    except Exception:
        return None, False
    return parsed, False


def _validate(proposal: MeasureGapProposal, record: Mapping[str, Any]) -> dict[str, Any] | str:
    name = str(proposal.name or "").strip()
    grain_raw = str(proposal.grain or "").strip()
    expression = str(proposal.expression or "").strip()
    if not _NAME.fullmatch(name):
        return "name_invalid"
    if not _IDENT.fullmatch(grain_raw):
        return "grain_invalid"
    catalog = _catalog(str(record.get("warehouse") or ""), list(record.get("grantable") or []))
    if catalog is None:
        return "schema_unavailable"
    grain = _table_spelling(grain_raw, catalog)
    if grain is None:
        return "ungranted_grain"
    try:
        trees = [tree for tree in sqlglot.parse(expression, read=SERVING_DIALECT) if tree]
    except Exception:
        return "expression_unparsed"
    if len(trees) != 1:
        return "expression_unparsed"
    tree = trees[0]
    if _writes(tree):
        return "statement_not_allowed"
    columns: dict[str, set[str]] = catalog["columns"]
    if _refs_ok(tree, grain, columns) is not None:
        return "ungranted_column"
    time_column = str(proposal.time_column or "").strip()
    if time_column:
        if not _IDENT.fullmatch(time_column):
            return "time_column_ungranted"
        if time_column.casefold() not in columns.get(grain.casefold(), set()):
            return "time_column_ungranted"
    where, filter_reason = _filters(proposal.filters, grain, columns)
    if filter_reason:
        return filter_reason
    if where is not None and isinstance(tree, exp.Select):
        return "filters_invalid"
    if where is not None and isinstance(tree, exp.AggFunc):
        tree = exp.Filter(this=tree, expression=exp.Where(this=where))
    stored_sql = _norm_tree(tree)
    if not stored_sql:
        return "expression_unparsed"
    probe = _probe_sql(tree, grain, None if isinstance(tree, exp.Filter) else where)
    if not probe:
        return "expression_unparsed"
    ran = _run(probe, str(record.get("warehouse") or ""))
    if ran:
        return ran
    known = _known_measures(record)
    if any(str(row.get("name") or "").casefold() == name.casefold() for row in known):
        return "name_collision"
    stored_key = _norm_sql(stored_sql)
    for row in known:
        other = _norm_sql(str(row.get("expression") or ""))
        if stored_key and other and stored_key == other:
            return "duplicate_measure"
    assumptions = proposal.assumptions if isinstance(proposal.assumptions, list) else []
    return {
        "name": name,
        "grain": grain,
        "expression": stored_sql,
        "description": str(proposal.description or "").strip(),
        "unit": str(proposal.unit or "").strip(),
        "time_column": time_column,
        "filters": proposal.filters if isinstance(proposal.filters, list) else [],
        "assumptions": [str(item) for item in assumptions if isinstance(item, str)],
        "key": catalog["keys"].get(grain.casefold()) or _first_column(catalog, grain),
        "checks": ["parsed", "granted", "explain", "executed", "name", "not_duplicate"],
    }


def _store(
    built: Mapping[str, Any], record: Mapping[str, Any], question: str
) -> dict[str, Any] | str:
    from dms_core.control_plane.onto_data import ontology_data_store

    grain = str(built["grain"])
    key = str(built.get("key") or "")
    if not key:
        return "grain_invalid"
    store = ontology_data_store()
    have_object = _approved_object(store, grain)
    provenance = {
        "question_hash": question_hash(question),
        "ask_id": str(record.get("ask_id") or ""),
        "model_stamp": {"role": ROLE, "schema": SCHEMA_NAME},
        "validation": {"ok": True, "checks": list(built.get("checks") or [])},
    }
    for key_name in _SKIP_PROVENANCE:
        provenance.pop(key_name, None)
    body: dict[str, Any] = {
        "status": "proposed",
        "served": False,
        "source": "onto-gap",
        "vendor_sha": "onto-gap",
        "provenance": provenance,
        "measures": [
            {
                "name": built["name"],
                "grain": grain,
                "expression": built["expression"],
                "description": built["description"],
                "additive": True,
                "unit": built["unit"],
                "time_column": built["time_column"],
                "filters": built["filters"],
                "assumptions": built["assumptions"],
            }
        ],
    }
    if not have_object:
        body["terms"] = [
            {
                "name": grain,
                "table": grain,
                "description": str(built["description"] or grain),
                "attributes": [],
                "key": [key],
            }
        ]
    try:
        stored = store.store_proposal(
            body,
            created_by=_WRITER,
            space_id=_uuid_or_none(record.get("space_id")),
        )
    except Exception as exc:
        return type(exc).__name__
    if stored.get("served") or stored.get("status") != "proposed":
        return "store_rejected"
    if not stored.get("version_id"):
        return "store_rejected"
    return stored


def _annotate(env: dict[str, Any] | None, code: str) -> None:
    if not isinstance(env, dict):
        return
    block = env.get("reconfirm")
    if not isinstance(block, dict):
        return
    name = code.split(":", 1)[1]
    text = f"A definition {name} was proposed and awaits steward approval. Yes does not approve it."
    before_text = env.get("text")
    before_assumptions = list(env.get("assumptions") or [])
    before_block = dict(block)
    block["reason_code"] = code
    block["code"] = code
    block["reason_text"] = text
    block["yes_is_approval"] = False
    env["text"] = text
    assumptions = list(env.get("assumptions") or [])
    assumptions.append(f"reconfirm: {code}")
    assumptions.append("yes is not steward approval")
    env["assumptions"] = assumptions
    try:
        from dms_executor.envelope import assert_envelope_valid

        assert_envelope_valid(env)
    except Exception:
        env["text"] = before_text
        env["assumptions"] = before_assumptions
        env["reconfirm"] = before_block


def _catalog(warehouse: str, grantable: Sequence[str]) -> dict[str, Any] | None:
    path = Path(warehouse) if warehouse else None
    if path is None or not path.is_file():
        return None
    allowed = {part.split(".")[-1].casefold() for part in grantable if str(part).strip()}
    if not allowed:
        return None
    try:
        con = connect_serving(path)
    except Exception:
        return None
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'main' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
        try:
            constraints = con.execute(
                "SELECT table_name, constraint_type, constraint_column_names, "
                "referenced_table, referenced_column_names "
                "FROM duckdb_constraints() WHERE schema_name = 'main'"
            ).fetchall()
        except Exception:
            constraints = []
    except Exception:
        return None
    finally:
        con.close()
    columns: dict[str, set[str]] = {}
    tables: list[dict[str, Any]] = []
    current = ""
    bucket: list[dict[str, str]] = []

    def _flush() -> None:
        nonlocal bucket, current
        if current:
            tables.append({"name": current, "columns": bucket})
        bucket = []

    for table_name, column_name, data_type in rows:
        table = str(table_name)
        if table.casefold() not in allowed:
            continue
        column = str(column_name)
        if not _IDENT.fullmatch(table) or not _IDENT.fullmatch(column):
            continue
        if table != current:
            _flush()
            current = table
        columns.setdefault(table.casefold(), set()).add(column.casefold())
        bucket.append({"name": column, "type": str(data_type or "")})
    _flush()
    if not tables:
        return None
    keys: dict[str, str] = {}
    declared: list[dict[str, str]] = []
    for table_name, kind, cols, ref_table, ref_cols in constraints:
        table = str(table_name)
        if table.casefold() not in allowed:
            continue
        kind_text = str(kind or "").upper()
        names = _constraint_names(cols)
        if kind_text == "PRIMARY KEY" and names and table.casefold() not in keys:
            keys[table.casefold()] = names[0]
        if kind_text != "FOREIGN KEY":
            continue
        ref = str(ref_table or "")
        targets = _constraint_names(ref_cols)
        if not names or not targets or ref.casefold() not in allowed:
            continue
        declared.append(
            {
                "source_table": table,
                "source_column": names[0],
                "target_table": ref,
                "target_column": targets[0],
            }
        )
    declared.extend(_ontology_fks(allowed))
    return {
        "tables": tables,
        "columns": columns,
        "keys": keys,
        "declared_fks": _dedupe_fks(declared),
    }


def _run(sql: str, warehouse: str) -> str | None:
    path = Path(warehouse)
    if not path.is_file():
        return "schema_unavailable"
    try:
        con = connect_serving(path)
    except Exception:
        return "schema_unavailable"
    try:
        if not _one_select(con, sql):
            return "statement_not_allowed"
        try:
            con.execute("EXPLAIN " + sql)
        except Exception:
            return "explain_failed"
        try:
            cursor = con.execute(sql)
            rows = cursor.fetchall()
        except Exception:
            return "execute_failed"
    finally:
        con.close()
    return _finite_rows(rows)


def _finite_rows(rows: Sequence[Any]) -> str | None:
    if not rows:
        return "empty_result"
    saw = False
    for row in rows:
        cells = row if isinstance(row, (tuple, list)) else (row,)
        for cell in cells:
            kind = _number(cell)
            if kind is None:
                continue
            if not kind:
                return "non_finite"
            saw = True
    if not saw:
        return "empty_result"
    return None


def _number(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return None


def _one_select(con: Any, sql: str) -> bool:
    import duckdb

    try:
        stmts = con.extract_statements(sql)
    except Exception:
        return False
    return len(stmts) == 1 and stmts[0].type == duckdb.StatementType.SELECT


def _filters(
    raw: Any, grain: str, columns: Mapping[str, set[str]]
) -> tuple[exp.Expression | None, str | None]:
    if raw is None or raw == []:
        return None, None
    if not isinstance(raw, list):
        return None, "filters_invalid"
    parts: list[exp.Expression] = []
    granted = columns.get(grain.casefold(), set())
    for item in raw:
        if not isinstance(item, dict):
            return None, "filters_invalid"
        column = str(item.get("column") or "").strip()
        op = str(item.get("op") or "").strip()
        if not _IDENT.fullmatch(column) or column.casefold() not in granted:
            return None, "ungranted_column"
        built = _predicate(column, op, item.get("value"))
        if built is None:
            return None, "filters_invalid"
        parts.append(built)
    if not parts:
        return None, None
    expr = parts[0]
    for part in parts[1:]:
        expr = exp.and_(expr, part)
    return expr, None


def _predicate(column: str, op: str, value: Any) -> exp.Expression | None:
    left = exp.column(column)
    if op.casefold() == "in":
        if not isinstance(value, list) or not value:
            return None
        literals = [_literal(item) for item in value]
        if any(item is None for item in literals):
            return None
        return exp.In(this=left, expressions=literals)
    maker = _OPS.get(op)
    if maker is None:
        return None
    right = _literal(value)
    if right is None:
        return None
    return maker(this=left, expression=right)


def _literal(value: Any) -> exp.Expression | None:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int) and not isinstance(value, bool):
        return exp.Literal.number(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return exp.Literal.number(value)
    if isinstance(value, str):
        return exp.Literal.string(value)
    return None


def _probe_sql(tree: exp.Expression, grain: str, where: exp.Expression | None) -> str | None:
    if isinstance(tree, exp.Select):
        return tree.sql(dialect=SERVING_DIALECT)
    try:
        probe = exp.select(exp.alias_(tree, "gap_value")).from_(exp.to_table(grain))
        if where is not None:
            probe = probe.where(where)
        return probe.sql(dialect=SERVING_DIALECT)
    except Exception:
        return None


def _refs_ok(tree: exp.Expression, grain: str, columns: Mapping[str, set[str]]) -> str | None:
    granted = set(columns)
    grain_l = grain.casefold()
    if grain_l not in granted:
        return "ungranted_grain"
    for table in tree.find_all(exp.Table):
        name = (table.name or "").casefold()
        if name and name not in granted:
            return "ungranted_column"
    for col in tree.find_all(exp.Column):
        qual = (col.table or "").casefold()
        if isinstance(col.this, exp.Star):
            if qual and qual not in granted:
                return "ungranted_column"
            continue
        name = (col.name or "").casefold()
        if not name:
            return "ungranted_column"
        if qual:
            if qual not in granted or name not in columns.get(qual, set()):
                return "ungranted_column"
        elif name not in columns.get(grain_l, set()):
            return "ungranted_column"
    return None


def _writes(tree: exp.Expression) -> bool:
    return isinstance(
        tree,
        (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Drop,
            exp.Create,
            exp.Alter,
            exp.Command,
            exp.Merge,
        ),
    )


def _known_measures(record: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    onto = ask_ontology()
    ctx = _CTX.get()
    same_ask = isinstance(ctx, dict) and ctx.get("question_hash") == record.get("question_hash")
    if onto is None and same_ask:
        candidate = ctx.get("ontology") if isinstance(ctx, dict) else None
        if isinstance(candidate, Ontology):
            onto = candidate
    if onto is not None:
        for measure in onto.measures.values():
            rows.append(
                {
                    "name": measure.name,
                    "grain": measure.grain,
                    "expression": measure.expression,
                }
            )
    try:
        from dms_core.control_plane.onto_data import ontology_data_store

        for item in ontology_data_store().approved_items():
            if str(item.get("kind") or "") != "measure":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            rows.append(
                {
                    "name": str(payload.get("name") or ""),
                    "grain": str(payload.get("grain") or ""),
                    "expression": str(payload.get("expression") or ""),
                }
            )
    except Exception:
        return rows
    return rows


def _approved_object(store: Any, grain: str) -> bool:
    try:
        items = store.approved_items()
    except Exception:
        return False
    for item in items:
        if str(item.get("kind") or "") != "object":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        if str(payload.get("name") or "") == grain or str(payload.get("relation") or "") == grain:
            return True
    return False


def _ontology_fks(allowed: set[str]) -> list[dict[str, str]]:
    onto = ask_ontology()
    if onto is None:
        return []
    rows: list[dict[str, str]] = []
    for link in onto.links.values():
        if not link.from_columns or not link.to_columns:
            continue
        if link.from_object.casefold() not in allowed or link.to_object.casefold() not in allowed:
            continue
        rows.append(
            {
                "source_table": link.from_object,
                "source_column": link.from_columns[0],
                "target_table": link.to_object,
                "target_column": link.to_columns[0],
            }
        )
    return rows


def _outer_agg_keys(sql: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(sql, read=SERVING_DIALECT)
    except Exception:
        return []
    keys: list[str] = []
    seen: list[exp.Expression] = []
    for node in tree.find_all(exp.AggFunc):
        parent = node.parent
        chosen: exp.Expression = parent if isinstance(parent, exp.Filter) else node
        if any(chosen is item for item in seen):
            continue
        seen.append(chosen)
        norm = _norm_tree(chosen)
        if norm:
            keys.append(norm)
    return keys


def _measure_keys(expression: str) -> set[str]:
    keys: set[str] = set()
    whole = _norm_sql(expression)
    if whole:
        keys.add(whole)
    keys.update(_outer_agg_keys(expression))
    return keys


def _norm_sql(sql: str) -> str:
    try:
        tree = sqlglot.parse_one(sql, read=SERVING_DIALECT)
    except Exception:
        return ""
    return _norm_tree(tree)


def _norm_tree(node: exp.Expression) -> str:
    try:
        copied = node.copy().transform(_unqualify)
        return copied.sql(dialect=SERVING_DIALECT, normalize=True)
    except Exception:
        return ""


def _unqualify(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
        name = node.name or ""
        if name:
            return exp.column(name)
    return node


def _table_spelling(grain: str, catalog: Mapping[str, Any]) -> str | None:
    for table in catalog.get("tables") or []:
        if isinstance(table, dict) and str(table.get("name") or "").casefold() == grain.casefold():
            return str(table["name"])
    return None


def _first_column(catalog: Mapping[str, Any], grain: str) -> str:
    for table in catalog.get("tables") or []:
        if not isinstance(table, dict):
            continue
        if str(table.get("name") or "") != grain:
            continue
        columns = table.get("columns") or []
        if columns and isinstance(columns[0], dict):
            return str(columns[0].get("name") or "")
    return ""


def _constraint_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip("[]{}")
        return [part.strip() for part in text.split(",") if _IDENT.fullmatch(part.strip() or "")]
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if _IDENT.fullmatch(str(part))]
    return []


def _dedupe_fks(rows: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (
            row.get("source_table", ""),
            row.get("source_column", ""),
            row.get("target_table", ""),
            row.get("target_column", ""),
        )
        if key in seen or not all(key):
            continue
        seen.add(key)
        out.append({name: str(row[name]) for name in _FK})
    return out


def _gap_reason(env: Mapping[str, Any]) -> str | None:
    block = env.get("reconfirm")
    if isinstance(block, dict):
        raw = block.get("reason_code")
        if isinstance(raw, str) and _head(raw) in _GAP_HEADS:
            return raw.strip()
    try:
        from dms_executor.abstain import _envelope_reason

        reason = _envelope_reason(dict(env))
    except Exception:
        reason = ""
    if _head(reason) in _GAP_HEADS:
        return reason.strip()
    return None


def _head(reason: str) -> str:
    return str(reason or "").split(":", 1)[0].strip().casefold()


def _context_for(question: str) -> dict[str, Any]:
    ctx = _CTX.get()
    if not isinstance(ctx, dict):
        return {}
    if ctx.get("question_hash") != question_hash(question):
        return {}
    return ctx


def _ask_id(env: Mapping[str, Any]) -> str:
    for key in ("answer_id", "audit_id", "ask_id"):
        raw = env.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "unspecified"


def _uuid_or_none(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        return None


def _new_client() -> Any:
    from ao_vendor.llm.ov_client import OpenVaultOntologyClient

    return OpenVaultOntologyClient()


def _outcome(
    gap_id: str,
    status: str,
    *,
    drop_reason: str | None = None,
    reason_code: str | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    return {
        "gap_id": gap_id,
        "ts": _now(),
        "status": status,
        "drop_reason": drop_reason,
        "reason_code": reason_code,
        "version_id": version_id,
    }


def _claim(gap_id: str) -> bool:
    with _LOCK:
        if gap_id in _BUSY:
            return False
        if _status(gap_id) != "pending":
            return False
        _BUSY.add(gap_id)
        return True


def _release(gap_id: str) -> None:
    with _LOCK:
        _BUSY.discard(gap_id)


def _pending(since: str) -> list[dict[str, Any]]:
    floor = "" if since.strip() in {"", "0"} else since.strip()
    rows = []
    for record in _payloads().values():
        if str(record.get("status") or "") != "pending":
            continue
        ts = str(record.get("ts") or "")
        if floor and ts < floor:
            continue
        rows.append(record)
    rows.sort(key=lambda row: str(row.get("ts") or ""))
    return rows


def _pending_ids(ids: Sequence[str]) -> list[dict[str, Any]]:
    payloads = _payloads()
    return [
        payloads[gid] for gid in ids if gid in payloads and payloads[gid].get("status") == "pending"
    ]


def _status(gap_id: str) -> str:
    found = _payloads().get(gap_id)
    if not found:
        return ""
    return str(found.get("status") or "")


def _payloads() -> dict[str, dict[str, Any]]:
    path = gap_log_path()
    if not path.is_file():
        return {}
    merged: dict[str, dict[str, Any]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        gap_id = str(row.get("gap_id") or "")
        if not gap_id:
            continue
        current = merged.get(gap_id, {})
        current.update(row)
        merged[gap_id] = current
    return merged


def _append(record: Mapping[str, Any]) -> None:
    path = gap_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(record), sort_keys=True, default=str)
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
