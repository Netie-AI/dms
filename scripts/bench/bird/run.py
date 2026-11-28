"""Run a Mini-Dev arm. Flag ``DMS_BIRD_HARNESS`` defaults off.

Off, the command sends no ask, writes no artifact, and leaves the product
prompt, envelope, and served route untouched. On, ``dms`` posts
``/v1/chat/ask`` (and ``/v1/chat/ask/confirm`` when simulating Yes).
``baseline`` is one OpenVault call with role ``sql``.

Records that contain 402 or 429 are contaminated. They are written so a later
run can retry them, and they are never scored. Finished ids are skipped.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bench.bird.baseline import baseline_body, copied_model, sql_from_reply
from bench.bird.corpus import asked_text, db_id_of, gold_sql_of, question_id
from bench.bird.gold import execute_readonly, locate_db, schema_block
from bench.bird.score import attribute, classify, confirm_token, is_askback, latest_by_id

FLAG = "DMS_BIRD_HARNESS"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EMPTY_SURFACE = {"prompt": None, "envelope": None, "route": None}
ARMS = ("dms", "baseline")

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
        return _public(record)
    gold_sql = gold_sql_of(question)
    text = asked_text(question, evidence=evidence)
    http_status = 200
    error: str | None = None
    code: str | None = None
    envelope: dict[str, Any] | None = None
    predicted: list[Any] | None = None
    pred_error: str | None = None
    model_sql: str | None = None
    if arm == "baseline":
        schema = schema_block(path)
        body = baseline_body(text, schema)
        http_status, payload = post(ov_url.rstrip("/") + "/v1/chat/completions", body, ov_headers)
        code = detail_code(payload) if http_status != 200 else None
        if isinstance(payload, dict) and payload.get("error") == "transport":
            error = "transport"
        if not _status_bad(http_status, error, code):
            sql = sql_from_reply(payload)
            model_sql = sql or None
            envelope = _stamp_from_baseline(payload)
            if sql:
                predicted, pred_error = execute_readonly(path, sql)
            else:
                pred_error = "empty_sql"
    else:
        if not space_id:
            pred_error = "space_missing"
            http_status = 200
        else:
            ask_body = {"question": text, "space_id": space_id}
            http_status, payload = post(api_base.rstrip("/") + "/v1/chat/ask", ask_body, {})
            code = detail_code(payload) if http_status != 200 else None
            if isinstance(payload, dict) and payload.get("error") == "transport":
                error = "transport"
                envelope = None
            elif (
                isinstance(payload, dict)
                and "detail" not in payload
                and payload.get("error") != "not_json"
            ):
                envelope = payload
            else:
                envelope = None
            if (
                simulate_yes
                and isinstance(envelope, dict)
                and is_askback(envelope)
                and confirm_token(envelope)
                and not _status_bad(http_status, error, code)
            ):
                token = confirm_token(envelope)
                http_status, payload = post(
                    api_base.rstrip("/") + "/v1/chat/ask/confirm",
                    {"token": token, "yes": True},
                    {},
                )
                record["yes"] = True
                code = detail_code(payload) if http_status != 200 else None
                if isinstance(payload, dict) and payload.get("error") == "transport":
                    error = "transport"
                    envelope = None
                elif (
                    isinstance(payload, dict)
                    and "detail" not in payload
                    and payload.get("error") != "not_json"
                ):
                    envelope = payload
                else:
                    envelope = None
            if isinstance(envelope, dict):
                used = envelope.get("sql_used")
                if isinstance(used, str) and used.strip():
                    model_sql = used
                    predicted, pred_error = execute_readonly(path, used)
                elif not is_askback(envelope) and envelope.get("abstained") is not True:
                    rows = envelope.get("rows")
                    if isinstance(rows, list):
                        predicted = rows
                        pred_error = None
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
    if record.get("outcome") in {"CORRECT", "WRONG"} and isinstance(envelope, dict):
        record["path"] = attribute(envelope, model_sql)
    record["http_status"] = http_status
    record["seconds"] = time.perf_counter() - started
    return _public(record)


def _status_bad(status: int | None, error: str | None, code: str | None) -> bool:
    from bench.bird.score import contaminated

    if contaminated(status, error) or contaminated(None, code):
        return True
    if status == 0 or (isinstance(status, int) and status >= 400):
        return True
    return False


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
        "http_status",
        "seconds",
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
