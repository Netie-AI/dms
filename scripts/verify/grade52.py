"""One grader for the 52-question pack.

Gold is each certified oracle in ``tests/fixtures/curated_ceo/oracles.yaml``,
executed read-only on a fresh ``ensure_demo_warehouse`` file. ``$as_of`` binds
the connection's current date. A refuse oracle, or a question with no SQL, has
empty gold. Gold is never read from an envelope or a canned stub.

``--envelopes`` grades those served rows (point 4, then the order rule). It
does not execute ``sql_used``. A submit-stub pack (every confident row is the
one-cell ``n=1`` stub) is labelled stub and is not a score. ``--main`` builds
envelopes with the exec-SQL stub, flags off, and labels that score stub-exec.

A trap is a declared ``trap`` or ``refusal_expected`` field on the question
or the oracle. Gold-broken is the non-trap case with no certified oracle,
and only when that case abstains. A served answer there is wrong.

``--envelopes`` prints the commit declared on the envelopes. ``--main``
prints the product commit (``git rev-parse HEAD`` of the graded checkout).

Each case records ``serve_path`` from that envelope's own ``badge``,
``plan_origin``, ``ladder_rung``, ``plan_source``, and ``served_model``.
``ontology_ranking``, ``ontology_plan``, and an ``ontology_compile`` stamp
are rule/compile, including when ``served_model`` is set. One exception:
``plan_source=ontology_plan`` beside a model-SQL ``plan_origin`` is not a
compile claim. Cortex stamps ``ontology_plan`` only when a model wrote the
SQL, and DMS stamps it on every generative answer, so the origin decides.
Stamps are not proof: AI also needs the served ``sql_used`` to equal the SQL
Cortex's generate returned, which only an armed capture records. A model
claim without that proof is unattributed.
The paths line counts AI model SQL, rule-served (L0, L1, compile, oracle),
and unattributed, once for correct answers and once for every served answer.
The split line puts every case in one of ai, rule, unattributed, or abstain.

The first output line is the header: ``mode=armed`` or ``mode=stub``, the
product sha, and the Cortex sha. Only ``--armed`` (or a capture whose header
says armed) is armed. Everything else is stub, including a capture that does
not say what it is.

``--armed`` posts each pack question through ``POST /v1/chat/ask`` in this
checkout's app, with a real ``CortexClient`` on ``CORTEX_BASE_URL`` and a
real OpenVault manifest mint. It needs ``CORTEX_BASE_URL``, an
OpenVault-issued ``CORTEX_API_KEY`` (not the demo viewer key), ``CORTEX_SHA``
(Cortex does not publish its commit over HTTP), and the minter's own
``OPENVAULT_URL`` plus ``DMS_OV_SERVICE_TOKEN``. It never falls back. It
exits non-zero with a named reason when a setting is missing, Cortex is
unreachable, Cortex says FreeRoute is unarmed, the mint fails, a question
carries a stub/placeholder answer or an unarmed Insights reason, or no answer
was served from the model's own SQL (``split ai=0``).

``DMS_ONTO_FROM_DATA=1`` (default off) makes ``--armed`` read approved items
from the persisted store before the first question. Zero approved objects
exits non-zero with ``no approved ontology version``. It does not fall
back to the spine. grade52 never approves. The artifact then records
``ontology_version_id``, approved counts by kind, and the approver actor
id beside ``paths_served``. Every armed artifact also records
``ov_calls_by_caller_role`` from the OpenVault usage ledger window of the
run (Cortex versus DMS-direct grain and ontology), so a model mismatch is
counted per role.

The risk line states what a score supports: per route, served answers with
gold, how many were wrong, and the exact one-sided 95 percent upper bound on
the wrong-served rate (0 wrong in n is about 3/n). Abstains are not served
answers and never shrink the bound. Coverage is served over graded.

Several ``--envelopes`` of one commit are graded run by run, then on the
worst case per question: wrong in any run is wrong, abstained in any run is
an abstain. ``--gate`` exits 1 on any wrong served answer in that worst case.
``--baseline`` diffs against an earlier artifact of the same grader version;
the log gets move counts, the artifact gets the ids.

Usage:
    python scripts/verify/grade52.py --self-test
    python scripts/verify/grade52.py --main
    python scripts/verify/grade52.py --envelopes PATH
    python scripts/verify/grade52.py --envelopes RUN1 --envelopes RUN2 --envelopes RUN3 --gate
    python scripts/verify/grade52.py --envelopes PATH --baseline OLD/grade52.json
    python scripts/verify/grade52.py --armed --layout copy-only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PACK_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
ORACLE_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"
PACK_DIR = PACK_PATH.parent
# What the recorded stub Cortex serves. Armed mode refuses any answer that
# carries one of its values, read from this file and never typed here.
STUB_RESPONSES_PATH = ROOT / "tests" / "fixtures" / "ask_guide" / "cortex_responses_52.json"
LAYOUTS = ("copy-only", "full-tree")

ABS_TOL = 0.005
# ponytail: product of per-column candidate counts. Above this the grade is
# unmappable. Upgrade is branch-and-bound if a real answer is wider than the
# value signature can narrow.
MAP_CAP = 1024
MODE_SERVED = "served"
MODE_STUB_EXEC = "stub-exec"
MODE_ARMED = "armed"
MODES = (
    MODE_SERVED,
    MODE_STUB_EXEC,
    MODE_ARMED,
)
# The header's run label. Armed only when the run proved it; anything else is stub.
RUN_ARMED = "armed"
RUN_STUB = "stub"
ARMED_CORTEX_ENV = "CORTEX_BASE_URL"
ARMED_KEY_ENV = "CORTEX_API_KEY"
ARMED_SHA_ENV = "CORTEX_SHA"
ARMED_OPENVAULT_ENV = "OPENVAULT_URL"
# Same default as the product's Settings.openvault_url and ManifestMinter.
ARMED_OPENVAULT_DEFAULT = "http://127.0.0.1:5000"
ARMED_SESSION_ID = "ses_armed_52"
ARMED_PREFLIGHT_TIMEOUT_S = 10.0
BUCKET_CORRECT = "CORRECT"
BUCKET_WRONG = "WRONG"
BUCKET_RECONFIRM = "RECONFIRM"
BUCKET_ABSTAIN = "ABSTAIN"
BUCKET_EMPTY = "EMPTY_GOLD"
BUCKET_REFUSAL_OK = "REFUSAL_OK"
BUCKET_REFUSAL_WRONG = "REFUSAL_WRONG"
BUCKET_GOLD_BROKEN = "GOLD_BROKEN"
_BUCKET_KEYS = (
    "correct",
    "wrong",
    "reconfirm",
    "abstain",
    "refusal_ok",
    "refusal_wrong",
    "empty_gold",
    "gold_broken",
)
# Bump when a bucket rule or the row comparison changes. A baseline graded by
# another version is not comparable and is refused.
# 4: printed wrong includes refusal_wrong. not_scored lists pin_mismatch ids.
GRADER_VERSION = 4
CONFIDENCE = 0.95
# Higher is worse. A case's worst-of-runs bucket is the one ranked highest.
_SEVERITY = {
    BUCKET_WRONG: 3,
    BUCKET_REFUSAL_WRONG: 3,
    BUCKET_ABSTAIN: 2,
    BUCKET_RECONFIRM: 2,
    BUCKET_GOLD_BROKEN: 2,
    BUCKET_EMPTY: 1,
    BUCKET_CORRECT: 0,
    BUCKET_REFUSAL_OK: 0,
}
# Served answers that have gold to be judged against. n of the wrong-rate bound.
_SERVED_GRADED = frozenset({BUCKET_CORRECT, BUCKET_WRONG, BUCKET_REFUSAL_WRONG})
_RISK_GROUPS = ("all", "ai", "rule", "unattributed")
_REFUSAL_ROUTES = frozenset({"abstain", "blocked", "needs_clarification", "refused"})
_CREDENTIAL_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
# Path claims the envelope may stamp. Anything else is not a path.
_RULE_STAMPS = {
    "l0": "L0",
    "l0_certified": "L0",
    "l1": "L1",
    "l1_governed_metric": "L1",
    "compile": "compile",
    "ladder": "ladder",
    "ladder_rung": "ladder",
    "oracle": "oracle",
}
_AI_STAMPS = frozenset({"generate_sql", "model_sql", "ai"})
_RULE_PATHS = frozenset({"L0", "L1", "compile", "ladder", "oracle"})
# OpenVault per-caller cap. The armed pacer stays under both. It does not raise them.
_OV_REQ_PER_MIN = 20
_OV_TOK_PER_MIN = 40_000
_ABSTAIN_PART_BUCKETS = {
    BUCKET_RECONFIRM: "reconfirm",
    BUCKET_REFUSAL_OK: "refusal_ok",
    BUCKET_GOLD_BROKEN: "gold_broken",
    BUCKET_ABSTAIN: "abstain",
    BUCKET_EMPTY: "empty_gold",
}
_ABSTAIN_PARTS = ("reconfirm", "refusal_ok", "gold_broken", "abstain", "empty_gold", "other")
_PATH_DETAIL = ("L0", "L1", "compile", "ladder", "oracle")
# Ontology compile is a rule path. A model name on the same envelope does not
# overturn it. ``ontology_compile:*`` notes use the same prefix.
_ONTOLOGY_COMPILE = frozenset({"ontology_ranking", "ontology_plan", "ontology_compile"})


class _Unmappable(Exception):
    """Candidate assignments exceed MAP_CAP."""


def _bootstrap() -> None:
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    ):
        entry = str(ROOT / rel)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _output_names(tree: Any) -> list[str]:
    """Select-list output names from the AST. Empty when the shape is unknown."""
    from sqlglot import exp

    node = tree
    if isinstance(node, exp.Union):
        node = node.this
    if not isinstance(node, exp.Select):
        return []
    names: list[str] = []
    for proj in node.expressions:
        if isinstance(proj, exp.Alias):
            names.append(proj.alias)
        elif isinstance(proj, exp.Column):
            names.append(proj.name)
        else:
            names.append(str(proj.alias_or_name))
    return names


def _order_name(node: Any, outputs: Sequence[str]) -> str | None:
    """Map one ORDER BY expression to an output column. AST only."""
    from sqlglot import exp

    if isinstance(node, exp.Literal) and node.is_int:
        index = int(node.this) - 1
        if 0 <= index < len(outputs):
            return outputs[index]
        return None
    if isinstance(node, exp.Column):
        if node.name in outputs:
            return node.name
        folded = {name.casefold(): name for name in outputs}
        return folded.get(node.name.casefold())
    return None


def order_keys(sql: str) -> list[str] | None:
    """Top-level ORDER BY output columns, or None when the statement is unordered.

    Nested ORDER BY (subquery, CTE, window) does not count. Keys are read from
    the sqlglot AST, including ordinals. An expression that is not an output
    column yields an empty list, which compares rows positionally.
    """
    from sqlglot import parse_one

    tree = parse_one(sql, read="duckdb")
    order = tree.args.get("order") if hasattr(tree, "args") else None
    if order is None:
        return None
    outputs = _output_names(tree)
    keys: list[str] = []
    for ordered in order.expressions:
        name = _order_name(ordered.this, outputs)
        if name is None:
            return []
        keys.append(name)
    return keys


def oracle_orders(sql: str) -> bool:
    """True when the parsed statement has a top-level ORDER BY."""
    return order_keys(sql) is not None


def _is_bool(value: Any) -> bool:
    return type(value) is bool


def _decimal_places(value: Any) -> int | None:
    if _is_bool(value) or value is None:
        return None
    if isinstance(value, int):
        return 0
    if isinstance(value, Decimal):
        return max(0, -value.as_tuple().exponent)
    if isinstance(value, float):
        return _decimal_places(Decimal(str(value)))
    if isinstance(value, str):
        text = value.strip()
        if _as_float(text) is None:
            return None
        if "." not in text:
            return 0
        frac = text.split(".", 1)[1]
        return len(frac)
    return None


def _as_float(value: Any) -> float | None:
    if _is_bool(value) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def _whole_number(value: Any) -> bool:
    """True when gold declares no fractional scale. Those values are not rounded."""
    places = _decimal_places(value)
    return places == 0


def _round_half_away(value: float, places: int) -> float:
    """DuckDB ROUND: half away from zero. Not Python round()."""
    quant = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def cells_equal(gold: Any, served: Any) -> bool:
    """Numbers within 0.005. Gold's decimal count wins when it is shorter."""
    if gold is None and served is None:
        return True
    if _is_bool(gold) or _is_bool(served):
        return _is_bool(gold) and _is_bool(served) and gold == served
    if gold is None or served is None:
        return False
    gold_iso = _iso(gold)
    served_iso = _iso(served)
    if gold_iso is not None or served_iso is not None:
        left = gold_iso if gold_iso is not None else str(gold).strip()
        right = served_iso if served_iso is not None else str(served).strip()
        return left == right
    gold_num = _as_float(gold)
    served_num = _as_float(served)
    if gold_num is not None and served_num is not None:
        gold_dp = _decimal_places(gold)
        served_dp = _decimal_places(served)
        if (
            gold_dp is not None
            and served_dp is not None
            and gold_dp < served_dp
            and not _whole_number(gold)
        ):
            served_num = _round_half_away(served_num, gold_dp)
        return abs(gold_num - served_num) <= ABS_TOL
    if isinstance(gold, str) or isinstance(served, str):
        return str(gold).strip() == str(served).strip()
    return gold == served


def _name_lock(gold_cols: Sequence[str], served_cols: Sequence[str]) -> dict[str, str] | None:
    """Exact names, then case-insensitive. None if any gold column is unnamed."""
    unused = set(served_cols)
    mapping: dict[str, str] = {}
    for name in gold_cols:
        if name in unused:
            mapping[name] = name
            unused.remove(name)
    folded: dict[str, str] = {}
    for name in served_cols:
        folded.setdefault(name.lower(), name)
    for name in gold_cols:
        if name in mapping:
            continue
        hit = folded.get(name.lower())
        if hit is None or hit not in unused:
            return None
        mapping[name] = hit
        unused.remove(hit)
    return mapping


def _column_values(rows: Sequence[Mapping[str, Any]], col: str) -> list[Any]:
    return [row.get(col) for row in rows]


def _column_compatible(
    gold_vals: Sequence[Any],
    served_vals: Sequence[Any],
) -> bool:
    """Value signature: one gold cell matches one served cell, duplicates kept."""
    if len(gold_vals) != len(served_vals):
        return False
    used = [False] * len(served_vals)
    for gold in gold_vals:
        found = False
        for index, served in enumerate(served_vals):
            if used[index]:
                continue
            if cells_equal(gold, served):
                used[index] = True
                found = True
                break
        if not found:
            return False
    return True


def _candidate_maps(
    gold_cols: Sequence[str],
    served_cols: Sequence[str],
    gold: Sequence[Mapping[str, Any]],
    served: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Exact name, then case-insensitive, then a capped signature assignment.

    A locked column is not reassigned. Unmatched columns permute only among
    served columns whose values are compatible. The product of those candidate
    counts is the cap. Over the cap this raises ``_Unmappable``.
    """
    if len(served_cols) < len(gold_cols):
        return []
    locked: dict[str, str] = {}
    unused = set(served_cols)
    pending: list[str] = []
    for name in gold_cols:
        if name in unused:
            locked[name] = name
            unused.remove(name)
        else:
            pending.append(name)
    folded: dict[str, str] = {}
    for name in served_cols:
        folded.setdefault(name.lower(), name)
    still: list[str] = []
    for name in pending:
        hit = folded.get(name.lower())
        if hit is not None and hit in unused:
            locked[name] = hit
            unused.remove(hit)
        else:
            still.append(name)
    if not still:
        return [locked]
    pool = [name for name in served_cols if name in unused]
    if len(pool) < len(still):
        return []
    gold_vals = {col: _column_values(gold, col) for col in still}
    served_vals = {col: _column_values(served, col) for col in pool}
    candidates: dict[str, list[str]] = {}
    upper = 1
    for col in still:
        hits = [
            name
            for name in pool
            if _column_compatible(gold_vals[col], served_vals[name])
        ]
        if not hits:
            return []
        candidates[col] = hits
        upper *= len(hits)
        if upper > MAP_CAP:
            raise _Unmappable
    maps: list[dict[str, str]] = []
    used = set(locked.values())

    def walk(index: int, current: dict[str, str]) -> None:
        if index == len(still):
            maps.append(dict(current))
            return
        col = still[index]
        for name in candidates[col]:
            if name in used:
                continue
            used.add(name)
            current[col] = name
            walk(index + 1, current)
            del current[col]
            used.remove(name)

    walk(0, dict(locked))
    return maps


def _row_agrees(gold_row: Mapping[str, Any], served_row: Mapping[str, Any], mapping: Mapping[str, str]) -> bool:
    return all(cells_equal(gold_row[col], served_row[mapping[col]]) for col in mapping)


def _multiset(gold: Sequence[Mapping[str, Any]], served: Sequence[Mapping[str, Any]], mapping: Mapping[str, str]) -> bool:
    used = [False] * len(served)
    for grow in gold:
        found = False
        for index, srow in enumerate(served):
            if used[index]:
                continue
            if _row_agrees(grow, srow, mapping):
                used[index] = True
                found = True
                break
        if not found:
            return False
    return True


def _positional(gold: Sequence[Mapping[str, Any]], served: Sequence[Mapping[str, Any]], mapping: Mapping[str, str]) -> bool:
    return all(_row_agrees(grow, srow, mapping) for grow, srow in zip(gold, served, strict=True))


def _keys_equal(left: Sequence[Any], right: Sequence[Any]) -> bool:
    return all(cells_equal(a, b) for a, b in zip(left, right, strict=True))


def _tie_ordered(
    gold: Sequence[Mapping[str, Any]],
    served: Sequence[Mapping[str, Any]],
    mapping: Mapping[str, str],
    keys: Sequence[str],
) -> bool:
    """Sort-key sequence exact. Rows that tie on that key match as a multiset."""
    if not keys or any(key not in gold[0] or key not in served[0] for key in keys):
        return _positional(gold, served, mapping)
    gold_keys = [[row[key] for key in keys] for row in gold]
    served_keys = [[row[key] for key in keys] for row in served]
    if any(not _keys_equal(left, right) for left, right in zip(gold_keys, served_keys, strict=True)):
        return False
    start = 0
    while start < len(gold):
        end = start + 1
        while end < len(gold) and _keys_equal(gold_keys[start], gold_keys[end]):
            end += 1
        if not _multiset(gold[start:end], served[start:end], mapping):
            return False
        start = end
    return True


def _project(
    served: Sequence[Mapping[str, Any]],
    gold_cols: Sequence[str],
    mapping: Mapping[str, str],
) -> list[dict[str, Any]]:
    """One projected row per answer row. Extra columns drop. Duplicates stay."""
    return [{col: row.get(mapping[col]) for col in gold_cols} for row in served]


def compare_rows(
    gold: Sequence[Mapping[str, Any]],
    served: Sequence[Mapping[str, Any]],
    *,
    ordered: bool,
    order_keys: Sequence[str] | None = None,
) -> tuple[str, str]:
    """Map each gold column to its own answer column, project, then multiset.

    Try exact names, then case-insensitive, then a permutation of the rest.
    Extra answer columns are ignored. Duplicate projected rows are kept, so a
    fan-out does not collapse into a match. When ``order_keys`` is set, the
    sort-key sequence must match and tied rows compare as a multiset.
    """
    if len(gold) != len(served):
        return BUCKET_WRONG, f"rowcount {len(served)} vs {len(gold)}"
    if not gold:
        return BUCKET_CORRECT, "match"
    gold_cols = list(gold[0].keys())
    served_cols = list(served[0].keys())
    try:
        maps = _candidate_maps(gold_cols, served_cols, gold, served)
    except _Unmappable:
        return BUCKET_WRONG, "unmappable"
    if not maps:
        return BUCKET_WRONG, "no column mapping"
    named = _name_lock(gold_cols, served_cols) is not None
    ident = {col: col for col in gold_cols}
    projected_hits: list[list[dict[str, Any]]] = []
    for item in maps:
        projected = _project(served, gold_cols, item)
        if _multiset(gold, projected, ident):
            projected_hits.append(projected)
    if ordered:
        keys = list(order_keys or [])
        if any(_tie_ordered(gold, item, ident, keys) for item in projected_hits):
            return BUCKET_CORRECT, "match"
        if projected_hits:
            return BUCKET_WRONG, "order mismatch"
        if named:
            return BUCKET_WRONG, "row mismatch"
        return BUCKET_WRONG, "no column mapping"
    if projected_hits:
        return BUCKET_CORRECT, "match"
    if named:
        return BUCKET_WRONG, "row mismatch"
    return BUCKET_WRONG, "no column mapping"


def _is_abstain(env: Mapping[str, Any]) -> bool:
    if str(env.get("badge") or "") == "ABSTAIN":
        return True
    if env.get("abstained") is True:
        return True
    return str(env.get("route") or "").lower() in _REFUSAL_ROUTES


def abstain_code(env: Mapping[str, Any]) -> str | None:
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if _is_abstain(env):
        return str(env.get("route") or "abstain")
    return None


def _rows_of(value: Any) -> list[dict[str, Any]]:
    """Envelope rows. A missing key is an error, not zero rows."""
    if not isinstance(value, list):
        raise SystemExit("grade52: envelope has no rows")
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
    return out


def _declared_trap(node: Mapping[str, Any] | None) -> bool:
    if not isinstance(node, Mapping):
        return False
    return node.get("trap") is True or node.get("refusal_expected") is True


def _is_trap(question: Mapping[str, Any], oracle: Mapping[str, Any] | None = None) -> bool:
    """A refusal case from a declared yaml field, not from the id."""
    return _declared_trap(question) or _declared_trap(oracle)


def _stamp_text(env: Mapping[str, Any], key: str) -> str:
    if key not in env:
        return ""
    raw = env.get(key)
    if raw is None:
        return ""
    return str(raw).strip()


def _ontology_compile(text: str) -> bool:
    return text in _ONTOLOGY_COMPILE or text.startswith("ontology_compile")


def _compile_source(source: str, origin: str) -> bool:
    """True when ``plan_source`` claims an ontology compile.

    ``ontology_plan`` beside a model-SQL ``plan_origin`` is not that claim.
    Cortex stamps ``plan_source=ontology_plan`` only when a model wrote the
    SQL (its Insights law), and DMS stamps it on every generative answer,
    model SQL and DMS compile alike. ``plan_origin`` is the stamp that tells
    them apart: DMS sets ``ontology_ranking`` or ``ontology_compile`` when it
    compiled. The stamps are still not proof: ``serve_path`` also needs the
    served SQL to be the model's SQL.
    """
    if source == "ontology_plan" and origin in _AI_STAMPS:
        return False
    return _ontology_compile(source)


def _field_claim(text: str, *, unknown_if_other: bool) -> str | None:
    if not text:
        return None
    if _ontology_compile(text):
        return "compile"
    if text in _RULE_STAMPS:
        return _RULE_STAMPS[text]
    if text in _AI_STAMPS:
        return "ai"
    if unknown_if_other:
        return "unknown"
    return None


def stamp_contradiction(env: Mapping[str, Any]) -> bool:
    """Compile stamp and a served model. The path stays compile."""
    if not isinstance(env, Mapping) or not _stamp_text(env, "served_model"):
        return False
    origin = _stamp_text(env, "plan_origin").lower()
    if _compile_source(_stamp_text(env, "plan_source").lower(), origin):
        return True
    return any(
        _ontology_compile(_stamp_text(env, key).lower())
        for key in ("plan_origin", "ladder_rung", "badge")
    )


def model_sql_served(env: Mapping[str, Any], model_sql: str | None) -> bool:
    """The served ``sql_used`` is the model's own SQL, by the product's equality.

    ``sql_byte_equal`` is the comparison the extract loop credits a model
    with. No model SQL, or none served, is never a match.
    """
    served = env.get("sql_used") if isinstance(env, Mapping) else None
    if not isinstance(served, str) or not isinstance(model_sql, str):
        return False
    from dms_executor.demo_warehouse import SERVING_DIALECT
    from dms_executor.sql_currency import sql_byte_equal

    return sql_byte_equal(served, model_sql, SERVING_DIALECT)


def serve_path(env: Mapping[str, Any], model_sql: str | None = None) -> str:
    """Path that served the answer, from the envelope's stamps and the model's SQL.

    ``L0``, ``L1``, ``compile``, and ``oracle`` are rule-served. An ontology
    compile stamp (``ontology_ranking``, ``ontology_plan``, ``ontology_compile``)
    is rule/compile, and a ``served_model`` beside it does not change that.
    AI model SQL needs a model-SQL stamp and a non-empty ``served_model`` that
    do not disagree with a rule stamp. When the envelope served SQL, that
    ``sql_used`` must be ``model_sql`` (what Cortex's generate returned). A
    served SQL that is not the model's is unattributed, so a DMS compile under
    stale model stamps is never AI. No served SQL leaves the stamp as the
    claim: the builder's model route has nothing to contradict. Missing or
    contradictory stamps are unattributed, and a model name alone is never AI.
    ``plan_source`` of ``ontology_plan`` under a model-SQL ``plan_origin`` is
    not a compile claim (see ``_compile_source``).
    """
    path = _stamped_path(env)
    served = env.get("sql_used") if isinstance(env, Mapping) else None
    if (
        path == "ai"
        and isinstance(served, str)
        and served.strip()
        and not model_sql_served(env, model_sql)
    ):
        return "unattributed"
    return path


def _stamped_path(env: Mapping[str, Any]) -> str:
    """What the envelope's own stamps claim. Not proof of a model answer."""
    if not isinstance(env, Mapping):
        return "unattributed"
    badge = _stamp_text(env, "badge").lower()
    origin = _stamp_text(env, "plan_origin").lower()
    rung = _stamp_text(env, "ladder_rung").lower()
    source = _stamp_text(env, "plan_source").lower()
    claims: list[str] = []
    for text, unknown_if_other in (
        (badge, False),
        (origin, True),
        (rung, True),
    ):
        claim = _field_claim(text, unknown_if_other=unknown_if_other)
        if claim:
            claims.append(claim)
    compile_source = _compile_source(source, origin)
    if compile_source:
        claims.append("compile")
    ontology = compile_source or any(_ontology_compile(text) for text in (badge, origin, rung))
    model = _stamp_text(env, "served_model")
    if model and not ontology:
        claims.append("ai")
    kinds = set(claims)
    if kinds == {"ai"} and model and (origin in _AI_STAMPS or rung in _AI_STAMPS):
        return "ai"
    if len(kinds) == 1:
        only = next(iter(kinds))
        if only in _RULE_PATHS:
            return only
    return "unattributed"


def path_group(path: str) -> str:
    """Roll a case path into ai, rule, or unattributed."""
    if path == "ai":
        return "ai"
    if path in _RULE_PATHS:
        return "rule"
    return "unattributed"


def aggregate_signature(sql: str | None) -> frozenset[str]:
    """Aggregate expressions in a statement. Empty when there is no aggregate."""
    if not isinstance(sql, str) or not sql.strip():
        return frozenset()
    from dms_executor.demo_warehouse import SERVING_DIALECT
    from sqlglot import exp, parse_one

    try:
        tree = parse_one(sql, read=SERVING_DIALECT)
    except Exception:
        return frozenset()
    return frozenset(
        node.sql(dialect=SERVING_DIALECT).casefold() for node in tree.find_all(exp.AggFunc)
    )


def different_measure(gold_sql: str | None, served_sql: str | None) -> bool:
    """True when both statements aggregate and the aggregates are not the same."""
    gold = aggregate_signature(gold_sql)
    served = aggregate_signature(served_sql)
    if not gold or not served:
        return False
    return gold != served


def _reconfirm_offer(env: Mapping[str, Any]) -> bool:
    raw = env.get("reconfirm")
    return isinstance(raw, Mapping) and "suggested_question" in raw


SERVED_ROUTES = ("ai", "rule", "unattributed", "abstain")


def served_route(env: Mapping[str, Any], model_sql: str | None = None) -> str:
    """ai, rule, unattributed, or abstain. An abstain is not a served path."""
    if not isinstance(env, Mapping) or _is_abstain(env):
        return "abstain"
    return path_group(serve_path(env, model_sql))


def _model_call_count(env: Mapping[str, Any]) -> int:
    """Generate posts recorded on the envelope. Missing counts as a first try."""
    raw = env.get("model_calls")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, dict):
        total = raw.get("total")
        if isinstance(total, int) and not isinstance(total, bool):
            return total
    return 0


def _pin_mismatch(requested: str, served: str) -> bool:
    """True when both sides are set and they differ. A missing request is not a mismatch."""
    return bool(requested) and bool(served) and requested != served


def grade_case(
    *,
    gold: list[dict[str, Any]] | None,
    gold_sql: str | None,
    env: Mapping[str, Any],
    served_rows: list[dict[str, Any]],
    as_of: str,
    trap: bool = False,
    gold_broken: bool = False,
    model_sql: str | None = None,
) -> dict[str, Any]:
    """One case. Traps are refusals. Gold-broken is an abstain with no oracle.

    A served answer on a gold-broken case is wrong. ``gold is None`` without
    ``gold_broken`` is empty gold (no certified rows). ``model_sql`` is what
    Cortex's generate returned for this case; only an armed capture has it.
    """
    badge = env.get("badge")
    route = env.get("route")
    code = abstain_code(env)
    served_n = len(served_rows)
    base = {
        "route": route,
        "badge": badge,
        "serve_path": serve_path(env, model_sql),
        "served_route": served_route(env, model_sql),
        "stamp_contradiction": stamp_contradiction(env),
        "answered": not _is_abstain(env),
        "abstain_code": code,
        "as_of": as_of,
        "served_n": served_n,
        "model_calls": _model_call_count(env),
    }
    if _reconfirm_offer(env):
        return {**base, "bucket": BUCKET_RECONFIRM, "reason": "reconfirm", "answered": False}
    if env.get("reconfirm_accepted") is True and different_measure(
        gold_sql, str(env.get("sql_used") or "")
    ):
        return {**base, "bucket": BUCKET_WRONG, "reason": "wrong measure"}
    if trap:
        if _is_abstain(env):
            return {**base, "bucket": BUCKET_REFUSAL_OK, "reason": "correct refusal"}
        unit = "row" if served_n == 1 else "rows"
        return {
            **base,
            "bucket": BUCKET_REFUSAL_WRONG,
            "reason": f"served with {served_n} {unit}",
        }
    if gold_broken:
        if _is_abstain(env):
            return {**base, "bucket": BUCKET_GOLD_BROKEN, "reason": "no certified gold"}
        return {**base, "bucket": BUCKET_WRONG, "reason": "served on gold-broken"}
    if gold is None or gold == []:
        if _is_abstain(env):
            return {**base, "bucket": BUCKET_EMPTY, "reason": "abstain"}
        unit = "row" if served_n == 1 else "rows"
        return {
            **base,
            "bucket": BUCKET_EMPTY,
            "reason": f"served with {served_n} {unit}",
        }
    if _is_abstain(env):
        return {**base, "bucket": BUCKET_ABSTAIN, "reason": "abstain"}
    keys = order_keys(str(gold_sql)) if gold_sql else None
    bucket, reason = compare_rows(
        gold,
        served_rows,
        ordered=keys is not None,
        order_keys=keys,
    )
    if bucket == BUCKET_CORRECT:
        # Matching rows are not enough: the customer reads the envelope.
        # An answer whose envelope breaks E1-E13 (abstention prose under a
        # served badge, no audit_id, no sql_used, orphan figures) is WRONG.
        invalid = envelope_violation(env)
        if invalid:
            return {**base, "bucket": BUCKET_WRONG, "reason": f"envelope invalid: {invalid}"}
    return {**base, "bucket": bucket, "reason": reason}


def envelope_violation(env: Mapping[str, Any]) -> str:
    """First E-rule the served envelope breaks, or ``""`` when it is valid."""
    from dms_executor.envelope import assert_envelope_valid

    try:
        assert_envelope_valid(dict(env))
    except AssertionError as exc:
        return str(exc).split("\n", 1)[0][:160] or "assertion"
    return ""


def _certified_sql(oracle: Mapping[str, Any] | None) -> str | None:
    if not isinstance(oracle, Mapping):
        return None
    expect = str(oracle.get("expect") or "").strip().lower()
    if expect == "refuse":
        return None
    sql = oracle.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return None
    return sql


def _execute(con: Any, sql: str, as_of: str) -> list[dict[str, Any]]:
    from dms_executor.demo_warehouse import sql_has_reserved_as_of

    bind = {"as_of": as_of} if sql_has_reserved_as_of(sql) else None
    cur = con.execute(sql, bind) if bind else con.execute(sql)
    cols = [str(item[0]) for item in (cur.description or [])]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _open_warehouse(tmp: Path) -> tuple[Any, str, Path]:
    import duckdb
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = ensure_demo_warehouse(tmp / "warehouse.duckdb")
    con = duckdb.connect(str(path), read_only=True)
    row = con.execute("SELECT CAST(CURRENT_DATE AS VARCHAR)").fetchone()
    as_of = str(row[0]) if row and row[0] is not None else ""
    return con, as_of, path


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"bad yaml: {path}")
    return data


def _is_stub_row(rows: Sequence[Mapping[str, Any]]) -> bool:
    """The canned submit stub: one cell, column ``n``, value 1."""
    if len(rows) != 1:
        return False
    row = rows[0]
    if set(row) != {"n"}:
        return False
    value = row.get("n")
    return value == 1 or value == 1.0


def _is_submit_stub(envelopes: Sequence[Mapping[str, Any]]) -> bool:
    """True when every confident envelope is the one-cell submit stub."""
    confident = 0
    for item in envelopes:
        env = item.get("env")
        if not isinstance(env, Mapping) or _is_abstain(env):
            continue
        confident += 1
        rows = env.get("rows")
        if not isinstance(rows, list) or not _is_stub_row(rows):
            return False
    return confident > 0


def _commit_sha(raw: Any) -> str:
    if not isinstance(raw, str):
        raise SystemExit("grade52: envelopes commit missing")
    text = raw.strip()
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise SystemExit("grade52: envelopes commit missing")
    return text


def _layout_text(raw: Any) -> str:
    if not isinstance(raw, str):
        raise SystemExit("grade52: envelope layout missing")
    text = raw.strip()
    if not text or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in text):
        raise SystemExit("grade52: envelope layout missing")
    return text


def _run_text(raw: Any) -> str:
    """The capture's own label. Only armed or stub; anything else is refused."""
    if raw not in (RUN_ARMED, RUN_STUB):
        raise SystemExit("grade52: envelope mode must be armed or stub")
    return str(raw)


def _cortex_sha(raw: Any) -> str:
    text = raw.strip().lower() if isinstance(raw, str) else ""
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise SystemExit("grade52: cortex sha missing (40 hex)")
    return text


_HEADER_KEYS = ("dms_sha", "layout", "mode", "cortex_sha")


def _header_meta(data: Any) -> dict[str, str | None] | None:
    """A JSONL header is commit, layout, mode and Cortex sha, not a case.

    None when it is a case.
    """
    if not isinstance(data, dict):
        return None
    if any(key in data for key in ("env", "rows", "badge", "id")):
        return None
    if not any(key in data for key in _HEADER_KEYS):
        return None
    return {
        "dms_sha": _commit_sha(data.get("dms_sha")) if "dms_sha" in data else None,
        "layout": _layout_text(data.get("layout")) if "layout" in data else None,
        "mode": _run_text(data.get("mode")) if "mode" in data else None,
        "cortex_sha": _cortex_sha(data.get("cortex_sha")) if "cortex_sha" in data else None,
    }


def _record(data: Any, default_id: str | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SystemExit("grade52: envelope record is not an object")
    if isinstance(data.get("env"), dict):
        if "rows" not in data["env"]:
            raise SystemExit("grade52: envelope has no rows")
        qid = str(data.get("id") or default_id or "")
        record = {"id": qid, "env": data["env"]}
        if isinstance(data.get("served"), dict):
            # An armed capture's per-question record (route, model SQL, Insights reason).
            record["served"] = data["served"]
        return record
    if "rows" in data or "badge" in data:
        if "rows" not in data:
            raise SystemExit("grade52: envelope has no rows")
        qid = str(data.get("id") or default_id or "")
        env = {key: value for key, value in data.items() if key != "id"}
        return {"id": qid, "env": env}
    raise SystemExit("grade52: envelope record has no env")


def load_envelopes(path: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Directory of ``<id>.json``, or JSONL of ``{id, env}``.

    The commit is a JSONL header ``{"dms_sha": "<40 hex>", "layout": "copy-only"}``
    or ``dms_sha`` and ``layout`` files in the directory. A JSON array carries
    neither. ``load_envelopes_meta`` also returns ``mode`` and ``cortex_sha``.
    """
    rows, meta = load_envelopes_meta(path)
    return rows, meta["dms_sha"], meta["layout"]


def _empty_meta() -> dict[str, str | None]:
    return {key: None for key in _HEADER_KEYS}


def load_envelopes_meta(path: Path) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    """Records plus the header: ``dms_sha``, ``layout``, ``mode``, ``cortex_sha``."""
    meta = _empty_meta()
    if path.is_dir():
        files = sorted(item for item in path.iterdir() if item.suffix == ".json" and item.is_file())
        if not files:
            raise SystemExit("grade52: envelope directory is empty")
        rows: list[dict[str, Any]] = []
        for item in files:
            try:
                data = json.loads(item.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SystemExit("grade52: envelope file is not json") from exc
            rows.append(_record(data, default_id=item.stem))
        readers = {
            "dms_sha": _commit_sha,
            "layout": _layout_text,
            "mode": lambda raw: _run_text(raw.strip()),
            "cortex_sha": _cortex_sha,
        }
        for key, read in readers.items():
            item = path / key
            if item.is_file():
                meta[key] = read(item.read_text(encoding="utf-8"))
        return rows, meta
    if not path.is_file():
        raise SystemExit("grade52: envelopes path is missing")
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit("grade52: envelope file is not json") from exc
        if not isinstance(data, list):
            raise SystemExit("grade52: envelope file is not json")
        return [_record(item) for item in data], meta
    rows = []
    seen_header = False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit("grade52: envelope file is not json") from exc
        header = _header_meta(data)
        if header is not None:
            if seen_header:
                raise SystemExit("grade52: envelopes commit missing")
            seen_header = True
            meta = header
            continue
        rows.append(_record(data))
    if not rows:
        raise SystemExit("grade52: envelope file is empty")
    return rows, meta


def _sql_same(left: str, right: str) -> bool:
    from sqlglot import parse_one

    try:
        a = parse_one(left, read="duckdb").sql(dialect="duckdb")
        b = parse_one(right, read="duckdb").sql(dialect="duckdb")
    except Exception:
        return False
    return a == b


def pack_gold_served_ids(
    questions: Sequence[Mapping[str, Any]],
    oracles: Mapping[str, Any],
) -> list[str]:
    """Score-pack exact ids, plus an ops mirror that serves the same gold SQL.

    The mirror is the ``ops_`` rename of a ``cq_`` id. Synonym ids stay out.
    """
    from dms_executor.demo_pack import SCORE_PACK_EXACT_IDS

    known = {str(item["id"]) for item in questions}
    found: set[str] = set()
    for qid in SCORE_PACK_EXACT_IDS:
        if qid not in known:
            continue
        found.add(qid)
        if not qid.startswith("cq_"):
            continue
        mirror = "ops_" + qid[len("cq_") :]
        if mirror not in known:
            continue
        left = _certified_sql(oracles.get(qid) if isinstance(oracles, Mapping) else None)
        right = _certified_sql(oracles.get(mirror) if isinstance(oracles, Mapping) else None)
        if left and right and _sql_same(left, right):
            found.add(mirror)
    return sorted(found)


def tally(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every case lands in one bucket. The seven buckets add up to the case count."""
    counts = {key: 0 for key in _BUCKET_KEYS}
    abstained = 0
    served: dict[str, int] = {}
    bucket_of = {
        BUCKET_CORRECT: "correct",
        BUCKET_WRONG: "wrong",
        BUCKET_RECONFIRM: "reconfirm",
        BUCKET_ABSTAIN: "abstain",
        BUCKET_REFUSAL_OK: "refusal_ok",
        BUCKET_REFUSAL_WRONG: "refusal_wrong",
        BUCKET_EMPTY: "empty_gold",
        BUCKET_GOLD_BROKEN: "gold_broken",
    }
    for item in cases:
        key = bucket_of.get(str(item["bucket"]))
        if key is None:
            raise SystemExit(f"unknown bucket {item['bucket']}")
        counts[key] += 1
        if key != "empty_gold":
            continue
        if item.get("reason") == "abstain":
            abstained += 1
        else:
            cell = str(item.get("served_n", 0))
            served[cell] = served.get(cell, 0) + 1
    return {
        **counts,
        "empty_gold_abstained": abstained,
        "empty_gold_served": served,
        "graded": counts["correct"] + counts["wrong"] + counts["abstain"],
    }


def grade_envelopes(
    envelopes: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    warehouse_dir: Path | None = None,
) -> dict[str, Any]:
    """Grade pack order against certified oracles. ``envelopes`` are ``{id, env}``."""
    if mode not in MODES:
        raise SystemExit("grade52: --mode is required: " + ", ".join(MODES))
    _bootstrap()
    pack = _load_yaml(PACK_PATH)
    oracle_doc = _load_yaml(ORACLE_PATH).get("oracles") or {}
    oracles = oracle_doc if isinstance(oracle_doc, dict) else {}
    questions = list(pack["questions"])
    if len(questions) != 52:
        raise SystemExit(f"pack size {len(questions)} != 52")
    flagged = set(pack_gold_served_ids(questions, oracles))
    by_id: dict[str, Any] = {}
    model_sql_by_id: dict[str, str | None] = {}
    served_by_id: dict[str, Mapping[str, Any]] = {}
    for item in envelopes:
        key = str(item.get("id"))
        if key in by_id:
            raise SystemExit(f"grade52: duplicate envelope for {key}")
        by_id[key] = item.get("env")
        model_sql_by_id[key] = _record_model_sql(item)
        block = item.get("served")
        if isinstance(block, Mapping):
            served_by_id[key] = block
    folder = warehouse_dir or Path(tempfile.mkdtemp(prefix="grade52-"))
    con, as_of, _path = _open_warehouse(folder)
    cases: list[dict[str, Any]] = []
    try:
        for question in questions:
            qid = str(question["id"])
            env = by_id.get(qid)
            if not isinstance(env, Mapping):
                raise SystemExit(f"missing envelope for {qid}")
            oracle = oracles.get(qid)
            trap = _is_trap(question, oracle if isinstance(oracle, Mapping) else None)
            sql = None if trap else _certified_sql(oracle if isinstance(oracle, Mapping) else None)
            broken = sql is None and not trap
            if sql is None:
                gold: list[dict[str, Any]] | None = None
            else:
                gold = _execute(con, sql, as_of)
            served = _rows_of(env.get("rows"))
            row = grade_case(
                gold=gold,
                gold_sql=sql,
                env=env,
                served_rows=served,
                as_of=as_of,
                trap=trap,
                gold_broken=broken,
                model_sql=model_sql_by_id.get(qid),
            )
            row["id"] = qid
            row["pack_gold_served"] = qid in flagged
            block = served_by_id.get(qid) or {}
            row["model_requested"] = _stamp_text(block, "model_requested") or _stamp_text(
                env, "model_requested"
            )
            row["served_model"] = _stamp_text(block, "served_model") or _stamp_text(
                env, "served_model"
            )
            row["pin_mismatch"] = _pin_mismatch(row["model_requested"], row["served_model"])
            cases.append(row)
    finally:
        con.close()
    counts = tally(cases)
    if _accounted(counts) != len(cases):
        raise SystemExit("grade52: buckets do not add up to n")
    return {
        "as_of": as_of,
        "n": len(cases),
        "pack_gold_served_ids": sorted(flagged),
        **counts,
        "cases": cases,
    }


def _artifact_dir() -> Path:
    root = os.environ.get("RUNNER_TEMP") or os.environ.get("TMPDIR") or tempfile.gettempdir()
    path = Path(root) / "grade52"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_report(report: dict[str, Any], name: str = "grade52.json") -> Path:
    """Write the run artifact. Caller prints the path. No SQL, no question text."""
    _require_label(report)
    dest = _artifact_dir() / name
    slim_cases = []
    for item in report["cases"]:
        reason = ""
        for row in not_scored_entries([item]):
            reason = row["reason"]
        slim_cases.append(
            {
                "id": item["id"],
                "bucket": item["bucket"],
                "reason": item["reason"],
                "route": item["route"],
                "serve_path": item.get("serve_path"),
                "served_route": item.get("served_route"),
                "badge": item["badge"],
                "abstain_code": item["abstain_code"],
                "as_of": item["as_of"],
                "pack_gold_served": bool(item.get("pack_gold_served")),
                "served_n": item.get("served_n"),
                "not_scored": reason or None,
                **({"runs": item["runs"]} if "runs" in item else {}),
            }
        )
    without = dict(report["without_pack_gold_served"])
    without["wrong"] = headline_wrong(without)
    body = {
        "mode": report["mode"],
        "run": run_label(report),
        "dms_sha": report["dms_sha"],
        "cortex_sha": report.get("cortex_sha"),
        "split": route_split(report["cases"]),
        "pack_gold_served": report["pack_gold_served"],
        "pack_gold_served_ids": report["pack_gold_served_ids"],
        "as_of": report["as_of"],
        "n": report["n"],
        "graded": report["graded"],
        "correct": report["correct"],
        "wrong": headline_wrong(report),
        "reconfirm": report["reconfirm"],
        "abstain": report["abstain"],
        "refusal_ok": report["refusal_ok"],
        "refusal_wrong": report["refusal_wrong"],
        "empty_gold": report["empty_gold"],
        "gold_broken": report["gold_broken"],
        "paths_correct": report.get("paths_correct"),
        "paths_served": report.get("paths_served"),
        "layout": report.get("layout"),
        "empty_gold_abstained": report["empty_gold_abstained"],
        "empty_gold_served": report["empty_gold_served"],
        "score": True,
        "grader_version": GRADER_VERSION,
        "tries": tries_line(report["cases"]),
        "not_scored": not_scored_entries(report["cases"]),
        "risk": risk_bounds(report["cases"]),
        "runs": int(report.get("runs") or 1),
        "unstable": int(report.get("unstable") or 0),
        "baseline": report.get("baseline"),
        "without_pack_gold_served": without,
        "cases": slim_cases,
    }
    for key in (
        "ontology_version_id",
        "approved_counts",
        "approver_actor_id",
        "ov_calls_by_caller_role",
    ):
        if report.get(key) is not None:
            body[key] = report[key]
    dest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def _served_label(served: Mapping[str, int]) -> str:
    if not served:
        return "none"
    parts = [f"{key}:{served[key]}" for key in sorted(served, key=int)]
    return ",".join(parts)


def _require_label(report: Mapping[str, Any]) -> None:
    mode = report.get("mode")
    sha = report.get("dms_sha")
    flag = report.get("pack_gold_served")
    if mode not in MODES or not isinstance(sha, str) or len(sha) < 40 or flag not in {
        "included",
        "excluded",
    }:
        raise SystemExit("grade52: output requires mode, sha, and pack_gold_served")


def _bucket_count(report: Mapping[str, Any], key: str) -> int:
    if key not in report:
        if key == "reconfirm":
            return 0
        raise SystemExit("grade52: output requires mode, sha, and pack_gold_served")
    return int(report[key])


def _accounted(report: Mapping[str, Any]) -> int:
    return sum(_bucket_count(report, key) for key in _BUCKET_KEYS)


def run_label(report: Mapping[str, Any]) -> str:
    """armed only for an armed grade with a Cortex sha. Everything else is stub."""
    if report.get("mode") == MODE_ARMED and isinstance(report.get("cortex_sha"), str):
        return RUN_ARMED
    return RUN_STUB


def header_line(report: Mapping[str, Any]) -> str:
    """First output line: armed or stub, the product sha, the Cortex sha."""
    _require_label(report)
    cortex = report.get("cortex_sha") if run_label(report) == RUN_ARMED else None
    return (
        f"header mode={run_label(report)} product_sha={report['dms_sha']} "
        f"cortex_sha={cortex or 'none'}"
    )


def route_split(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Every case in one of ai, rule, unattributed, abstain. Adds up to n."""
    counts = {key: 0 for key in SERVED_ROUTES}
    for item in cases:
        route = str(item.get("served_route") or "")
        if route not in counts:
            raise SystemExit(f"grade52: case {item.get('id')} has no served route")
        counts[route] += 1
    return counts


def abstain_part_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Buckets among cases whose served route is abstain. They add up to that count."""
    counts = {key: 0 for key in _ABSTAIN_PARTS}
    for item in cases:
        if str(item.get("served_route") or "") != "abstain":
            continue
        name = _ABSTAIN_PART_BUCKETS.get(str(item.get("bucket") or ""))
        counts[name or "other"] += 1
    return counts


def metric_line(report: Mapping[str, Any]) -> str:
    """AI share among correct answers, and the wrong count beside it.

    ``ai_share`` is ``ai/correct``. ``0/0`` means nothing was correct, which
    is not a share. Wrong is the served-wrong bucket and must stay 0.
    """
    rolls = path_counts(report["cases"])
    ai = int(rolls["correct"]["ai"])
    correct = int(report["correct"])
    wrong = int(report["wrong"])
    return f"metric ai_share={ai}/{correct} wrong={wrong}"


def split_line(report: Mapping[str, Any]) -> str:
    counts = route_split(report["cases"])
    parts = " ".join(f"{key}={counts[key]}" for key in SERVED_ROUTES)
    breakdown = abstain_part_counts(report["cases"])
    shown = [key for key in _ABSTAIN_PARTS if breakdown[key] or key != "other"]
    tail = " ".join(f"{key}={breakdown[key]}" for key in shown)
    return f"split {parts} n={len(report['cases'])} abstain_parts {tail}"


def tries_line(cases: Sequence[Mapping[str, Any]]) -> str:
    """Correct on the first generate, and correct only after a retry.

    A correct answer whose served model is not the requested pin is omitted.
    ``correct_first + correct_retry`` is less than ``correct`` by that count.
    """
    first = 0
    retry = 0
    omitted = 0
    for item in cases:
        if item.get("bucket") != BUCKET_CORRECT:
            continue
        if item.get("pin_mismatch"):
            omitted += 1
            continue
        if int(item.get("model_calls") or 0) > 1:
            retry += 1
        else:
            first += 1
    line = f"tries correct_first={first} correct_retry={retry} omitted_pin_mismatch={omitted}"
    if omitted:
        line += " reason=pin_mismatch_not_in_tries"
    return line


def not_scored_entries(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Per question the tries line left out, with the pin that did not match."""
    rows: list[dict[str, str]] = []
    for item in cases:
        if not item.get("pin_mismatch"):
            continue
        requested = str(item.get("model_requested") or "missing")
        served = str(item.get("served_model") or "missing")
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "reason": f"pin_mismatch:{requested}/{served}",
            }
        )
    return rows


def not_scored_line(cases: Sequence[Mapping[str, Any]]) -> str:
    """Served model differs from the requested pin. Not in the tries counts.

    The count is followed by each question id and its pin_mismatch reason.
    """
    rows = not_scored_entries(cases)
    head = f"not_scored pin_mismatch={len(rows)}"
    if not rows:
        return head
    shown = " ".join(
        f"{row['id']}={row['reason']}" if row["id"] else row["reason"] for row in rows
    )
    return f"{head} {shown}"


def headline_wrong(report: Mapping[str, Any]) -> int:
    """Wrong served answers. A trap that was served counts. The WRONG=0 bar reads this."""
    return _bucket_count(report, "wrong") + _bucket_count(report, "refusal_wrong")


def summary_line(report: Mapping[str, Any]) -> str:
    """One score line. The buckets are disjoint and add up to n.

    Printed ``wrong`` is bucket wrong plus refusal_wrong, so a served trap
    is not a zero on the WRONG=0 bar. ``refusal_wrong`` stays on the line.
    """
    _require_label(report)
    counts = {key: _bucket_count(report, key) for key in _BUCKET_KEYS}
    if sum(counts.values()) != int(report["n"]):
        raise SystemExit("grade52: buckets do not add up to n")
    layout = report.get("layout")
    head = f"sha={report['dms_sha']}"
    if isinstance(layout, str) and layout:
        head += f" layout={layout}"
    shown_wrong = counts["wrong"] + counts["refusal_wrong"]
    return (
        f"{head} pack_gold_served={report['pack_gold_served']} "
        f"{report['n']}: correct={counts['correct']} wrong={shown_wrong} "
        f"reconfirm={counts['reconfirm']} abstain={counts['abstain']} "
        f"refusal_ok={counts['refusal_ok']} "
        f"refusal_wrong={counts['refusal_wrong']} empty_gold={counts['empty_gold']} "
        f"gold_broken={counts['gold_broken']} mode={report['mode']}"
    )


def _zero_paths() -> dict[str, int]:
    return {"ai": 0, "rule": 0, "unattributed": 0}


def path_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """AI vs rule-served vs unattributed, for correct answers and served answers.

    Rule counts are also split by path (L0, L1, compile, oracle) so a correct
    set can be read off the line.
    """
    correct = _zero_paths()
    served = _zero_paths()
    contradictions = 0
    detail = {
        "correct": {key: 0 for key in _PATH_DETAIL},
        "served": {key: 0 for key in _PATH_DETAIL},
    }
    for item in cases:
        path = str(item.get("serve_path") or "unattributed")
        group = path_group(path)
        if item.get("bucket") == BUCKET_CORRECT:
            correct[group] += 1
            if path in detail["correct"]:
                detail["correct"][path] += 1
        if item.get("answered") is True:
            served[group] += 1
            if path in detail["served"]:
                detail["served"][path] += 1
        if item.get("stamp_contradiction"):
            contradictions += 1
    return {
        "correct": correct,
        "served": served,
        "detail": detail,
        "stamp_contradiction": contradictions,
    }


def wrong_rate_upper(wrong: int, served: int, confidence: float = CONFIDENCE) -> float | None:
    """One-sided exact (Clopper-Pearson) upper bound on the wrong-served rate.

    0 wrong in n served gives ``1 - 0.05 ** (1 / n)``, a little under the rule
    of three's 3/n. None when nothing was served: no answers, no claim.
    """
    if served <= 0:
        return None
    if wrong >= served:
        return 1.0
    alpha = 1.0 - confidence
    if wrong == 0:
        return 1.0 - alpha ** (1.0 / served)
    low, high = wrong / served, 1.0
    for _ in range(200):
        mid = (low + high) / 2
        if _binom_cdf(wrong, served, mid) > alpha:
            low = mid
        else:
            high = mid
    return high


def _binom_cdf(k: int, n: int, p: float) -> float:
    log_p, log_q = math.log(p), math.log1p(-p)
    head = math.lgamma(n + 1)
    return sum(
        math.exp(head - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q)
        for i in range(k + 1)
    )


def risk_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Served answers with gold, and how many were wrong, per route group.

    A trap that was answered is a wrong served answer. Abstains, correct
    refusals, empty gold and gold-broken abstains were not served, so they
    never shrink the bound.
    """
    out = {group: {"served": 0, "wrong": 0} for group in _RISK_GROUPS}
    for item in cases:
        bucket = item.get("bucket")
        if bucket not in _SERVED_GRADED:
            continue
        group = path_group(str(item.get("serve_path") or "unattributed"))
        for key in ("all", group):
            out[key]["served"] += 1
            if bucket != BUCKET_CORRECT:
                out[key]["wrong"] += 1
    return out


def risk_bounds(cases: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        group: {**block, "wrong_ub95": wrong_rate_upper(block["wrong"], block["served"])}
        for group, block in risk_counts(cases).items()
    }


def _risk_side(block: Mapping[str, Any]) -> str:
    bound = block["wrong_ub95"]
    shown = "none" if bound is None else f"{bound * 100:.1f}%"
    return f"served={block['served']} wrong={block['wrong']} wrong_ub95={shown}"


def risk_line(report: Mapping[str, Any]) -> str:
    """The claim a score supports: wrong-rate bound per route, and coverage.

    Coverage is served over graded (correct, wrong and abstain on gold cases).
    """
    bounds = risk_bounds(report["cases"])
    answered = int(report["correct"]) + int(report["wrong"])
    parts = [f"risk {_risk_side(bounds['all'])} coverage={answered}/{int(report['graded'])}"]
    parts += [f"{group} {_risk_side(bounds[group])}" for group in _RISK_GROUPS[1:]]
    return " | ".join(parts)


def _path_side(block: Mapping[str, int], detail: Mapping[str, int]) -> str:
    parts = [f"ai={block['ai']}", f"rule={block['rule']}"]
    for key in _PATH_DETAIL:
        count = int(detail.get(key) or 0)
        if count:
            parts.append(f"{key}={count}")
    parts.append(f"unattributed={block['unattributed']}")
    return " ".join(parts)


def paths_line(counts: Mapping[str, Any]) -> str:
    detail = counts.get("detail") or {}
    correct_detail = detail.get("correct") if isinstance(detail, Mapping) else {}
    served_detail = detail.get("served") if isinstance(detail, Mapping) else {}
    if not isinstance(correct_detail, Mapping):
        correct_detail = {}
    if not isinstance(served_detail, Mapping):
        served_detail = {}
    return (
        "paths correct "
        + _path_side(counts["correct"], correct_detail)
        + " served "
        + _path_side(counts["served"], served_detail)
        + f" stamp_contradiction={int(counts.get('stamp_contradiction') or 0)}"
    )


def _record_model_sql(item: Mapping[str, Any]) -> str | None:
    """The model SQL an armed capture recorded for one case, or None."""
    served = item.get("served") if isinstance(item, Mapping) else None
    raw = served.get("model_sql") if isinstance(served, Mapping) else None
    return raw if isinstance(raw, str) and raw.strip() else None


def served_paths_line(envelopes: Sequence[Mapping[str, Any]]) -> str:
    """Served-answer paths when the pack is not a value score."""
    counts = _zero_paths()
    for item in envelopes:
        env = item.get("env") if isinstance(item, Mapping) else None
        if not isinstance(env, Mapping) or _is_abstain(env):
            continue
        counts[path_group(serve_path(env, _record_model_sql(item)))] += 1
    return (
        f"paths served ai={counts['ai']} rule={counts['rule']} "
        f"unattributed={counts['unattributed']}"
    )


def _scrub_credentials() -> None:
    """Drop credential-shaped and model-shaped env names. No vendor list."""
    for name in list(os.environ):
        upper = name.upper()
        if (
            upper == "MODEL"
            or upper.startswith("MODEL_")
            or upper.endswith("_MODEL")
            or "_MODEL_" in upper
            or any(upper.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES)
        ):
            os.environ.pop(name, None)


def _prepare_flag_off() -> None:
    _bootstrap()
    os.environ.pop("DMS_ASK_CLARIFY", None)
    os.environ.pop("DMS_CLOOP_B", None)
    os.environ.pop("DMS_AI_FIRST", None)
    os.environ["DMS_DEMO_FALLBACK"] = "0"
    _scrub_credentials()


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def product_sha() -> str:
    """Commit of the tree being graded: ``git rev-parse HEAD`` of the checkout.

    The product code that runs is this checkout's, so its HEAD is the only
    honest stamp (a merge-base names main even when a PR changed the product).
    Fails closed when apps/ or packages/ carry uncommitted changes, because
    then no commit names the code that ran.
    """
    head = _git(["rev-parse", "HEAD"]).stdout.strip()
    if len(head) != 40 or any(char not in "0123456789abcdef" for char in head):
        raise SystemExit("grade52: dms sha unavailable (not a git checkout)")
    dirty = _git(["status", "--porcelain", "--", "apps", "packages"]).stdout.strip()
    if dirty:
        raise SystemExit(
            "grade52: apps/ or packages/ have uncommitted changes; commit first so "
            "the stamp names the graded code"
        )
    return head


def _resolve_mode(mode: str | None) -> str:
    """External envelopes are ``served``. The in-repo proof is ``stub-exec``."""
    if mode is None:
        return MODE_SERVED
    if mode not in MODES:
        raise SystemExit("grade52: --mode is required: " + ", ".join(MODES))
    return str(mode)


def replay_executing(*, ai_first: bool = False) -> list[dict[str, Any]]:
    """Same capture, but submit executes the SQL it is given.

    ``ai_first`` sets ``DMS_AI_FIRST`` after the flag-off scrub. The default
    stays off, which is the number of record. The flag is cleared before
    return so a later in-process grade stays off.
    """
    _prepare_flag_off()
    if ai_first:
        os.environ["DMS_AI_FIRST"] = "1"
    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from cortex_contract.execution import Manifest, QueryResult
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter
    from fastapi.testclient import TestClient
    from tests.fixtures.ask_guide.capture_flag_off_52 import (
        SESSION_ID,
        load_responses,
    )

    recorded = load_responses()
    by_id = recorded["by_id"]
    bind = recorded["session_bind"]
    pack = _load_yaml(PACK_PATH)
    questions = list(pack["questions"])
    spaces = dict(pack["spaces"])
    db = Path(tempfile.mkdtemp(prefix="grade52-exec-")) / "warehouse.duckdb"
    ensure_demo_warehouse(db)

    class _Stub:
        def __init__(self) -> None:
            self.current = ""

        def _row(self) -> dict[str, Any]:
            return by_id[self.current]

        def submit(self, req: Any) -> QueryResult:
            plan = getattr(req, "plan", None)
            kind = plan.get("kind") if isinstance(plan, dict) else None
            if kind == "session_bind":
                return QueryResult(
                    ok=bool(bind["ok"]),
                    status=str(bind["status"]),
                    run_id=str(bind["run_id"]),
                )
            body = getattr(req, "body", None)
            sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
            con = connect_file(db)
            try:
                cur = con.execute(sql)
                cols = [str(item[0]) for item in (cur.description or [])]
                rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            finally:
                con.close()
            return QueryResult(ok=True, status="ok", run_id="run_exec", output={"rows": rows})

        def ask(self, req: AskRequest) -> AskResponse:
            body = self._row()["ask"]
            _ = req
            return AskResponse(
                answer=str(body["answer"]),
                badge=str(body["badge"]),
                sql_used=str(body["sql_used"]),
                rows=list(body["rows"]),
                assumptions=body["assumptions"],
                audit_id=str(body["audit_id"]),
                route=str(body["route"]),
            )

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            body = self._row()["ledger"]
            return LedgerAppendResponse(
                entry_id=str(body["entry_id"]),
                hash=str(body["hash"]),
            )

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            _ = question
            return dict(self._row()["insights"])

    minter = ManifestMinter(openvault_url="http://127.0.0.1:9")

    def _mint(acl: Any) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    cortex = _Stub()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    rows: list[dict[str, Any]] = []
    for question in questions:
        cortex.current = str(question["id"])
        res = client.post(
            "/v1/chat/ask",
            json={
                "question": question["question"],
                "space_id": spaces[str(question["space"])],
                "session_id": SESSION_ID,
            },
        )
        if res.status_code != 200:
            raise SystemExit(f"{question['id']} HTTP {res.status_code}")
        body = res.json()
        if not isinstance(body, dict):
            raise SystemExit(f"{question['id']} envelope is not an object")
        rows.append({"id": question["id"], "env": body})
    if len(rows) != 52:
        raise SystemExit(f"replay size {len(rows)} != 52")
    if ai_first:
        os.environ.pop("DMS_AI_FIRST", None)
    return rows


# Armed: this checkout's POST /v1/chat/ask against a live Cortex. Never falls
# back. Every refusal and failure is a named SystemExit before a score exists.


def _refuse(code: str, hint: str) -> SystemExit:
    """Run-level refusal. Callers pass names, codes and redacted text, never a key."""
    return SystemExit(f"grade52 armed: REFUSED {code}: {hint}")


def _fail(qid: str, reason: str) -> SystemExit:
    """Question-level failure. The run stops at the first one."""
    return SystemExit(f"grade52 armed: FAIL {qid}: {reason}")


@dataclass(frozen=True)
class ArmedConfig:
    cortex_url: str
    api_key: str = field(repr=False)
    cortex_sha: str
    openvault_url: str


def public_url(url: str) -> str:
    """``scheme://host[:port]`` only. Userinfo, path and query never reach output."""
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname or ''}{port}"


def armed_config(env: Mapping[str, str] | None = None) -> ArmedConfig:
    """Read the armed settings. No network. A missing or unusable one is refused by name.

    The key check is the product's own generate gate: empty, the demo viewer
    key, or plain http to a non-loopback host never generates.
    """
    _bootstrap()
    from cortex_client.insights import generate_bearer_refuse

    source = os.environ if env is None else env
    base = str(source.get(ARMED_CORTEX_ENV) or "").strip().rstrip("/")
    if not base:
        raise _refuse("cortex_base_url_unset", f"set {ARMED_CORTEX_ENV} to the Cortex under test")
    key = source.get(ARMED_KEY_ENV)
    refused = generate_bearer_refuse(key, base)
    if refused or key is None:
        raise _refuse(
            refused or "insights_bearer_missing",
            f"{ARMED_KEY_ENV} must be an OpenVault-issued key (not empty, not the demo "
            "viewer key) sent over https or to loopback",
        )
    raw_sha = source.get(ARMED_SHA_ENV)
    text = raw_sha.strip().lower() if isinstance(raw_sha, str) else ""
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise _refuse(
            "cortex_sha_unset",
            f"set {ARMED_SHA_ENV} to the 40-hex commit of the Cortex under test "
            "(Cortex does not publish it over HTTP)",
        )
    openvault = str(source.get(ARMED_OPENVAULT_ENV) or ARMED_OPENVAULT_DEFAULT)
    return ArmedConfig(
        cortex_url=base,
        api_key=key.strip(),
        cortex_sha=text,
        openvault_url=openvault.strip().rstrip("/"),
    )


def armed_preflight(cfg: ArmedConfig) -> dict[str, Any]:
    """Cortex answers ``/health`` and its own key posture says FreeRoute is armed.

    ``GET /v1/insights/keys`` is Cortex's FreeRoute arming snapshot from
    OpenVault (``armed``, ``reason``). DMS holds no provider key, so Cortex's
    word is the only arming fact there is.
    """
    import httpx
    from cortex_client.insights import auth_headers, redact_secrets

    where = public_url(cfg.cortex_url)
    try:
        with httpx.Client(timeout=ARMED_PREFLIGHT_TIMEOUT_S) as http:
            health = http.get(f"{cfg.cortex_url}/health")
            if health.status_code != 200:
                raise _refuse("cortex_unreachable", f"{where}/health HTTP {health.status_code}")
            keys = http.get(
                f"{cfg.cortex_url}/v1/insights/keys", headers=auth_headers(cfg.api_key)
            )
    except httpx.HTTPError as exc:
        raise _refuse("cortex_unreachable", f"{where}: {type(exc).__name__}") from None
    try:
        posture = keys.json()
    except ValueError:
        posture = None
    if keys.status_code != 200 or not isinstance(posture, dict):
        raise _refuse("freeroute_unverified", f"{where}/v1/insights/keys HTTP {keys.status_code}")
    if posture.get("armed") is not True:
        reason = redact_secrets(str(posture.get("reason") or "no reason given"), cfg.api_key)
        raise _refuse("freeroute_unarmed", f"Cortex reports FreeRoute unarmed: {reason[:200]}")
    return {"spendable_hops": posture.get("spendable_hops")}


def _armed_minter(cfg: ArmedConfig) -> Any:
    """The product's ManifestMinter, proven by one intermediate-key fetch."""
    from cortex_client.insights import redact_secrets
    from dms_executor.manifest import ManifestMinter

    minter = ManifestMinter(openvault_url=cfg.openvault_url)
    try:
        minter.fetch_intermediate()
    except Exception as exc:  # noqa: BLE001 - every mint failure is a named refusal
        minter.close()
        detail = redact_secrets(str(exc), cfg.api_key)[:200] or type(exc).__name__
        raise _refuse(
            "openvault_mint_failed", f"{public_url(cfg.openvault_url)}: {detail}"
        ) from None
    return minter


@contextmanager
def _env_value(name: str, value: str) -> Iterator[None]:
    before = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before


@contextmanager
def pack_layout(layout: str) -> Iterator[None]:
    """copy-only hides the curated pack, which is what the API image holds.

    The Dockerfile does not COPY ``tests/``. full-tree leaves it in place.
    """
    if layout not in LAYOUTS:
        raise SystemExit("grade52: layout must be one of " + ", ".join(LAYOUTS))
    if layout != "copy-only":
        yield
        return
    hidden = PACK_DIR.with_name("curated_ceo.hide_capture")
    if hidden.exists():
        raise SystemExit("grade52: capture hide path is already present")
    PACK_DIR.rename(hidden)
    try:
        yield
    finally:
        if hidden.exists() and not PACK_DIR.exists():
            hidden.rename(PACK_DIR)


@dataclass(frozen=True)
class StubSignatures:
    """Values the recorded stub Cortex serves, read from its fixture."""

    texts: frozenset[str]
    ids: frozenset[str]
    sql: frozenset[str]


def _canon_sql(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    from sqlglot import parse_one

    try:
        return parse_one(text, read="duckdb").sql(dialect="duckdb")
    except Exception:  # noqa: BLE001 - an unparsable statement compares as written
        return text


def stub_signatures(path: Path = STUB_RESPONSES_PATH) -> StubSignatures:
    """Answer texts, ids and SQL from the stub fixture. Never a typed list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = data.get("by_id") if isinstance(data, dict) else None
    if not isinstance(by_id, dict):
        raise SystemExit(f"grade52: stub responses unreadable: {path.name}")
    texts: set[str] = set()
    ids: set[str] = set()
    sql: set[str] = set()
    bind = data.get("session_bind")
    if isinstance(bind, dict):
        ids.add(str(bind.get("run_id") or ""))
    def _block(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = row.get(key)
        return value if isinstance(value, Mapping) else {}

    for row in by_id.values():
        if not isinstance(row, dict):
            continue
        ask = _block(row, "ask")
        ledger = _block(row, "ledger")
        submit = _block(row, "submit")
        texts.add(str(ask.get("answer") or "").strip())
        ids.update(
            str(value or "")
            for value in (
                ask.get("audit_id"),
                ledger.get("entry_id"),
                ledger.get("hash"),
                submit.get("run_id"),
            )
        )
        canon = _canon_sql(ask.get("sql_used"))
        if canon:
            sql.add(canon)
    texts.discard("")
    ids.discard("")
    return StubSignatures(frozenset(texts), frozenset(ids), frozenset(sql))


def _arming_fail(reason: str) -> bool:
    """An Insights reason that means no model could run: unarmed, unauthorized, no bearer, pin."""
    from cortex_client.compute import INSIGHTS_FAIL_UNARMED, INSIGHTS_FAIL_UNAUTHORIZED
    from cortex_client.insights import (
        INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
        INSIGHTS_FAIL_BEARER_MISSING,
    )

    named = {
        INSIGHTS_FAIL_UNARMED,
        INSIGHTS_FAIL_UNAUTHORIZED,
        INSIGHTS_FAIL_BEARER_MISSING,
        INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
    }
    return reason in named or reason.startswith(("pin_caller_error:", "pin_unavailable:"))


def armed_violation(record: Mapping[str, Any], stub: StubSignatures) -> str | None:
    """Why an armed record is not a live answer, or None. Names the field, not the value."""
    env = record.get("env")
    if not isinstance(env, Mapping):
        return "no envelope"
    served = record.get("served")
    if not isinstance(served, Mapping):
        return "no served record; not an armed capture"
    if env.get("demo_fallback_used") is True or str(env.get("ask_mode") or "") == "demo":
        return "demo_fallback: demo numbers are not a live answer"
    for key in ("text", "answer"):
        if str(env.get(key) or "").strip() in stub.texts:
            return f"placeholder: {key} is a recorded stub answer"
    if str(env.get("audit_id") or "") in stub.ids:
        return "placeholder: audit_id is a recorded stub id"
    if _canon_sql(env.get("sql_used")) in stub.sql:
        return "placeholder: sql_used is the recorded stub SQL"
    if _canon_sql(served.get("model_sql")) in stub.sql:
        return "placeholder: model SQL is the recorded stub SQL"
    fail = str(served.get("insights_fail") or "")
    if fail and _arming_fail(fail):
        return f"freeroute_unarmed: Insights answered {fail}"
    return None


def armed_check(records: Sequence[Mapping[str, Any]]) -> None:
    """Refuse an armed grade holding any non-live answer, or one with no ai answer.

    A run where no answer was served from the model's own SQL (``split ai=0``)
    did not measure the AI lane, whatever its other numbers say. A model that
    was called but whose SQL DMS replaced or refused does not count.
    """
    _bootstrap()
    stub = stub_signatures()
    ai = 0
    for record in records:
        why = armed_violation(record, stub)
        if why:
            raise _fail(str(record.get("id")), why)
        ai += served_route(record["env"], _record_model_sql(record)) == "ai"
    if ai == 0:
        raise SystemExit(
            "grade52 armed: FAIL ai_lane_not_reached: no answer was served from model SQL "
            "(split ai=0); the AI lane was not measured"
        )


def _model_reported(payload: Mapping[str, Any]) -> bool:
    """Cortex stamped a served model on this generate answer or one of its legs.

    A model was called. It says nothing about whether its SQL was served.
    """
    if str(payload.get("served_model") or "").strip():
        return True
    legs = payload.get("generate_legs")
    items = legs.get("legs") if isinstance(legs, Mapping) else None
    return isinstance(items, list) and any(
        isinstance(leg, Mapping) and str(leg.get("served_model") or "").strip() for leg in items
    )


def _served_block(
    env: Mapping[str, Any],
    payloads: Sequence[Any],
    *,
    model_requested: str = "",
) -> dict[str, Any]:
    """Per question: served route, model SQL, rows, answer text, Insights reason."""
    from cortex_client.compute import insights_fail_reason, insights_query_sql

    model_sql: str | None = None
    fail: str | None = None
    calls = 0
    reported = False
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        calls += 1
        reported = reported or _model_reported(payload)
        sql = str(payload.get("query_sql") or "").strip() or insights_query_sql(payload)
        if sql:
            model_sql = sql
        fail = fail or insights_fail_reason(payload)
    rows = env.get("rows")
    return {
        "route": served_route(env, model_sql),
        "serve_path": serve_path(env, model_sql),
        "model_sql": model_sql,
        "sql_used": env.get("sql_used"),
        "row_count": len(rows) if isinstance(rows, list) else None,
        "text": env.get("text"),
        "served_model": env.get("served_model"),
        "model_requested": model_requested,
        "insights_calls": calls,
        "model_reported": reported,
        "insights_fail": fail,
    }


def _detail_code(res: Any, api_key: str) -> str:
    from cortex_client.insights import redact_secrets

    try:
        body = res.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else detail
    return redact_secrets(str(code or ""), api_key)[:120]


def _pace_int(name: str, default: int, ceiling: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except ValueError:
            value = default
    if value < 1:
        value = default
    return min(value, ceiling)


def pace_plan(
    req_per_min: int | None = None,
    tok_per_min: int | None = None,
    reserve: int | None = None,
) -> tuple[int, float]:
    """Requests per minute, and seconds between them. Both stay under the OV caps.

    Defaults are 18 req/min and 36000 tok/min with a 2000-token reserve, so the
    gap is 60/18 seconds. A requested rate at or above the cap is clamped under it.
    """
    if req_per_min is None:
        req = _pace_int("DMS_GRADE52_REQ_PER_MIN", 18, _OV_REQ_PER_MIN - 1)
    else:
        req = min(max(1, req_per_min), _OV_REQ_PER_MIN - 1)
    if tok_per_min is None:
        tok = _pace_int("DMS_GRADE52_TOK_PER_MIN", 36_000, _OV_TOK_PER_MIN - 1)
    else:
        tok = min(max(1, tok_per_min), _OV_TOK_PER_MIN - 1)
    if reserve is None:
        reserve = _pace_int("DMS_GRADE52_TOKEN_RESERVE", 2000, tok)
    else:
        reserve = min(max(1, reserve), tok)
    per_min = max(1, min(req, max(1, tok // reserve)))
    return per_min, 60.0 / per_min


def pace_backoff_s(attempt: int) -> float:
    """429 wait. ``attempt`` is 1-based. Doubles from 2s and stops at 60s."""
    step = attempt if attempt >= 1 else 1
    return min(60.0, 2.0 * (2 ** (step - 1)))


class _ArmedPacer:
    """One armed run, one gap. Sequential so two asks cannot share a minute slot."""

    def __init__(self) -> None:
        self.per_min, self.interval = pace_plan()
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        # The OV cap is the live armed run. Pytest's fake Cortex is not that cap,
        # and a 3s gap on every fake request does not fit the CI job.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                time.sleep(delay)
            self._next = time.monotonic() + self.interval


def _install_pacer(pacer: _ArmedPacer) -> Any:
    """Pace every httpx client request for this armed run. Caller restores it."""
    import httpx

    original = httpx.Client.request

    def request(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            pacer.wait()
            response = original(self, method, url, *args, **kwargs)
            if getattr(response, "status_code", None) != 429 or attempt >= 4:
                return response
            attempt += 1
            time.sleep(pace_backoff_s(attempt))

    httpx.Client.request = request  # type: ignore[method-assign]
    return original


def _armed_ask(
    cfg: ArmedConfig,
    minter: Any,
    questions: Sequence[Mapping[str, Any]],
    spaces: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Same app and loop as the stub, with the product CortexClient and minter.

    Each answer is checked as it arrives, so a broken Cortex stops the run at
    the first question that shows it.
    """
    stub = stub_signatures()
    from cortex_client import CortexClient
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from fastapi.testclient import TestClient

    class _Observed(CortexClient):
        """The product client. Keeps each Insights payload for the armed checks."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.payloads: list[Any] = []

        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
            got = super().compute_insights(question, **kwargs)
            self.payloads.append(got)
            return got

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        cortex_url=cfg.cortex_url,
        cortex_api_key=cfg.api_key,
        openvault_url=cfg.openvault_url,
    )
    cortex = _Observed(
        cfg.cortex_url, timeout=settings.cortex_timeout_seconds, api_key=cfg.api_key
    )
    db = Path(tempfile.mkdtemp(prefix="grade52-armed-")) / "warehouse.duckdb"
    ensure_demo_warehouse(db)
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=db)
    app.state.cortex = cortex
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    records: list[dict[str, Any]] = []
    from cortex_client.strict_pin import pin_config

    requested_model = pin_config().model
    pacer = _ArmedPacer()
    original_request = _install_pacer(pacer)
    try:
        for question in questions:
            qid = str(question["id"])
            cortex.payloads = []
            try:
                res = client.post(
                    "/v1/chat/ask",
                    json={
                        "question": question["question"],
                        "space_id": spaces[str(question["space"])],
                        "session_id": ARMED_SESSION_ID,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - a crash is a named failure, not a score
                raise _fail(qid, f"ask raised {type(exc).__name__}") from None
            if res.status_code != 200:
                raise _fail(qid, f"HTTP {res.status_code} {_detail_code(res, cfg.api_key)}")
            env = res.json()
            if not isinstance(env, dict) or not isinstance(env.get("rows"), list):
                raise _fail(qid, "envelope has no rows")
            record = {
                "id": qid,
                "env": env,
                "served": _served_block(
                    env, cortex.payloads, model_requested=requested_model
                ),
            }
            why = armed_violation(record, stub)
            if why:
                raise _fail(qid, why)
            records.append(record)
    finally:
        import httpx

        httpx.Client.request = original_request  # type: ignore[method-assign]
        cortex.close()
    if len(records) != 52:
        raise SystemExit(f"grade52 armed: FAIL capture size {len(records)} != 52")
    return records


_CALLER_ROLES = ("cortex", "grain", "ontology")
_GATEWAY_UNSET_MODEL = "auto"


def _onto_from_data_on() -> bool:
    return os.environ.get("DMS_ONTO_FROM_DATA", "0").strip() == "1"


def approved_counts_by_kind(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Approved item counts by kind. ``unkeyed`` is not a stored kind."""
    counts = {"object": 0, "link": 0}
    for row in items:
        kind = str(row.get("kind") or "")
        if not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def approved_ontology_precheck(store: Any = None) -> dict[str, Any]:
    """Read approved items before the first armed question.

    With no injected store, the factory must be the Postgres store
    (``DMS_ONTO_PG=1`` and ``DATABASE_URL``) and ``DMS_TENANT_ID`` must be
    set. An empty object list exits non-zero. There is no spine fallback.
    """
    _bootstrap()
    if store is None:
        from dms_core.control_plane.onto_data import (
            PostgresOntologyDataStore,
            onto_pg_enabled,
            ontology_data_store,
        )

        if not os.environ.get("DMS_TENANT_ID", "").strip():
            raise SystemExit("grade52 armed: FAIL tenant_unset")
        if not onto_pg_enabled() or not os.environ.get("DATABASE_URL", "").strip():
            raise SystemExit("grade52 armed: FAIL ontology store is not persisted")
        store = ontology_data_store()
        if not isinstance(store, PostgresOntologyDataStore):
            raise SystemExit("grade52 armed: FAIL ontology store is not persisted")
    items = list(store.approved_items())
    objects = [row for row in items if str(row.get("kind") or "") == "object"]
    if not objects:
        raise SystemExit("grade52 armed: FAIL no approved ontology version")
    versions = {str(row.get("version_id") or "") for row in objects}
    versions.discard("")
    actors = {str(row.get("approved_by") or "") for row in objects}
    actors.discard("")
    if len(versions) != 1:
        raise SystemExit("grade52 armed: FAIL multiple approved ontology versions")
    if len(actors) != 1:
        raise SystemExit("grade52 armed: FAIL mixed approver on approved ontology")
    return {
        "ontology_version_id": next(iter(versions)),
        "approved_counts": approved_counts_by_kind(items),
        "approver_actor_id": next(iter(actors)),
    }


def ov_calls_by_caller_role(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count ledger rows and model mismatches per caller role.

    A row names its role in ``caller_role`` or ``role``. Cortex, grain, and
    ontology are the roles this grade reports. A row with no role and a
    pinned model request is Cortex. A row with no role and no pinned model
    stays ``unattributed`` so a DMS-direct call is not counted as Cortex.
    """
    out: dict[str, dict[str, int]] = {
        role: {"calls": 0, "model_mismatch": 0} for role in _CALLER_ROLES
    }
    for event in events:
        role = _ledger_role(event)
        bucket = out.setdefault(role, {"calls": 0, "model_mismatch": 0})
        bucket["calls"] += 1
        requested = str(event.get("model_requested") or "")
        served = str(event.get("model_served") or "")
        if _pin_mismatch(requested, served):
            bucket["model_mismatch"] += 1
    return out


def _ledger_role(event: Mapping[str, Any]) -> str:
    for key in ("caller_role", "role"):
        raw = event.get(key)
        if isinstance(raw, str) and raw.strip() in _CALLER_ROLES:
            return raw.strip()
    requested = str(event.get("model_requested") or "").strip()
    if requested and requested != _GATEWAY_UNSET_MODEL:
        return "cortex"
    return "unattributed"


def read_ov_calls(openvault_url: str, since: float) -> dict[str, Any]:
    """Usage-ledger window. A failed read is named on the artifact, not a score."""
    import httpx

    url = openvault_url.rstrip("/") + "/api/usage"
    try:
        response = httpx.get(url, params={"since": since, "limit": 1000}, timeout=5.0)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # noqa: BLE001 - the grade keeps the failure name
        return {"read": "failed", "reason": type(exc).__name__}
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return {"read": "failed", "reason": "usage_ledger_shape"}
    rows = [row for row in events if isinstance(row, dict)]
    return ov_calls_by_caller_role(rows)


def armed_replay(
    layout: str = "copy-only", cfg: ArmedConfig | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every pack question through the real ask path against a live Cortex.

    Order: settings, Cortex health and FreeRoute posture, one OpenVault mint,
    then 52 asks with flags off (the stub's flag set, without its credential
    scrub), then the armed checks. Returns ``{id, env, served}`` records and
    the capture header fields.
    """
    if layout not in LAYOUTS:
        raise SystemExit("grade52: layout must be one of " + ", ".join(LAYOUTS))
    cfg = cfg or armed_config()
    pack = _load_yaml(PACK_PATH)
    questions = list(pack["questions"])
    spaces = dict(pack["spaces"])
    if len(questions) != 52:
        raise SystemExit(f"pack size {len(questions)} != 52")
    posture = armed_preflight(cfg)
    os.environ.pop("DMS_ASK_CLARIFY", None)
    os.environ.pop("DMS_CLOOP_B", None)
    os.environ.pop("DMS_AI_FIRST", None)
    os.environ["DMS_DEMO_FALLBACK"] = "0"
    onto_fields: dict[str, Any] | None = None
    if _onto_from_data_on():
        onto_fields = approved_ontology_precheck()
    # The minter tells Cortex to refresh JWKS at CORTEX_URL after each key fetch.
    started = time.time()
    with _env_value("CORTEX_URL", cfg.cortex_url):
        minter = _armed_minter(cfg)
        try:
            with pack_layout(layout):
                records = _armed_ask(cfg, minter, questions, spaces)
        finally:
            minter.close()
    armed_check(records)
    meta = {
        "mode": RUN_ARMED,
        "cortex_sha": cfg.cortex_sha,
        "layout": layout,
        "cortex": public_url(cfg.cortex_url),
        "ov_calls_by_caller_role": read_ov_calls(cfg.openvault_url, started),
        **posture,
    }
    if onto_fields:
        meta.update(onto_fields)
    return records, meta


def write_capture(
    records: Sequence[Mapping[str, Any]], header: Mapping[str, Any], path: Path
) -> Path:
    """JSONL: one header line, then one ``{id, env, served}`` line per question."""
    lines = [json.dumps(dict(header), sort_keys=True)]
    lines.extend(json.dumps(dict(row), sort_keys=True, default=str) for row in records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _labelled(
    report: dict[str, Any],
    *,
    mode: str,
    included: str,
    dms_sha: str,
) -> dict[str, Any]:
    report["mode"] = mode
    report["dms_sha"] = dms_sha
    report["pack_gold_served"] = included
    return report


def _finish(
    report: dict[str, Any],
    mode: str,
    dms_sha: str,
    layout: str | None = None,
    *,
    name: str = "grade52.json",
    prefix: str = "",
    baseline: Mapping[str, Any] | None = None,
    cortex_sha: str | None = None,
) -> dict[str, Any]:
    _labelled(report, mode=mode, included="included", dms_sha=dms_sha)
    report["cortex_sha"] = cortex_sha
    if mode == MODE_ARMED and cortex_sha is None:
        raise SystemExit("grade52 armed: cortex sha missing; an armed score names both commits")
    if layout:
        report["layout"] = layout
    rest = [item for item in report["cases"] if not item.get("pack_gold_served")]
    without = tally(rest)
    without.update(
        {
            "n": len(rest),
            "as_of": report["as_of"],
            "mode": mode,
            "dms_sha": report["dms_sha"],
            "pack_gold_served": "excluded",
            "pack_gold_served_ids": report["pack_gold_served_ids"],
        }
    )
    if layout:
        without["layout"] = layout
    report["without_pack_gold_served"] = without
    rolls = path_counts(report["cases"])
    report["paths_correct"] = rolls["correct"]
    report["paths_served"] = rolls["served"]
    if baseline is not None:
        report["baseline"] = baseline_diff(report, baseline)
    path = write_report(report, name)
    report["artifact"] = str(path)
    print(prefix + header_line(report))
    print(prefix + summary_line(report))
    print(prefix + summary_line(without))
    print(prefix + paths_line(rolls))
    print(prefix + metric_line(report))
    print(prefix + risk_line(report))
    print(prefix + split_line(report))
    print(prefix + tries_line(report["cases"]))
    print(prefix + not_scored_line(report["cases"]))
    if report.get("baseline"):
        print(prefix + baseline_line(report["baseline"]))
    print(f"{prefix}artifact: {path}")
    return report


def grade_loaded(
    envelopes: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    dms_sha: str | None = None,
    layout: str | None = None,
    cortex_sha: str | None = None,
    extra: Mapping[str, Any] | None = None,
    **finish: Any,
) -> dict[str, Any]:
    """Grade served rows. A submit-stub pack is labelled stub and is not a score.

    An armed grade first refuses any record that is not a live answer.
    ``extra`` is copied onto the artifact (ontology version, OV role counts).
    """
    if mode == MODE_ARMED:
        armed_check(envelopes)
    if _is_submit_stub(envelopes):
        print("mode=stub not a score")
        print(served_paths_line(envelopes))
        raise SystemExit("grade52: stub not a score")
    if not isinstance(dms_sha, str):
        raise SystemExit("grade52: envelopes commit missing")
    report = grade_envelopes(envelopes, mode=mode)
    if extra:
        report.update(dict(extra))
    return _finish(report, mode, _commit_sha(dms_sha), layout, cortex_sha=cortex_sha, **finish)


def grade_main(mode: str | None = None, *, ai_first: bool = False) -> dict[str, Any]:
    """In-repo proof: exec-SQL stub. Default flags off.

    ``ai_first`` turns ``DMS_AI_FIRST`` on for this replay only.
    """
    chosen = MODE_STUB_EXEC if mode is None else _resolve_mode(mode)
    if chosen != MODE_STUB_EXEC:
        raise SystemExit("grade52: --main grades the exec-SQL stub as stub-exec")
    return grade_loaded(
        replay_executing(ai_first=ai_first), mode=MODE_STUB_EXEC, dms_sha=product_sha()
    )


def grade_armed(layout: str = "copy-only", **finish: Any) -> dict[str, Any]:
    """Armed capture of this checkout against a live Cortex, then the grade.

    Settings are refused before the product sha is read, so a missing key
    never reaches the network. The capture (env, model SQL, rows, answer text
    per question) is written beside the artifact.
    """
    cfg = armed_config()
    sha = product_sha()
    records, meta = armed_replay(layout, cfg=cfg)
    dest = _artifact_dir() / "grade52.armed.jsonl"
    capture = write_capture(records, {"dms_sha": sha, **meta}, dest)
    print(f"armed capture: {capture}")
    extra = {
        key: meta[key]
        for key in (
            "ontology_version_id",
            "approved_counts",
            "approver_actor_id",
            "ov_calls_by_caller_role",
        )
        if key in meta
    }
    return grade_loaded(
        records,
        mode=MODE_ARMED,
        dms_sha=sha,
        layout=layout,
        cortex_sha=str(meta["cortex_sha"]),
        extra=extra,
        **finish,
    )


def grade_envelopes_path(path: Path, **finish: Any) -> dict[str, Any]:
    """A capture whose header says armed is graded armed, with every armed check."""
    rows, meta = load_envelopes_meta(path)
    armed = meta["mode"] == RUN_ARMED
    if armed and meta["cortex_sha"] is None:
        raise SystemExit("grade52 armed: capture says armed but names no cortex sha")
    return grade_loaded(
        rows,
        mode=MODE_ARMED if armed else MODE_SERVED,
        dms_sha=meta["dms_sha"],
        layout=meta["layout"],
        cortex_sha=meta["cortex_sha"] if armed else None,
        **finish,
    )


def worst_case(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per case, the worst bucket any run gave it. One lucky run is not a score.

    A case correct in one run and wrong in another is wrong; correct in one and
    abstained in another is an abstain. Runs must grade one commit, layout and
    as-of date, or the worst case mixes two products.
    """
    first = reports[0]
    for key in ("dms_sha", "cortex_sha", "layout", "as_of", "mode", "n"):
        if any(other.get(key) != first.get(key) for other in reports[1:]):
            raise SystemExit(f"grade52: runs differ on {key}; worst case needs one product")
    per_run = [{str(item["id"]): item for item in run["cases"]} for run in reports]
    cases: list[dict[str, Any]] = []
    unstable = 0
    for item in first["cases"]:
        seen = [run[str(item["id"])] for run in per_run]
        worst = max(seen, key=lambda case: _SEVERITY[str(case["bucket"])])
        buckets = [str(case["bucket"]) for case in seen]
        unstable += len(set(buckets)) > 1
        cases.append({**worst, "runs": buckets})
    return {
        "as_of": first["as_of"],
        "n": len(cases),
        "pack_gold_served_ids": first["pack_gold_served_ids"],
        **tally(cases),
        "cases": cases,
        "runs": len(reports),
        "unstable": unstable,
    }


def grade_runs(
    paths: Sequence[Path], baseline: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Grade each run, then the worst case of all runs. The worst case is the score."""
    if len(paths) == 1:
        return grade_envelopes_path(paths[0], baseline=baseline)
    total = len(paths)
    runs = [
        grade_envelopes_path(path, name=f"grade52.run{i}.json", prefix=f"run {i}/{total} ")
        for i, path in enumerate(paths, start=1)
    ]
    report = worst_case(runs)
    report = _finish(
        report,
        str(runs[0]["mode"]),
        str(runs[0]["dms_sha"]),
        runs[0].get("layout"),
        prefix=f"worst of {total} unstable={report['unstable']} ",
        baseline=baseline,
        cortex_sha=runs[0].get("cortex_sha"),
    )
    return report


def baseline_diff(report: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Bucket moves since a baseline artifact. Ids stay in the artifact, never the log."""
    if baseline.get("grader_version") != GRADER_VERSION:
        raise SystemExit(
            f"grade52: baseline graded by grader {baseline.get('grader_version')}, "
            f"this is grader {GRADER_VERSION}; not comparable"
        )
    before = {str(item["id"]): str(item["bucket"]) for item in baseline.get("cases") or []}
    now = {str(item["id"]): str(item["bucket"]) for item in report["cases"]}
    if set(before) != set(now):
        raise SystemExit("grade52: baseline graded a different set of cases")
    moves: dict[str, list[str]] = {}
    regressed = improved = 0
    for qid in sorted(now):
        old, new = before[qid], now[qid]
        if old == new:
            continue
        moves.setdefault(f"{old}->{new}", []).append(qid)
        if _SEVERITY[new] > _SEVERITY[old]:
            regressed += 1
        elif _SEVERITY[new] < _SEVERITY[old]:
            improved += 1
    return {
        "dms_sha": baseline.get("dms_sha"),
        "regressed": regressed,
        "improved": improved,
        "moves": moves,
    }


def baseline_line(diff: Mapping[str, Any]) -> str:
    counts = " ".join(f"{key}:{len(ids)}" for key, ids in sorted(diff["moves"].items()))
    return (
        f"baseline regressed={diff['regressed']} improved={diff['improved']} "
        f"moves {counts or 'none'} since {str(diff.get('dms_sha') or 'unknown')[:8]}"
    )


def self_test() -> dict[str, str]:
    """Plants. No pack ids. Returns the plant name -> bucket."""
    renamed_gold = [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 3}]
    renamed_served = [{"item": "A", "qty": 2}, {"item": "B", "qty": 3}]
    renamed = compare_rows(renamed_gold, renamed_served, ordered=False)

    dropped = compare_rows(renamed_gold, [{"item": "A", "qty": 2}], ordered=False)

    ordered_sql = "SELECT sku, qty FROM src ORDER BY qty"
    plain_sql = "SELECT sku, qty FROM src"
    nested_sql = "SELECT sku FROM (SELECT sku, qty FROM src ORDER BY qty) s"
    window_sql = "SELECT sku, ROW_NUMBER() OVER (ORDER BY qty) AS n FROM src"
    ordinal_sql = "SELECT k, name FROM src ORDER BY 1"
    limit_sql = "SELECT k, name FROM src ORDER BY k LIMIT 2"
    if order_keys(ordered_sql) != ["qty"]:
        raise SystemExit("self-test: ORDER BY key was not the AST output column")
    if order_keys(ordinal_sql) != ["k"]:
        raise SystemExit("self-test: ORDER BY ordinal was read as text")
    if order_keys(limit_sql) != ["k"]:
        raise SystemExit("self-test: LIMIT hid the ORDER BY key")
    if order_keys(plain_sql) or order_keys(nested_sql) or order_keys(window_sql):
        raise SystemExit("self-test: non-top-level ORDER BY counted")
    swapped_gold = [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]
    swapped_served = [{"sku": "B", "qty": 2}, {"sku": "A", "qty": 1}]
    swapped = compare_rows(
        swapped_gold,
        swapped_served,
        ordered=True,
        order_keys=order_keys(ordered_sql),
    )
    shuffled = compare_rows(swapped_gold, swapped_served, ordered=False)
    tie_gold = [{"k": 1, "name": "A"}, {"k": 1, "name": "B"}, {"k": 2, "name": "C"}]
    tie_swap = compare_rows(
        tie_gold,
        [{"k": 1, "name": "B"}, {"k": 1, "name": "A"}, {"k": 2, "name": "C"}],
        ordered=True,
        order_keys=order_keys("SELECT k, name FROM src ORDER BY k"),
    )
    tie_break = compare_rows(
        tie_gold,
        [{"k": 2, "name": "C"}, {"k": 1, "name": "A"}, {"k": 1, "name": "B"}],
        ordered=True,
        order_keys=["k"],
    )
    limit_tie = compare_rows(
        [{"k": 1, "name": "A"}, {"k": 1, "name": "B"}],
        [{"k": 1, "name": "B"}, {"k": 1, "name": "A"}],
        ordered=True,
        order_keys=order_keys(limit_sql),
    )
    limit_cut = compare_rows(
        [{"k": 1, "name": "A"}, {"k": 1, "name": "B"}],
        [{"k": 1, "name": "A"}, {"k": 2, "name": "C"}],
        ordered=True,
        order_keys=order_keys(limit_sql),
    )

    rounded = compare_rows([{"n": 1.2}], [{"n": 1.23}], ordered=False)
    half_up = compare_rows([{"n": 56.3}], [{"n": 56.25}], ordered=False)
    whole_gold = compare_rows([{"n": 100}], [{"n": 100.49}], ordered=False)
    loose = compare_rows([{"n": 1.0}], [{"n": "1.004"}], ordered=False)
    bool_cell = compare_rows([{"flag": True}], [{"flag": 1}], ordered=False)
    null_cell = compare_rows([{"v": None}], [{"v": 0}], ordered=False)
    case_text = compare_rows([{"name": "Ab"}], [{"name": " ab "}], ordered=False)
    extra = compare_rows([{"sku": "A"}], [{"sku": "A", "extra": 9}], ordered=False)
    # Same row as extra, with that gold row duplicated on the answer side.
    duplicated = compare_rows(
        [{"sku": "A"}],
        [{"sku": "A", "extra": 1}, {"sku": "A", "extra": 2}],
        ordered=False,
    )
    kept = compare_rows(
        [{"sku": "A"}, {"sku": "A"}],
        [{"sku": "A", "extra": 1}, {"sku": "A", "extra": 2}],
        ordered=False,
    )
    folded = compare_rows([{"Sku": "A"}], [{"sku": "A"}], ordered=False)
    named_mismatch = compare_rows(
        [{"a": 1, "b": 2}],
        [{"a": 2, "b": 1}],
        ordered=False,
    )

    plants = {
        "renamed_column": renamed[0],
        "dropped_row": dropped[0],
        "swapped_order": swapped[0],
        "ordered_shuffle": swapped[0],
        "unordered_shuffle": shuffled[0],
        "extra_column": extra[0],
        "duplicated_row": duplicated[0],
        "tie_swap": tie_swap[0],
        "tie_break": tie_break[0],
        "limit_tie": limit_tie[0],
    }
    expect = {
        "renamed_column": BUCKET_CORRECT,
        "dropped_row": BUCKET_WRONG,
        "swapped_order": BUCKET_WRONG,
        "ordered_shuffle": BUCKET_WRONG,
        "unordered_shuffle": BUCKET_CORRECT,
        "extra_column": BUCKET_CORRECT,
        "duplicated_row": BUCKET_WRONG,
        "tie_swap": BUCKET_CORRECT,
        "tie_break": BUCKET_WRONG,
        "limit_tie": BUCKET_CORRECT,
    }
    for name, bucket in expect.items():
        if plants[name] != bucket:
            raise SystemExit(f"self-test {name}: {plants[name]} != {bucket}")
    if dropped[1] != "rowcount 1 vs 2":
        raise SystemExit(f"self-test dropped reason: {dropped[1]}")
    if swapped[1] != "order mismatch":
        raise SystemExit(f"self-test order reason: {swapped[1]}")
    if tie_swap[1] != "match":
        raise SystemExit(f"self-test tie swap: {tie_swap}")
    if tie_break[1] != "order mismatch":
        raise SystemExit(f"self-test tie break: {tie_break}")
    if limit_tie[1] != "match" or limit_cut[0] != BUCKET_WRONG:
        raise SystemExit(f"self-test limit tie: {limit_tie} cut {limit_cut}")
    if shuffled[1] != "match":
        raise SystemExit(f"self-test shuffle reason: {shuffled[1]}")
    if rounded[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: gold decimals did not round the served value")
    if half_up[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: 56.25 vs 56.3 was not half away from zero")
    if whole_gold[0] != BUCKET_WRONG:
        raise SystemExit("self-test: 100.49 vs 100 was rounded")
    if loose[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: abs_tol 0.005 rejected a numeric string")
    if bool_cell[0] != BUCKET_WRONG or null_cell[0] != BUCKET_WRONG:
        raise SystemExit("self-test: bool or null collapsed into a number")
    if case_text[0] != BUCKET_WRONG:
        raise SystemExit("self-test: string case was folded")
    if extra[0] != BUCKET_CORRECT or extra[1] != "match":
        raise SystemExit("self-test: extra served column rejected")
    if duplicated[0] != BUCKET_WRONG or duplicated[1] != "rowcount 2 vs 1":
        raise SystemExit(f"self-test: duplicated gold row collapsed ({duplicated})")
    if kept[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: gold duplicates were dropped")
    if folded[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: case-insensitive column name missed")
    if named_mismatch[0] != BUCKET_WRONG or named_mismatch[1] != "row mismatch":
        raise SystemExit("self-test: exact names were permuted")

    empty_served = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L0_CERTIFIED", "route": "sql", "abstained": False},
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
    )
    empty_abstain = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "abstain", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
    )
    empty_zero = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L1_GOVERNED_METRIC", "route": "governed_metric", "abstained": False},
        served_rows=[],
        as_of="2026-10-09",
    )
    if empty_served["bucket"] != BUCKET_EMPTY or empty_served["reason"] != "served with 1 row":
        raise SystemExit("self-test: served empty-gold folded out of its bucket")
    if empty_abstain["bucket"] != BUCKET_EMPTY or empty_abstain["reason"] != "abstain":
        raise SystemExit("self-test: abstained empty-gold left its bucket")
    if empty_zero["bucket"] != BUCKET_EMPTY:
        raise SystemExit("self-test: zero-row empty-gold became a graded answer")
    counts = tally([empty_served, empty_abstain, empty_zero])
    if counts["wrong"] or counts["correct"] or counts["graded"]:
        raise SystemExit("self-test: empty-gold counted inside graded")
    if counts["empty_gold"] != 3 or counts["empty_gold_abstained"] != 1:
        raise SystemExit("self-test: empty-gold sub-counts drifted")
    if counts["empty_gold_served"] != {"0": 1, "1": 1}:
        raise SystemExit(f"self-test: served sub-count {counts['empty_gold_served']}")
    if _accounted(counts) != 3:
        raise SystemExit("self-test: empty-gold buckets did not add up")

    trap_served = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L0_CERTIFIED", "route": "sql", "abstained": False},
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
        trap=True,
    )
    trap_abstain = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "abstain", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
        trap=True,
    )
    broken = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "abstain", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
        gold_broken=True,
    )
    if trap_served["bucket"] != BUCKET_REFUSAL_WRONG:
        raise SystemExit("self-test: served trap was not a wrong refusal")
    if trap_abstain["bucket"] != BUCKET_REFUSAL_OK:
        raise SystemExit("self-test: abstained trap was not a correct refusal")
    broken_served = grade_case(
        gold=None,
        gold_sql=None,
        env={"badge": "L1_GOVERNED_METRIC", "route": "governed_metric", "abstained": False},
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
        gold_broken=True,
    )
    if broken["bucket"] != BUCKET_GOLD_BROKEN:
        raise SystemExit("self-test: missing gold was not gold-broken")
    if broken_served["bucket"] != BUCKET_WRONG or broken_served["reason"] != "served on gold-broken":
        raise SystemExit("self-test: a served gold-broken answer was not wrong")
    if trap_served["bucket"] == BUCKET_EMPTY or trap_abstain["bucket"] == BUCKET_EMPTY:
        raise SystemExit("self-test: a trap landed in empty gold")
    if _is_trap({"id": "trap_categoty", "expect": "l0"}):
        raise SystemExit("self-test: trap id prefix counted")
    if _is_trap({"id": "plain", "expect": "refuse"}):
        raise SystemExit("self-test: expect refuse counted without a declared field")
    if not _is_trap({"id": "plain", "expect": "l0", "trap": True}):
        raise SystemExit("self-test: trap field was ignored")
    if not _is_trap({"id": "plain", "expect": "abstain"}, {"refusal_expected": True}):
        raise SystemExit("self-test: refusal_expected was ignored")
    try:
        _rows_of(None)
    except SystemExit as exc:
        if "rows" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: a missing rows key loaded as zero rows")
    if _rows_of([]) != []:
        raise SystemExit("self-test: an empty rows list was not zero rows")

    try:
        summary_line({"n": 52, "correct": 0, "wrong": 0, "abstain": 0})
    except SystemExit as exc:
        if "mode" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: unlabelled summary was accepted")
    if _resolve_mode(None) != MODE_SERVED:
        raise SystemExit("self-test: served is not the envelope mode")
    stub_only = [
        {
            "id": f"s{i}",
            "env": {"badge": "L1_GOVERNED_METRIC", "abstained": False, "rows": [{"n": 1}]},
        }
        for i in range(10)
    ]
    mixed = [
        *stub_only,
        {"id": "real", "env": {"badge": "L1_GOVERNED_METRIC", "abstained": False, "rows": [{"sku": "A"}]}},
    ]
    if not _is_submit_stub(stub_only) or _is_submit_stub(mixed):
        raise SystemExit("self-test: submit stub detector drifted")
    try:
        grade_loaded(stub_only, mode=MODE_SERVED)
    except SystemExit as exc:
        if "stub" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: submit stub was printed as a score")
    check_line = summary_line(
        {
            "mode": MODE_SERVED,
            "dms_sha": "0" * 40,
            "pack_gold_served": "included",
            "n": 52,
            "correct": 23,
            "wrong": 0,
            "abstain": 20,
            "refusal_ok": 8,
            "refusal_wrong": 0,
            "empty_gold": 0,
            "gold_broken": 1,
        }
    )
    if not check_line.endswith(
        "52: correct=23 wrong=0 reconfirm=0 abstain=20 refusal_ok=8 refusal_wrong=0 "
        "empty_gold=0 gold_broken=1 mode=served"
    ):
        raise SystemExit("self-test: summary shape drifted")
    try:
        summary_line(
            {
                "mode": MODE_SERVED,
                "dms_sha": "0" * 40,
                "pack_gold_served": "included",
                "n": 52,
                "correct": 23,
                "wrong": 0,
                "abstain": 21,
                "refusal_ok": 6,
                "refusal_wrong": 2,
                "empty_gold": 0,
                "gold_broken": 1,
            }
        )
    except SystemExit as exc:
        if "add up" not in str(exc):
            raise
    else:
        raise SystemExit("self-test: 23+21+8+1 was accepted as 52")

    gold_wide = [{f"g{i}": i for i in range(8)}]
    served_unique = [{**{f"s{i}": i for i in range(8)}, **{f"x{i}": 100 + i for i in range(12)}}]
    served_named = [{**{f"g{i}": i for i in range(8)}, **{f"x{i}": 100 + i for i in range(12)}}]
    served_ambiguous = [{f"s{i}": 1 for i in range(20)}]
    gold_ambiguous = [{f"g{i}": 1 for i in range(8)}]
    started = time.monotonic()
    wide_unique = compare_rows(gold_wide, served_unique, ordered=False)
    wide_named = compare_rows(gold_wide, served_named, ordered=False)
    wide_ambiguous = compare_rows(gold_ambiguous, served_ambiguous, ordered=False)
    elapsed = time.monotonic() - started
    if elapsed > 0.5:
        raise SystemExit(f"self-test: 20-wide map took {elapsed:.3f}s")
    if wide_unique[0] != BUCKET_CORRECT or wide_named[0] != BUCKET_CORRECT:
        raise SystemExit("self-test: signature-narrowed wide answer was rejected")
    if wide_ambiguous[1] != "unmappable":
        raise SystemExit(f"self-test: wide ambiguous map did not cap ({wide_ambiguous})")
    contradictory = {
        "badge": "L1_GOVERNED_METRIC",
        "plan_origin": "generate_sql",
        "ladder_rung": "compile",
        "served_model": "some-model",
    }
    compile_empty = {"ladder_rung": "compile", "served_model": ""}
    if serve_path(contradictory) != "unattributed":
        raise SystemExit("self-test: contradictory stamps were called AI")
    if serve_path({"served_model": "some-model"}) != "unattributed":
        raise SystemExit("self-test: a model name alone was called AI")
    if serve_path(compile_empty) != "compile" or path_group(serve_path(compile_empty)) != "rule":
        raise SystemExit("self-test: compile with an empty model was not rule-served")
    ranking = {
        "badge": "L2_VALIDATED",
        "plan_origin": "ontology_ranking",
        "plan_source": "ontology_plan",
        "served_model": "some-model",
    }
    if serve_path(ranking) != "compile" or path_group(serve_path(ranking)) != "rule":
        raise SystemExit("self-test: ontology_ranking with a model was not rule/compile")
    if serve_path({"ladder_rung": "ontology_compile", "served_model": "some-model"}) != "compile":
        raise SystemExit("self-test: ontology_compile with a model was not compile")
    model_answer = {
        "badge": "L2_VALIDATED",
        "plan_origin": "generate_sql",
        "plan_source": "ontology_plan",
        "served_model": "some-model",
        "sql_used": "SELECT sku FROM inventory",
    }
    wrote = "SELECT sku FROM inventory"
    if serve_path(model_answer, wrote) != "ai" or stamp_contradiction(model_answer):
        raise SystemExit("self-test: served model SQL under Cortex's ontology_plan was not AI")
    if serve_path(model_answer) == "ai":
        raise SystemExit("self-test: model stamps without the model's SQL were called AI")
    # A DMS compile served under stale model stamps: the model's SQL never ran.
    if serve_path(model_answer, "SELECT sku, SUM(qty) AS q FROM inventory GROUP BY sku") == "ai":
        raise SystemExit("self-test: a DMS compile under model stamps was called AI")
    if serve_path({**model_answer, "served_model": ""}, wrote) == "ai":
        raise SystemExit("self-test: model SQL with no served model was called AI")
    compiled = {**model_answer, "plan_origin": "ontology_compile"}
    compiled.pop("served_model")
    if serve_path(compiled, wrote) != "compile":
        raise SystemExit("self-test: a DMS multi-grain compile was not rule/compile")
    stub = StubSignatures(
        texts=frozenset({"Recorded answer."}), ids=frozenset({"aud-x"}), sql=frozenset()
    )
    live = {"env": {"text": "Found 1 row(s).", "audit_id": "led-1"}, "served": {}}
    canned = {"env": {"text": "Recorded answer.", "audit_id": "led-1"}, "served": {}}
    if armed_violation(live, stub) is not None or armed_violation(canned, stub) is None:
        raise SystemExit("self-test: armed placeholder detector drifted")

    reconfirm_case = grade_case(
        gold=[{"n": 1}],
        gold_sql="SELECT count(*) AS n FROM src",
        env={
            "badge": "ABSTAIN",
            "route": "abstain",
            "abstained": True,
            "reconfirm": {
                "reason_code": "unknown_measure",
                "reason_text": "no measure",
                "suggested_question": "closest",
                "token": "rcf",
            },
        },
        served_rows=[],
        as_of="2026-10-09",
    )
    if reconfirm_case["bucket"] != BUCKET_RECONFIRM:
        raise SystemExit("self-test: a reconfirm offer was graded as an abstain")
    wrong_measure = grade_case(
        gold=[{"n": 1}],
        gold_sql="SELECT count(*) AS n FROM src",
        env={
            "badge": "L2_VALIDATED",
            "route": "generated",
            "abstained": False,
            "reconfirm_accepted": True,
            "sql_used": "SELECT sum(amount) AS n FROM src",
        },
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
    )
    if wrong_measure["bucket"] != BUCKET_WRONG or wrong_measure["reason"] != "wrong measure":
        raise SystemExit(f"self-test: yes into another measure was {wrong_measure}")
    same_measure = grade_case(
        gold=[{"n": 1}],
        gold_sql="SELECT count(*) AS n FROM src",
        env={
            "badge": "L1_GOVERNED_METRIC",
            "route": "governed_metric",
            "abstained": False,
            "reconfirm_accepted": True,
            "sql_used": "SELECT count(*) AS n FROM src",
        },
        served_rows=[{"n": 1}],
        as_of="2026-10-09",
    )
    if same_measure["reason"] == "wrong measure":
        raise SystemExit(f"self-test: the same aggregate was a wrong measure {same_measure}")

    first = {
        "bucket": BUCKET_CORRECT,
        "served_route": "ai",
        "model_calls": 1,
        "pin_mismatch": False,
    }
    retried = {
        "bucket": BUCKET_CORRECT,
        "served_route": "ai",
        "model_calls": 2,
        "pin_mismatch": False,
    }
    mismatched = {
        "bucket": BUCKET_CORRECT,
        "served_route": "ai",
        "model_calls": 1,
        "pin_mismatch": True,
        "model_requested": "openai/gpt-oss-120b",
        "served_model": "openai/gpt-oss-20b",
    }
    abstained = {
        "bucket": BUCKET_ABSTAIN,
        "served_route": "abstain",
        "model_calls": 0,
    }
    refused = {
        "bucket": BUCKET_REFUSAL_OK,
        "served_route": "abstain",
        "model_calls": 0,
    }
    sample = [first, retried, mismatched, abstained, refused]
    if tries_line(sample) != (
        "tries correct_first=1 correct_retry=1 omitted_pin_mismatch=1 "
        "reason=pin_mismatch_not_in_tries"
    ):
        raise SystemExit(f"self-test: tries line drifted ({tries_line(sample)})")
    mismatched["id"] = "cq_pin"
    if not_scored_line(sample) != (
        "not_scored pin_mismatch=1 "
        "cq_pin=pin_mismatch:openai/gpt-oss-120b/openai/gpt-oss-20b"
    ):
        raise SystemExit(f"self-test: pin mismatch was scored ({not_scored_line(sample)})")
    shown = summary_line(
        {
            "mode": MODE_SERVED,
            "dms_sha": "0" * 40,
            "pack_gold_served": "included",
            "n": 2,
            "correct": 1,
            "wrong": 0,
            "reconfirm": 0,
            "abstain": 0,
            "refusal_ok": 0,
            "refusal_wrong": 1,
            "empty_gold": 0,
            "gold_broken": 0,
        }
    )
    if "wrong=1" not in shown or "refusal_wrong=1" not in shown:
        raise SystemExit(f"self-test: refusal_wrong was not in headline wrong ({shown})")
    parts = abstain_part_counts(sample)
    if parts["abstain"] + parts["refusal_ok"] != 2 or sum(parts.values()) != 2:
        raise SystemExit(f"self-test: abstain parts do not add up ({parts})")
    per_min, gap = pace_plan(18, 36_000, 2000)
    if per_min >= _OV_REQ_PER_MIN or per_min != 18 or abs(gap - (60.0 / 18)) > 1e-9:
        raise SystemExit(f"self-test: pacer is not under the cap ({per_min}, {gap})")
    if pace_plan(100, 80_000, 2000)[0] >= _OV_REQ_PER_MIN:
        raise SystemExit("self-test: pacer accepted a rate at the cap")
    if pace_backoff_s(1) != 2 or pace_backoff_s(2) != 4 or pace_backoff_s(8) != 60:
        raise SystemExit("self-test: 429 backoff drifted")
    share = metric_line(
        {
            "correct": 2,
            "wrong": 0,
            "cases": [
                {
                    "serve_path": "ai",
                    "bucket": BUCKET_CORRECT,
                    "answered": True,
                    "stamp_contradiction": False,
                },
                {
                    "serve_path": "L1",
                    "bucket": BUCKET_CORRECT,
                    "answered": True,
                    "stamp_contradiction": False,
                },
            ],
        }
    )
    if share != "metric ai_share=1/2 wrong=0":
        raise SystemExit(f"self-test: metric line was {share}")
    empty_share = metric_line({"correct": 0, "wrong": 0, "cases": []})
    if empty_share != "metric ai_share=0/0 wrong=0":
        raise SystemExit(f"self-test: empty metric line was {empty_share}")
    if _pin_mismatch("", "openai/gpt-oss-20b") or not _pin_mismatch(
        "openai/gpt-oss-120b", "openai/gpt-oss-20b"
    ):
        raise SystemExit("self-test: pin mismatch rule drifted")

    print("self-test ok")
    print(
        "plants: renamed_column=CORRECT dropped_row=WRONG "
        "swapped_order=WRONG ordered_shuffle=WRONG unordered_shuffle=CORRECT "
        "extra_column=CORRECT duplicated_row=WRONG "
        "tie_swap=CORRECT tie_break=WRONG limit_tie=CORRECT "
        "empty_gold_served=EMPTY_GOLD refusal_wrong=REFUSAL_WRONG "
        "unlabelled=refused wide_ambiguous=unmappable"
    )
    return plants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--main", action="store_true")
    parser.add_argument(
        "--ai-first",
        action="store_true",
        help="with --main, set DMS_AI_FIRST=1 for the stub replay",
    )
    parser.add_argument(
        "--envelopes",
        type=Path,
        action="append",
        help="captured run; repeat for several runs of one commit, graded on the worst case",
    )
    parser.add_argument("--baseline", type=Path, help="an earlier grade52.json to diff against")
    parser.add_argument("--gate", action="store_true", help="exit 1 on any wrong served answer")
    parser.add_argument(
        "--armed",
        action="store_true",
        help=f"ask the pack against the live Cortex at {ARMED_CORTEX_ENV}; refuses unless armed",
    )
    parser.add_argument(
        "--layout",
        choices=LAYOUTS,
        help="--armed only: copy-only (the API image, default) or full-tree",
    )
    args = parser.parse_args(argv)
    sources = int(args.main) + int(args.envelopes is not None) + int(args.armed)
    if sources > 1:
        raise SystemExit("grade52: pass one of --envelopes, --main, --armed")
    if (args.baseline or args.gate) and args.envelopes is None and not args.armed:
        raise SystemExit("grade52: --baseline and --gate grade --envelopes or --armed")
    if args.layout and not args.armed:
        raise SystemExit("grade52: --layout is for --armed; a capture carries its own layout")
    if args.ai_first and not args.main:
        raise SystemExit("grade52: --ai-first is the stub switch for --main")
    if args.self_test or sources == 0:
        self_test()
    baseline = None
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    report: dict[str, Any] | None = None
    if args.envelopes is not None:
        report = grade_runs(args.envelopes, baseline=baseline)
    elif args.armed:
        report = grade_armed(args.layout or "copy-only", baseline=baseline)
    elif args.main:
        grade_main(ai_first=bool(args.ai_first))
    if args.gate and report is not None:
        if headline_wrong(report) > 0:
            print(
                f"gate FAIL wrong={headline_wrong(report)} "
                f"refusal_wrong={report['refusal_wrong']}"
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
