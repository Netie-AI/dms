"""Lane 1: one model in isolation on the curated_ceo 52. A bench, never a serve path.

What it proves: how well a model writes DuckDB SQL from the granted schema of a demo
Space, graded on executed rows against the certified oracle. What it does not prove:
anything about the product envelope (POST /v1/chat/ask), Cortex's prompt, routing, or
the badge. Lane 1 and Lane 2 numbers are never pooled.

Per call the harness renders one prompt from the Space's granted tables (names, types,
nothing else; no sample values, no oracle SQL, no other pack question), sends it through
a transport, parses {"sql": ...} or {"abstain": ...}, checks the SQL with sqlglot (one
read-only SELECT over granted tables only), executes it on a fresh read-only demo
warehouse and grades the rows with grade52.compare_rows. Labels follow dms#299:
CORRECT / WRONG / ABSTAIN / RATE_LIMIT / ORACLE_ERROR / INVALID, one CSV row per attempt.

Transports:
  fake       scripted replies, tests only.
  replay     reads a record directory; a missing entry is an error, never a model call.
  anthropic  official SDK, imported lazily. Runs only with --allow-egress anthropic.
             Credentials come from the shell (SDK resolution order). Never sends
             temperature; depth is output_config.effort.
  openvault  POST {OPENVAULT_URL}/v1/chat/completions with X-OpenVault-Strict: true and
             the exact pin. Runs only with --allow-egress openvault. A served model that
             differs from the pin is INVALID(pin_mismatch).
Any live transport can be wrapped with --record DIR so the run replays byte-for-byte.

Strategies: s0 plain; s1 plan then SQL; s3 s0 plus one repair on an execution error or
an empty result (feedback is the engine's own message, never gold); s4 two independent
readings, answer only when both execute to the same row hash, else abstain. s2
(few-shot) needs a non-scored example pool and is refused until one exists.

LEARN=0: the harness writes no store, imports no VQ/route store, and seeds nothing.

Usage (cwd = repo root):
    python scripts/bench/lane1.py --transport replay --record DIR --models M --out OUT
    python scripts/bench/lane1.py --transport anthropic --allow-egress anthropic \
        --models claude-haiku-5-5 --effort low --strategy s0 --rounds 3 --record DIR --out OUT
    python scripts/bench/lane1.py --sizes --out OUT
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
for _rel in ("scripts", "scripts/verify", "scripts/bench"):
    _entry = str(ROOT / _rel)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import grade52  # noqa: E402

grade52._bootstrap()

PACK_DIR = ROOT / "tests" / "fixtures" / "curated_ceo"
HARNESS_VERSION = "lane1-v1"
LABELS = ("CORRECT", "WRONG", "ABSTAIN", "RATE_LIMIT", "ORACLE_ERROR", "INVALID")
STRATEGIES = ("s0", "s1", "s2", "s3", "s4")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_ROWS = 10_000
CSV_COLUMNS = (
    "dataset",
    "pack_hash",
    "model",
    "provider",
    "served_model",
    "served_provider",
    "key_ref",
    "strategy",
    "effort",
    "context",
    "round",
    "qid",
    "space",
    "expected",
    "dms_sha",
    "harness_sha",
    "harness_note",
    "as_of",
    "label",
    "abstain_reason",
    "wrong_kind",
    "reason",
    "sql",
    "row_hash",
    "served_n",
    "gold_n",
    "plan_source",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "model_calls",
    "model_calls_ok",
)

SYSTEM_TEMPLATE = """You write one read-only DuckDB SQL query that answers a business question.

Rules:
- Use only the tables and columns listed below. Any other table is not granted.
- Write a single SELECT (WITH ... SELECT is fine). No DDL, DML, PRAGMA, ATTACH or COPY.
- Return only the columns the question asks for, with short snake_case aliases.
- Round money to 2 decimals. Order rows when the question implies a ranking.
- Today's date is {as_of}. A question with no period and no date range means all data.
- If the listed tables cannot answer the question, or the question is too vague to pin
  one metric and one period, abstain instead of guessing.

Reply with JSON only, one of:
{{"sql": "<query>"}}
{{"abstain": "<one short reason>"}}

Granted schema (dialect: duckdb):
{schema}
"""

PLAN_INSTRUCTION = (
    "Before any SQL, reply with JSON only: "
    '{"tables": [...], "columns": [...], "filters": [...], "grain": "...", '
    '"metric": "...", "answerable": true|false, "why": "..."}'
)
SQL_AFTER_PLAN = "Now write the query for that plan, or abstain. Reply with the JSON reply format."
READINGS = (
    "Question:",
    "Read the question literally, then answer it. Question:",
)


class RateLimited(Exception):
    """The provider refused for rate. Labelled RATE_LIMIT, never retried silently."""


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    served_model: str = ""
    served_provider: str = ""
    latency_ms: float = 0.0
    refusal: bool = False


class Transport(Protocol):
    provider: str
    key_ref: str

    def complete(
        self, system: str, messages: Sequence[Mapping[str, str]], *, model: str, effort: str
    ) -> Reply: ...


# ---------------------------------------------------------------- transports


class FakeTransport:
    """Scripted replies keyed by question text; a list is consumed in call order."""

    provider = "fake"
    key_ref = "none"

    def __init__(self, script: Mapping[str, str | Sequence[str]] | Callable[..., str]):
        self._script = script
        self._calls: dict[str, int] = {}

    def complete(
        self, system: str, messages: Sequence[Mapping[str, str]], *, model: str, effort: str
    ) -> Reply:
        question = _question_of(messages)
        if callable(self._script):
            return Reply(
                text=self._script(system, messages, model=model, effort=effort),
                served_model=model,
                served_provider="fake",
            )
        entry = self._script.get(question)
        if entry is None:
            return Reply(
                text='{"abstain": "fake has no script"}', served_model=model, served_provider="fake"
            )
        if isinstance(entry, str):
            return Reply(text=entry, served_model=model, served_provider="fake")
        index = self._calls.get(question, 0)
        self._calls[question] = index + 1
        text = entry[min(index, len(entry) - 1)]
        if text == "RATE_LIMIT":
            raise RateLimited("fake 429")
        return Reply(text=text, served_model=model, served_provider="fake")


def request_key(system: str, messages: Sequence[Mapping[str, str]], model: str, effort: str) -> str:
    blob = json.dumps(
        {"system": system, "messages": list(messages), "model": model, "effort": effort},
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RecordingTransport:
    """Wraps a live transport and writes each reply as <request hash>-<occurrence>.json.

    Rounds repeat identical requests, so the occurrence number keeps every reply.
    """

    def __init__(self, inner: Transport, folder: Path):
        self.inner = inner
        self.provider = inner.provider
        self.key_ref = inner.key_ref
        self.folder = folder
        self._seen: dict[str, int] = {}
        folder.mkdir(parents=True, exist_ok=True)

    def complete(
        self, system: str, messages: Sequence[Mapping[str, str]], *, model: str, effort: str
    ) -> Reply:
        reply = self.inner.complete(system, messages, model=model, effort=effort)
        key = request_key(system, messages, model, effort)
        index = self._seen.get(key, 0)
        self._seen[key] = index + 1
        path = self.folder / f"{key}-{index}.json"
        path.write_text(json.dumps(reply.__dict__, sort_keys=True), encoding="utf-8")
        return reply


class ReplayTransport:
    """Reads recorded replies in occurrence order. A missing record fails; no model call."""

    def __init__(self, folder: Path, provider: str = "replay"):
        self.folder = folder
        self.provider = provider
        self.key_ref = "replay"
        self._seen: dict[str, int] = {}

    def complete(
        self, system: str, messages: Sequence[Mapping[str, str]], *, model: str, effort: str
    ) -> Reply:
        key = request_key(system, messages, model, effort)
        index = self._seen.get(key, 0)
        self._seen[key] = index + 1
        path = self.folder / f"{key}-{index}.json"
        if not path.is_file():
            raise FileNotFoundError(f"no recorded reply for this request: {path.name}")
        return Reply(**json.loads(path.read_text(encoding="utf-8")))


class AnthropicTransport:
    """Official SDK. No temperature (rejected on current models); effort sets depth."""

    provider = "anthropic"
    key_ref = "shell:sdk-default-credentials"

    def __init__(self, max_tokens: int = 16000):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - bench shell only
            raise SystemExit(
                "anthropic transport needs the SDK in the bench shell: pip install anthropic. "
                "It is deliberately not a repo dependency."
            ) from exc
        self._sdk = anthropic
        self._client = anthropic.Anthropic(max_retries=2)
        self._max_tokens = max_tokens

    def complete(
        self, system: str, messages: Sequence[Mapping[str, str]], *, model: str, effort: str
    ) -> Reply:
        started = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=self._max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[dict(item) for item in messages],
                output_config={"effort": effort},
            )
        except self._sdk.RateLimitError as exc:
            raise RateLimited(str(exc)[:200]) from exc
        latency = (time.perf_counter() - started) * 1000
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return Reply(
            text=text,
            input_tokens=int(usage.input_tokens or 0),
            output_tokens=int(usage.output_tokens or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            served_model=str(response.model or ""),
            served_provider="anthropic",
            latency_ms=round(latency, 1),
            refusal=response.stop_reason == "refusal",
        )

    def count_tokens(self, system: str, messages: Sequence[Mapping[str, str]], model: str) -> int:
        result = self._client.messages.count_tokens(
            model=model, system=system, messages=[dict(item) for item in messages]
        )
        return int(result.input_tokens)


class OpenVaultTransport:
    """The free arms through OpenVault's strict pin. Temperature 0, capped output."""

    provider = "openvault"

    def __init__(self, base: str | None = None, max_tokens: int = 1024):
        import httpx

        self._httpx = httpx
        self._base = (base or os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000")).rstrip("/")
        self._max_tokens = max_tokens
        token = os.environ.get("OPENVAULT_TOKEN")
        self._headers = {"X-OpenVault-Strict": "true"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self.key_ref = "openvault:served-key-header"

    def complete(
        self, system: str, messages: Sequence[Mapping[str, str]], *, model: str, effort: str
    ) -> Reply:
        started = time.perf_counter()
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *[dict(m) for m in messages]],
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }
        response = self._httpx.post(
            f"{self._base}/v1/chat/completions", json=body, headers=self._headers, timeout=120.0
        )
        latency = (time.perf_counter() - started) * 1000
        if response.status_code == 429:
            raise RateLimited(response.text[:200])
        response.raise_for_status()
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return Reply(
            text=str((choice.get("message") or {}).get("content") or ""),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            served_model=response.headers.get("X-OpenVault-Served-Model", ""),
            served_provider=response.headers.get("X-OpenVault-Served-Provider", ""),
            latency_ms=round(latency, 1),
        )


# ---------------------------------------------------------------- pack and gold


@dataclass
class Case:
    qid: str
    space: str
    question: str
    expected: str  # "answer" or "refuse"
    tables: tuple[str, ...]
    gold_sql: str | None = None
    gold: list[dict[str, Any]] | None = None
    oracle_error: str = ""
    order_keys: list[str] | None = field(default=None)


def pack_hash(folder: Path = PACK_DIR) -> str:
    digest = hashlib.sha256()
    for name in ("questions.yaml", "oracles.yaml"):
        digest.update((folder / name).read_bytes())
    return digest.hexdigest()


def load_cases(con: Any, as_of: str, folder: Path = PACK_DIR) -> list[Case]:
    from dms_executor.demo_grants import DEMO_SPACE_GRANTS

    questions = grade52._load_yaml(folder / "questions.yaml")
    oracles = grade52._load_yaml(folder / "oracles.yaml").get("oracles") or {}
    spaces = questions.get("spaces") or {}
    cases: list[Case] = []
    for item in questions.get("questions") or []:
        qid = str(item["id"])
        space_id = str(spaces.get(item.get("space"), item.get("space")))
        tables = tuple(DEMO_SPACE_GRANTS.get(space_id, ("", ()))[1])
        oracle = oracles.get(qid) or oracles.get(str(item.get("cortex_id") or ""))
        expect = str(item.get("expect") or "").lower()
        refuse = grade52._is_trap(item, oracle) or expect in {"refuse", "abstain"}
        case = Case(
            qid=qid,
            space=str(item.get("space")),
            question=str(item["question"]),
            expected="refuse" if refuse else "answer",
            tables=tables,
        )
        if not refuse:
            sql = grade52._certified_sql(oracle)
            if sql is None:
                case.oracle_error = "no certified sql"
            else:
                case.gold_sql = sql
                try:
                    case.gold = grade52._execute(con, sql, as_of)
                    case.order_keys = grade52.order_keys(sql)
                except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                    case.oracle_error = f"oracle failed: {type(exc).__name__}"
        cases.append(case)
    return cases


CONTEXTS = ("schema", "values", "product")
MAX_DOMAIN = 12


def render_schema(con: Any, tables: Sequence[str], *, values: bool = False) -> str:
    """Names and types; with values, the stored codes of short text columns too.

    A filter on a guessed code ('sale' where the data says 'outbound') executes and
    matches nothing (law 12). Values are opt-in because they are data, not schema.
    """
    lines: list[str] = []
    for table in sorted(tables):
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        rendered = ", ".join(f"{name} {dtype}" for name, dtype in cols)
        lines.append(f"- {table}({rendered})")
        if not values:
            continue
        for name, dtype in cols:
            if dtype != "VARCHAR":
                continue
            found = con.execute(
                f'SELECT DISTINCT "{name}" FROM "{table}" WHERE "{name}" IS NOT NULL '
                f"ORDER BY 1 LIMIT {MAX_DOMAIN + 1}"
            ).fetchall()
            if 0 < len(found) <= MAX_DOMAIN:
                codes = ", ".join(repr(str(v[0])) for v in found)
                lines.append(f"  {table}.{name} values: {codes}")
    return "\n".join(lines)


def product_schema(serving: Path, case: Case, cache: dict[str, Any]) -> str:
    """The shipping ask path's own schema context, so a score predicts the product.

    Same builder as ``prepare_generate_context``: catalog, the stored Space index,
    then the question-ranked render inside its token cap. No ontology is passed;
    the curated Spaces declare none here.
    """
    from dms_executor import schema_context as sc

    grant = set(case.tables)
    space_id = "bench-" + "-".join(sorted(grant))
    if space_id not in cache:
        stamp = sc.build_space_index(serving, space_id, grant, "duckdb")
        schema, pending = sc.lookup_schema(serving, grant, "duckdb", space_id)
        if stamp or pending:
            raise SystemExit(f"product context: index {stamp or pending} for {space_id}")
        cache[space_id] = schema
    built = sc.build_schema_context(
        case.question, cache[space_id], grantable=grant, space_id=space_id
    )
    return built.prompt


def warm_product_index(
    con: Any, serving: Path, cases: Sequence[Case], cache: dict[str, Any]
) -> Any:
    """Build the Space indexes with our handle closed (the index read needs the file)."""
    import duckdb

    con.close()
    for case in cases:
        product_schema(serving, case, cache)
    return duckdb.connect(str(serving), read_only=True)


def system_prompt(
    con: Any,
    case: Case,
    as_of: str,
    context: str = "schema",
    *,
    serving: Path | None = None,
    cache: dict[str, Any] | None = None,
) -> str:
    if context not in CONTEXTS:
        raise SystemExit(f"unknown context {context}")
    if context == "product":
        if serving is None:
            raise SystemExit("product context needs the serving warehouse path")
        schema = product_schema(serving, case, {} if cache is None else cache)
    else:
        schema = render_schema(con, case.tables, values=context == "values")
    return SYSTEM_TEMPLATE.format(as_of=as_of, schema=schema)


def quarantine_violation(prompt_text: str, case: Case, cases: Sequence[Case]) -> str:
    """Scored-pack hygiene: no oracle SQL and no other pack question in any prompt."""
    folded = " ".join(prompt_text.lower().split())
    for other in cases:
        if other.gold_sql and " ".join(other.gold_sql.lower().split()) in folded:
            return f"oracle sql of {other.qid} in prompt"
        if other.qid != case.qid and other.question != case.question:
            text = " ".join(other.question.lower().split())
            if len(text) > 12 and text in folded:
                return f"question of {other.qid} in prompt"
    return ""


# ---------------------------------------------------------------- parse, check, run

_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)
_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)


def parse_reply(text: str) -> tuple[str, str]:
    """Return ("sql", query) | ("abstain", reason) | ("invalid", why)."""
    match = _JSON_OBJ.search(text or "")
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if isinstance(data.get("sql"), str) and data["sql"].strip():
                return "sql", data["sql"].strip()
            if "abstain" in data:
                return "abstain", str(data.get("abstain") or "model abstained")[:200]
    fence = _SQL_FENCE.search(text or "")
    if fence:
        return "sql", fence.group(1).strip()
    return "invalid", "unparseable reply"


def check_sql(sql: str, granted: Sequence[str]) -> tuple[str, str]:
    """Return ("ok", "") | ("invalid", why) | ("ungranted", table)."""
    import sqlglot
    from sqlglot import exp

    try:
        trees = [tree for tree in sqlglot.parse(sql, read="duckdb") if tree is not None]
    except sqlglot.errors.ParseError:
        return "invalid", "parse error"
    if len(trees) != 1:
        return "invalid", "not exactly one statement"
    tree = trees[0]
    if not isinstance(tree, exp.Query):
        return "invalid", f"not a query: {type(tree).__name__}"
    for banned in (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command):
        if tree.find(banned) is not None:
            return "invalid", f"contains {banned.__name__}"
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {name.lower() for name in granted}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name or name in ctes:
            continue
        if table.args.get("db") or name not in allowed:
            return "ungranted", name
    return "ok", ""


def run_sql(con: Any, sql: str, as_of: str) -> list[dict[str, Any]]:
    rows = grade52._execute(con, sql, as_of)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"more than {MAX_ROWS} rows")
    return rows


def row_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    import grader_agreement

    return grader_agreement.c_hash(rows)


# ---------------------------------------------------------------- one attempt


@dataclass
class Attempt:
    kind: str = "invalid"  # sql | abstain | invalid | rate_limit | refusal
    sql: str = ""
    why: str = ""
    rows: list[dict[str, Any]] | None = None
    exec_error: str = ""
    calls: int = 0
    calls_ok: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: float = 0.0
    served_model: str = ""
    served_provider: str = ""
    plan_source: str = "none"


def _question_of(messages: Sequence[Mapping[str, str]]) -> str:
    first = str(messages[0].get("content") or "") if messages else ""
    first = first.split("\n\n", 1)[0]
    for prefix in sorted(READINGS, key=len, reverse=True):
        if first.startswith(prefix):
            return first[len(prefix) :].strip()
    return first.strip()


def _call(
    transport: Transport,
    attempt: Attempt,
    system: str,
    messages: Sequence[Mapping[str, str]],
    model: str,
    effort: str,
) -> Reply | None:
    attempt.calls += 1
    try:
        reply = transport.complete(system, messages, model=model, effort=effort)
    except RateLimited as exc:
        attempt.kind, attempt.why = "rate_limit", str(exc)[:200]
        return None
    attempt.calls_ok += 1
    attempt.input_tokens += reply.input_tokens
    attempt.output_tokens += reply.output_tokens
    attempt.cache_read_tokens += reply.cache_read_tokens
    attempt.latency_ms += reply.latency_ms
    attempt.served_model = reply.served_model or attempt.served_model
    attempt.served_provider = reply.served_provider or attempt.served_provider
    if reply.refusal:
        attempt.kind, attempt.why = "refusal", "model refusal"
        return None
    return reply


def _settle(attempt: Attempt, reply_text: str, con: Any, case: Case, as_of: str) -> None:
    kind, value = parse_reply(reply_text)
    attempt.exec_error, attempt.rows = "", None
    if kind != "sql":
        attempt.kind, attempt.why, attempt.sql = kind, value, ""
        return
    attempt.sql = value
    verdict, detail = check_sql(value, case.tables)
    if verdict == "ungranted":
        attempt.kind, attempt.why = "ungranted", detail
        return
    if verdict != "ok":
        attempt.kind, attempt.why = "invalid", detail
        return
    try:
        attempt.rows = run_sql(con, value, as_of)
        attempt.kind = "sql"
    except Exception as exc:  # noqa: BLE001 - the engine's message is the repair feedback
        attempt.kind, attempt.exec_error = "invalid", f"{type(exc).__name__}: {str(exc)[:300]}"
        attempt.why = "execution error"


def run_strategy(
    strategy: str,
    transport: Transport,
    con: Any,
    case: Case,
    as_of: str,
    *,
    model: str,
    effort: str,
    system: str,
) -> Attempt:
    if strategy == "s2":
        raise SystemExit("s2 needs a non-scored few-shot pool; none exists, refusing to run")
    attempt = Attempt()
    question = [{"role": "user", "content": f"{READINGS[0]} {case.question}"}]
    if strategy == "s1":
        plan_turn = [
            {"role": "user", "content": f"{READINGS[0]} {case.question}\n\n{PLAN_INSTRUCTION}"}
        ]
        plan = _call(transport, attempt, system, plan_turn, model, effort)
        if plan is None:
            return attempt
        attempt.plan_source = "model"
        convo = [
            *plan_turn,
            {"role": "assistant", "content": plan.text},
            {"role": "user", "content": SQL_AFTER_PLAN},
        ]
        reply = _call(transport, attempt, system, convo, model, effort)
        if reply is not None:
            _settle(attempt, reply.text, con, case, as_of)
        return attempt
    if strategy == "s4":
        readings: list[Attempt] = []
        for prefix in READINGS:
            one = Attempt()
            msg = [{"role": "user", "content": f"{prefix} {case.question}"}]
            reply = _call(transport, one, system, msg, model, effort)
            if reply is not None:
                _settle(one, reply.text, con, case, as_of)
            readings.append(one)
        attempt = readings[0]
        for other in readings[1:]:
            attempt.calls += other.calls
            attempt.calls_ok += other.calls_ok
            attempt.input_tokens += other.input_tokens
            attempt.output_tokens += other.output_tokens
            attempt.cache_read_tokens += other.cache_read_tokens
            attempt.latency_ms += other.latency_ms
        kinds = {item.kind for item in readings}
        if "rate_limit" in kinds:
            attempt.kind = "rate_limit"
        elif kinds == {"sql"}:
            hashes = {row_hash(item.rows or []) for item in readings}
            if len(hashes) != 1:
                attempt.kind, attempt.why, attempt.rows = "abstain", "vote_disagree", None
        elif "sql" in kinds or "ungranted" in kinds:
            attempt.kind, attempt.why, attempt.rows = "abstain", "vote_split", None
        return attempt
    reply = _call(transport, attempt, system, question, model, effort)
    if reply is None:
        return attempt
    _settle(attempt, reply.text, con, case, as_of)
    if strategy == "s3":
        feedback = ""
        if attempt.kind == "invalid" and attempt.exec_error:
            feedback = f"The query failed in DuckDB: {attempt.exec_error}"
        elif attempt.kind == "sql" and not attempt.rows:
            feedback = "The query ran but returned 0 rows."
        if feedback:
            convo = [
                *question,
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": f"{feedback} Fix it or abstain. Same reply format."},
            ]
            second = _call(transport, attempt, system, convo, model, effort)
            if second is not None:
                _settle(attempt, second.text, con, case, as_of)
    return attempt


# ---------------------------------------------------------------- grading


def grade(case: Case, attempt: Attempt, *, pinned: str = "") -> dict[str, str]:
    """One #299 label. Traps answered are WRONG; ungranted tables are WRONG."""
    out = {"label": "", "abstain_reason": "", "wrong_kind": "", "reason": ""}
    if attempt.kind == "rate_limit":
        out.update(label="RATE_LIMIT", reason=attempt.why)
        return out
    if pinned and attempt.served_model and attempt.served_model != pinned:
        out.update(label="INVALID", reason=f"pin_mismatch served={attempt.served_model}")
        return out
    if case.expected == "answer" and case.oracle_error:
        out.update(label="ORACLE_ERROR", reason=case.oracle_error)
        return out
    if attempt.kind in {"abstain", "refusal"}:
        reason = "correct_refusal" if case.expected == "refuse" else attempt.why
        out.update(label="ABSTAIN", abstain_reason=reason or "abstain", reason=attempt.why)
        return out
    if attempt.kind == "ungranted":
        out.update(label="WRONG", wrong_kind="ungranted_table", reason=attempt.why)
        return out
    if attempt.kind == "invalid":
        if case.expected == "refuse":
            out.update(
                label="ABSTAIN",
                abstain_reason="correct_refusal",
                reason=f"invalid sql on a trap: {attempt.why}",
            )
        else:
            out.update(label="INVALID", reason=attempt.exec_error or attempt.why)
        return out
    if case.expected == "refuse":
        out.update(
            label="WRONG",
            wrong_kind="served_trap",
            reason=f"served {len(attempt.rows or [])} rows on a refusal case",
        )
        return out
    bucket, reason = grade52.compare_rows(
        case.gold or [],
        attempt.rows or [],
        ordered=case.order_keys is not None,
        order_keys=case.order_keys,
    )
    if bucket == grade52.BUCKET_CORRECT:
        out.update(label="CORRECT", reason=reason)
    else:
        out.update(
            label="WRONG", wrong_kind=reason.split(" ")[0] if reason else "rows", reason=reason
        )
    return out


def clopper_pearson_upper(wrong: int, n: int, alpha: float = 0.05) -> float:
    """One-sided 95 pct upper bound on the wrong rate; rule of three at zero."""
    if n <= 0:
        return 1.0
    if wrong == 0:
        return min(1.0, 3.0 / n)
    if wrong >= n:
        return 1.0
    low, high = wrong / n, 1.0
    for _ in range(80):
        mid = (low + high) / 2
        cdf = sum(math.comb(n, k) * mid**k * (1 - mid) ** (n - k) for k in range(wrong + 1))
        if cdf > alpha:
            low = mid
        else:
            high = mid
    return high


def summarise(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cells: dict[tuple[str, str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["model"]),
            str(row["strategy"]),
            str(row["effort"]),
            str(row.get("context", "schema")),
            str(row["round"]),
        )
        cells.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for (model, strategy, effort, context, rnd), items in sorted(cells.items()):
        count = {label: sum(1 for r in items if r["label"] == label) for label in LABELS}
        answer_cases = [r for r in items if r["expected"] == "answer"]
        traps = [r for r in items if r["expected"] == "refuse"]
        answered = count["CORRECT"] + count["WRONG"]
        out.append(
            {
                "model": model,
                "strategy": strategy,
                "effort": effort,
                "context": context,
                "round": rnd,
                **{label.lower(): count[label] for label in LABELS},
                "answer_cases": len(answer_cases),
                "trap_cases": len(traps),
                "trap_refused": sum(1 for r in traps if r["label"] == "ABSTAIN"),
                "answered": answered,
                "precision_on_answered": round(count["CORRECT"] / answered, 4)
                if answered
                else None,
                "coverage": round(
                    sum(1 for r in answer_cases if r["label"] == "CORRECT") / len(answer_cases), 4
                )
                if answer_cases
                else None,
                "wrong_ub95": round(clopper_pearson_upper(count["WRONG"], answered), 4)
                if answered
                else None,
                "model_calls": sum(int(r["model_calls"]) for r in items),
                "input_tokens": sum(int(r["input_tokens"]) for r in items),
                "output_tokens": sum(int(r["output_tokens"]) for r in items),
            }
        )
    return out


# ---------------------------------------------------------------- run


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def harness_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def run(
    transport: Transport,
    *,
    models: Sequence[str],
    strategies: Sequence[str],
    efforts: Sequence[str],
    rounds: int,
    note: str = "",
    only: Sequence[str] = (),
    workdir: Path | None = None,
    context: str = "schema",
    as_of: str = "",
) -> list[dict[str, Any]]:
    """``as_of`` pins the date in the prompt and the ``$as_of`` bind, so a recording
    replays on a later day; empty means the warehouse's today."""
    tmp_ctx = tempfile.TemporaryDirectory() if workdir is None else None
    folder = Path(tmp_ctx.name) if tmp_ctx else workdir
    assert folder is not None
    con, today, serving = grade52._open_warehouse(folder)
    as_of = as_of or today
    index_cache: dict[str, Any] = {}
    try:
        cases = load_cases(con, as_of)
        if only:
            cases = [case for case in cases if case.qid in set(only)]
        if context == "product":
            con = warm_product_index(con, serving, cases, index_cache)
        meta = {
            "dataset": "curated_ceo_52",
            "pack_hash": pack_hash()[:16],
            "dms_sha": _git_sha(),
            "harness_sha": harness_sha(),
            "harness_note": note or HARNESS_VERSION,
            "as_of": as_of,
        }
        rows: list[dict[str, Any]] = []
        for model in models:
            for strategy in strategies:
                for effort in efforts:
                    for rnd in range(1, rounds + 1):
                        for case in cases:
                            system = system_prompt(
                                con, case, as_of, context, serving=serving, cache=index_cache
                            )
                            bad = quarantine_violation(system, case, cases)
                            if bad:
                                raise SystemExit(f"quarantine: {bad}")
                            attempt = run_strategy(
                                strategy,
                                transport,
                                con,
                                case,
                                as_of,
                                model=model,
                                effort=effort,
                                system=system,
                            )
                            pinned = model if transport.provider == "openvault" else ""
                            label = grade(case, attempt, pinned=pinned)
                            rows.append(
                                {
                                    **meta,
                                    "model": model,
                                    "provider": transport.provider,
                                    "served_model": attempt.served_model,
                                    "served_provider": attempt.served_provider,
                                    "key_ref": transport.key_ref,
                                    "strategy": strategy,
                                    "effort": effort,
                                    "context": context,
                                    "round": rnd,
                                    "qid": case.qid,
                                    "space": case.space,
                                    "expected": case.expected,
                                    **label,
                                    "sql": attempt.sql,
                                    "row_hash": row_hash(attempt.rows)
                                    if attempt.rows is not None
                                    else "",
                                    "served_n": len(attempt.rows or []),
                                    "gold_n": len(case.gold or []),
                                    "plan_source": attempt.plan_source,
                                    "latency_ms": round(attempt.latency_ms, 1),
                                    "input_tokens": attempt.input_tokens,
                                    "output_tokens": attempt.output_tokens,
                                    "cache_read_tokens": attempt.cache_read_tokens,
                                    "model_calls": attempt.calls,
                                    "model_calls_ok": attempt.calls_ok,
                                }
                            )
        return rows
    finally:
        con.close()
        if tmp_ctx:
            tmp_ctx.cleanup()


def sizes(out: Path, context: str = "schema") -> dict[str, Any]:
    """Rendered prompt sizes per Space, no model call. Tokens need count_tokens."""
    from dms_executor.schema_context import estimate_tokens

    with tempfile.TemporaryDirectory() as tmp:
        con, as_of, serving = grade52._open_warehouse(Path(tmp))
        index_cache: dict[str, Any] = {}
        try:
            cases = load_cases(con, as_of)
            per_space: dict[str, dict[str, Any]] = {}
            if context == "product":
                con = warm_product_index(con, serving, cases, index_cache)
            for case in cases:
                system = system_prompt(
                    con, case, as_of, context, serving=serving, cache=index_cache
                )
                entry = per_space.setdefault(
                    case.space,
                    {
                        "system_chars": len(system),
                        "system_est_tokens": estimate_tokens(system),
                        "questions": 0,
                        "question_chars": 0,
                    },
                )
                entry["questions"] += 1
                entry["question_chars"] += len(case.question)
        finally:
            con.close()
    report = {
        "harness": HARNESS_VERSION,
        "estimator": "dms_executor.schema_context.estimate_tokens",
        "spaces": per_space,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "sizes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def write_outputs(out: Path, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "attempts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    summary = summarise(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _transport(args: argparse.Namespace) -> Transport:
    egress = set(args.allow_egress or [])
    if args.transport == "replay":
        if not args.record:
            raise SystemExit("--transport replay needs --record DIR")
        return ReplayTransport(Path(args.record))
    if args.transport == "anthropic":
        if "anthropic" not in egress:
            raise SystemExit(
                "anthropic sends prompts off this machine: pass --allow-egress anthropic"
            )
        live: Transport = AnthropicTransport()
    elif args.transport == "openvault":
        if "openvault" not in egress:
            raise SystemExit(
                "openvault forwards prompts to providers: pass --allow-egress openvault"
            )
        live = OpenVaultTransport()
    else:
        raise SystemExit(f"unknown transport {args.transport}")
    return RecordingTransport(live, Path(args.record)) if args.record else live


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--transport", choices=("replay", "anthropic", "openvault"), default="replay"
    )
    parser.add_argument("--allow-egress", action="append", choices=("anthropic", "openvault"))
    parser.add_argument("--models", nargs="+", default=[])
    parser.add_argument("--strategy", nargs="+", default=["s0"], choices=STRATEGIES)
    parser.add_argument("--effort", nargs="+", default=["medium"], choices=EFFORTS)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--as-of", default="", help="YYYY-MM-DD; pin it to replay a recording on a later day"
    )
    parser.add_argument(
        "--context",
        default="schema",
        choices=CONTEXTS,
        help="schema: names and types; values: plus codes of short text columns",
    )
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--record", help="record dir (write when live, read when replay)")
    parser.add_argument("--note", default="", help="harness-change note id")
    parser.add_argument("--sizes", action="store_true", help="prompt sizes only, no model")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.sizes:
        print(json.dumps(sizes(out, args.context), indent=2))
        return 0
    if not args.models:
        raise SystemExit("--models is required")
    rows = run(
        _transport(args),
        models=args.models,
        strategies=args.strategy,
        efforts=args.effort,
        rounds=args.rounds,
        note=args.note,
        only=args.only,
        context=args.context,
        as_of=args.as_of,
    )
    for cell in write_outputs(out, rows):
        print(json.dumps(cell))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
