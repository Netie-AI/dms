"""UX-GRANT-DATA-01. Default off. Digit 1 only.

On, an abstain that names a missing measure or an ungranted table ranks
connected-but-ungranted columns and ontology candidates with the existing
embedder, or Okapi BM25 when that embedder is absent. The ask returns a
card and no rows. Nothing is granted until a steward sends allow=true.
That call writes the existing grant book (and its audit) and re-runs the
original question through the ask path. A user-confirmed measure is stored
as a proposed ontology metric and a verified example that lookup will not
replay.

Off, ``consider`` returns the same envelope object and does not read the
catalog. No keyword table and no regex on the question.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

ENV = "DMS_GRANT_ASKBACK"
# ponytail: process dict, oldest dropped at 64. Upgrade: a row keyed by token.
_CAP = 64
_LOCK = Lock()
_PENDING: dict[str, dict[str, Any]] = {}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MEASURE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEADS = frozenset(
    {
        "unknown_measure",
        "no_declared_measure",
        "ungranted",
        "ungranted_table",
        "sql_relation_not_granted",
        "unknown_column",
        "unknown_object",
        "missing_metric",
    }
)
_REASON_WORDS = frozenset({"no", "measure", "named", "a", "the", "table", "column", "gap"})
_ACTIONS = (
    {"id": "grant", "label": "Ask steward to grant"},
    {"id": "define_measure", "label": "Define the measure"},
    {"id": "pick_measure", "label": "Pick a different measure"},
)
_ACTION_LINE = " / ".join(str(row["label"]) for row in _ACTIONS)
_CONNECT = ({"id": "connect_source", "label": "Connect a source"},)
_DIGIT = re.compile(r"\d")

Propose = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Rerun = Callable[..., dict[str, Any]]


def enabled() -> bool:
    """True only when ``DMS_GRANT_ASKBACK`` is the digit 1."""
    return os.environ.get(ENV, "0").strip() == "1"


def reset_pending() -> None:
    """Test isolation. Production never calls this."""
    with _LOCK:
        _PENDING.clear()


def steward_may_grant(
    *,
    role: str,
    actor: str,
    allow: bool,
    store: Any,
) -> bool:
    """True only when this steward explicitly allows the grant."""
    if allow is not True:
        return False
    holds = getattr(store, "holds_steward_grant", None)
    if not callable(holds):
        return False
    return bool(holds(role=role, actor_id=actor))


def consider(
    env: dict[str, Any],
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    warehouse: Path | None,
    grantable: set[str] | None,
    embedder: Callable[[list[str]], Any] | None = None,
    propose: Propose | None = None,
    ontology: Any = None,
    catalog: Sequence[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Attach a grant card onto a scope-miss abstain. Flag off is a no-op."""
    if not enabled() or not isinstance(env, dict):
        return env
    if env.get("abstained") is not True or "grant_askback" in env:
        return env
    blob = _scope_blob(env)
    if blob is None:
        return env
    try:
        card, text = _card(
            question=question,
            space_id=space_id,
            session_id=session_id,
            warehouse=warehouse,
            grantable=set(grantable or ()),
            embedder=embedder,
            propose=propose,
            ontology=ontology if ontology is not None else _load_ontology(space_id),
            catalog=catalog,
            blob=blob,
        )
    except Exception:
        logger.warning("grant_askback_unavailable")
        return env
    shown = card.get("text") if isinstance(card, dict) else None
    if isinstance(shown, str) and shown.strip():
        text = shown
    if card is None or not text or _DIGIT.search(text):
        return env
    trial = dict(env)
    trial["text"] = text
    trial["rows"] = []
    trial["values"] = []
    trial["sql_used"] = None
    trial["drillthrough_token"] = None
    trial["contributing_sources"] = []
    trial["badge"] = "ABSTAIN"
    trial["abstained"] = True
    trial["grant_askback"] = card
    assumptions = list(trial.get("assumptions") or [])
    assumptions.append(f"grant_askback:{card.get('kind')}")
    trial["assumptions"] = assumptions
    from dms_executor.envelope import assert_envelope_valid

    try:
        assert_envelope_valid(trial)
    except AssertionError:
        logger.warning("grant_askback_envelope_rejected")
        return env
    env.update(trial)
    return env


def apply_action(
    *,
    token: str,
    action: str,
    allow: bool,
    role: str,
    actor: str,
    token_id: str,
    store: Any,
    rerun: Rerun | None = None,
    warehouse: Path | None = None,
    dialect: str | None = None,
    formula: str | None = None,
    measure: str | None = None,
) -> dict[str, Any]:
    """Steward grant, a filed measure, a pick, or a connect note.

    Grant is the only action that writes the grant book, and only when
    ``steward_may_grant`` is true. The question is then re-run. The preview
    SQL is not executed here.
    """
    kind = str(action or "").strip()
    if kind == "grant":
        if not steward_may_grant(role=role, actor=actor, allow=allow, store=store):
            code = "steward_allow_required" if allow is not True else "steward_grant_required"
            raise PermissionError(code)
        pending = _take(token)
        if pending is None:
            raise KeyError(token)
        source_id = str(pending.get("source_id") or "")
        table = str(pending.get("table") or "")
        if not source_id or not table:
            raise ValueError("nothing_to_grant")
        from dms_core.control_plane.connect_grants import grant_tables

        grant_tables(
            source_id=source_id,
            tables=[table],
            actor=actor,
            token_id=token_id or actor,
        )
        return _call_rerun(rerun, pending)
    pending = _take(token)
    if pending is None:
        raise KeyError(token)
    if kind == "define_measure":
        return _file_measure(
            pending,
            store=store,
            actor=actor,
            warehouse=warehouse,
            dialect=dialect,
            formula=formula,
        )
    if kind == "pick_measure":
        name = str(measure or "").strip()
        picks = {str(row.get("name") or "") for row in pending.get("picks") or []}
        if not name or name not in picks:
            raise ValueError("measure_not_offered")
        return _call_rerun(rerun, pending)
    if kind == "connect_source":
        return _note(
            pending,
            text=_missing_text(str(pending.get("measure") or "")),
            reason="grant_askback_connect",
        )
    raise ValueError("grant_askback_action")


def _card(
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    warehouse: Path | None,
    grantable: set[str],
    embedder: Callable[[list[str]], Any] | None,
    propose: Propose | None,
    ontology: Any,
    catalog: Sequence[tuple[str, str, str]] | None,
    blob: str,
) -> tuple[dict[str, Any] | None, str]:
    measure = _measure_token(blob)
    candidates = _candidates(
        space_id=space_id,
        warehouse=warehouse,
        grantable=grantable,
        ontology=ontology,
        catalog=catalog,
    )
    query = question if not measure else f"{question}\n{measure}"
    winner, picks = _rank(query, candidates, embedder)
    if winner is None:
        text = _missing_text(measure)
        token = _remember(
            _pending(
                question=question,
                space_id=space_id,
                session_id=session_id,
                measure=measure,
                kind="missing",
            )
        )
        card = _mask_card(
            {
                "kind": "missing",
                "text": text,
                "actions": [dict(row) for row in _CONNECT],
                "picks": [],
                "token": token,
                "served": False,
            }
        )
        return card, text
    table = str(winner["table"])
    column = str(winner["column"])
    source = str(winner["source"] or "source")
    need, formula, preview = _draft(winner, propose, question, measure)
    if winner["granted"]:
        text = (
            f"{need} can use {table}.{column}, which this Space can see, "
            f"but no measure is declared. {_ACTION_LINE}"
        )
        kind = "define"
    else:
        target = f"{table}.{column}" if column else table
        text = (
            f"{need} needs {target} from {source}, which this Space can't see yet. {_ACTION_LINE}"
        )
        kind = "grant"
    if _DIGIT.search(text):
        return None, ""
    token = _remember(
        _pending(
            question=question,
            space_id=space_id,
            session_id=session_id,
            measure=measure,
            kind=kind,
            source_id=str(winner.get("source_id") or ""),
            table=table,
            column=column,
            formula=formula,
            sql_preview=preview,
            picks=picks,
        )
    )
    card = _mask_card(
        {
            "kind": kind,
            "text": text,
            "formula": formula,
            "sql_preview": preview,
            "actions": [dict(row) for row in _ACTIONS],
            "picks": [{"name": name} for name in picks],
            "token": token,
            "table": table,
            "column": column,
            "source": source,
            "can_grant": kind == "grant" and bool(winner.get("source_id")),
            "served": False,
        }
    )
    return card, text


def _pending(**row: Any) -> dict[str, Any]:
    row.setdefault("picks", [])
    row.setdefault("source_id", "")
    row.setdefault("table", "")
    row.setdefault("column", "")
    row.setdefault("formula", "")
    row.setdefault("sql_preview", "")
    return row


def _candidates(
    *,
    space_id: str | None,
    warehouse: Path | None,
    grantable: set[str],
    ontology: Any,
    catalog: Sequence[tuple[str, str, str]] | None,
) -> list[dict[str, Any]]:
    connected: dict[str, dict[str, str]] = {}
    if space_id:
        from dms_core.control_plane.connect_grants import exposed_ungranted

        for row in exposed_ungranted(space_id):
            connected[row["table"].lower()] = row
    out: list[dict[str, Any]] = []
    for table, column, typ in _columns(warehouse, catalog):
        if not _IDENT.fullmatch(table) or not _IDENT.fullmatch(column):
            continue
        src = connected.get(table.lower())
        granted = _is_granted(table, grantable)
        if src is None and not granted:
            continue
        if src is not None and granted:
            src = None
        source = ""
        source_id = ""
        if src is not None:
            source_id = src["source_id"]
            source = src["connector_id"] or src["source_id"]
        elif granted:
            source = "this Space"
        if not _IDENT.fullmatch(source):
            source = "source" if src is not None else "this Space"
        out.append(
            {
                "kind": "column",
                "name": column,
                "table": table,
                "column": column,
                "type": typ,
                "source": source,
                "source_id": source_id,
                "granted": granted and src is None,
                "expression": "",
                "doc": " ".join(part for part in (source, f"{table}.{column}", typ) if part),
            }
        )
    out.extend(_ontology_candidates(ontology, grantable, connected))
    return out


def _ontology_candidates(
    ontology: Any,
    grantable: set[str],
    connected: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    measures = getattr(ontology, "measures", None)
    if not isinstance(measures, dict):
        return []
    out: list[dict[str, Any]] = []
    for measure in measures.values():
        name = str(getattr(measure, "name", "") or "")
        expression = str(getattr(measure, "expression", "") or "")
        description = str(getattr(measure, "description", "") or "")
        synonyms = getattr(measure, "synonyms", ()) or ()
        grain = str(getattr(measure, "grain", "") or "")
        table = grain if _IDENT.fullmatch(grain) else ""
        column = _expr_column(expression)
        if not table or not column:
            continue
        src = connected.get(table.lower())
        granted = _is_granted(table, grantable)
        source = "ontology"
        source_id = ""
        if src is not None and not granted:
            source_id = str(src.get("source_id") or "")
            source = str(src.get("connector_id") or source_id or "source")
        phrase = " ".join(
            part
            for part in (name, description, expression, " ".join(str(s) for s in synonyms))
            if part
        )
        out.append(
            {
                "kind": "measure",
                "name": name,
                "table": table,
                "column": column,
                "type": "",
                "source": source if _IDENT.fullmatch(source) else "ontology",
                "source_id": source_id,
                "granted": granted and not source_id,
                "expression": expression,
                "doc": phrase or f"{table}.{column}",
            }
        )
    return out


def _columns(
    warehouse: Path | None,
    catalog: Sequence[tuple[str, str, str]] | None,
) -> list[tuple[str, str, str]]:
    if catalog is not None:
        return [(str(t), str(c), str(typ)) for t, c, typ in catalog]
    from dms_executor.schema_retry import _catalog_columns

    try:
        return list(_catalog_columns(warehouse))
    except Exception:
        logger.warning("grant_askback_catalog_unavailable")
        return []


def _load_ontology(space_id: str | None) -> Any:
    if not space_id:
        return None
    try:
        from dms_executor.space_ontology import load_space_ontology

        return load_space_ontology(space_id)
    except Exception:
        logger.warning("grant_askback_ontology_unavailable")
        return None


def _rank(
    question: str,
    candidates: list[dict[str, Any]],
    embedder: Callable[[list[str]], Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not question.strip() or not candidates:
        return None, []
    from dms_executor.verified_queries import rank_texts

    ranked = rank_texts(
        question,
        [str(row["doc"]) for row in candidates],
        embedder,
        limit=8,
        floor=0.0,
    )
    winner: dict[str, Any] | None = None
    picks: list[str] = []
    for _score, index in ranked:
        row = candidates[index]
        if winner is None:
            winner = row
            continue
        if row["kind"] == "measure":
            name = str(row["name"] or "")
            if name and name not in picks and name != winner.get("name"):
                picks.append(name)
        if len(picks) >= 3:
            break
    return winner, picks


def _draft(
    winner: Mapping[str, Any],
    propose: Propose | None,
    question: str,
    measure: str,
) -> tuple[str, str, str]:
    """Need label, formula, and one unread SELECT. The model may write them.

    A reply that names a different column, or a numeric literal, is dropped.
    The structural formula is the fallback. Nothing here is executed.
    """
    table = str(winner["table"])
    column = str(winner["column"])
    formula, preview = _structural(winner)
    need = measure.replace("_", " ") if measure and not _DIGIT.search(measure) else "This question"
    if propose is None:
        return need, formula, preview
    try:
        raw = propose(
            {
                "question": question,
                "measure": measure,
                "table": table,
                "column": column,
                "source": str(winner.get("source") or ""),
                "type": str(winner.get("type") or ""),
            }
        )
    except Exception:
        logger.warning("grant_askback_propose_unavailable")
        return need, formula, preview
    if not isinstance(raw, Mapping):
        return need, formula, preview
    got_need = " ".join(str(raw.get("need") or "").split())
    if got_need and len(got_need) <= 80 and not _DIGIT.search(got_need):
        need = got_need
    got_formula = str(raw.get("formula") or "").strip()
    got_sql = str(raw.get("sql_preview") or "").strip()
    checked = _one_read(got_sql, table, column) if got_sql else None
    if checked and got_formula and column in got_formula and not _DIGIT.search(got_formula):
        return need, got_formula, checked
    if checked:
        return need, formula, checked
    return need, formula, preview


def _structural(winner: Mapping[str, Any]) -> tuple[str, str]:
    table = str(winner["table"])
    column = str(winner["column"])
    expression = str(winner.get("expression") or "").strip()
    formula = ""
    if expression and column in expression and not _has_number(expression):
        formula = expression
    if not formula:
        from dms_executor.schema_context import _type_family

        if _type_family(str(winner.get("type") or "")) == "numeric":
            formula = f"SUM({table}.{column})"
        else:
            formula = f"{table}.{column}"
    preview = _one_read(f"SELECT {formula} AS value FROM {table}", table, column) or formula
    return formula, preview


def _one_read(sql: str, table: str, column: str) -> str | None:
    from sqlglot import exp, parse

    from dms_executor.demo_warehouse import SERVING_DIALECT

    try:
        trees = [tree for tree in parse(sql, read=SERVING_DIALECT) if tree is not None]
    except Exception:
        return None
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        return None
    tree = trees[0]
    if any(isinstance(node, exp.Literal) and node.is_number for node in tree.walk()):
        return None
    columns = {node.name.lower() for node in tree.find_all(exp.Column) if node.name}
    if column.lower() not in columns:
        return None
    tables = {node.name.lower() for node in tree.find_all(exp.Table) if node.name}
    if tables and table.lower() not in tables:
        return None
    return tree.sql(dialect=SERVING_DIALECT)


def _has_number(sql: str) -> bool:
    from sqlglot import exp, parse_one

    from dms_executor.demo_warehouse import SERVING_DIALECT

    try:
        tree = parse_one(sql, read=SERVING_DIALECT)
    except Exception:
        return bool(_DIGIT.search(sql))
    if tree is None:
        return True
    return any(isinstance(node, exp.Literal) and node.is_number for node in tree.walk())


def _expr_column(expression: str) -> str:
    from sqlglot import exp, parse_one

    from dms_executor.demo_warehouse import SERVING_DIALECT

    try:
        tree = parse_one(expression, read=SERVING_DIALECT)
    except Exception:
        return ""
    if tree is None:
        return ""
    for node in tree.find_all(exp.Column):
        if node.name and _IDENT.fullmatch(node.name):
            return node.name
    return ""


def _file_measure(
    pending: Mapping[str, Any],
    *,
    store: Any,
    actor: str,
    warehouse: Path | None,
    dialect: str | None,
    formula: str | None,
) -> dict[str, Any]:
    table = str(pending.get("table") or "")
    column = str(pending.get("column") or "")
    chosen = str(formula or pending.get("formula") or "").strip()
    if not _IDENT.fullmatch(table) or not _IDENT.fullmatch(column):
        raise ValueError("measure_formula_rejected")
    if not chosen or column not in chosen or _has_number(chosen):
        raise ValueError("measure_formula_rejected")
    preview = str(pending.get("sql_preview") or "")
    checked = _one_read(preview, table, column) if preview else None
    sql_text = checked or _one_read(f"SELECT {chosen} AS value FROM {table}", table, column)
    if not sql_text:
        raise ValueError("measure_formula_rejected")
    name = _proposal_name(str(pending.get("measure") or ""), column)
    body = {
        "status": "proposed",
        "served": False,
        "source": "grant-askback",
        "vendor_sha": "grant-askback",
        "provenance": {"role": "ontology", "source": "grant-askback"},
        "terms": [
            {
                "name": table,
                "table": table,
                "description": table,
                "key": [column],
                "attributes": [],
            }
        ],
        "measures": [
            {
                "name": name,
                "grain": table,
                "expression": chosen,
                "description": chosen,
                "additive": True,
            }
        ],
    }
    stored = store.store_proposal(
        body,
        created_by=actor or "grant-askback",
        space_id=pending.get("space_id"),
    )
    if stored.get("served") or stored.get("status") != "proposed":
        raise RuntimeError("proposal_served")
    from dms_executor.verified_queries import save_proposed_example

    save_proposed_example(
        question=str(pending.get("question") or ""),
        sql=sql_text,
        space_id=pending.get("space_id") if isinstance(pending.get("space_id"), str) else None,
        warehouse=warehouse,
        dialect=dialect,
        actor=actor,
    )
    return _note(
        pending,
        text="The measure was filed as a proposal. It is not an answer.",
        reason="measure_proposed",
        extra={"kind": "filed", "served": False, "version_id": stored.get("version_id")},
    )


def _note(
    pending: Mapping[str, Any],
    *,
    text: str,
    reason: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from datetime import UTC, datetime

    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    env = build_abstain(
        reason=reason,
        question=str(pending.get("question") or ""),
        stage="grant_askback",
        answer_id="ans_grant_askback",
        text=text,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[f"grant_askback:{reason}", "served:false"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=pending.get("space_id") if isinstance(pending.get("space_id"), str) else None,
        session_id=(
            pending.get("session_id") if isinstance(pending.get("session_id"), str) else None
        ),
        ask_mode="live",
        route="abstain",
        abstain_reason=reason,
    )
    block = {"kind": "missing", "text": text, "served": False}
    if extra:
        block.update(dict(extra))
    env["grant_askback"] = block
    assert_envelope_valid(env)
    return env


def _call_rerun(rerun: Rerun | None, pending: Mapping[str, Any]) -> dict[str, Any]:
    if rerun is None:
        raise RuntimeError("ask_rerun_required")
    env = rerun(
        str(pending.get("question") or ""),
        space_id=pending.get("space_id"),
        session_id=pending.get("session_id"),
    )
    if not isinstance(env, dict):
        raise RuntimeError("ask_rerun_empty")
    return env


def _remember(row: dict[str, Any]) -> str:
    token = "gak_" + uuid.uuid4().hex
    with _LOCK:
        if len(_PENDING) >= _CAP:
            _PENDING.pop(next(iter(_PENDING)), None)
        _PENDING[token] = row
    return token


def _take(token: str) -> dict[str, Any] | None:
    with _LOCK:
        return _PENDING.pop(str(token or ""), None)


def _mask_card(card: dict[str, Any]) -> dict[str, Any]:
    from dms_core.pii import mask_unknown_keys

    scanned = mask_unknown_keys({"grant_askback": card})
    got = scanned.get("grant_askback")
    return got if isinstance(got, dict) else card


def _missing_text(measure: str) -> str:
    name = measure if measure and not _DIGIT.search(measure) else "the measure"
    return (
        f"{name} is not in this Space's grant, and no connected column ranks for it. "
        f"Connect a source that contains {name}."
    )


def _proposal_name(measure: str, column: str) -> str:
    raw = measure.lower()
    if _MEASURE_NAME.fullmatch(raw):
        return raw
    col = column.lower()
    if _MEASURE_NAME.fullmatch(col):
        return f"proposed_{col}"[:64]
    return "proposed_measure"


def _is_granted(table: str, grantable: set[str]) -> bool:
    names = {name.lower() for name in grantable}
    low = table.lower()
    if low in names:
        return True
    return any(name.endswith("." + low) for name in names)


def _scope_blob(env: Mapping[str, Any]) -> str | None:
    blobs: list[str] = []
    reason = env.get("abstain_reason")
    if isinstance(reason, str):
        blobs.append(reason)
    assumptions = env.get("assumptions")
    if isinstance(assumptions, list):
        blobs.extend(item for item in assumptions if isinstance(item, str))
    block = env.get("reconfirm")
    if isinstance(block, dict):
        code = block.get("reason_code")
        if isinstance(code, str):
            blobs.append(code)
    text = env.get("text")
    if isinstance(text, str) and "gap:" in text:
        blobs.append(text)
    hit: str | None = None
    for blob in blobs:
        if not any(part in _HEADS for part in re.split(r"[^A-Za-z0-9_]+", blob)):
            continue
        if _measure_token(blob):
            return blob
        hit = hit or blob
    return hit


def _measure_token(blob: str) -> str:
    for head in _HEADS:
        marker = head + ":"
        if marker not in blob:
            continue
        tail = blob.split(marker, 1)[1]
        tail = tail.split(")", 1)[0].split(",", 1)[0]
        found = [
            item
            for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", tail)
            if item.lower() not in _REASON_WORDS and item not in _HEADS
        ]
        if found:
            return found[-1]
    return ""
