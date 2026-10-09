"""SCHEMA-RETRIEVE: schema context for the SQL the model writes.

The input is a source description any connector can supply. It is not one
database. Datasets (or tables), columns, types, keys,
relationships, descriptions, samples, and the SQL dialect all come from that
description. Synonyms and extra joins come only from an ontology payload.
A curated ``Ontology`` and a later model-built mapping use one shape.
An empty ontology still yields a prompt from the connector description.

Text columns send no sample values unless the ontology payload marks the
column ``non_personal`` with ``source`` and ``field``. A tag on the column
dict is ignored. This module never sets that tag. Numeric, date, and
boolean columns may send samples. Descriptions are masked against the
value index before ``fail_closed_mask_payload``. Every sample,
description, and filter hint still passes that call. If it fails, the
prompt goes out with no samples, no hints, and no descriptions. The ask
still runs.

Untagged hints come from the question's word n-grams looked up exactly,
after case, whitespace, and punctuation are folded, in that column's
granted values. Only the longest matching span is kept. If that span is a
strict prefix or substring of any other granted value in the column, no
hint is sent. The model prompt shows the question's own characters for
that span, not the stored casing. A model span is never consulted.
A hint is emitted only from a column that is positively cleared: an
ontology ``non_personal`` tag with source and field, or a column name the
mask's own metric skip treats as non-personal. An unknown column such as
``col1`` is not cleared, so it sends no hint even when the value probe
would leave the cell raw. Value hints are looked up on the masker's
output for the question, never on the raw question. That output is
``fail_closed_mask_payload``. Only a column the classifier marks as a
person-name column contributes values to that mask. A cleared non-person
column stays typed and may hint, including when the question has words
around the stored value. A column the classifier has not decided is
unsure: its values stay typed and are not hints. On a cleared column the value probe is the
second layer. It flags a cell the detector already flags, including a
repeated Title-Case word. It does not re-title a one-word or hyphenated
code, because that drop removes a cleared metric code. If any check
flags, or the detector raises, nothing is sent. Doubt or a mask failure
sends nothing and the ask continues. Fuzzy hints stay on a tagged
column; that tag does not re-title a multi-word phrase into a name.
Fuzzy or partial hints exist only for a tagged column, and those
candidates are capped. Hints and the columns they cite
take token budget before measures. The value index is built in the
background when a source connects or its catalog fingerprint changes,
and kept in memory per Space. The file next to the serving store records
status and catalog shape only, with no cell values. The ask path only
looks the in-memory index up. A missing or failed index still answers,
with catalog names and types and a named stamp. The model prompt is per
request. Each picked table, column, and join carries a short reason.
The customer envelope gets the same text with each hint value replaced
by a token taken from the hint list. When the flag is off this module
is not called. The background build stops when the process shuts down.

Cortex pin 279cbd85 ``InsightsAskIn`` does not read this text. The wire field
is ``schema_context`` and leaves the box only when ``DMS_SCHEMA_CONTEXT`` is
on.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cortex_client.insights import SCHEMA_CONTEXT_FIELD, schema_context_enabled
from dms_core.pii import (
    _ACCOUNT_COL,
    _ADDRESS_COL,
    _DOB_COL,
    _EMAIL_COL,
    _METRIC_SKIP,
    _NAME_COL,
    _NRIC_COL,
    _PERSON_TABLE,
    _PHONE_COL,
    NAME_MASK_KEY,
    classify_column,
    fail_closed_mask_payload,
    is_mask_token,
)

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
# ponytail: a column over the cap is omitted whole, not truncated, so a
# missing longer value cannot unlock a short hint. Ceiling: a wide text
# column sends no hint. Upgrade: an equality index that still knows the
# longest value without holding every string.
INDEX_COLUMN_CAP = 500
INDEX_TOTAL_CAP = 5000
# Rows pulled per column, and across the whole build. LIMIT, never a full scan.
SAMPLE_ROWS = 20
INDEX_ROW_BUDGET = 8000
# Background build ceiling. The ask path does not wait on this.
INDEX_BUILD_BUDGET_S = 20.0
# Columns considered when packing a prompt. Cited hint columns are kept first.
_SHORTLIST = 48
_SPAN_WIDTH = 6
_PARTIAL_MIN_CHARS = 3
_LOG = logging.getLogger("dms_executor.schema_context")
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
_SPACE_SAMPLES: dict[str, dict[str, tuple[str, ...]]] = {}
_SPACE_FP: dict[str, str] = {}
_STOP = threading.Event()
_WORKER_LOCK = threading.Lock()
_WORKERS: list[threading.Thread] = []


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


def _remember_built(
    space_id: str,
    fingerprint: str,
    index: dict[str, tuple[str, ...]],
    samples: dict[str, tuple[str, ...]],
) -> None:
    snap = {key: tuple(vals) for key, vals in index.items()}
    held = {key: tuple(vals) for key, vals in samples.items()}
    with _INDEX_LOCK:
        _SPACE_INDEX[space_id] = snap
        _SPACE_SAMPLES[space_id] = held
        _SPACE_FP[space_id] = fingerprint


def _forget_index(space_id: str) -> None:
    with _INDEX_LOCK:
        _SPACE_INDEX.pop(space_id, None)
        _SPACE_SAMPLES.pop(space_id, None)
        _SPACE_FP.pop(space_id, None)


def _space_fingerprint(space_id: str) -> str:
    with _INDEX_LOCK:
        return _SPACE_FP.get(space_id, "")


def _space_samples(space_id: str) -> dict[str, tuple[str, ...]]:
    with _INDEX_LOCK:
        found = _SPACE_SAMPLES.get(space_id)
        if not found:
            return {}
        return {key: tuple(vals) for key, vals in found.items()}


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
    reason: str = ""


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
    reason: str = ""


@dataclass(frozen=True)
class HintFact:
    table: str
    column: str
    value: str
    fuzzy: bool
    span_id: str = ""


@dataclass(frozen=True)
class SchemaContext:
    prompt: str
    samples_included: bool
    dialect: str
    envelope_prompt: str
    # Evidence for the one masker. Not a prompt and not a wire field.
    name_mask: dict[str, list[str]] | None = None


# Popped off the compute catalog before Insights sees it. Not a wire field.
SCHEMA_CONTEXT_ENVELOPE_KEY = "_schema_context_envelope"
# Popped before Insights. Copied onto the envelope as ``index_stamp``.
SCHEMA_INDEX_STAMP_KEY = "_schema_index_stamp"


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


def _ontology_tags(ontology: Ontology | Mapping[str, Any] | None) -> set[tuple[str, str]]:
    """Columns the ontology payload marks non_personal, with source and field.

    A ``non_personal`` entry on a column dict is ignored. Evidence is a
    non-empty ``source`` and a non-empty ``field``, plus the table and column.
    """
    raw: Any = None
    if isinstance(ontology, Mapping):
        raw = ontology.get("non_personal")
    elif ontology is not None:
        raw = getattr(ontology, "non_personal", None)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return set()
    found: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        table = _text(item.get("table"))
        column = _text(item.get("column"))
        source = item.get("source")
        field = item.get("field")
        if not table or not column:
            continue
        if not isinstance(source, str) or not source.strip():
            continue
        if not isinstance(field, str) or not field.strip():
            continue
        found.add((table, column))
    return found


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


def _question_spans(question: str) -> list[tuple[int, str, str, int]]:
    """Word n-grams as ``(width, folded, original slice, word index)``.

    The original slice is the question's own characters, punctuation
    included. Folding is only for the lookup.
    """
    text = question or ""
    parts: list[tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not text[i].isspace():
            i += 1
        folded = _normalize(text[start:i])
        if folded:
            parts.append((folded, start, i))
    out: list[tuple[int, str, str, int]] = []
    width_max = min(_SPAN_WIDTH, len(parts))
    for width in range(1, width_max + 1):
        for start_i in range(len(parts) - width + 1):
            chunk = parts[start_i : start_i + width]
            folded = " ".join(item[0] for item in chunk)
            original = text[chunk[0][1] : chunk[-1][2]]
            if folded:
                out.append((width, folded, original, start_i))
    return out


def _hint_blocked(folded: str, others: Sequence[str]) -> bool:
    """True when ``folded`` is a strict prefix or substring of another value."""
    return any(folded != other and folded in other for other in others)


def _column_positively_cleared(table: str, column: str, *, tagged: bool) -> bool:
    """True only when the column itself has been cleared, not merely unflagged.

    An ontology ``non_personal`` tag with source and field is one clear.
    The other is the mask's metric-skip name, its own non-personal pattern.
    ``classify_column`` returns None for both that name and an unknown
    column, so None is not a clear. A
    birth, contact, or name pattern is never a clear. Doubt sends nothing.
    """
    if tagged:
        return True
    col = str(column or "").strip()
    if not col:
        return False
    try:
        if _DOB_COL.search(col):
            return False
        if _METRIC_SKIP.search(col) is None:
            return False
        if (
            _EMAIL_COL.search(col)
            or _NRIC_COL.search(col)
            or _PHONE_COL.search(col)
            or _ACCOUNT_COL.search(col)
            or _ADDRESS_COL.search(col)
            or _NAME_COL.search(col)
        ):
            return False
        if _PERSON_TABLE.search(str(table or "")) and re.fullmatch(
            "name", col, re.I
        ):
            return False
        return True
    except Exception:  # noqa: BLE001 -- doubt: the column is not cleared
        return False


def _mask_would_flag(value: str, *, variants: bool) -> bool:
    """True when the mask would not leave ``value`` raw.

    Second layer, after :func:`_column_positively_cleared`. A repeated
    copy catches a Title-Case word the detector needs two words to see.
    The cell is not re-titled and separators are not folded into spaces:
    that drop is what removed a cleared metric code. ``variants`` is
    kept so a tagged phrase and an untagged code share this probe; the
    column gate is what closes an unknown column. An exception is doubt.
    """
    del variants
    text = " ".join(str(value).split())
    if not text or is_mask_token(text):
        return False
    try:
        if classify_column("note", [text]) is not None:
            return True
        return classify_column("note", [f"{text} {text}"]) is not None
    except Exception:  # noqa: BLE001 -- doubt: send no hint
        return True


def _cleared_value_blocked(value: str) -> bool:
    """Hint probe. A multi-word cleared value is not dropped for Title-Case.

    Storage still uses :func:`_mask_would_flag`, so a Title-Case name does
    not enter the index. The column classifier already cleared this column.
    A single Title-Case token still uses that probe.
    """
    text = " ".join(str(value).split())
    if " " in text:
        try:
            return classify_column("category", [text]) is not None
        except Exception:  # noqa: BLE001 -- doubt: send no hint
            return True
    return _mask_would_flag(text, variants=True)


def _hint_column_blocked(
    table: str,
    column: str,
    values: Sequence[str],
    description: str,
    *,
    tagged: bool,
) -> bool:
    """True when this column must not contribute a filter hint."""
    if not _column_positively_cleared(table, column, tagged=tagged):
        return True
    try:
        if classify_column(column, list(values), table=table) is not None:
            return True
        if description and classify_column(description, (), table=table) is not None:
            return True
    except Exception:  # noqa: BLE001 -- doubt: send no hint
        return True
    # A non_personal tag does not re-title a multi-word phrase.
    return any(_cleared_value_blocked(value) for value in values)


def _hint_value_cleared(
    table: str,
    column: str,
    emitted: str,
    stored: str,
    *,
    variants: bool,
) -> bool:
    """True when the stored cell clears the mask, including casing variants.

    The emitted span is the question's own slice of that cell. It is checked
    as typed so a lowercase rendering of an all-caps code is not turned into
    a repeated Title-Case pair.
    """
    del variants
    if _cleared_value_blocked(stored) or _cleared_value_blocked(emitted):
        return False
    key = f"{table}.{column}"
    try:
        masked = fail_closed_mask_payload(
            text=emitted,
            rows=[{key: stored}],
            values=[emitted],
        )
    except Exception:  # noqa: BLE001 -- doubt: send no hint
        return False
    if not isinstance(masked, dict) or masked.get("text") != emitted:
        return False
    if is_mask_token(emitted):
        return False
    rows = masked.get("rows")
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or rows[0].get(key) != stored
        or is_mask_token(str(rows[0].get(key)))
    ):
        return False
    vals = masked.get("values")
    return isinstance(vals, list) and len(vals) == 1 and vals[0] == emitted


def _question_words(question: str) -> list[str]:
    return (question or "").split()


def _word_fold(word: str) -> str:
    return "".join(ch.lower() for ch in word if ch.isalnum())


def _pure_letters(word: str) -> str:
    """Letters when the token is a name-shaped word. A digit or hyphen is not."""
    letters: list[str] = []
    for ch in word:
        if ch.isalpha():
            letters.append(ch)
        elif ch.isdigit() or ch in "-_'’":
            return ""
    return "".join(letters)


def _letter_case(word: str) -> str:
    letters = _pure_letters(word)
    if not letters:
        return ""
    if letters.isupper():
        return "upper"
    if letters.islower():
        return "lower"
    return "mixed"


def _phrase_key(value: str) -> tuple[str, ...]:
    return tuple(part for part in (_word_fold(raw) for raw in str(value).split()) if part)


def _starts_phrase(words: Sequence[str], index: int, phrases: set[tuple[str, ...]]) -> bool:
    got: list[str] = []
    last = min(len(words), index + 4)
    for pos in range(index, last):
        fold = _word_fold(words[pos])
        if not fold:
            break
        got.append(fold)
        if tuple(got) in phrases:
            return True
    return False


def _short_same(word: str, case: str) -> bool:
    letters = _pure_letters(word)
    return bool(letters) and len(letters) <= 3 and _letter_case(word) == case


def _extend_right_short(
    words: Sequence[str],
    end: int,
    case: str,
    granted: set[str],
    schema: set[str],
    phrases: set[tuple[str, ...]],
) -> int:
    """Trailing short tokens of a multi-word name. A separator before a value stops."""
    pos = end
    while pos < len(words):
        word = words[pos]
        if not _short_same(word, case):
            break
        fold = _word_fold(word)
        if fold in granted or fold in schema:
            break
        if _starts_phrase(words, pos + 1, phrases):
            break
        if pos + 1 < len(words) and not _short_same(words[pos + 1], case):
            break
        pos += 1
    return pos


def _extend_left_short(
    words: Sequence[str],
    start: int,
    case: str,
    granted: set[str],
    schema: set[str],
) -> int:
    pos = start
    while pos > 0 and _short_same(words[pos - 1], case):
        fold = _word_fold(words[pos - 1])
        if fold in granted or fold in schema:
            break
        pos -= 1
    return pos


def _span_is_fragment(
    words: Sequence[str],
    start: int,
    width: int,
    granted: set[str],
    schema: set[str],
    phrases: set[tuple[str, ...]],
) -> bool:
    """True when the match is a piece of a multi-word span whose other words match nothing.

    A cleared value hints only when it covers the whole span. A short token
    inside a longer multi-word span does not. A leading frame word is not
    that span, and a short separator before another stored value is not either.
    """
    if width < 1 or start < 0 or start + width > len(words):
        return False
    # A full stored value from a cleared non-person column is not a name
    # fragment. ``parts`` inside ``spare parts`` still hints when ``PARTS``
    # is that value. Neighboring words do not take it away.
    matched = tuple(_word_fold(words[start + i]) for i in range(width))
    if matched in phrases:
        return False
    case = _letter_case(words[start])
    if not case:
        return False
    end = start + width
    right = _extend_right_short(words, end, case, granted, schema, phrases)
    if right > end:
        return True
    left = _extend_left_short(words, start, case, granted, schema)
    # Only a run of short tokens is a multi-part name (``ali bin abu``).
    # A short separator in front of a longer value is not.
    if right - left >= 3 and left < start and all(
        0 < len(_pure_letters(words[pos])) <= 3 for pos in range(left, right)
    ):
        return True
    if width != 1 or not case:
        return False
    match_len = len(_pure_letters(words[start]))
    pos = start
    # A longer same-case neighbor is part of the span. The first word of the
    # question is the frame, not that neighbor. A short word in front of that
    # neighbor (``how many east``) is a frame too when the value ends the ask.
    while pos > 1:
        word = words[pos - 1]
        letters = _pure_letters(word)
        if len(letters) < 4 or len(letters) < match_len or _letter_case(word) != case:
            break
        if start + width == len(words) and pos >= 2:
            before = _pure_letters(words[pos - 2])
            if 0 < len(before) <= 3:
                break
        fold = _word_fold(word)
        if fold in granted or fold in schema:
            break
        pos -= 1
    if pos < start:
        return True
    # Mixed case: a short upper token after a longer lower token that is
    # not itself a stored value. When that lower token is the second word,
    # a 1-3 letter first word is a frame (the metric question stays a hint).
    # Any later position is a name span, including a short separator in front.
    if start >= 2 and case == "upper" and match_len <= 4:
        left_word = words[start - 1]
        left_letters = _pure_letters(left_word)
        before = _pure_letters(words[start - 2])
        frame = start == 2 and 0 < len(before) <= 3
        if (
            len(left_letters) >= 4
            and len(left_letters) >= match_len
            and _letter_case(left_word) == "lower"
            and not frame
            and _word_fold(left_word) not in granted
            and _word_fold(left_word) not in schema
        ):
            return True
    return False


def _hint_values(
    spans: Sequence[tuple[int, str, str, int]],
    values: Sequence[str],
    *,
    tagged: bool,
    table: str,
    column: str,
    description: str = "",
    words: Sequence[str] = (),
    granted: set[str] | None = None,
    schema: set[str] | None = None,
    phrases: set[tuple[str, ...]] | None = None,
) -> list[tuple[str, bool, str]]:
    """Prompt text for each hint. Untagged text is the question's own slice.

    Untagged: exact fold only, longest span only, and nothing when that
    span sits inside another granted value. Tagged: capped fuzzy, stored
    text. A model span is not an input. An uncleared column sends nothing
    before the value probe runs. A value the mask would flag is not a hint.
    """
    # Column clearance is the control. A stubbed value probe cannot open it.
    if not _column_positively_cleared(table, column, tagged=tagged):
        return []
    if _hint_column_blocked(table, column, values, description, tagged=tagged):
        return []
    groups: dict[str, list[str]] = {}
    for raw in values:
        folded = _normalize(raw)
        if folded:
            groups.setdefault(folded, []).append(raw)
    token_granted = granted or set()
    token_schema = schema or set()
    token_phrases = phrases or set()
    if not tagged:
        matches: list[tuple[int, str, str, int, int]] = []
        for idx, (width, folded, original, word_at) in enumerate(spans):
            stored = groups.get(folded) or []
            if len(stored) != 1:
                continue
            matches.append((width, folded, original, idx, word_at))
        if not matches:
            return []
        longest = max(item[0] for item in matches)
        chosen: list[tuple[str, bool, str]] = []
        seen: set[str] = set()
        folds = list(groups)
        for width, folded, original, idx, word_at in matches:
            if width != longest or folded in seen:
                continue
            if _hint_blocked(folded, folds):
                continue
            if words and _span_is_fragment(
                words, word_at, width, token_granted, token_schema, token_phrases
            ):
                continue
            held = groups.get(folded) or []
            if len(held) != 1 or not _hint_value_cleared(
                table, column, original, held[0], variants=not tagged
            ):
                continue
            seen.add(folded)
            chosen.append((original, False, f"s{idx}"))
        return chosen
    chosen = []
    seen = set()
    for idx, (width, span, _original, word_at) in enumerate(spans):
        if words and _span_is_fragment(
            words, word_at, width, token_granted, token_schema, token_phrases
        ):
            continue
        fuzzy_matches: list[tuple[str, bool]] = []
        for folded, originals in groups.items():
            exact = folded == span
            partial = (
                not exact
                and len(span) >= _PARTIAL_MIN_CHARS
                and (_word_hit(folded, span) or _word_hit(span, folded))
            )
            if not exact and not partial:
                continue
            for value in originals:
                fuzzy_matches.append((value, not exact))
        fuzzy_matches.sort(key=lambda item: (_normalize(item[0]), item[0]))
        for value, fuzzy in fuzzy_matches[:HINT_CANDIDATE_CAP]:
            if value in seen:
                continue
            if not _hint_value_cleared(table, column, value, value, variants=not tagged):
                continue
            seen.add(value)
            chosen.append((value, fuzzy, f"s{idx}"))
    return chosen


def _envelope_hint(index: int) -> str:
    """Token for hint ``index`` (1-based). Built from the list, not the masker."""
    return f"DMSHINT_{index:02d}"


def _limit_index(
    rows: Sequence[tuple[str, tuple[str, ...]]],
) -> tuple[dict[str, tuple[str, ...]], bool]:
    """Drop a column that would exceed the per-column or total cap.

    A partial column is not kept. Hinting from the first N values would
    miss a longer name and allow a short span.
    """
    kept: dict[str, tuple[str, ...]] = {}
    total = 0
    capped = False
    for key, values in rows:
        unique = tuple(dict.fromkeys(value for value in values if value))
        if not unique:
            continue
        if len(unique) > INDEX_COLUMN_CAP or total + len(unique) > INDEX_TOTAL_CAP:
            capped = True
            continue
        kept[key] = unique
        total += len(unique)
    return kept, capped


def _value_token_pairs(values: Sequence[str]) -> list[tuple[str, str]]:
    """Longest values first. One token per folded value, from the value list."""
    raws: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = " ".join(str(raw).split())
        if not text or text in seen:
            continue
        seen.add(text)
        raws.append(text)
    raws.sort(key=lambda item: (-len(item), item.lower()))
    by_fold: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    number = 0
    for raw in raws:
        fold = _normalize(raw)
        if not fold:
            continue
        token = by_fold.get(fold)
        if token is None:
            number += 1
            token = f"DMSVAL_{number:02d}"
            by_fold[fold] = token
        pairs.append((raw, token))
    return pairs


def _mask_known(text: str, pairs: Sequence[tuple[str, str]]) -> str:
    """Replace index values in ``text`` on word boundaries.

    A shorter known value does not match inside a longer word. Longest
    alternative is tried first.
    """
    if not text or not pairs:
        return text
    ordered = sorted((raw for raw, _token in pairs if raw), key=len, reverse=True)
    if not ordered:
        return text
    pattern = "|".join(re.escape(raw) for raw in ordered)
    lookup = {raw.lower(): token for raw, token in pairs}

    def _sub(match: re.Match[str]) -> str:
        return lookup.get(match.group(0).lower(), match.group(0))

    return re.compile(
        rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])",
        re.IGNORECASE,
    ).sub(_sub, text)


def _word_hit(haystack: str, needle: str) -> bool:
    """True when ``needle`` is a whole word or phrase inside ``haystack``."""
    if not needle or not haystack:
        return False
    return (
        re.search(rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9a-z])", haystack) is not None
    )


def _model_question(
    question: str,
    evidence: Mapping[str, Sequence[str]],
) -> tuple[str, bool]:
    """Masker output for the question. ``failed`` means no samples and no hints.

    The hinter reads this text. ``_insights_body`` calls the same masker with
    the same evidence. A masker failure is a blank question here: the ask
    still runs, with no samples.
    """
    try:
        masked = fail_closed_mask_payload(
            text=question or "",
            name_values=list(evidence.get("name_values") or ()),
            exempt_values=list(evidence.get("exempt_values") or ()),
            schema_terms=list(evidence.get("schema_terms") or ()),
        )
    except Exception:  # noqa: BLE001 -- doubt: no value hint, no samples
        return "", True
    if not isinstance(masked, dict):
        return "", True
    text = masked.get("text")
    if not isinstance(text, str) or text == "DMSMASK_unknown_00":
        return "", True
    if text and is_mask_token(text) and text.startswith("DMSMASK_unknown"):
        return "", True
    return text, False


def _fk_if_present(col: ColumnFact, chosen_ids: set[tuple[str, str]]) -> ColumnFact:
    target = col.foreign_key
    if "." not in target:
        return col
    table, _, name = target.partition(".")
    if (table, name) in chosen_ids:
        return col
    return replace(col, foreign_key="")


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
                reason=(
                    f"path:{item['from']}.{item['from_column']}"
                    f">{item['to']}.{item['to_column']}"
                ),
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
    if col.reason:
        bits.append(f"reason={col.reason}")
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
        f"{link.right_table}.{link.right_col} ({link.label}) "
        f"reason={link.reason or 'path:' + link.left_table + '.' + link.left_col}"
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
        best = max((col.score for col in by_table[table]), default=0.0)
        head = f"- {table} reason=score:{best:.2f}"
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
    """Hints and the columns they cite take budget before measures.

    With no hints, measures are still reserved before unrelated columns.

    ponytail: greedy pack, O(n^2) over columns. Ceiling: a few hundred
    columns. Upgrade: a fixed token budget per dataset with a vector index.
    """
    by_key = {(col.table, col.name): col for col in columns}
    kept_hints: list[HintFact] = []
    cited: list[ColumnFact] = []
    cited_ids: set[tuple[str, str]] = set()
    for hint in hints:
        key = (hint.table, hint.column)
        col = by_key.get(key)
        trial_hints = [*kept_hints, hint]
        if col is not None and key not in cited_ids:
            trial_cited = [*cited, col]
        else:
            trial_cited = list(cited)
        if not _fits(
            dialect,
            trial_cited,
            _joins_for(trial_cited, joins),
            trial_hints,
            [],
            max_tokens,
        ):
            continue
        kept_hints = trial_hints
        cited = trial_cited
        if col is not None and key not in cited_ids:
            cited_ids.add(key)
    reserved: list[MeasureFact] = []
    for measure in sorted(measures, key=lambda item: (-item.score, item.name)):
        trial_measures = [*reserved, measure]
        if _fits(
            dialect,
            cited,
            _joins_for(cited, joins),
            kept_hints,
            trial_measures,
            max_tokens,
        ):
            reserved = trial_measures
    ordered = sorted(
        (col for col in columns if (col.table, col.name) not in cited_ids),
        key=lambda item: (-item.score, item.table, item.name),
    )
    room = _SHORTLIST - len(cited)
    if room < 1:
        ordered = []
    elif len(ordered) > room:
        ordered = ordered[:room]
    pending: deque[ColumnFact] = deque(ordered)
    queued = {(col.table, col.name) for col in ordered}
    chosen: list[ColumnFact] = list(cited)
    chosen_ids: set[tuple[str, str]] = set(cited_ids)
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
            if len(chosen_ids) + len(queued) >= _SHORTLIST:
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


def _term_folds(terms: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for term in terms:
        out.update(_phrase_key(term))
    return out


def _granted_folds(
    values: Sequence[str],
) -> tuple[set[str], set[tuple[str, ...]]]:
    tokens: set[str] = set()
    phrases: set[tuple[str, ...]] = set()
    for value in values:
        phrase = _phrase_key(value)
        if not phrase:
            continue
        phrases.add(phrase)
        if len(phrase) == 1:
            tokens.add(phrase[0])
    return tokens, phrases


def _label_terms(
    ontology: Ontology | Mapping[str, Any] | None,
    onto: Mapping[str, Any],
) -> list[str]:
    terms: list[str] = []
    if isinstance(ontology, Ontology):
        terms.extend(ontology.objects)
        terms.extend(ontology.measures)
    elif isinstance(ontology, Mapping):
        for key in ("objects", "measures"):
            block = ontology.get(key)
            if isinstance(block, Mapping):
                terms.extend(str(name) for name in block)
    for item in onto.get("measures") or []:
        if isinstance(item, Mapping) and item.get("name"):
            terms.append(str(item["name"]))
            for alias in item.get("aliases") or ():
                terms.append(str(alias))
    return terms


def _column_is_person(table: str, column: str) -> bool:
    """True only when the column classifier says the column holds names.

    Metadata only. A cell's shape is not a vote. None or an error is
    unsure: the value stays typed and is not a hint.
    """
    try:
        return classify_column(column, (), table=table) == "name"
    except Exception:  # noqa: BLE001 -- unsure: do not mask, do not hint
        return False


def _name_evidence(
    prepared: Sequence[Mapping[str, Any]],
    index: Mapping[str, Sequence[str]],
    ontology: Ontology | Mapping[str, Any] | None,
    onto: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Person columns are masked. Cleared columns may hint. Unsure does neither."""
    roles: dict[str, str] = {}
    terms: list[str] = []
    seen_tables: set[str] = set()
    for item in prepared:
        table = str(item["table"])
        name = str(item["name"])
        if table not in seen_tables:
            seen_tables.add(table)
            terms.append(table)
        terms.append(name)
        key = f"{table}.{name}"
        if item.get("cleared"):
            roles[key] = "exempt"
        elif _column_is_person(table, name):
            roles[key] = "person"
        else:
            roles[key] = "unsure"
    terms.extend(_label_terms(ontology, onto))
    names: list[str] = []
    exempt: list[str] = []
    for key, values in index.items():
        role = roles.get(key, "unsure")
        if role == "unsure":
            continue
        target = names if role == "person" else exempt
        target.extend(str(value) for value in values)
    return {
        "name_values": names,
        "exempt_values": exempt,
        "schema_terms": terms,
    }


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
    entity_spans: Any = None,
) -> SchemaContext:
    """Rank the connector description and render a prompt inside ``max_tokens``.

    ``entity_spans`` is not read. Hints come from the value index only, so a
    stub that returns a span cannot create one.
    """
    del entity_spans
    described = schema if isinstance(schema, Mapping) else {}
    dialect = _dialect(described.get("dialect"))
    question_vec = _trigrams(question or "")
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

    tags = _ontology_tags(ontology)
    loaded_values: list[str] = []
    index_rows: list[tuple[str, tuple[str, ...]]] = []
    prepared: list[dict[str, Any]] = []
    for dataset in datasets:
        table = _text(dataset.get("name"))
        table_desc = _text(dataset.get("description"))
        for column in _column_items(dataset):
            name = _text(column.get("name"))
            if not name:
                continue
            family = _type_family(_text(column.get("type")))
            tagged = (table, name) in tags
            values = _index_values(column, family)
            if values:
                loaded_values.extend(values)
                index_rows.append((f"{table}.{name}", tuple(values)))
            samples = _sample_values(column, family, tagged)
            cleared = _column_positively_cleared(table, name, tagged=tagged)
            prepared.append(
                {
                    "table": table,
                    "name": name,
                    "type": _text(column.get("type")),
                    "primary_key": bool(column.get("primary_key")),
                    "description": _text(column.get("description")),
                    "samples": samples,
                    "distinct": _distinct_count(column, values or samples),
                    "table_desc": table_desc,
                    "tagged": tagged,
                    "cleared": cleared,
                }
            )
    index, capped = _limit_index(index_rows)
    if capped:
        _LOG.warning("schema_index_cap")
    evidence = _name_evidence(prepared, index, ontology, onto)
    model_text, mask_failed = _model_question(question or "", evidence)
    words = _question_words(model_text)
    spans = [] if mask_failed else _question_spans(model_text)
    granted_tokens, granted_phrases = _granted_folds(evidence["exempt_values"])
    schema_folds = _term_folds(evidence["schema_terms"])
    known = _value_token_pairs(loaded_values)
    slots: list[tuple[str, str]] = []
    slot_meta: list[tuple[str, str]] = []
    seen_tables: set[str] = set()
    for item in prepared:
        table = str(item["table"])
        name = str(item["name"])
        key = f"{table}.{name}"
        table_desc = _mask_known(str(item["table_desc"]), known)
        if table_desc and table not in seen_tables:
            seen_tables.add(table)
            slots.append((f"{table}.__table__", table_desc))
            slot_meta.append(("table", table))
        description = _mask_known(str(item["description"]), known)
        hints = [] if mask_failed else _hint_values(
            spans,
            index.get(key, ()),
            tagged=bool(item["tagged"]),
            table=table,
            column=name,
            description=str(item["description"]),
            words=words,
            granted=granted_tokens,
            schema=schema_folds,
            phrases=granted_phrases,
        )
        item["hints"] = hints
        if description:
            slots.append((key, description))
            slot_meta.append(("column", key))
        for sample in item["samples"]:
            slots.append((key, str(sample)))
            slot_meta.append(("sample", key))
        for value, fuzzy, span_id in hints:
            slots.append((key, value))
            slot_meta.append(("hint", f"{key}|{int(fuzzy)}|{span_id}|{value}"))
    measure_items = [
        item
        for item in (onto.get("measures") or [])
        if _grain_ok(str(item["grain"]), object_tables, names)
    ]
    for item in measure_items:
        description = _mask_known(_text(item.get("description")), known)
        if description:
            slots.append((str(item["name"]), description))
            slot_meta.append(("measure", str(item["name"])))

    masked = None if mask_failed else _mask_slots(slots)
    table_desc_out: dict[str, str] = {}
    column_desc_out: dict[str, str] = {}
    sample_groups: dict[str, list[tuple[str, str]]] = {}
    hint_rows: list[tuple[str, str, bool, str, str]] = []
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
                col_key, flag, span_id, value = ref.split("|", 3)
                hint_rows.append((col_key, value, flag == "1", span_id, got))
            elif kind == "measure" and got.strip():
                measure_desc_out[ref] = " ".join(got.split())
    sample_out: dict[str, tuple[str, ...]] = {}
    for ref, pairs in sample_groups.items():
        if any(not _cleared(raw, got) for raw, got in pairs):
            continue
        sample_out[ref] = tuple(got for _raw, got in pairs)
    hint_out: list[HintFact] = []
    for ref, value, fuzzy, span_id, got in hint_rows:
        if not _cleared(value, got):
            continue
        hint_table, hint_column = ref.split(".", 1)
        hint_out.append(HintFact(hint_table, hint_column, value, fuzzy, span_id))

    _remember_index(space_id, index)
    span_of = {
        f"{hint.table}.{hint.column}": hint.span_id
        for hint in hint_out
        if hint.span_id
    }
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
        score = _cosine(question_vec, _trigrams(document))
        reason = f"score:{score:.2f}"
        if span_of.get(key):
            reason = f"{reason},span:{span_of[key]}"
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
                score=score,
                reason=reason,
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
    chosen_ids = {(col.table, col.name) for col in chosen}
    chosen = [_fk_if_present(col, chosen_ids) for col in chosen]
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
        envelope_prompt = prompt
    else:
        token_hints = [
            HintFact(hint.table, hint.column, _envelope_hint(i), hint.fuzzy, hint.span_id)
            for i, hint in enumerate(kept_hints, start=1)
        ]
        envelope_prompt = _render(
            dialect,
            chosen,
            kept_joins,
            token_hints,
            kept_measures,
            table_desc_out,
        )
    return SchemaContext(
        prompt=prompt,
        samples_included=any(col.samples for col in chosen),
        dialect=dialect,
        envelope_prompt=envelope_prompt,
        name_mask=evidence,
    )


def _constraint_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in _name_list(value.strip("[]{}").split(",")) if _ident(part)]
    if isinstance(value, Sequence):
        return [part for part in _name_list(value) if _ident(part)]
    return []


def _serving_file(serving: Path, space_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in space_id)[:96]
    return Path(str(Path(serving)) + ".schema_index") / f"{safe or 'default'}.json"


def _load_record(serving: Path, space_id: str) -> dict[str, Any] | None:
    path = _serving_file(serving, space_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return raw if isinstance(raw, dict) else None


def _disk_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Status and catalog shape only. Cell values never reach the file."""

    def _strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: ([] if key in {"values", "samples"} else _strip(val))
                for key, val in node.items()
            }
        if isinstance(node, list):
            return [_strip(item) for item in node]
        return node

    return _strip(dict(payload))


def _save_record(serving: Path, space_id: str, payload: Mapping[str, Any]) -> None:
    path = _serving_file(serving, space_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # The prompt log stays off disk. This file is status and catalog shape.
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_disk_payload(payload), sort_keys=True))
    tmp.replace(path)


def _open_serving(serving: Path) -> Any:
    from dms_executor.demo_warehouse import connect_file

    return connect_file(Path(serving))


def _fingerprint(
    columns: Sequence[tuple[str, str, str]], sizes: Mapping[str, int]
) -> str:
    lines = [
        f"{table}\t{column}\t{data_type}\t{int(sizes.get(table, 0))}"
        for table, column, data_type in columns
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:20]


def _bounded_read(con: Any, table: str, column: str, limit: int) -> list[str]:
    """At most ``limit`` rows. No DISTINCT and no full-table aggregate."""
    if limit < 1:
        return []
    try:
        rows = con.execute(
            f"SELECT CAST({_quote(column)} AS VARCHAR) FROM {_quote(table)} "
            f"WHERE {_quote(column)} IS NOT NULL LIMIT {int(limit)}"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [
        " ".join(str(row[0]).split())
        for row in rows
        if row and row[0] is not None and str(row[0]).strip()
    ]


def _stored_values(values: Sequence[str], *, table: str, column: str) -> list[str]:
    """Keep a cell in memory only when the mask clears it. Tokens stay; raw names do not."""
    listed = [value for value in values if value]
    if not listed:
        return []
    try:
        if classify_column(column, listed, table=table) is not None:
            return []
    except Exception:  # noqa: BLE001 -- doubt: store nothing from this column
        return []
    masked = _mask_slots([(f"v{i}", value) for i, value in enumerate(listed)])
    if masked is None:
        return []
    out: list[str] = []
    for raw, got in zip(listed, masked, strict=True):
        text = " ".join(got.split())
        if not text:
            continue
        if is_mask_token(text):
            out.append(text)
            continue
        if _cleared(raw, got) and not _mask_would_flag(raw, variants=True):
            out.append(text)
    return out


def read_catalog(
    serving: Path | None,
    grantable: set[str] | None,
    dialect: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Names, types, keys, and row-count estimates. No table-body reads."""
    named = _dialect(dialect)
    body: dict[str, Any] = {"datasets": [], "relationships": []}
    if named:
        body["dialect"] = named
    if serving is None or not Path(serving).is_file():
        return "", body
    try:
        con = _open_serving(Path(serving))
    except Exception:  # noqa: BLE001
        return "", body
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'main' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
        sizes: dict[str, int] = {}
        try:
            for table_name, estimate in con.execute(
                "SELECT table_name, estimated_size FROM duckdb_tables() "
                "WHERE schema_name = 'main'"
            ).fetchall():
                sizes[str(table_name)] = int(estimate or 0)
        except Exception:  # noqa: BLE001
            sizes = {}
        keys: dict[str, set[str]] = {}
        try:
            constraints = con.execute(
                "SELECT table_name, constraint_type, constraint_column_names "
                "FROM duckdb_constraints() WHERE schema_name = 'main'"
            ).fetchall()
        except Exception:  # noqa: BLE001
            constraints = []
        for table_name, kind, cols in constraints:
            if _text(kind).upper() != "PRIMARY KEY":
                continue
            table = str(table_name)
            keys.setdefault(table, set()).update(_constraint_names(cols))
        grouped: dict[str, list[dict[str, Any]]] = {}
        fp_rows: list[tuple[str, str, str]] = []
        for table_name, column_name, data_type in rows:
            table = str(table_name)
            column_name_text = str(column_name)
            if not _ident(table) or not _ident(column_name_text):
                continue
            if not _granted(table, grantable):
                continue
            data = str(data_type or "")
            fp_rows.append((table, column_name_text, data))
            grouped.setdefault(table, []).append(
                {
                    "name": column_name_text,
                    "type": data,
                    "description": "",
                    "primary_key": column_name_text in keys.get(table, set()),
                    "distinct": None,
                    "values": [],
                    "samples": [],
                }
            )
        body["datasets"] = [
            {"name": table, "description": "", "columns": cols}
            for table, cols in sorted(grouped.items())
        ]
        if not fp_rows:
            return "", body
        return _fingerprint(fp_rows, sizes), body
    except Exception:  # noqa: BLE001
        return "", body
    finally:
        con.close()


def schema_from_serving(
    serving: Path | None,
    grantable: set[str] | None,
    *,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Catalog description only. Value samples are not read here."""
    _fp, body = read_catalog(serving, grantable, dialect)
    return body


def build_space_index(
    serving: Path | None,
    space_id: str | None,
    grantable: set[str] | None = None,
    dialect: str | None = None,
) -> str:
    """Build one Space index off the ask path. Returns a stamp, or ``""``.

    Each column read is ``LIMIT``-bounded. The serving lock is taken per
    table and released, so a live ask can attach between tables.
    """
    if not space_id or serving is None or not Path(serving).is_file():
        return "index_failed:ValueError"
    fingerprint, plain = read_catalog(serving, grantable, dialect)
    if not fingerprint:
        _forget_index(space_id)
        _save_record(
            Path(serving),
            space_id,
            {
                "status": "failed",
                "fingerprint": "",
                "error": "OSError",
                "schema": plain,
            },
        )
        return "index_failed:OSError"
    _save_record(
        Path(serving),
        space_id,
        {
            "status": "building",
            "fingerprint": fingerprint,
            "error": "",
            "schema": {},
        },
    )
    rows_left = INDEX_ROW_BUDGET
    deadline = time.monotonic() + INDEX_BUILD_BUDGET_S
    try:
        for dataset in plain["datasets"]:
            if _STOP.is_set() or time.monotonic() >= deadline or rows_left < 1:
                break
            table = str(dataset["name"])
            con = _open_serving(Path(serving))
            try:
                for col in dataset["columns"]:
                    if _STOP.is_set() or time.monotonic() >= deadline or rows_left < 1:
                        break
                    take = min(SAMPLE_ROWS, rows_left)
                    got = _bounded_read(con, table, str(col["name"]), take)
                    rows_left -= len(got)
                    stored = _stored_values(got, table=table, column=str(col["name"]))
                    if _type_family(str(col.get("type") or "")) == "text":
                        col["values"] = stored[:INDEX_COLUMN_CAP]
                    else:
                        col["samples"] = stored[:_SAMPLE_LIMIT]
            finally:
                con.close()
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        _forget_index(space_id)
        _save_record(
            Path(serving),
            space_id,
            {
                "status": "failed",
                "fingerprint": fingerprint,
                "error": name,
                "schema": {},
            },
        )
        _LOG.warning("schema_index_failed %s", name)
        return f"index_failed:{name}"
    if _STOP.is_set():
        return ""
    values: dict[str, tuple[str, ...]] = {}
    samples: dict[str, tuple[str, ...]] = {}
    for dataset in plain["datasets"]:
        table = str(dataset["name"])
        for col in dataset["columns"]:
            key = f"{table}.{col['name']}"
            if col.get("values"):
                values[key] = tuple(col["values"])
            if col.get("samples"):
                samples[key] = tuple(col["samples"])
    _save_record(
        Path(serving),
        space_id,
        {
            "status": "ready",
            "fingerprint": fingerprint,
            "error": "",
            "schema": plain,
        },
    )
    _remember_built(space_id, fingerprint, values, samples)
    return ""


_BUILD_LOCK = threading.Lock()
_BUILDING: set[str] = set()


def stop_index_builds(timeout: float = 5.0) -> None:
    """Stop background index builds and wait until their DuckDB calls finish."""
    _STOP.set()
    with _WORKER_LOCK:
        workers = list(_WORKERS)
    for worker in workers:
        if worker.ident is None and not worker.is_alive():
            continue
        worker.join(timeout)
    with _WORKER_LOCK:
        alive = [worker for worker in _WORKERS if worker.is_alive()]
        if not alive:
            _WORKERS.clear()
            _STOP.clear()


def schedule_index_build(
    serving: Path,
    space_id: str,
    grantable: set[str] | None,
    dialect: str | None,
    fingerprint: str,
) -> None:
    """Start a background build. Returns immediately. Not a daemon thread."""
    if _STOP.is_set():
        return
    key = f"{space_id}\n{fingerprint}"
    with _BUILD_LOCK:
        if key in _BUILDING:
            return
        _BUILDING.add(key)
    _save_record(
        Path(serving),
        space_id,
        {
            "status": "building",
            "fingerprint": fingerprint,
            "error": "",
            "schema": {},
        },
    )

    def _job() -> None:
        try:
            if not _STOP.is_set():
                build_space_index(serving, space_id, grantable, dialect)
        finally:
            with _BUILD_LOCK:
                _BUILDING.discard(key)

    worker = threading.Thread(target=_job, name="schema-index", daemon=False)
    with _WORKER_LOCK:
        _WORKERS.append(worker)
    worker.start()


atexit.register(stop_index_builds)


def note_serving_source(
    serving: Path | None,
    space_id: str | None,
    grantable: set[str] | None = None,
) -> None:
    """Schedule an index build when a source connects or its catalog changes."""
    if not schema_context_enabled():
        return
    if not space_id or serving is None or not Path(serving).is_file():
        return
    try:
        fingerprint, _plain = read_catalog(serving, grantable, None)
        if not fingerprint:
            return
        rec = _load_record(Path(serving), space_id)
        if (
            rec
            and rec.get("status") == "ready"
            and rec.get("fingerprint") == fingerprint
            and _space_fingerprint(space_id) == fingerprint
        ):
            return
        schedule_index_build(Path(serving), space_id, grantable, None, fingerprint)
    except Exception:  # noqa: BLE001
        return


def _overlay_index(
    schema: Mapping[str, Any],
    values: Mapping[str, Sequence[str]],
    samples: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Catalog shape plus the in-memory cells. The file's cells are not read."""
    out = dict(schema)
    datasets: list[dict[str, Any]] = []
    for dataset in schema.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        table = _text(dataset.get("name"))
        columns: list[dict[str, Any]] = []
        for col in dataset.get("columns") or []:
            if not isinstance(col, dict):
                continue
            copied = dict(col)
            key = f"{table}.{_text(copied.get('name'))}"
            if _type_family(_text(copied.get("type"))) == "text":
                if key in values:
                    copied["values"] = list(values[key])
            elif key in samples:
                copied["samples"] = list(samples[key])
            columns.append(copied)
        item = dict(dataset)
        item["columns"] = columns
        datasets.append(item)
    out["datasets"] = datasets
    return out


def lookup_schema(
    serving: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    space_id: str | None,
) -> tuple[dict[str, Any], str]:
    """Ask path. Catalog read plus a stored-index lookup. Never samples values."""
    fingerprint, plain = read_catalog(serving, grantable, dialect)
    if serving is None or not Path(serving).is_file() or not space_id:
        return plain, "index_pending"
    if not fingerprint:
        return plain, "index_failed:OSError"
    if _space_fingerprint(space_id) == fingerprint:
        schema = _overlay_index(
            plain,
            cached_value_index(space_id),
            _space_samples(space_id),
        )
        if dialect and not _text(schema.get("dialect")):
            schema["dialect"] = _dialect(dialect)
        return schema, ""
    rec = _load_record(Path(serving), space_id)
    status = str((rec or {}).get("status") or "")
    same = (rec or {}).get("fingerprint") == fingerprint
    if status == "failed" and same:
        err = str((rec or {}).get("error") or "Error")
        return plain, f"index_failed:{err}"
    schedule_index_build(Path(serving), space_id, grantable, dialect, fingerprint)
    return plain, "index_pending"


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

    A caller-supplied ``schema`` is rendered as given. A serving file is a
    lookup: the stored index when it is ready, otherwise catalog names and
    types plus ``index_pending`` or ``index_failed:<class>``. The ask does
    not build the index and does not wait for it.
    """
    if not schema_context_enabled():
        return ctx
    stamp = ""
    try:
        described = schema
        if described is None:
            described, stamp = lookup_schema(serving, grantable, dialect, space_id)
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
    out[SCHEMA_CONTEXT_ENVELOPE_KEY] = built.envelope_prompt
    if built.name_mask is not None:
        out[NAME_MASK_KEY] = built.name_mask
    if stamp:
        out[SCHEMA_INDEX_STAMP_KEY] = stamp
    return out
