"""SCHEMA-RETRIEVE: schema context for the SQL the model writes.

The input is a source description any connector can supply. It is not one
database. Datasets (or tables), columns, types, keys,
relationships, descriptions, samples, and the SQL dialect all come from that
description. Synonyms and extra joins come only from an ontology payload.
A curated ``Ontology`` and a later model-built mapping use one shape.
An empty ontology still yields a prompt from the connector description.

Samples are kept only for columns ``column_is_pii`` has cleared, then every
value passes through ``fail_closed_mask_payload``. A mask exception drops
every sample. The ask still runs.

Cortex pin 279cbd85 ``InsightsAskIn`` does not read this text. The wire field
is ``schema_context`` and leaves the box only when ``DMS_SCHEMA_CONTEXT`` is
on. The built prompt is always stored for audit.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex_client.insights import SCHEMA_CONTEXT_FIELD, schema_context_enabled
from dms_core.pii import column_is_pii, fail_closed_mask_payload, is_mask_token

from dms_executor.ontology import Ontology, relation_tables, table_is_granted

# ponytail: 4 characters per token. Ceiling: a model tokenizer disagrees.
# Upgrade: count with the tokenizer of the model that writes the SQL.
CHARS_PER_TOKEN = 4
MAX_PROMPT_TOKENS = 480
_SAMPLE_LIMIT = 3
_SAMPLE_CHARS = 80
_UNDECLARED_DIALECT = "undeclared"
_ROOT = Path(__file__).resolve().parents[3]
_LAST: dict[str, str] = {"prompt": "", "question": "", "space_id": ""}


def estimate_tokens(text: str) -> int:
    """Upper bound used to keep the schema prompt inside ``MAX_PROMPT_TOKENS``."""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def schema_prompt_audit_path() -> Path:
    """Where the latest prompt is written so Check can read it for raw PII."""
    override = os.environ.get("DMS_SCHEMA_PROMPT_LOG", "").strip()
    if override:
        return Path(override)
    return _ROOT / ".tmp" / "schema_prompt.txt"


def last_schema_prompt() -> str:
    """The last prompt this process stored. Empty before the first build."""
    return _LAST["prompt"]


def store_schema_prompt(
    prompt: str,
    *,
    question: str,
    space_id: str | None = None,
) -> None:
    """Remember the prompt and overwrite the audit file. A write error is ignored."""
    _LAST["prompt"] = prompt
    _LAST["question"] = question
    _LAST["space_id"] = space_id or ""
    path = schema_prompt_audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    except OSError:
        return


@dataclass(frozen=True)
class ColumnFact:
    table: str
    name: str
    data_type: str
    primary_key: bool
    foreign_key: str
    description: str
    samples: tuple[str, ...]
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
    text = _text(value)
    return text or _UNDECLARED_DIALECT


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


def _raw_samples(column: Mapping[str, Any]) -> list[str]:
    vals = _name_list(column.get("samples"))[:_SAMPLE_LIMIT]
    return [v for v in vals if v]


def _mask_samples(
    pending: dict[tuple[str, str], list[str]],
) -> dict[tuple[str, str], tuple[str, ...]]:
    """Run every sample through the served-path mask. Any failure drops all."""
    if not pending:
        return {}
    rows: list[dict[str, str]] = []
    index: list[tuple[tuple[str, str], str]] = []
    for key, vals in pending.items():
        table, column = key
        field = f"{table}.{column}"
        for val in vals:
            rows.append({field: val})
            index.append((key, val))
    try:
        masked = fail_closed_mask_payload(text="", rows=rows, values=[], sources=[])
    except Exception:  # noqa: BLE001 -- fail closed: no samples, ask continues
        return {}
    if not isinstance(masked, dict):
        return {}
    got = masked.get("rows")
    if not isinstance(got, list) or len(got) != len(index):
        return {}
    cleared: dict[tuple[str, str], list[str]] = {}
    blocked: set[tuple[str, str]] = set()
    for (key, raw), row in zip(index, got, strict=True):
        if not isinstance(row, dict):
            return {}
        table, column = key
        out = row.get(f"{table}.{column}")
        if out is None or str(out) != raw or is_mask_token(str(out)):
            blocked.add(key)
            continue
        cleared.setdefault(key, []).append(str(out))
    for key in blocked:
        cleared.pop(key, None)
    return {key: tuple(vals) for key, vals in cleared.items()}


def _collect_samples(
    datasets: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], tuple[str, ...]]:
    pending: dict[tuple[str, str], list[str]] = {}
    for dataset in datasets:
        table = _text(dataset.get("name"))
        if not table:
            continue
        for column in _column_items(dataset):
            name = _text(column.get("name"))
            if not name or column_is_pii(name, (), table=table):
                continue
            vals = _raw_samples(column)
            if not vals or column_is_pii(name, vals, table=table):
                continue
            pending[(table, name)] = vals
    return _mask_samples(pending)


def _join_facts(items: Sequence[Mapping[str, str]]) -> list[JoinFact]:
    out: list[JoinFact] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
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


def _render(
    dialect: str,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
    measures: Sequence[MeasureFact],
) -> str:
    lines = [f"DIALECT: {dialect}", "SCHEMA"]
    by_table: dict[str, list[ColumnFact]] = {}
    for col in columns:
        by_table.setdefault(col.table, []).append(col)
    for table in sorted(by_table):
        lines.append(f"- {table}")
        for col in sorted(by_table[table], key=lambda item: item.name):
            lines.append(_format_column(col))
    if joins:
        lines.append("JOINS")
        for link in joins:
            lines.append(_format_join(link))
    if measures:
        lines.append("MEASURES")
        for measure in measures:
            lines.append(_format_measure(measure))
    return "\n".join(lines)


def _joins_for(columns: Sequence[ColumnFact], joins: Sequence[JoinFact]) -> list[JoinFact]:
    tables = {col.table for col in columns}
    return [
        link
        for link in joins
        if link.left_table in tables and link.right_table in tables
    ]


def _measures_that_fit(
    dialect: str,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> list[MeasureFact]:
    chosen: list[MeasureFact] = []
    ordered = sorted(measures, key=lambda item: (-item.score, item.name))
    for measure in ordered:
        trial = [*chosen, measure]
        if estimate_tokens(_render(dialect, columns, joins, trial)) <= max_tokens:
            chosen = trial
    return chosen


def _fits(
    dialect: str,
    columns: Sequence[ColumnFact],
    joins: Sequence[JoinFact],
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> bool:
    kept = _measures_that_fit(dialect, columns, joins, measures, max_tokens)
    rendered = _render(dialect, columns, _joins_for(columns, joins), kept)
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
    measures: Sequence[MeasureFact],
    max_tokens: int,
) -> tuple[list[ColumnFact], list[JoinFact], list[MeasureFact]]:
    """ponytail: greedy pack, O(n^2) over columns. Ceiling: a few hundred
    columns. Upgrade: a fixed token budget per dataset with a vector index.
    """
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
        if not _fits(dialect, trial, trial_joins, measures, max_tokens):
            continue
        chosen = trial
        chosen_ids.add(key)
        for extra in _related(col, columns, joins):
            extra_key = (extra.table, extra.name)
            if extra_key in chosen_ids or extra_key in queued:
                continue
            queued.add(extra_key)
            pending.appendleft(extra)
    kept_joins = _joins_for(chosen, joins)
    kept_measures = _measures_that_fit(dialect, chosen, kept_joins, measures, max_tokens)
    return chosen, kept_joins, kept_measures


def _measure_facts(
    payload: Mapping[str, Any],
    question_vec: Mapping[str, int],
) -> list[MeasureFact]:
    out: list[MeasureFact] = []
    for item in payload.get("measures") or []:
        grain = str(item["grain"])
        document = " ".join(
            (
                str(item["name"]),
                grain,
                str(item.get("description") or ""),
                str(item["expression"]),
                " ".join(item.get("aliases") or ()),
            )
        )
        out.append(
            MeasureFact(
                name=str(item["name"]),
                grain=grain,
                expression=str(item["expression"]),
                description=str(item.get("description") or ""),
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


def build_schema_context(
    question: str,
    schema: Mapping[str, Any],
    *,
    ontology: Ontology | Mapping[str, Any] | None = None,
    grantable: set[str] | None = None,
    max_tokens: int = MAX_PROMPT_TOKENS,
) -> SchemaContext:
    """Rank the connector description and render a prompt inside ``max_tokens``."""
    dialect = _dialect(schema.get("dialect") if isinstance(schema, Mapping) else None)
    question_vec = _trigrams(question or "")
    datasets = [
        item
        for item in _dataset_items(schema if isinstance(schema, Mapping) else {})
        if _text(item.get("name")) and _granted(_text(item.get("name")), grantable)
    ]
    names = {_text(item.get("name")) for item in datasets}
    onto = ontology_payload(ontology)
    object_tables: dict[str, str] = {}
    if isinstance(ontology, Ontology):
        for obj in ontology.objects.values():
            table = _physical_name(obj.relation)
            if table:
                object_tables[obj.name] = table
    rel_items = [
        item
        for item in _relationship_items(
            schema.get("relationships") if isinstance(schema, Mapping) else None
        )
        if item["from"] in names and item["to"] in names
    ]
    for item in onto["relationships"]:
        if item["from"] in names and item["to"] in names:
            rel_items.append(item)
    joins = _join_facts(rel_items)
    fk_of: dict[tuple[str, str], str] = {}
    for link in joins:
        fk_of.setdefault((link.left_table, link.left_col), f"{link.right_table}.{link.right_col}")
    samples = _collect_samples(datasets)
    facts: list[ColumnFact] = []
    for dataset in datasets:
        table = _text(dataset.get("name"))
        table_desc = _text(dataset.get("description"))
        for column in _column_items(dataset):
            name = _text(column.get("name"))
            if not name:
                continue
            description = _text(column.get("description")) or table_desc
            sample = samples.get((table, name), ())
            document = " ".join(
                (table, name, _text(column.get("type")), description, " ".join(sample))
            )
            facts.append(
                ColumnFact(
                    table=table,
                    name=name,
                    data_type=_text(column.get("type")),
                    primary_key=bool(column.get("primary_key")),
                    foreign_key=fk_of.get((table, name), ""),
                    description=description,
                    samples=sample,
                    score=_cosine(question_vec, _trigrams(document)),
                )
            )
    measures = [
        fact
        for fact in _measure_facts(onto, question_vec)
        if _grain_ok(fact.grain, object_tables, names)
    ]
    chosen, kept_joins, kept_measures = _select(dialect, facts, joins, measures, max_tokens)
    prompt = _render(dialect, chosen, kept_joins, kept_measures)
    if estimate_tokens(prompt) > max_tokens:
        prompt = f"DIALECT: {dialect}\nSCHEMA"
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


def schema_from_serving(
    serving: Path | None,
    grantable: set[str] | None,
    *,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Reflect granted datasets from a serving file. No dataset name is fixed.

    The dialect is whatever the caller already knows. This reader does not
    invent one. Samples are distinct values; the builder still masks them.
    """
    body: dict[str, Any] = {
        "dialect": _text(dialect),
        "datasets": [],
        "relationships": [],
    }
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
            samples: list[str] = []
            if not column_is_pii(column, (), table=table):
                try:
                    fetched = con.execute(
                        f"SELECT DISTINCT CAST({_quote(column)} AS VARCHAR) "
                        f"FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL "
                        f"ORDER BY 1 LIMIT {_SAMPLE_LIMIT}"
                    ).fetchall()
                except Exception:  # noqa: BLE001
                    fetched = []
                samples = [
                    " ".join(str(row[0]).split())
                    for row in fetched
                    if row and row[0] is not None and str(row[0]).strip()
                ]
            grouped.setdefault(table, []).append(
                {
                    "name": column,
                    "type": str(data_type or ""),
                    "description": "",
                    "primary_key": column in keys.get(table, set()),
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
    """Store the prompt. Attach it for the wire only when the flag is on.

    A connector ``schema`` wins. Otherwise the granted serving file is
    reflected. A build error leaves ``ctx`` unchanged. The ask continues.
    """
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
            grantable=grantable if schema is None else grantable,
            max_tokens=max_tokens,
        )
    except Exception:  # noqa: BLE001 -- schema context must not sink the ask
        return ctx
    store_schema_prompt(built.prompt, question=question, space_id=space_id)
    if not schema_context_enabled() or not built.prompt:
        return ctx
    out = dict(ctx)
    out[SCHEMA_CONTEXT_FIELD] = built.prompt
    return out
