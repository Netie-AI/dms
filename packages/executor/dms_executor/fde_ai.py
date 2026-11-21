"""FDE-ONBOARD-01 (dms#541): the model stages of the onboarding pack.

Privacy rule. The model sees only pack content: names, landed types, counts,
shapes and PII tokens. A database error is reduced to its class name before it
is recorded, because the message can quote a cell value. Query results, raw
SQL and raw value maps stay in process memory and in the local item store; the
pack carries only masked views (``masked_question_row``).

What the model writes is data, never authority:

* an ontology draft is back-mapped and kept only for tables and columns that
  exist (rule 16 still decides every join at ask time);
* a verified question's SQL is back-mapped, parsed, refused if it carries a
  string literal (rule 12: the model never saw a value encoding), refused if it
  reads a table outside the Space, passed through the same join doors the ask
  uses (``ask_join_ontology`` + ``check_sql_against_space``, or
  ``no_ontology_join_reason``), executed on the serving cursor, and kept only
  with exact, non-empty, bounded rows (#540: a served figure is exact or
  abstains);
* a value mapping is back-mapped from masks to the raw values locally.

Every kept item is stored ``proposed`` by the caller. Nothing here changes an
answer. No model id is named anywhere: the role alias ``ontology`` is sent and
OpenVault picks the hop (rule 14).

Swap: OpenVault FreeRoute via ``ao_vendor`` ``ov_client``, no new port.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

import httpx
import sqlglot
from ao_vendor.llm.messages import HumanMessage, SystemMessage
from ao_vendor.llm.ov_client import AoLlmError, OpenVaultOntologyClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlglot import exp

from dms_executor.demo_warehouse import SERVING_DIALECT, connect_serving
from dms_executor.generative_ask import no_ontology_join_reason
from dms_executor.space_ontology import check_sql_against_space
from dms_executor.total_guard import repeated_input_reason

PROMPT_VERSION = "fde_onboard/v1"
PROMPT_DIR = Path(__file__).parent / "prompts" / "fde_onboard" / "v1"
#: sha256 of each prompt file. An edit is a new version directory, not a new hash.
PROMPT_SHA256: dict[str, str] = {
    "01_ontology_draft.md": "64750e72740bf14fc73725bdc996e8584d39e61587fe454f6e41198c9f162da5",
    "02_verified_questions.md": "c346aa7615c8c458904081ef3956993293a0147f028b738cbfcef6d88eb299af",
    "03_value_mappings.md": "d5e8ef0c6ae6e2f0182ae901447bf25b657a70b8982dd606d84b0316b1d157b5",
}

MAX_ROWS = 1000
#: Wall-clock bound on one question check; the serving connection is interrupted after it.
CHECK_TIMEOUT_S = 20.0
_FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL")
_FLOAT_ANYWHERE = re.compile(r"\b(DOUBLE|FLOAT|REAL)\b")
#: DuckDB reports these as CONSISTENT, but their value depends on row order, which a
#: question's SQL does not pin. Refused as order-dependent aggregates.
_ORDER_DEPENDENT = frozenset({"any_value", "arbitrary", "first", "last", "mode"})
#: sqlglot parses the bare keywords below into typed nodes that DuckDB does not list
#: as functions; each is a clock or random read.
_VOLATILE_NODES = (
    exp.CurrentDate,
    exp.CurrentTime,
    exp.CurrentTimestamp,
    exp.CurrentDatetime,
    exp.Rand,
    exp.Randn,
    exp.Uuid,
)
_STRING_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n)
    for n in ("RawString", "ByteString", "National", "HexString", "BitString", "UnicodeString")
    if hasattr(exp, n)
)
_COMPARISONS = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.NullSafeEQ,
    exp.NullSafeNEQ,
    exp.Like,
    exp.ILike,
    exp.SimilarTo,
    exp.RegexpLike,
    exp.RegexpILike,
    exp.Glob,
)
_MATCH_FUNCTIONS = frozenset(
    {
        "contains",
        "starts_with",
        "prefix",
        "suffix",
        "strpos",
        "instr",
        "position",
        "regexp_matches",
        "regexp_full_match",
        "regexp_extract",
        "like_escape",
    }
)
_MATCH_PREFIXES = ("jaro", "levenshtein")
_REASON_CODE = re.compile(r"[A-Za-z_]+(?::[A-Za-z_]+)?")
_REASON_HEAD = re.compile(r"[A-Za-z_]+(?=:|$)")


class PromptDrift(Exception):
    """A prompt file is missing or differs from its pinned sha256."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"prompt_drift:{name}")


class StageRefused(Exception):
    """A model stage could not run. ``code`` is a named reason, never a message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def load_prompts() -> dict[str, str]:
    """Read every pinned prompt, verifying its sha256. Fail closed."""
    out: dict[str, str] = {}
    for name, pinned in PROMPT_SHA256.items():
        try:
            raw = (PROMPT_DIR / name).read_bytes()
        except OSError:
            raise PromptDrift(name) from None
        if hashlib.sha256(raw).hexdigest() != pinned:
            raise PromptDrift(name)
        out[name] = raw.decode("utf-8")
    return out


def prompt_manifest() -> dict[str, Any]:
    return {
        "version": PROMPT_VERSION,
        "files": {name: {"sha256": sha} for name, sha in sorted(PROMPT_SHA256.items())},
    }


# --- output models -------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


_Name = Field(max_length=200)
_Names = Field(max_length=20)


class DraftObject(_Strict):
    table: str = _Name
    key: list[str] = _Names
    description: str = Field(default="", max_length=500)


class DraftLink(_Strict):
    from_table: str = _Name
    from_columns: list[str] = _Names
    to_table: str = _Name
    to_columns: list[str] = _Names


class OntologyDraftOut(_Strict):
    objects: list[DraftObject] = Field(max_length=50)
    links: list[DraftLink] = Field(max_length=100)


class QuestionOut(_Strict):
    question: str = Field(max_length=500)
    sql: str = Field(max_length=4000)


class VerifiedQuestionsOut(_Strict):
    questions: list[QuestionOut] = Field(max_length=20)


class DecisionOut(_Strict):
    group_id: str = Field(max_length=100)
    same: bool
    target: str = Field(default="", max_length=100)
    reason: str = Field(default="", max_length=300)


class ValueMappingsOut(_Strict):
    decisions: list[DecisionOut] = Field(max_length=200)


T = TypeVar("T", bound=BaseModel)


def run_stage(
    client: OpenVaultOntologyClient,
    prompt_name: str,
    prompt_text: str,
    user_payload: dict[str, Any],
    schema: type[T],
) -> T:
    """One fixed prompt plus one pack payload. Never logs payload or model text."""
    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(prompt_text),
        HumanMessage(json.dumps(user_payload, ensure_ascii=False, sort_keys=True)),
    ]
    try:
        return client.complete_structured(messages, schema)
    except Exception as exc:
        # A test may purge ``ao_vendor`` from sys.modules, so the client can raise an
        # AoLlmError class other than the one imported here. Match both, by class.
        current = getattr(sys.modules.get("ao_vendor.llm.ov_client"), "AoLlmError", None)
        classes = tuple(c for c in (AoLlmError, current) if isinstance(c, type))
        if isinstance(exc, classes):
            raise StageRefused(str(exc.code)) from None  # type: ignore[attr-defined]
        if isinstance(exc, ValidationError | ValueError):
            raise StageRefused("ao_model_output_invalid") from None
        if isinstance(exc, httpx.HTTPError | OSError):
            raise StageRefused("ao_ov_unreachable") from None
        raise


# --- stage 5a: ontology draft --------------------------------------------------

Item = tuple[str, dict[str, Any], dict[str, Any]]


def _resolve_table(
    token: str, back: Callable[[str], str], tables: Mapping[str, set[str]]
) -> str | None:
    raw = back(token)
    if raw in tables:
        return raw
    if f"bronze.{raw}" in tables:
        return f"bronze.{raw}"
    return None


def _resolve_columns(
    label: str, tokens: list[str], back: Callable[[str], str], tables: Mapping[str, set[str]]
) -> list[str] | None:
    cols = [back(t) for t in tokens]
    return cols if all(c in tables[label] for c in cols) else None


def ontology_items(
    out: OntologyDraftOut,
    *,
    back: Callable[[str], str],
    tables: Mapping[str, set[str]],
) -> tuple[list[Item], list[dict[str, Any]]]:
    """``(kind, raw payload, masked payload)`` per kept item, plus the drops.

    The masked payload keeps the tokens the model wrote and is what a pack file
    may carry. Drops carry a reason and a position, never a name or a value.
    """
    items: list[Item] = []
    dropped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, obj in enumerate(out.objects):
        label = _resolve_table(obj.table, back, tables)
        if label is None:
            dropped.append({"kind": "object", "index": i, "reason": "unknown_table"})
            continue
        key = _resolve_columns(label, obj.key, back, tables)
        if key is None:
            dropped.append({"kind": "object", "index": i, "reason": "unknown_column"})
            continue
        if label in seen:
            dropped.append({"kind": "object", "index": i, "reason": "duplicate"})
            continue
        seen.add(label)
        raw: dict[str, Any] = {
            "name": label,
            "relation": label,
            "key": key,
            "description": obj.description,
        }
        masked: dict[str, Any] = {
            "name": obj.table,
            "relation": obj.table,
            "key": list(obj.key),
            "description": obj.description,
        }
        items.append(("object", raw, masked))
    for i, link in enumerate(out.links):
        a = _resolve_table(link.from_table, back, tables)
        b = _resolve_table(link.to_table, back, tables)
        if a is None or b is None:
            dropped.append({"kind": "link", "index": i, "reason": "unknown_table"})
            continue
        fc = _resolve_columns(a, link.from_columns, back, tables)
        tc = _resolve_columns(b, link.to_columns, back, tables)
        if fc is None or tc is None or not fc or len(fc) != len(tc):
            dropped.append({"kind": "link", "index": i, "reason": "unknown_column"})
            continue
        name = f"fde_onboard:{a}.{','.join(fc)}->{b}.{','.join(tc)}"
        if name in seen:
            dropped.append({"kind": "link", "index": i, "reason": "duplicate"})
            continue
        seen.add(name)
        raw = {
            "name": name,
            "from_object": a,
            "from_columns": fc,
            "to_object": b,
            "to_columns": tc,
            "one_to_one": False,
        }
        masked = {
            "name": (
                f"fde_onboard:{link.from_table}.{','.join(link.from_columns)}"
                f"->{link.to_table}.{','.join(link.to_columns)}"
            ),
            "from_object": link.from_table,
            "from_columns": list(link.from_columns),
            "to_object": link.to_table,
            "to_columns": list(link.to_columns),
            "one_to_one": False,
        }
        items.append(("link", raw, masked))
    return items, dropped


# --- stage 5b: verified questions ----------------------------------------------


def _table_ref(t: exp.Table) -> str | None:
    if not isinstance(t.this, exp.Identifier) or t.args.get("catalog"):
        return None
    name = t.name.lower()
    return f"{t.db.lower()}.{name}" if t.db else name


def _result_types(con: Any, sql: str) -> list[tuple[str, str]]:
    rows = con.execute(f"DESCRIBE SELECT * FROM ({sql}) AS q").fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def _string_constant(root: exp.Expression) -> bool:
    """A string-typed constant in any spelling: 'x', $$x$$, E'x', N'x', hex, bit, U&'x'."""
    for node in root.walk():
        if isinstance(node, exp.Literal) and node.is_string:
            return True
        if _STRING_NODES and isinstance(node, _STRING_NODES):
            return True
    return False


def _func_names(node: exp.Func) -> set[str]:
    names = {node.sql_name().lower()}
    if isinstance(node, exp.Anonymous):
        names.add(str(node.name).lower())
    try:
        head = re.match(r"[A-Za-z_0-9]+", node.sql(dialect=SERVING_DIALECT))
    except Exception:  # noqa: BLE001
        head = None
    if head:
        names.add(head.group(0).lower())
    return names


def _landed_types(con: Any, owned: set[str]) -> dict[str, set[str]]:
    """Column name -> landed types across the Space's tables. Names collide on purpose."""
    out: dict[str, set[str]] = {}
    rows = con.execute(
        "SELECT lower(table_schema) || '.' || lower(table_name), lower(column_name), "
        "upper(data_type) FROM information_schema.columns"
    ).fetchall()
    for label, col, dtype in rows:
        if label in owned:
            out.setdefault(str(col), set()).add(str(dtype))
    return out


def _alias_exprs(root: exp.Expression) -> dict[str, list[exp.Expression]]:
    out: dict[str, list[exp.Expression]] = {}
    for al in root.find_all(exp.Alias):
        out.setdefault(str(al.alias).lower(), []).append(al.this)
    return out


def _read_types(
    node: exp.Expression,
    landed: Mapping[str, set[str]],
    aliases: Mapping[str, list[exp.Expression]],
    seen: frozenset[str] = frozenset(),
) -> set[str]:
    """Landed types of every column ``node`` reads, following derived aliases."""
    out: set[str] = set()
    for col in node.find_all(exp.Column):
        name = col.name.lower()
        if name in landed:
            out |= landed[name]
        elif name in aliases and name not in seen:
            for inner in aliases[name]:
                out |= _read_types(inner, landed, aliases, seen | {name})
    for cast in node.find_all(exp.Cast):
        if cast.to.is_type(*exp.DataType.TEXT_TYPES):
            out.add("VARCHAR")
    return out


def _reads_column(node: exp.Expression) -> bool:
    return node.find(exp.Column) is not None


def _encoding_reason(
    root: exp.Expression,
    landed: Mapping[str, set[str]],
    aliases: Mapping[str, list[exp.Expression]],
) -> str | None:
    """Rule 12: a VARCHAR column compared with something no column supplied."""

    def varchar(n: exp.Expression) -> bool:
        return "VARCHAR" in _read_types(n, landed, aliases)

    def mixed(operands: list[exp.Expression]) -> bool:
        return any(varchar(o) for o in operands) and any(not _reads_column(o) for o in operands)

    for node in root.walk():
        operands: list[exp.Expression] = []
        if isinstance(node, _COMPARISONS):
            operands = [node.this, node.expression]
        elif isinstance(node, exp.In):
            if node.args.get("query") is not None:
                continue
            operands = [node.this, *node.expressions]
        elif isinstance(node, exp.Between):
            operands = [node.this, node.args["low"], node.args["high"]]
        elif isinstance(node, exp.Case) and node.this is not None:
            operands = [node.this, *[i.this for i in node.args.get("ifs") or []]]
        elif isinstance(node, exp.Func):
            lowered = {n for n in _func_names(node)}
            if lowered & _MATCH_FUNCTIONS or any(n.startswith(_MATCH_PREFIXES) for n in lowered):
                for v in node.args.values():
                    operands.extend(x for x in (v if isinstance(v, list) else [v]) if x is not None)
                operands = [o for o in operands if isinstance(o, exp.Expression)]
        if operands and mixed(operands):
            return "unverified_value_encoding"
    return None


_CMP = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _inexact_input(root: exp.Expression, landed: Mapping[str, set[str]]) -> bool:
    """#540: a DOUBLE/FLOAT/REAL column may only be counted or compared as a filter."""
    floats = {n for n, ts in landed.items() if any(t.startswith(_FLOAT_TYPES) for t in ts)}
    for col in root.find_all(exp.Column):
        if col.name.lower() not in floats:
            continue
        parent = col.parent
        if isinstance(parent, exp.Distinct) and isinstance(parent.parent, exp.Count):
            continue
        if isinstance(parent, exp.Count):
            continue
        if not isinstance(parent, _CMP):
            return True
        in_filter = False
        child: exp.Expression = parent
        anc = parent.parent
        while anc is not None:
            if isinstance(anc, exp.AggFunc | exp.Window):
                return True
            if isinstance(anc, exp.Where | exp.Having) or (
                isinstance(anc, exp.Join) and child.arg_key == "on"
            ):
                in_filter = True
            child = anc
            anc = anc.parent
        if not in_filter:
            return True
    return False


def _volatile_reason(root: exp.Expression, volatile: set[str]) -> str | None:
    for node in root.walk():
        if isinstance(node, exp.TableSample):
            return "volatile_function"
        if isinstance(node, exp.Func) and (
            isinstance(node, _VOLATILE_NODES) or _func_names(node) & volatile
        ):
            return "volatile_function"
    return None


def _leaf_selects(root: exp.Expression) -> list[exp.Select]:
    if isinstance(root, exp.Select):
        return [root]
    if isinstance(root, exp.SetOperation):
        return [*_leaf_selects(root.left), *_leaf_selects(root.right)]
    return []


def _no_column_read(root: exp.Expression) -> bool:
    for sel in _leaf_selects(root):
        for item in sel.expressions:
            inner = item.this if isinstance(item, exp.Alias) else item
            if isinstance(inner, exp.Count) and isinstance(inner.this, exp.Star):
                continue
            if inner.find(exp.Column) is None and not any(
                not isinstance(s.parent, exp.Count) for s in inner.find_all(exp.Star)
            ):
                return True
    return False


def _count_query(root: exp.Select) -> str:
    c = root.copy()
    c.set("expressions", [exp.Count(this=exp.Star())])
    for key in ("group", "having", "qualify", "order", "limit", "offset", "distinct", "windows"):
        c.set(key, None)
    return str(c.sql(dialect=SERVING_DIALECT))


def ask_join_ontology(space_id: str) -> Any:
    """The ontology the ask path's join rule would read for this Space, the same calls.

    With ``DMS_ONTO_FROM_DATA`` on, the asking Space's items (ONTO-SCOPE-01
    ``ask_ontology``) when it has any; otherwise ``space_join_ontology`` (the derived
    ontology, else the steward-approved profile keys and links of #515). No second copy
    of either rule lives here.
    """
    from dms_executor.onto_profile import space_join_ontology
    from dms_executor.ontology import onto_from_data_enabled

    if onto_from_data_enabled():
        from dms_executor.onto_scope import ask_ontology

        data_onto = ask_ontology(space_id)
        if data_onto is not None:
            return data_onto
    return space_join_ontology(space_id)


def check_question_sql(
    sql: str,
    *,
    space_id: str,
    warehouse: Path,
    tables: Mapping[str, set[str]],
    back: Callable[[str], str],
) -> tuple[str | None, dict[str, Any]]:
    """``(drop reason or None, info)``. Info carries the executed rows, locally only."""
    from dms_executor.fde_pack import space_file_tables

    mapped = back(sql).strip().rstrip(";").strip()
    info: dict[str, Any] = {"sql": mapped}
    try:
        roots = [r for r in sqlglot.parse(mapped, read=SERVING_DIALECT) if r is not None]
    except Exception:  # noqa: BLE001 - any parse failure is a refusal
        return "not_one_select", info
    if len(roots) != 1 or not isinstance(roots[0], exp.Select | exp.SetOperation):
        return "not_one_select", info
    root = roots[0]
    if _string_constant(root):
        return "literal_value", info
    # Ownership is re-read here: a table another Space took over is not this Space's.
    try:
        owned = {t.lower() for t in space_file_tables(space_id, path=warehouse)}
    except Exception:  # noqa: BLE001 - fail closed
        owned = set()
    tables = {label: cols for label, cols in tables.items() if label.lower() in owned}
    allowed = {label.lower() for label in tables}
    ctes = {c.alias.lower() for c in root.find_all(exp.CTE)}
    for ref in root.find_all(exp.Table):
        name = _table_ref(ref)
        if name is None or (name not in allowed and not (name in ctes and "." not in name)):
            return "table_not_in_space", info
    # Short-lived connection: the join doors below open their own.
    probe = connect_serving(warehouse)
    try:
        lake_names = {
            str(r[0])
            for r in probe.execute(
                "SELECT lower(table_name) FROM information_schema.tables"
            ).fetchall()
        } | {label.lower().split(".")[-1] for label in tables}
    except Exception:  # noqa: BLE001 - fail closed
        return "execution_error:ProbeFailed", info
    finally:
        probe.close()
    if any(c in lake_names for c in ctes):
        return "cte_shadows_table", info
    try:
        onto = ask_join_ontology(space_id)
    except Exception:  # noqa: BLE001
        return "ontology_store_unavailable", info
    grantable = sorted(tables)
    why = (
        no_ontology_join_reason(mapped, space_id=space_id, warehouse=warehouse, grantable=grantable)
        if onto is None
        else check_sql_against_space(mapped, onto, warehouse, grantable)
    )
    if why:
        return why, info
    why = repeated_input_reason(mapped)
    if why:
        return why, info
    con = connect_serving(warehouse)
    timed_out = threading.Event()

    def _interrupt() -> None:
        timed_out.set()
        con.interrupt()

    timer = threading.Timer(CHECK_TIMEOUT_S, _interrupt)
    timer.daemon = True
    timer.start()
    try:
        try:
            landed = _landed_types(con, allowed)
            aliases = _alias_exprs(root)
            volatile = {
                str(r[0]).lower()
                for r in con.execute(
                    "SELECT DISTINCT function_name FROM duckdb_functions() "
                    "WHERE stability <> 'CONSISTENT'"
                ).fetchall()
            } | _ORDER_DEPENDENT
            if _volatile_reason(root, volatile):
                return "volatile_function", info
            if root.args.get("limit") is not None and root.args.get("order") is None:
                return "unordered_limit", info
            why = _encoding_reason(root, landed, aliases)
            if why:
                return why, info
            if _no_column_read(root):
                return "no_column_read", info
            types = _result_types(con, mapped)
            cur = con.execute(f"SELECT * FROM ({mapped}) AS q LIMIT {MAX_ROWS + 1}")
            columns = [d[0] for d in cur.description]
            fetched = cur.fetchall()
            matched = None
            if fetched and isinstance(root, exp.Select) and root.args.get("where") is not None:
                matched = con.execute(_count_query(root)).fetchone()
        except Exception as exc:  # noqa: BLE001 - class name only, never the message
            if timed_out.is_set():
                return "execution_timeout", info
            return f"execution_error:{type(exc).__name__}", info
    finally:
        timer.cancel()
        con.close()
    if not fetched:
        return "no_rows", info
    if len(fetched) > MAX_ROWS:
        return "too_many_rows", info
    if any(_FLOAT_ANYWHERE.search(t.upper()) for _n, t in types):
        return "inexact_result", info
    # After the executed result type, so an inexact figure keeps its older reason.
    if _inexact_input(root, landed):
        return "inexact_input", info
    if all(v is None for r in fetched for v in r):
        return "no_value", info
    if matched is not None and not matched[0]:
        return "filter_matches_nothing", info
    rows = [dict(zip(columns, r, strict=True)) for r in fetched]
    canon = sorted(json.dumps(r, default=str, sort_keys=True) for r in rows)
    info.update(
        {
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "result_sha256": hashlib.sha256(json.dumps(canon).encode()).hexdigest(),
        }
    )
    return None, info


def masked_question_row(
    qid: str, question: str, masked_sql: str, info: Mapping[str, Any]
) -> dict[str, Any]:
    """The pack-safe row of a verified question: the model's own text, never results.

    ``row_count`` stays in ``info`` (local). A count is a channel a hostile SQL can
    write a cell value into, so the pack never carries it.
    """
    del info
    return {"id": qid, "status": "proposed", "question": question, "sql": masked_sql}


def reason_code(reason: str) -> str:
    """The leading reason code only, safe for a pack file; anything else is unknown.

    A reason from ``check_sql_against_space`` can carry raw table names after the
    code. A code with a detail that is not a plain word keeps only its head.
    """
    if _REASON_CODE.fullmatch(reason):
        return reason
    head = _REASON_HEAD.match(reason)
    return head.group(0) if head else "unknown_reason"


# --- stage 5c: value mappings --------------------------------------------------


def value_mapping_items(
    out: ValueMappingsOut, groups: Mapping[str, dict[str, Any]]
) -> tuple[list[Item], list[dict[str, Any]]]:
    """``(kind, raw payload, masked payload)`` per mapping, plus the drops.

    The raw payload holds cell values and stays in the local item store. The
    masked payload holds group id and masks only.
    """
    items: list[Item] = []
    dropped: list[dict[str, Any]] = []
    for d in out.decisions:
        group = groups.get(d.group_id)
        if group is None:
            dropped.append({"group_id": d.group_id, "reason": "unknown_group"})
            continue
        if not d.same:
            dropped.append({"group_id": d.group_id, "reason": "model_says_distinct"})
            continue
        members = {str(m["mask"]): m for m in group["members"]}
        target = members.get(d.target)
        if target is None:
            dropped.append({"group_id": d.group_id, "reason": "unknown_target"})
            continue
        mapping = {
            str(m["value"]): str(target["value"])
            for m in group["members"]
            if str(m["value"]) != str(target["value"])
        }
        t, c, gid = group["table"], group["column"], group["group_id"]
        raw = {
            "name": f"value_map:{t}.{c}:{gid}:model",
            "table": t,
            "column": c,
            "group_id": gid,
            "folds": list(group.get("folds", [])),
            "map": mapping,
            "origin": "model",
        }
        masked = {
            "group_id": gid,
            "target_mask": d.target,
            "variants": len(mapping),
            "origin": "model",
        }
        items.append(("value_map", raw, masked))
    return items, dropped
