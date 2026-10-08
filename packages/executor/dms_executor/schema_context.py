"""SCHEMA-RETRIEVE: schema context for the SQL the model writes.

The input is a source description any connector can supply. It is not one
database. Datasets (or tables), columns, types, keys,
relationships, descriptions, samples, and the SQL dialect all come from that
description. Synonyms and extra joins come only from an ontology payload.
A curated ``Ontology`` and a later model-built mapping use one shape.
An empty ontology still yields a prompt from the connector description.

Text columns send no sample values unless a later model has tagged the
column ``non_personal`` with stored evidence. This module never sets that
tag. Numeric, date, and boolean columns may send samples. Every sample,
description, and filter hint passes ``fail_closed_mask_payload``. If that
call fails, the prompt goes out with no samples, no hints, and no
descriptions. The ask still runs.

Untagged text columns can still contribute one filter hint when a span of
the question equals one distinct value after case, whitespace, and
punctuation are folded. Fuzzy or partial hints exist only for a tagged
column, and those candidates are capped. The value index lives in memory,
keyed by Space. It is not written to disk and it is not shared across
Spaces. The prompt itself is per request. When the flag is on it is copied
onto the ask envelope. When the flag is off this module is not called.

Cortex pin 279cbd85 ``InsightsAskIn`` does not read this text. The wire field
is ``schema_context`` and leaves the box only when ``DMS_SCHEMA_CONTEXT`` is
on.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex_client.insights import SCHEMA_CONTEXT_FIELD, schema_context_enabled
from dms_core.pii import fail_closed_mask_payload, is_mask_token

from dms_executor.ontology import Ontology, relation_tables, table_is_granted

# ponytail: 4 characters per token. Ceiling: a model tokenizer disagrees.
# Upgrade: count with the tokenizer of the model that writes the SQL.
CHARS_PER_TOKEN = 4
MAX_PROMPT_TOKENS = 480
_SAMPLE_LIMIT = 3
_SAMPLE_CHARS = 80
# Partial hints on a tagged text column. Untagged columns never use this.
# ponytail: 3 candidates per span. Ceiling: a tagged column with many
# near-matches keeps only the first 3 in normalised order. Upgrade: the
# tagging model ranks them.
HINT_CANDIDATE_CAP = 3
_SPAN_WIDTH = 6
_PARTIAL_MIN_CHARS = 3
_BOOL_TYPES = frozenset({"bool", "boolean"})
_DATE_TYPES = frozenset(
    {"date", "time", "timestamp", "datetime", "timestamptz", "timetz"}
)
_NUM_TYPES = frozenset(
    {
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "int2",
        "int4",
        "int8",
        "decimal",
        "numeric",
        "float",
        "double",
        "real",
        "number",
        "money",
        "serial",
        "bigserial",
        "float4",
        "float8",
        "dec",
        "fixed",
    }
)
_INDEX_LOCK = threading.Lock()
_SPACE_INDEX: dict[str, dict[str, tuple[str, ...]]] = {}


def estimate_tokens(text: str) -> int:
    """Upper bound used to keep the schema prompt inside ``MAX_PROMPT_TOKENS``."""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def cached_value_index(space_id: str) -> dict[str, tuple[str, ...]]:
    """Copy of one Space's in-memory text-value index. Empty if that Space has none."""
    with _INDEX_LOCK:
        found = _SPACE_INDEX.get(space_id or "")
        if not found:
            return {}
        return {key: tuple(vals) for key, vals in found.items()}


def _remember_index(space_id: str | None, index: dict[str, tuple[str, ...]]) -> None:
    if not space_id:
        return
    snap = {key: tuple(vals) for key, vals in index.items()}
    with _INDEX_LOCK:
        _SPACE_INDEX[space_id] = snap


@dataclass(frozen=True)
class ColumnFact:
    table: str
    name: str
    data_type: str
    primary_key: bool
    foreign_key: str
    description: str
    samples: tuple[str, ...]
    distinct: int | None
    score: float


@dataclass(frozen=True)
class MeasureFact:
    name: str
    grain: str
    expression: str
    description: str
    aliases: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class JoinFact:
    left_table: str
    left_col: str
    right_table: str
    right_col: str
    label: str


@dataclass(frozen=True)
class HintFact:
    table: str
    column: str
    value: str
    fuzzy: bool


@dataclass(frozen=True)
class SchemaContext:
    prompt: str
    samples_included: bool
    dialect: str


def _ident(name: str) -> bool:
    if not name or len(name) > 128:
        return False
    first = name[0]
    if not (first.isalpha() or first == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name[1:])


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _fold(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        chars.append(ch.lower() if ch.isalnum() else " ")
    return " ".join("".join(chars).split())


def _trigrams(text: str) -> dict[str, int]:
    folded = _fold(text)
    if not folded:
        return {}
    padded = f"  {folded} "
    if len(padded) < 3:
        return {padded: 1}
    out: dict[str, int] = {}
    for i in range(len(padded) - 2):
        gram = padded[i : i + 3]
        out[gram] = out.get(gram, 0) + 1
    return out


def _cosine(left: Mapping[str, int], right: Mapping[str, int]) -> float:
    if not left or not right:
        return 0.0
    dot = 0
    for key, count in left.items():
        other = right.get(key)
        if other:
            dot += count * other
    if dot <= 0:
        return 0.0
    norm_l = sum(count * count for count in left.values()) ** 0.5
    norm_r = sum(count * count for count in right.values()) ** 0.5
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return dot / (norm_l * norm_r)


def _dialect(value: Any) -> str:
    """Connector dialect, copied through. Blank when the connector did not name one."""
    return _text(value)


def _name_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw = list(value)
    else:
        return []
    out: list[str] = []
    for item in raw:
        text = _text(item)
        if text:
            out.append(text)
    return out


def _relationship_items(raw: Any) -> list[dict[str, str]]:
    """One relationship shape: name, from, from_column, to, to_column."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        left = _text(item.get("from"))
        right = _text(item.get("to"))
        if not left or not right:
            continue
        from_cols = _name_list(item.get("from_column"))
        to_cols = _name_list(item.get("to_column"))
        if len(from_cols) == 1 and len(to_cols) > 1:
            from_cols = from_cols * len(to_cols)
        width = min(len(from_cols), len(to_cols))
        label = _text(item.get("name")) or f"{left}_{right}"
        for idx in range(width):
            out.append(
                {
                    "name": label,
                    "from": left,
                    "from_column": from_cols[idx],
                    "to": right,
                    "to_column": to_cols[idx],
                }
            )
    return out


def _measure_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        grain = _text(item.get("grain"))
        expression = _text(item.get("expression"))
        if not name or not grain or not expression:
            continue
        aliases = tuple(dict.fromkeys(_name_list(item.get("aliases"))))
        out.append(
            {
                "name": name,
                "grain": grain,
                "expression": expression,
                "description": _text(item.get("description")),
                "aliases": aliases,
            }
        )
    return out


def ontology_payload(ontology: Ontology | Mapping[str, Any] | None) -> dict[str, Any]:
    """Curated object or model mapping, both as measures + relationships.

    ``None`` and an empty mapping are an empty ontology. No synonyms are
    invented here. Aliases are copied only when the payload already has them.
    """
    if ontology is None:
        return {"measures": [], "relationships": []}
    if isinstance(ontology, Ontology):
        return _payload_from_ontology(ontology)
    if isinstance(ontology, Mapping):
        return {
            "measures": _measure_items(ontology.get("measures")),
            "relationships": _relationship_items(ontology.get("relationships")),
        }
    return {"measures": [], "relationships": []}


def _physical_name(relation: str) -> str | None:
    raw = (relation or "").strip()
    if _ident(raw):
        return raw
    tables = [t for t in relation_tables(raw) if _ident(t)]
    if len(tables) == 1:
        return tables[0]
    return None


def _payload_from_ontology(onto: Ontology) -> dict[str, Any]:
    plain: dict[str, str] = {}
    for obj in onto.objects.values():
        table = _physical_name(obj.relation)
        if table:
            plain[obj.name] = table
    relationships: list[dict[str, str]] = []
    for link in onto.links.values():
        left = plain.get(link.from_object)
        right = plain.get(link.to_object)
        if not left or not right:
            continue
        for src, dst in zip(link.from_columns, link.to_columns, strict=False):
            if not _ident(src) or not _ident(dst):
                continue
            relationships.append(
                {
                    "name": link.name,
                    "from": left,
                    "from_column": src,
                    "to": right,
                    "to_column": dst,
                }
            )
    measures: list[dict[str, Any]] = []
    for measure in onto.measures.values():
        if not measure.name or not measure.grain or not measure.expression:
            continue
        measures.append(
            {
                "name": measure.name,
                "grain": measure.grain,
                "expression": " ".join(measure.expression.split()),
                "description": _text(measure.description),
                "aliases": (),
            }
        )
    return {"measures": measures, "relationships": relationships}


def _dataset_items(schema: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = schema.get("datasets")
    if raw is None:
        raw = schema.get("tables")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _granted(name: str, grantable: set[str] | None) -> bool:
    if grantable is None:
        return True
    return table_is_granted(name, grantable)


def _column_items(dataset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = dataset.get("columns")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _type_family(data_type: str) -> str:
    """numeric, date, boolean, or text. An empty type is text."""
    raw = _text(data_type).lower()
    head = raw.split("(", 1)[0].strip().split()
    token = head[0] if head else ""
    if token in _BOOL_TYPES:
        return "boolean"
    if token in _DATE_TYPES:
        return "date"
    if token in _NUM_TYPES or token.startswith("uint"):
        return "numeric"
    return "text"


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _tagged(column: Mapping[str, Any]) -> bool:
    """True only when a later model stored evidence on ``non_personal``.

    Nothing in this module writes that tag.
    """
    tag = column.get("non_personal")
    if not isinstance(tag, Mapping):
        return False
    evidence = tag.get("evidence")
    return isinstance(evidence, str) and bool(evidence.strip())


def _string_list(column: Mapping[str, Any], key: str) -> list[str]:
    return _name_list(column.get(key))


def _index_values(column: Mapping[str, Any], family: str) -> list[str]:
    if family != "text":
        return []
    listed = _string_list(column, "values")
    if not listed:
        listed = _string_list(column, "samples")
    return listed


def _sample_values(column: Mapping[str, Any], family: str, tagged: bool) -> list[str]:
    if family == "text" and not tagged:
        return []
    listed = _string_list(column, "samples")
    if not listed:
        listed = _string_list(column, "values")
    return listed[:_SAMPLE_LIMIT]


def _distinct_count(column: Mapping[str, Any], values: Sequence[str]) -> int | None:
    given = _count(column.get("distinct"))
    if given is not None:
        return given
    if not values:
        return None
    return len(dict.fromkeys(values))


def _normalize(text: str) -> str:
    """Fold case, whitespace, and punctuation. Alphanumerics stay, in order."""
    chars: list[str] = []
    for ch in text:
        if ch.isalnum():
            chars.append(ch.lower())
        elif ch.isspace():
            chars.append(" ")
    return " ".join("".join(chars).split())


def _spans(question: str) -> list[str]:
    tokens = _normalize(question).split()
    out: list[str] = []
    seen: set[str] = set()
    width_max = min(_SPAN_WIDTH, len(tokens))
    for width in range(1, width_max + 1):
        for start in range(len(tokens) - width + 1):
            span = " ".join(tokens[start : start + width])
            if span and span not in seen:
                seen.add(span)
                out.append(span)
    return out


def _hint_values(
    spans: Sequence[str],
    values: Sequence[str],
    *,
    tagged: bool,
) -> list[tuple[str, bool]]:
    """Original values to hint. Untagged: one exact normalised match. Tagged: capped fuzzy."""
    groups: dict[str, list[str]] = {}
    for raw in values:
        folded = _normalize(raw)
        if folded:
            groups.setdefault(folded, []).append(raw)
    chosen: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for span in spans:
        if not tagged:
            originals = groups.get(span) or []
            if len(originals) != 1:
                continue
            value = originals[0]
            if value in seen:
                continue
            seen.add(value)
            chosen.append((value, False))
            continue
        matches: list[tuple[str, bool]] = []
        for folded, originals in groups.items():
            exact = folded == span
            partial = (
                not exact
                and len(span) >= _PARTIAL_MIN_CHARS
                and (span in folded or folded in span)
            )
            if not exact and not partial:
                continue
            for value in originals:
                matches.append((value, not exact))
        matches.sort(key=lambda item: (_normalize(item[0]), item[0]))
        for value, fuzzy in matches[:HINT_CANDIDATE_CAP]:
            if value in seen:
                continue
            seen.add(value)
            chosen.append((value, fuzzy))
    return chosen


def _mask_slots(slots: list[tuple[str, str]]) -> list[str] | None:
    """Mask each ``(field, raw)`` string. ``None`` means the mask failed."""
    if not slots:
        return []
    rows = [{field: raw} for field, raw in slots]
    try:
        masked = fail_closed_mask_payload(text="", rows=rows, values=[], sources=[])
    except Exception:  # noqa: BLE001 -- fail closed: no samples, no hints, no descriptions
        return None
    if not isinstance(masked, dict):
        return None
    got = masked.get("rows")
    if not isinstance(got, list) or len(got) != len(slots):
        return None
    out: list[str] = []
    for (field, _raw), row in zip(slots, got, strict=True):
        if not isinstance(row, dict) or field not in row or row[field] is None:
            return None
        out.append(str(row[field]))
    return out


def _cleared(raw: str, got: str) -> bool:
    return got == raw and not is_mask_token(got)


def _join_facts(items: Sequence[Mapping[str, str]]) -> list[JoinFact]:
    out: list[JoinFact] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        if item["from"] == item["to"]:
            continue
        key = (item["from"], item["from_column"], item["to"], item["to_column"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            JoinFact(
                left_table=item["from"],
                left_col=item["from_column"],
                right_table=item["to"],
                right_col=item["to_column"],
                label=item["name"],
            )
        )
    return out


def _format_column(col: ColumnFact) -> str:
    bits = [col.name, col.data_type or "unknown"]
    if col.primary_key:
        bits.append("primary_key")
    if col.foreign_key:
        bits.append(f"foreign_key={col.foreign_key}")
    if col.description:
        bits.append(f"description={col.description}")
    if col.samples:
        bits.append("samples=" + ", ".join(v[:_SAMPLE_CHARS] for v in col.samples))
    elif col.distinct is not None:
        bits.append(f"distinct={col.distinct}")
    return "- " + " ".join(bits)


def _format_measure(measure: MeasureFact) -> str:
    alias = f" aliases={','.join(measure.aliases)}" if measure.aliases else ""
    desc = f" -- {measure.description}" if measure.description else ""
    return (
        f"- {measure.name} grain={measure.grain} "
        f"expression={measure.expression}{alias}{desc}"
    )


def _format_join(link: JoinFact) -> str:
    return (
        f"- {link.left_table}.{link.left_col} = "
        f"{link.right_table}.{link.right_col} ({link.label})"
    )


def _format_hint(hint: HintFact) -> str:
    op = "~" if hint.fuzzy else "="
    return f"- {hint.table}.{hint.column} {op} {hint.value}"


def _render(
    dialect: str,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
    hints: Sequence[HintFact],
    measures: Sequence[MeasureFact],
    table_notes: Mapping[str, str] | None = None,
) -> str:
    lines: list[str] = []
    if dialect:
        lines.append(f"DIALECT: {dialect}")
    lines.append("SCHEMA")
    notes = table_notes or {}
    by_table: dict[str, list[ColumnFact]] = {}
    for col in columns:
        by_table.setdefault(col.table, []).append(col)
    for table in sorted(by_table):
        head = f"- {table}"
        note = notes.get(table, "")
        if note:
            head = f"{head} description={note}"
        lines.append(head)
        for col in sorted(by_table[table], key=lambda item: item.name):
            lines.append(_format_column(col))
    if joins:
        lines.append("JOINS")
        for link in joins:
            lines.append(_format_join(link))
    if hints:
        lines.append("FILTER HINTS")
        for hint in hints:
            lines.append(_format_hint(hint))
    if measures:
        lines.append("MEASURES")
        for measure in measures:
            lines.append(_format_measure(measure))
    return "\n".join(lines)


def _joins_for(columns: Sequence[ColumnFact], joins: Sequence[JoinFact]) -> list[JoinFact]:
    chosen = {(col.table, col.name) for col in columns}
    return [
        link
        for link in joins
        if link.left_table != link.right_table
        and (link.left_table, link.left_col) in chosen
        and (link.right_table, link.right_col) in chosen
    ]


def _header_tokens(
    dialect: str,
    hints: Sequence[HintFact],
    measures: Sequence[MeasureFact],
) -> int:
    return estimate_tokens(_render(dialect, [], [], hints, measures))


def _measures_that_fit(
    dialect: str,
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> list[MeasureFact]:
    chosen: list[MeasureFact] = []
    ordered = sorted(measures, key=lambda item: (-item.score, item.name))
    for measure in ordered:
        trial = [*chosen, measure]
        if _header_tokens(dialect, [], trial) <= max_tokens:
            chosen = trial
    return chosen


def _hints_that_fit(
    dialect: str,
    hints: Sequence[HintFact],
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> list[HintFact]:
    chosen: list[HintFact] = []
    for hint in hints:
        trial = [*chosen, hint]
        if _header_tokens(dialect, trial, measures) <= max_tokens:
            chosen = trial
    return chosen


def _fits(
    dialect: str,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
    hints: Sequence[HintFact],
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> bool:
    rendered = _render(dialect, columns, _joins_for(columns, joins), hints, measures)
    return estimate_tokens(rendered) <= max_tokens


def _related(
    col: ColumnFact,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
) -> list[ColumnFact]:
    """Key columns on this dataset and one hop along a declared relationship."""
    wanted: set[tuple[str, str]] = set()
    for other in columns:
        if other.table == col.table and (other.primary_key or other.foreign_key):
            wanted.add((other.table, other.name))
    for link in joins:
        if link.left_table == col.table:
            wanted.add((link.left_table, link.left_col))
            wanted.add((link.right_table, link.right_col))
        elif link.right_table == col.table:
            wanted.add((link.right_table, link.right_col))
            wanted.add((link.left_table, link.left_col))
    return [other for other in columns if (other.table, other.name) in wanted]


def _select(
    dialect: str,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
    hints: Sequence[HintFact],
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> tuple[list[ColumnFact], list[JoinFact], list[HintFact], list[MeasureFact]]:
    """Reserve measures, then hints, then pack columns into what is left.

    ponytail: greedy pack, O(n^2) over columns. Ceiling: a few hundred
    columns. Upgrade: a fixed token budget per dataset with a vector index.
    """
    reserved = _measures_that_fit(dialect, measures, max_tokens)
    kept_hints = _hints_that_fit(dialect, hints, reserved, max_tokens)
    ordered = sorted(columns, key=lambda item: (-item.score, item.table, item.name))
    pending: deque[ColumnFact] = deque(ordered)
    queued = {(col.table, col.name) for col in ordered}
    chosen: list[ColumnFact] = []
    chosen_ids: set[tuple[str, str]] = set()
    while pending:
        col = pending.popleft()
        key = (col.table, col.name)
        queued.discard(key)
        if key in chosen_ids:
            continue
        trial = [*chosen, col]
        trial_joins = _joins_for(trial, joins)
        if not _fits(dialect, trial, trial_joins, kept_hints, reserved, max_tokens):
            continue
        chosen = trial
        chosen_ids.add(key)
        for extra in _related(col, columns, joins):
            extra_key = (extra.table, extra.name)
            if extra_key in chosen_ids or extra_key in queued:
                continue
            queued.add(extra_key)
            pending.appendleft(extra)
    return chosen, _joins_for(chosen, joins), kept_hints, reserved


def _measure_facts(
    payload: Mapping[str, Any],
    question_vec: Mapping[str, int],
    descriptions: Mapping[str, str],
) -> list[MeasureFact]:
    out: list[MeasureFact] = []
    for item in payload.get("measures") or []:
        grain = str(item["grain"])
        name = str(item["name"])
        description = descriptions.get(name, "")
        document = " ".join(
            (
                name,
                grain,
                description,
                str(item["expression"]),
                " ".join(item.get("aliases") or ()),
            )
        )
        out.append(
            MeasureFact(
                name=name,
                grain=grain,
                expression=str(item["expression"]),
                description=description,
                aliases=tuple(item.get("aliases") or ()),
                score=_cosine(question_vec, _trigrams(document)),
            )
        )
    return out


def _grain_ok(grain: str, object_tables: Mapping[str, str], datasets: set[str]) -> bool:
    if not datasets:
        return True
    if grain in datasets:
        return True
    table = object_tables.get(grain)
    return bool(table and table in datasets)


def _declared_columns(datasets: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for dataset in datasets:
        table = _text(dataset.get("name"))
        out[table] = {_text(col.get("name")) for col in _column_items(dataset)}
        out[table].discard("")
    return out


def _fallback_prompt(dialect: str) -> str:
    if dialect:
        return f"DIALECT: {dialect}\nSCHEMA"
    return "SCHEMA"


def build_schema_context(
    question: str,
    schema: Mapping[str, Any],
    *,
    ontology: Ontology | Mapping[str, Any] | None = None,
    grantable: set[str] | None = None,
    space_id: str | None = None,
    max_tokens: int = MAX_PROMPT_TOKENS,
) -> SchemaContext:
    """Rank the connector description and render a prompt inside ``max_tokens``."""
    described = schema if isinstance(schema, Mapping) else {}
    dialect = _dialect(described.get("dialect"))
    question_vec = _trigrams(question or "")
    spans = _spans(question or "")
    datasets = [
        item
        for item in _dataset_items(described)
        if _text(item.get("name")) and _granted(_text(item.get("name")), grantable)
    ]
    names = {_text(item.get("name")) for item in datasets}
    declared = _declared_columns(datasets)
    onto = ontology_payload(ontology)
    object_tables: dict[str, str] = {}
    if isinstance(ontology, Ontology):
        for obj in ontology.objects.values():
            table = _physical_name(obj.relation)
            if table:
                object_tables[obj.name] = table
    rel_items = [
        item
        for item in _relationship_items(described.get("relationships"))
        if item["from"] in names and item["to"] in names
        and item["from_column"] in declared.get(item["from"], set())
        and item["to_column"] in declared.get(item["to"], set())
    ]
    for item in onto["relationships"]:
        if (
            item["from"] in names
            and item["to"] in names
            and item["from_column"] in declared.get(item["from"], set())
            and item["to_column"] in declared.get(item["to"], set())
        ):
            rel_items.append(item)
    joins = _join_facts(rel_items)
    fk_of: dict[tuple[str, str], str] = {}
    for link in joins:
        fk_of.setdefault((link.left_table, link.left_col), f"{link.right_table}.{link.right_col}")

    slots: list[tuple[str, str]] = []
    slot_meta: list[tuple[str, str]] = []
    prepared: list[dict[str, Any]] = []
    index: dict[str, tuple[str, ...]] = {}
    for dataset in datasets:
        table = _text(dataset.get("name"))
        table_desc = _text(dataset.get("description"))
        if table_desc:
            slots.append((f"{table}.__table__", table_desc))
            slot_meta.append(("table", table))
        for column in _column_items(dataset):
            name = _text(column.get("name"))
            if not name:
                continue
            family = _type_family(_text(column.get("type")))
            tagged = _tagged(column)
            values = _index_values(column, family)
            if values:
                index[f"{table}.{name}"] = tuple(values)
            samples = _sample_values(column, family, tagged)
            hints = _hint_values(spans, values, tagged=tagged)
            description = _text(column.get("description"))
            prepared.append(
                {
                    "table": table,
                    "name": name,
                    "family": family,
                    "type": _text(column.get("type")),
                    "primary_key": bool(column.get("primary_key")),
                    "description": description,
                    "samples": samples,
                    "hints": hints,
                    "distinct": _distinct_count(column, values or samples),
                    "table_desc": table_desc,
                }
            )
            if description:
                slots.append((f"{table}.{name}", description))
                slot_meta.append(("column", f"{table}.{name}"))
            for sample in samples:
                slots.append((f"{table}.{name}", sample))
                slot_meta.append(("sample", f"{table}.{name}"))
            for value, fuzzy in hints:
                slots.append((f"{table}.{name}", value))
                slot_meta.append(("hint", f"{table}.{name}|{int(fuzzy)}|{value}"))
    measure_items = [
        item
        for item in (onto.get("measures") or [])
        if _grain_ok(str(item["grain"]), object_tables, names)
    ]
    for item in measure_items:
        description = _text(item.get("description"))
        if description:
            slots.append((str(item["name"]), description))
            slot_meta.append(("measure", str(item["name"])))

    masked = _mask_slots(slots)
    table_desc_out: dict[str, str] = {}
    column_desc_out: dict[str, str] = {}
    sample_groups: dict[str, list[tuple[str, str]]] = {}
    hint_rows: list[tuple[str, str, bool, str]] = []
    measure_desc_out: dict[str, str] = {}
    if masked is not None:
        for (kind, ref), got, (_field, raw) in zip(slot_meta, masked, slots, strict=True):
            if kind == "table" and got.strip():
                table_desc_out[ref] = " ".join(got.split())
            elif kind == "column" and got.strip():
                column_desc_out[ref] = " ".join(got.split())
            elif kind == "sample":
                sample_groups.setdefault(ref, []).append((raw, got))
            elif kind == "hint":
                col_key, flag, value = ref.split("|", 2)
                hint_rows.append((col_key, value, flag == "1", got))
            elif kind == "measure" and got.strip():
                measure_desc_out[ref] = " ".join(got.split())
    sample_out: dict[str, tuple[str, ...]] = {}
    for ref, pairs in sample_groups.items():
        if any(not _cleared(raw, got) for raw, got in pairs):
            continue
        sample_out[ref] = tuple(got for _raw, got in pairs)
    hint_out: list[HintFact] = []
    for ref, value, fuzzy, got in hint_rows:
        if not _cleared(value, got):
            continue
        hint_table, hint_column = ref.split(".", 1)
        hint_out.append(HintFact(hint_table, hint_column, value, fuzzy))

    _remember_index(space_id, index)
    facts: list[ColumnFact] = []
    for item in prepared:
        table = str(item["table"])
        name = str(item["name"])
        key = f"{table}.{name}"
        description = column_desc_out.get(key, "") if masked is not None else ""
        kept_samples = sample_out.get(key, ()) if masked is not None else ()
        document = " ".join(
            (table, name, str(item["type"]), description, " ".join(kept_samples))
        )
        facts.append(
            ColumnFact(
                table=table,
                name=name,
                data_type=str(item["type"]),
                primary_key=bool(item["primary_key"]),
                foreign_key=fk_of.get((table, name), ""),
                description=description,
                samples=kept_samples,
                distinct=item["distinct"] if isinstance(item["distinct"], int) else None,
                score=_cosine(question_vec, _trigrams(document)),
            )
        )
    if masked is None:
        hint_out = []
        measure_desc_out = {}
        table_desc_out = {}
    measures = _measure_facts(
        {"measures": measure_items},
        question_vec,
        measure_desc_out,
    )
    chosen, kept_joins, kept_hints, kept_measures = _select(
        dialect, facts, joins, hint_out, measures, max_tokens
    )
    prompt = _render(
        dialect,
        chosen,
        kept_joins,
        kept_hints,
        kept_measures,
        table_desc_out,
    )
    if estimate_tokens(prompt) > max_tokens:
        prompt = _fallback_prompt(dialect)
    return SchemaContext(
        prompt=prompt,
        samples_included=any(col.samples for col in chosen),
        dialect=dialect,
    )


def _constraint_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in _name_list(value.strip("[]{}").split(",")) if _ident(part)]
    if isinstance(value, Sequence):
        return [part for part in _name_list(value) if _ident(part)]
    return []


def _count_distinct(con: Any, table: str, column: str) -> int | None:
    try:
        row = con.execute(
            f"SELECT COUNT(DISTINCT {_quote(column)}) FROM {_quote(table)}"
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row or row[0] is None:
        return None
    return int(row[0])


def _distinct_text(con: Any, table: str, column: str, *, limit: int | None) -> list[str]:
    # ponytail: an untagged text column loads every distinct value into the
    # Space index so an exact hint can match. Ceiling: memory grows with
    # distinct text. Upgrade: a capped in-memory index that still answers
    # equality and reports the true distinct count.
    tail = "" if limit is None else f" LIMIT {int(limit)}"
    try:
        rows = con.execute(
            f"SELECT DISTINCT CAST({_quote(column)} AS VARCHAR) FROM {_quote(table)} "
            f"WHERE {_quote(column)} IS NOT NULL ORDER BY 1{tail}"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [
        " ".join(str(row[0]).split())
        for row in rows
        if row and row[0] is not None and str(row[0]).strip()
    ]


def schema_from_serving(
    serving: Path | None,
    grantable: set[str] | None,
    *,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Reflect granted datasets from a serving file. No dataset name is fixed.

    ``dialect`` is copied when the caller already has it. This reader does
    not invent one. Text values stay in ``values`` for the local index.
    Only numeric, date, and boolean columns get ``samples``.
    """
    named = _dialect(dialect)
    body: dict[str, Any] = {"datasets": [], "relationships": []}
    if named:
        body["dialect"] = named
    if serving is None or not Path(serving).is_file():
        return body
    import duckdb

    try:
        con = duckdb.connect(str(serving), read_only=True)
    except Exception:  # noqa: BLE001 -- empty description, ask continues
        return body
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'main' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
        keys: dict[str, set[str]] = {}
        try:
            constraints = con.execute(
                "SELECT table_name, constraint_type, constraint_column_names "
                "FROM duckdb_constraints() WHERE schema_name = 'main'"
            ).fetchall()
        except Exception:  # noqa: BLE001 -- types still ship without key metadata
            constraints = []
        for table_name, kind, cols in constraints:
            if _text(kind).upper() != "PRIMARY KEY":
                continue
            table = str(table_name)
            keys.setdefault(table, set()).update(_constraint_names(cols))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for table_name, column_name, data_type in rows:
            table = str(table_name)
            column = str(column_name)
            if not _ident(table) or not _ident(column):
                continue
            if not _granted(table, grantable):
                continue
            family = _type_family(str(data_type or ""))
            distinct = _count_distinct(con, table, column)
            if family == "text":
                values = _distinct_text(con, table, column, limit=None)
                samples: list[str] = []
            else:
                values = []
                samples = _distinct_text(con, table, column, limit=_SAMPLE_LIMIT)
            grouped.setdefault(table, []).append(
                {
                    "name": column,
                    "type": str(data_type or ""),
                    "description": "",
                    "primary_key": column in keys.get(table, set()),
                    "distinct": distinct,
                    "values": values,
                    "samples": samples,
                }
            )
        body["datasets"] = [
            {"name": table, "description": "", "columns": cols}
            for table, cols in sorted(grouped.items())
        ]
    except Exception:  # noqa: BLE001
        return body
    finally:
        con.close()
    return body


def prepare_generate_context(
    ctx: dict[str, Any],
    *,
    question: str,
    schema: Mapping[str, Any] | None = None,
    serving: Path | None = None,
    grantable: set[str] | None = None,
    ontology: Ontology | Mapping[str, Any] | None = None,
    dialect: str | None = None,
    space_id: str | None = None,
    max_tokens: int = MAX_PROMPT_TOKENS,
) -> dict[str, Any]:
    """Attach the prompt when the flag is on. Do nothing when it is off.

    A connector ``schema`` wins. Otherwise the granted serving file is
    reflected. A build error leaves ``ctx`` unchanged. The ask continues.
    The prompt is returned on ``ctx`` only. Nothing is written to disk.
    """
    if not schema_context_enabled():
        return ctx
    try:
        described = schema
        if described is None:
            described = schema_from_serving(serving, grantable, dialect=dialect)
        elif dialect and isinstance(described, Mapping) and not _text(described.get("dialect")):
            described = dict(described)
            described["dialect"] = dialect
        built = build_schema_context(
            question,
            described if isinstance(described, Mapping) else {},
            ontology=ontology,
            grantable=grantable,
            space_id=space_id,
            max_tokens=max_tokens,
        )
    except Exception:  # noqa: BLE001 -- schema context must not sink the ask
        return ctx
    if not built.prompt:
        return ctx
    out = dict(ctx)
    out[SCHEMA_CONTEXT_FIELD] = built.prompt
    return out
