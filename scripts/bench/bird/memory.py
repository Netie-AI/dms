"""Session memory for the held-out runner. Context only. Never an answer.

Confirmed sessions become examples. Known wrong solutions become failure
notes. Retrieval is token overlap. The model may read them. The runner
does not execute a reply that copies a retrieved example.

``DMS_HELDOUT_MEMORY`` defaults off. Only the digit ``1`` turns it on.
The daily call cap is 500. A compare does not use a smaller default.

Plain control is role ``sql``, compact schema, no examples, no clarify
instruction. The treatment adds retrieved context and asks for one SELECT
or a CLARIFY with two schema readings. OpenVault routes. No provider and
no model id are sent.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.bird.baseline import (
    ROLE,
    baseline_body,
    copied_model,
    escalate_body,
    reject_model_keys,
    sql_from_reply,
)
from bench.bird.gold import execute_readonly, locate_db, schema_block
from bench.bird.heldout import annotate, summarize
from bench.bird.run import climb_post
from bench.bird.score import attribute, classify

FLAG = "DMS_HELDOUT_MEMORY"
DAILY_CAP = 500
_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_SECRET = re.compile(r"ov_[A-Za-z0-9]|bearer\s+\S+|api[_-]?key", re.IGNORECASE)
_K1 = 1.5
_B = 0.75
_TOP = 3

Post = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, Any]]


def memory_on(env: Mapping[str, str] | None = None) -> bool:
    """True only for the digit 1. ``true`` and ``yes`` stay off."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip() == "1"


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def _dirty(text: str) -> bool:
    return bool(_SECRET.search(text))


def mine_sessions(path: Path) -> dict[str, list[dict[str, str]]]:
    """Read a session JSONL. Confirmed rows are examples. Wrong rows are notes.

    A row value is not a field we keep. A secret-looking line is dropped.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    examples: list[dict[str, str]] = []
    notes: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if not isinstance(row, dict):
            continue
        question = " ".join(str(row.get("question") or "").split())
        sql = " ".join(str(row.get("sql") or "").split())
        db_id = str(row.get("db_id") or "")
        kind = str(row.get("kind") or "")
        note = " ".join(str(row.get("note") or "").split())
        if not question or not sql or _dirty(question) or _dirty(sql) or _dirty(note):
            continue
        item = {"question": question, "sql": sql, "db_id": db_id, "note": note}
        if kind == "confirmed":
            examples.append(item)
        elif kind == "wrong":
            notes.append(item)
    return {"examples": examples, "notes": notes}


def _rank(
    query: str, rows: Sequence[Mapping[str, str]], *, exclude_db: str
) -> list[dict[str, str]]:
    pool = [dict(row) for row in rows if str(row.get("db_id") or "") != exclude_db]
    q_tokens = _tokens(query)
    if not pool or not q_tokens:
        return []
    docs = [_tokens(str(row.get("question") or "")) for row in pool]
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))
    n_docs = len(docs)
    avg = sum(len(doc) for doc in docs) / n_docs
    q_set = set(q_tokens)
    scored: list[tuple[float, int]] = []
    for index, doc in enumerate(docs):
        if not doc:
            continue
        counts = Counter(doc)
        score = 0.0
        for term in q_set:
            if term not in counts:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            freq = counts[term]
            denom = freq + _K1 * (1 - _B + _B * len(doc) / avg)
            score += idf * (freq * (_K1 + 1)) / denom
        if score > 0:
            scored.append((score, index))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [pool[index] for _score, index in scored[:_TOP]]


def retrieve(
    memory: Mapping[str, Sequence[Mapping[str, str]]], question: str, *, exclude_db: str
) -> dict[str, list[dict[str, str]]]:
    """Top examples and notes from other databases. Empty when nothing overlaps."""
    return {
        "examples": _rank(question, memory.get("examples") or [], exclude_db=exclude_db),
        "notes": _rank(question, memory.get("notes") or [], exclude_db=exclude_db),
    }


def _same_sql(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def copied_example(sql: str, examples: Sequence[Mapping[str, str]]) -> bool:
    return any(_same_sql(sql, str(row.get("sql") or "")) for row in examples)


def treatment_body(
    question: str,
    schema_text: str,
    pack: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, Any]:
    """Role ``sql``. Examples are context. The instruction is not a phrase list."""
    system = (
        "Write one SQLite SELECT that answers the question. "
        "Use only tables and columns in the schema. "
        "If the schema supports two readings, reply with the line CLARIFY "
        "and then two lines that name columns from the schema. "
        "Examples and failure notes are context from other databases. "
        "Do not copy an example statement. Return the SQL only, or CLARIFY."
    )
    examples = [
        {"question": row["question"], "sql": row["sql"]} for row in pack.get("examples") or []
    ]
    notes = [
        {"question": row["question"], "failed_sql": row["sql"], "note": row.get("note") or ""}
        for row in pack.get("notes") or []
    ]
    body: dict[str, Any] = {
        "role": ROLE,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": schema_text,
                        "question": question,
                        "examples": examples,
                        "failure_notes": notes,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        ],
    }
    reject_model_keys(body)
    return body


def parse_reply(payload: Any) -> tuple[str, str | list[str]]:
    """``sql`` plus text, or ``clarify`` plus reading lines. Not a question matcher."""
    text = sql_from_reply(payload).strip()
    if text.upper().startswith("CLARIFY"):
        readings = [line.strip() for line in text.splitlines()[1:] if line.strip()]
        return "clarify", readings[:4]
    return "sql", text


class Budget:
    """Counts posts. The cap is the daily limit, not a smoke size."""

    def __init__(self, cap: int) -> None:
        self.cap = cap if isinstance(cap, int) and cap > 0 else DAILY_CAP
        self.used = 0

    def room(self) -> bool:
        return self.used < self.cap

    def wrap(self, post: Post) -> Post:
        budget = self

        def wrapped(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            if budget.used >= budget.cap:
                return 0, {"error": "budget"}
            budget.used += 1
            return post(url, body, headers)

        return wrapped


def _stamp(payload: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "plan_origin": "generate_sql",
        "ladder_rung": "plan",
        "abstained": False,
    }
    model = copied_model(payload)
    if model:
        env["served_model"] = model
    return env


def _record(
    *,
    qid: str,
    db_id: str,
    arm: str,
    outcome: str,
    reason: str,
    path: str,
    rung: str,
    seconds: float,
    ex: int | None,
) -> dict[str, Any]:
    return annotate(
        {
            "id": qid,
            "db_id": db_id,
            "arm": arm,
            "outcome": outcome,
            "ex": ex,
            "ex_norm": ex,
            "reason": reason,
            "path": path,
            "rung": rung,
            "escalation": rung if rung in {"escalate", "retry"} else "plan",
            "stage_code": reason if reason in {"clarify", "stored_replay"} else None,
            "contaminated": False,
            "seconds": seconds,
            "latency_ms": int(round(seconds * 1000)),
            "cost": None,
            "http_status": 200,
            "yes": False,
            "evidence": False,
        }
    )


def grade_reply(
    *,
    qid: str,
    db_id: str,
    arm: str,
    gold_sql: str,
    db_file: Path,
    payload: Any,
    rung: str,
    examples: Sequence[Mapping[str, str]],
    seconds: float,
) -> dict[str, Any]:
    kind, parsed = parse_reply(payload)
    if kind == "clarify":
        return _record(
            qid=qid,
            db_id=db_id,
            arm=arm,
            outcome="ABSTAIN",
            reason="clarify",
            path="unattributed",
            rung=rung,
            seconds=seconds,
            ex=0,
        )
    sql = parsed if isinstance(parsed, str) else ""
    if arm == "treatment" and copied_example(sql, examples):
        return _record(
            qid=qid,
            db_id=db_id,
            arm=arm,
            outcome="ABSTAIN",
            reason="stored_replay",
            path="unattributed",
            rung=rung,
            seconds=seconds,
            ex=0,
        )
    predicted, pred_error = execute_readonly(db_file, sql) if sql else (None, "empty_sql")
    gold_rows, gold_error = execute_readonly(db_file, gold_sql)
    env = _stamp(payload)
    graded = classify(
        http_status=200,
        error=None,
        envelope=env,
        predicted=predicted,
        gold_rows=gold_rows,
        pred_error=pred_error,
        gold_error=gold_error,
        model_sql=None,
    )
    if graded.get("outcome") in {"CORRECT", "WRONG"}:
        graded["path"] = attribute(env, None)
    else:
        graded["path"] = "unattributed"
    graded.update(
        {
            "id": qid,
            "db_id": db_id,
            "arm": arm,
            "rung": rung,
            "escalation": rung if rung in {"escalate", "retry"} else "plan",
            "seconds": seconds,
            "latency_ms": int(round(seconds * 1000)),
            "cost": None,
            "http_status": 200,
            "contaminated": False,
            "stage_code": None,
            "yes": False,
            "evidence": False,
        }
    )
    return annotate(graded)


def control_body(question: str, schema_text: str) -> dict[str, Any]:
    """Plain Claude control: schema and question. No examples."""
    return baseline_body(question, schema_text)


def format_compare(control: Mapping[str, Any], treatment: Mapping[str, Any] | None) -> str:
    def line(name: str, summary: Mapping[str, Any]) -> str:
        return (
            f"{name} WRONG={summary.get('wrong')} "
            f"heldout_correct={summary.get('heldout_correct')} "
            f"ABSTAIN={summary.get('abstain')} "
            f"rule_fail={summary.get('rule_fail')} "
            f"n={summary.get('n_total')} "
            f"heldout_ex={summary.get('heldout_ex')}"
        )

    parts = [line("CONTROL", control)]
    if treatment is None:
        parts.append("TREATMENT not_run memory=off")
    else:
        parts.append(line("TREATMENT", treatment))
    parts.append("pass=false target=null improvement=unmeasured")
    return "\n".join(parts)


def _write_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE items (n INTEGER, name TEXT)")
    con.execute("INSERT INTO items VALUES (1, 'RowTokenZZ')")
    con.commit()
    con.close()


def run_compare_check(root: Path) -> dict[str, Any]:
    """Scripted arms. Same reply scores match. A copied example is not served.

    This does not call Claude and does not claim an accuracy gain.
    """
    db = root / "shop" / "shop.sqlite"
    _write_db(db)
    sessions = root / "sessions.jsonl"
    sessions.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "confirmed",
                        "db_id": "archive",
                        "question": "count items in the archive",
                        "sql": "SELECT 999",
                    }
                ),
                json.dumps(
                    {
                        "kind": "wrong",
                        "db_id": "archive",
                        "question": "count items the wrong way",
                        "sql": "SELECT name FROM items",
                        "note": "sqlite_error",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    memory = mine_sessions(sessions)
    assert memory_on({"DMS_HELDOUT_MEMORY": "1"})
    assert not memory_on({"DMS_HELDOUT_MEMORY": "true"})
    question = {
        "question_id": "c1",
        "db_id": "shop",
        "question": "count items",
        "SQL": "SELECT COUNT(*) FROM items",
    }
    schema = schema_block(db, sample_rows=0)
    assert "RowTokenZZ" not in schema
    pack = retrieve(memory, str(question["question"]), exclude_db="shop")
    assert pack["examples"] and pack["examples"][0]["sql"] == "SELECT 999"
    control = control_body(str(question["question"]), schema)
    treated = treatment_body(str(question["question"]), schema, pack)
    control_blob = json.dumps(control)
    treated_blob = json.dumps(treated)
    assert "SELECT 999" not in control_blob
    assert "SELECT 999" in treated_blob
    assert "CLARIFY" in treated_blob
    assert "provider" not in treated
    assert "model" not in treated

    def same_sql(_url: str, body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, dict]:
        del body
        return 200, {
            "choices": [{"message": {"content": "SELECT COUNT(*) FROM items"}}],
            "served_model": "copied-from-openvault",
        }

    budget = Budget(DAILY_CAP)
    equal = _pair(question, db.parent.parent, memory, same_sql, budget)
    assert budget.used == 2
    assert equal["control"]["heldout"] == "correct"
    assert equal["treatment"]["heldout"] == "correct"
    assert equal["control"]["outcome"] == "CORRECT"
    assert equal["treatment"]["outcome"] == "CORRECT"

    def copy_on_treatment(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict]:
        del url, headers
        blob = json.dumps(body)
        content = "SELECT 999" if "failure_notes" in blob else "SELECT COUNT(*) FROM items"
        return 200, {
            "choices": [{"message": {"content": content}}],
            "served_model": "copied-from-openvault",
        }

    replay_budget = Budget(DAILY_CAP)
    replay = _pair(question, db.parent.parent, memory, copy_on_treatment, replay_budget)
    assert replay["control"]["heldout"] == "correct"
    assert replay["treatment"]["reason"] == "stored_replay"
    assert replay["treatment"]["stage"] == "stored_replay"
    assert replay["treatment"]["outcome"] != "WRONG"

    tiny = Budget(2)
    second = {
        "question_id": "c2",
        "db_id": "shop",
        "question": "count items again",
        "SQL": "SELECT COUNT(*) FROM items",
    }
    ran = run_pairs([question, second], db.parent.parent, memory, same_sql, tiny)
    assert tiny.used == 2
    assert len(ran) == 1

    clarify_q = dict(question)
    clarify_q["question_id"] = "c3"

    def clarify_treatment(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict]:
        del url, headers
        blob = json.dumps(body)
        if "failure_notes" in blob:
            content = "CLARIFY\nitems.n\nitems.name"
        else:
            content = "SELECT 0"
        return 200, {
            "choices": [{"message": {"content": content}}],
            "served_model": "copied-from-openvault",
        }

    clarified = _pair(clarify_q, db.parent.parent, memory, clarify_treatment, Budget(DAILY_CAP))
    assert clarified["treatment"]["reason"] == "clarify"
    assert clarified["treatment"]["stage"] == "intent"
    assert clarified["treatment"]["heldout"] == "abstain"
    assert clarified["control"]["heldout"] == "wrong"
    leaked = json.dumps(clarified)
    assert "RowTokenZZ" not in leaked
    assert "SELECT 999" not in leaked
    return {
        "equal_wrong": 0,
        "replay_treatment": replay["treatment"]["heldout"],
        "clarify_treatment": clarified["treatment"]["heldout"],
        "control_wrong_on_clarify_case": 1 if clarified["control"]["heldout"] == "wrong" else 0,
        "budget_default": DAILY_CAP,
        "calls_on_cap_2": tiny.used,
    }


def _pair(
    question: Mapping[str, Any],
    db_root: Path,
    memory: Mapping[str, Sequence[Mapping[str, str]]],
    post: Post,
    budget: Budget,
) -> dict[str, Any]:
    rows = run_pairs([question], db_root, memory, post, budget)
    if len(rows) != 1:
        raise AssertionError("pair")
    return rows[0]


def run_pairs(
    questions: Sequence[Mapping[str, Any]],
    db_root: Path,
    memory: Mapping[str, Sequence[Mapping[str, str]]],
    post: Post,
    budget: Budget,
    *,
    ov_url: str = "http://ov.example",
) -> list[dict[str, Any]]:
    """One control call and one treatment call per question, while the cap allows both."""
    sender = budget.wrap(post)
    out: list[dict[str, Any]] = []
    for question in questions:
        if budget.used + 2 > budget.cap:
            break
        db_id = str(question.get("db_id") or "")
        qid = str(question.get("question_id") or "")
        text = str(question.get("question") or "")
        gold = str(question.get("SQL") or "")
        db_file = locate_db(db_root, db_id)
        schema = schema_block(db_file, sample_rows=0)
        pack = retrieve(memory, text, exclude_db=db_id)
        control = _ask(
            qid=qid,
            db_id=db_id,
            arm="control",
            gold_sql=gold,
            db_file=db_file,
            body=control_body(text, schema),
            examples=[],
            post=sender,
            ov_url=ov_url,
        )
        treatment = _ask(
            qid=qid,
            db_id=db_id,
            arm="treatment",
            gold_sql=gold,
            db_file=db_file,
            body=treatment_body(text, schema, pack),
            examples=pack["examples"],
            post=sender,
            ov_url=ov_url,
        )
        out.append({"control": control, "treatment": treatment})
    return out


def _ask(
    *,
    qid: str,
    db_id: str,
    arm: str,
    gold_sql: str,
    db_file: Path,
    body: dict[str, Any],
    examples: Sequence[Mapping[str, str]],
    post: Post,
    ov_url: str,
) -> dict[str, Any]:
    import time

    started = time.perf_counter()
    url = ov_url.rstrip("/") + "/v1/chat/completions"

    def escalate() -> tuple[int, Any]:
        return post(url, escalate_body(body), {})

    _status, payload, rung = climb_post(
        post,
        url,
        body,
        {},
        escalate=escalate,
    )
    return grade_reply(
        qid=qid,
        db_id=db_id,
        arm=arm,
        gold_sql=gold_sql,
        db_file=db_file,
        payload=payload,
        rung=rung,
        examples=examples,
        seconds=time.perf_counter() - started,
    )


def arm_summary(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    return summarize([row[key] for row in rows if key in row])


def write_pairs(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Ids and labels only. No SQL, no question text, no keys."""
    keep = ("id", "db_id", "arm", "heldout", "outcome", "reason", "stage", "ex", "escalation")
    lines: list[str] = []
    for pair in rows:
        for key in ("control", "treatment"):
            row = pair.get(key)
            if not isinstance(row, Mapping):
                continue
            lines.append(json.dumps({name: row.get(name) for name in keep}, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

