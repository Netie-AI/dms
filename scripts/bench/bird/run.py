"""Run a Mini-Dev arm. Flag ``DMS_BIRD_HARNESS`` defaults off.

Off, the command sends no ask, writes no artifact, and leaves the product
prompt, envelope, and served route untouched. On, ``dms`` posts
``/v1/chat/ask`` (and ``/v1/chat/ask/confirm`` when simulating Yes).
``baseline`` is one OpenVault call with role ``sql``.

On 402 or 429 the dms arm retries ``POST /v1/chat/ask`` with role
``escalate``. OpenVault routes that role. The graded body is the DMS
envelope. A raw completion is not executed. One plain retry of the original
POST is the last rung. The baseline arm sends the same role to OpenVault,
because that arm is the raw model. A 402 or 429 that is still there after
the last rung stays in the denominator as infra_unscored. Finished ids are
skipped. An unresolved id is climbed again on a later run.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bench.bird.baseline import baseline_body, copied_model, escalate_body, sql_from_reply
from bench.bird.corpus import asked_text, db_id_of, gold_sql_of, question_id
from bench.bird.gold import SAMPLE_ROWS, execute_readonly, locate_db, schema_block
from bench.bird.score import (
    attribute,
    classify,
    confirm_token,
    contaminated,
    is_askback,
    latest_by_id,
)

FLAG = "DMS_BIRD_HARNESS"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EMPTY_SURFACE = {"prompt": None, "envelope": None, "route": None}
ARMS = ("dms", "baseline")
# ponytail: one same-POST retry after the escalate role. A 402 that survives
# that single retry stays infra_unscored. Raise this only with a test that the
# extra call still comes after the role.
LAST_RUNG_RETRIES = 1

PostJson = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, Any]]
_WRITE = threading.Lock()


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when the flag is the digit 1. Unset is off."""
    raw = (env if env is not None else os.environ).get(FLAG, "")
    return str(raw).strip() == "1"


def surface() -> dict[str, Any]:
    """Product prompt, envelope, and route. This runner never changes them."""
    return dict(EMPTY_SURFACE)


def detail_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    code = payload.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    return None


def post_json(
    url: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    """POST JSON. No timeout. The body of an error is not returned, only a code."""
    data = json.dumps(body).encode("utf-8")
    header = {"Content-Type": "application/json"}
    if headers:
        header.update(headers)
    request = urllib.request.Request(url, data=data, headers=header, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=None) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    except urllib.error.URLError:
        return 0, {"error": "transport"}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return status, {"error": "not_json"}
    return status, parsed


def _stamp_from_baseline(payload: Any) -> dict[str, Any]:
    """Stamps for path_group. The model name is copied only if OpenVault sent one."""
    model = copied_model(payload)
    env: dict[str, Any] = {
        "plan_origin": "generate_sql",
        "ladder_rung": "plan",
        "abstained": False,
    }
    if model:
        env["served_model"] = model
    return env


def _call_contaminated(status: int | None, payload: Any) -> bool:
    """402 or 429 on this response. A 200 envelope is not read for those tokens."""
    error = None
    if isinstance(payload, dict) and payload.get("error") == "transport":
        error = "transport"
    if contaminated(status, error):
        return True
    if status == 200:
        return False
    return contaminated(None, detail_code(payload))


def _raw_completion(payload: Any) -> bool:
    """A chat-completion body. It is not a DMS envelope and is not graded."""
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        return False
    if "rows" in payload or "sql_used" in payload or "abstained" in payload:
        return False
    if payload.get("reconfirm") or payload.get("badge"):
        return False
    return True


def climb_post(
    post: PostJson,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    escalate: Callable[[], tuple[int, Any] | None],
    accept: Callable[[int, Any], bool] | None = None,
) -> tuple[int, Any, str]:
    """Original POST, then the escalate call, then one retry of the original POST.

    ``accept`` rejects a body that must not count as an answer. A raw
    completion fails it, so the dms arm does not stop on one. The same-POST
    retry is only the last rung, and only while the failure is still 402 or
    429 or a rejected body.
    """

    def answered(status: int, payload: Any) -> bool:
        if _call_contaminated(status, payload):
            return False
        if accept is not None and not accept(status, payload):
            return False
        return True

    status, payload = post(url, body, headers)
    if answered(status, payload):
        return status, payload, "first"
    escalated = escalate()
    if escalated is not None:
        status, payload = escalated
        if answered(status, payload):
            return status, payload, "escalate"
    last_status, last_payload = status, payload
    for _ in range(LAST_RUNG_RETRIES):
        last_status, last_payload = post(url, body, headers)
        if answered(last_status, last_payload):
            return last_status, last_payload, "retry"
    return last_status, last_payload, "unresolved"


def grade_item(
    question: Mapping[str, Any],
    *,
    arm: str,
    db_root: Path,
    space_id: str | None,
    evidence: bool,
    simulate_yes: bool,
    post: PostJson,
    api_base: str,
    ov_url: str,
    ov_headers: dict[str, str],
    compact: bool = False,
) -> dict[str, Any]:
    """One question. The record stores ids and codes, never rows or SQL."""
    qid = question_id(question)
    database = db_id_of(question)
    record: dict[str, Any] = {
        "id": qid,
        "db_id": database,
        "arm": arm,
        "evidence": evidence,
        "yes": False,
        "contaminated": False,
    }
    started = time.perf_counter()
    try:
        path = locate_db(db_root, database)
    except (ValueError, FileNotFoundError):
        record.update(
            classify(
                http_status=200,
                error=None,
                envelope=None,
                predicted=None,
                gold_rows=None,
                pred_error="db_missing",
                gold_error=None,
            )
        )
        record["seconds"] = time.perf_counter() - started
        record["http_status"] = 200
        _fill_metrics(record, payload=None, envelope=None, pred_error="db_missing", rung="unknown")
        return _public(record)
    gold_sql = gold_sql_of(question)
    text = asked_text(question, evidence=evidence)
    held: dict[str, Any] = {
        "http_status": 200,
        "error": None,
        "code": None,
        "envelope": None,
        "predicted": None,
        "pred_error": None,
        "model_sql": None,
        "rung": "first",
    }

    def note(status: int, payload: Any) -> None:
        held["http_status"] = status
        held["code"] = detail_code(payload) if status != 200 else None
        held["error"] = None
        if isinstance(payload, dict) and payload.get("error") == "transport":
            held["error"] = "transport"

    def blocked() -> bool:
        return _status_bad(held["http_status"], held["error"], held["code"])

    def take_completion(payload: Any) -> None:
        sql = sql_from_reply(payload)
        held["model_sql"] = sql or None
        held["envelope"] = _stamp_from_baseline(payload)
        if sql:
            held["predicted"], held["pred_error"] = execute_readonly(path, sql)
        else:
            held["predicted"] = None
            held["pred_error"] = "empty_sql"

    def take_envelope(payload: Any) -> None:
        ok = (
            isinstance(payload, dict)
            and "detail" not in payload
            and payload.get("error") not in {"not_json", "transport"}
        )
        held["envelope"] = payload if ok else None
        env = held["envelope"]
        if not isinstance(env, dict):
            return
        used = env.get("sql_used")
        if isinstance(used, str) and used.strip():
            held["model_sql"] = used
            held["predicted"], held["pred_error"] = execute_readonly(path, used)
            return
        if is_askback(env) or env.get("abstained") is True:
            return
        rows = env.get("rows")
        if isinstance(rows, list):
            held["predicted"] = rows
            held["pred_error"] = None

    payload: Any = None
    samples = 0 if compact else SAMPLE_ROWS
    if arm == "baseline":
        body = baseline_body(text, schema_block(path, sample_rows=samples))

        def escalate_same() -> tuple[int, Any] | None:
            if not ov_url:
                return None
            return post(
                ov_url.rstrip("/") + "/v1/chat/completions",
                escalate_body(body),
                ov_headers,
            )

        status, payload, rung = climb_post(
            post,
            ov_url.rstrip("/") + "/v1/chat/completions",
            body,
            ov_headers,
            escalate=escalate_same,
        )
        held["rung"] = rung
        note(status, payload)
        if not blocked():
            take_completion(payload)
    elif not space_id:
        held["pred_error"] = "space_missing"
    else:
        ask_url = api_base.rstrip("/") + "/v1/chat/ask"
        ask_body = {"question": text, "space_id": space_id}

        def escalate_ask() -> tuple[int, Any]:
            """Same ask, role ``escalate``. DMS runs the pipeline. No model id."""
            return post(ask_url, escalate_body(ask_body), {})

        def dms_body(_status: int, payload: Any) -> bool:
            return not _raw_completion(payload)

        status, payload, rung = climb_post(
            post,
            ask_url,
            ask_body,
            {},
            escalate=escalate_ask,
            accept=dms_body,
        )
        held["rung"] = rung
        note(status, payload)
        if not blocked() and not _raw_completion(payload):
            env = payload if isinstance(payload, dict) else None
            if (
                simulate_yes
                and isinstance(env, dict)
                and is_askback(env)
                and confirm_token(env)
            ):
                confirm_body = {"token": confirm_token(env), "yes": True}
                status, payload, confirm_rung = climb_post(
                    post,
                    api_base.rstrip("/") + "/v1/chat/ask/confirm",
                    confirm_body,
                    {},
                    escalate=escalate_ask,
                    accept=dms_body,
                )
                held["rung"] = confirm_rung
                note(status, payload)
                if not blocked() and not _raw_completion(payload):
                    if confirm_rung != "escalate":
                        record["yes"] = True
                    take_envelope(payload)
            else:
                take_envelope(payload)
    http_status = held["http_status"]
    error = held["error"]
    code = held["code"]
    envelope = held["envelope"]
    predicted = held["predicted"]
    pred_error = held["pred_error"]
    model_sql = held["model_sql"]
    gold_rows, gold_error = (None, None)
    if not _status_bad(http_status, error, code) and pred_error != "space_missing":
        gold_rows, gold_error = execute_readonly(path, gold_sql)
    graded = classify(
        http_status=http_status,
        error=error,
        envelope=envelope,
        predicted=predicted,
        gold_rows=gold_rows,
        pred_error=pred_error,
        gold_error=gold_error,
        detail_code=code,
        model_sql=model_sql,
    )
    record.update(graded)
    record["rung"] = held["rung"]
    if record.get("outcome") in {"CORRECT", "WRONG"} and isinstance(envelope, dict):
        record["path"] = attribute(envelope, model_sql)
    record["http_status"] = http_status
    record["seconds"] = time.perf_counter() - started
    _fill_metrics(
        record,
        payload=payload if isinstance(payload, dict) else None,
        envelope=envelope if isinstance(envelope, dict) else None,
        pred_error=pred_error if isinstance(pred_error, str) else None,
        rung=str(held["rung"] or ""),
    )
    return _public(record)


def _status_bad(status: int | None, error: str | None, code: str | None) -> bool:
    if contaminated(status, error) or contaminated(None, code):
        return True
    if status == 0 or (isinstance(status, int) and status >= 400):
        return True
    return False


_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
# Short codes only. The question text is not an input.
_STAGE_CODES = frozenset(
    {
        "sql_relation_not_granted",
        "ungranted",
        "db_missing",
        "space_missing",
        "outside_grounded_scope",
        "unverified_join",
        "no_declared_measure",
        "unknown_measure",
        "join_unconfirmed",
        "not_in_ontology",
        "missing_join",
        "missing_metric",
        "empty_sql",
        "sqlite_error",
        "no_result",
    }
)


def _usage_cost(payload: Mapping[str, Any] | None) -> int | float | None:
    """A number OpenVault already returned under ``usage``. Absent stays null."""
    if not isinstance(payload, Mapping):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    for key in ("cost", "total_cost", "usd"):
        raw = usage.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        return raw
    return None


def _escalation(envelope: Mapping[str, Any] | None, rung: str) -> str:
    """The step that answered. A repair rung wins over the stamp inside the body."""
    if rung in {"escalate", "retry"}:
        return rung
    if isinstance(envelope, Mapping):
        raw = envelope.get("ladder_rung")
        if isinstance(raw, str) and _TOKEN.fullmatch(raw):
            return raw
    if _TOKEN.fullmatch(rung):
        return rung
    return "unknown"


def _stage_code(envelope: Mapping[str, Any] | None, pred_error: str | None) -> str | None:
    if isinstance(envelope, Mapping):
        for key in ("abstain_reason", "reason", "code"):
            raw = envelope.get(key)
            if isinstance(raw, str) and raw in _STAGE_CODES:
                return raw
    if isinstance(pred_error, str) and pred_error in _STAGE_CODES:
        return pred_error
    return None


def _fill_metrics(
    record: dict[str, Any],
    *,
    payload: Mapping[str, Any] | None,
    envelope: Mapping[str, Any] | None,
    pred_error: str | None,
    rung: str,
) -> None:
    record["cost"] = _usage_cost(payload)
    record["escalation"] = _escalation(envelope, rung)
    record["stage_code"] = _stage_code(envelope, pred_error)
    seconds = record.get("seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        record["latency_ms"] = int(round(float(seconds) * 1000))
    else:
        record["latency_ms"] = None


def _public(record: Mapping[str, Any]) -> dict[str, Any]:
    """Drop anything that is not an id, a code, or a number."""
    keep = (
        "id",
        "db_id",
        "arm",
        "evidence",
        "yes",
        "contaminated",
        "outcome",
        "ex",
        "ex_norm",
        "reason",
        "path",
        "rung",
        "http_status",
        "seconds",
        "cost",
        "escalation",
        "latency_ms",
        "stage_code",
    )
    return {key: record.get(key) for key in keep}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def finished_ids(records: Sequence[Mapping[str, Any]]) -> set[str]:
    """Ids whose latest line is scored. Contaminated ids stay pending."""
    done: set[str] = set()
    for row in latest_by_id(records):
        if row.get("contaminated"):
            continue
        if row.get("outcome"):
            done.add(str(row.get("id")))
    return done


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_public(record), sort_keys=True) + "\n"
    with _WRITE:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def run_questions(
    questions: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    db_root: Path,
    spaces: Mapping[str, str],
    evidence: bool,
    simulate_yes: bool,
    concurrency: int,
    out: Path,
    post: PostJson | None = None,
    api_base: str = "",
    ov_url: str = "",
    ov_headers: dict[str, str] | None = None,
    prior: Sequence[Mapping[str, Any]] | None = None,
    compact: bool = False,
) -> list[dict[str, Any]]:
    """Grade pending questions. Writes one JSONL line each. Returns every line."""
    if arm not in ARMS:
        raise ValueError("arm")
    workers = concurrency if isinstance(concurrency, int) and concurrency > 0 else 1
    sender = post or post_json
    headers = ov_headers or {}
    done = finished_ids(list(prior or []))
    pending = [row for row in questions if question_id(row) not in done]
    written = list(prior or [])

    def one(question: Mapping[str, Any]) -> dict[str, Any]:
        return grade_item(
            question,
            arm=arm,
            db_root=db_root,
            space_id=spaces.get(db_id_of(question)),
            evidence=evidence,
            simulate_yes=simulate_yes and arm == "dms",
            post=sender,
            api_base=api_base,
            ov_url=ov_url,
            ov_headers=headers,
            compact=compact,
        )

    if workers == 1 or len(pending) <= 1:
        produced = [one(row) for row in pending]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            produced = list(pool.map(one, pending))
    for record in produced:
        append_jsonl(out, record)
        written.append(record)
    return written
