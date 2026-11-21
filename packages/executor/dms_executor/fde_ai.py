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
  uses (``load_space_ontology`` + ``check_sql_against_space``, or
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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

import duckdb
import httpx
import sqlglot
from ao_vendor.llm.messages import HumanMessage, SystemMessage
from ao_vendor.llm.ov_client import AoLlmError, OpenVaultOntologyClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlglot import exp

from dms_executor.demo_warehouse import SERVING_DIALECT, connect_serving
from dms_executor.generative_ask import no_ontology_join_reason
from dms_executor.space_ontology import check_sql_against_space, load_space_ontology
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
_FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL")


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
    except AoLlmError as exc:
        raise StageRefused(exc.code) from None
    except (ValidationError, ValueError):
        raise StageRefused("ao_model_output_invalid") from None
    except (httpx.HTTPError, OSError):
        raise StageRefused("ao_ov_unreachable") from None


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


def check_question_sql(
    sql: str,
    *,
    space_id: str,
    warehouse: Path,
    tables: Mapping[str, set[str]],
    back: Callable[[str], str],
) -> tuple[str | None, dict[str, Any]]:
    """``(drop reason or None, info)``. Info carries the executed rows, locally only."""
    mapped = back(sql).strip().rstrip(";").strip()
    info: dict[str, Any] = {"sql": mapped}
    try:
        roots = [r for r in sqlglot.parse(mapped, read=SERVING_DIALECT) if r is not None]
    except Exception:  # noqa: BLE001 - any parse failure is a refusal
        return "not_one_select", info
    if len(roots) != 1 or not isinstance(roots[0], exp.Select | exp.SetOperation):
        return "not_one_select", info
    root = roots[0]
    if any(lit.is_string for lit in root.find_all(exp.Literal)):
        return "literal_value", info
    allowed = {label.lower() for label in tables}
    ctes = {c.alias.lower() for c in root.find_all(exp.CTE)}
    for ref in root.find_all(exp.Table):
        name = _table_ref(ref)
        if name is None or (name not in allowed and not (name in ctes and "." not in name)):
            return "table_not_in_space", info
    try:
        onto = load_space_ontology(space_id)
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
    try:
        try:
            types = _result_types(con, mapped)
            cur = con.execute(f"SELECT * FROM ({mapped}) AS q LIMIT {MAX_ROWS + 1}")
            columns = [d[0] for d in cur.description]
            fetched = cur.fetchall()
        except duckdb.Error as exc:
            return f"execution_error:{type(exc).__name__}", info
    finally:
        con.close()
    if not fetched:
        return "no_rows", info
    if len(fetched) > MAX_ROWS:
        return "too_many_rows", info
    if any(t.upper().startswith(_FLOAT_TYPES) for _n, t in types):
        return "inexact_result", info
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
    """The pack-safe row of a verified question: the model's own text, never results."""
    return {
        "id": qid,
        "status": "proposed",
        "question": question,
        "sql": masked_sql,
        "row_count": int(info.get("row_count", 0)),
    }


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
