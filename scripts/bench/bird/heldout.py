"""Held-out tally on the Mini-Dev runner. Not a second harness.

Official EX still counts a matching row set as CORRECT, including when a
rule served it. Held-out credit is model-served only. A rule-served match
is ``rule_fail``: a failure, and not a WRONG number. A served row set that
disagrees with gold stays WRONG on every path.

``stub`` answers nothing. It abstains, so a CI run cannot invent a correct.
``live`` is one OpenVault call with role ``sql``, then ``escalate``. The
body has no provider and no model id. OpenVault routes. The schema text is
names and types only.

Spider 2.0-lite questions and gold SQL ship under the MIT licence in
``xlang-ai/Spider2``. This module does not download them and does not
download the SQLite files. A local unpack is scored only for databases
that are on disk. A question with no gold SQL is unscored, stays in the
denominator, and is not WRONG. Cloud databases are excluded. That run is
not a Spider leaderboard score. ``pass`` stays false.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.bird.corpus import db_id_of, question_id
from bench.bird.ex import nearest_rank
from bench.bird.run import grade_item, load_jsonl, run_questions
from bench.bird.score import report as official_report

SCHEMA = frozenset(
    {
        "sql_relation_not_granted",
        "ungranted",
        "db_missing",
        "space_missing",
        "outside_grounded_scope",
    }
)
ONTOLOGY = frozenset(
    {
        "unverified_join",
        "no_declared_measure",
        "unknown_measure",
        "join_unconfirmed",
        "not_in_ontology",
        "missing_join",
        "missing_metric",
    }
)
SQL_GEN = frozenset({"empty_sql", "sqlite_error", "no_result"})
REPAIR = frozenset({"escalate", "retry"})
FAIL_STAGES = (
    "schema_retrieval",
    "ontology_mapping",
    "sql_generation",
    "self_correction",
    "rule_served",
    "unknown",
)
SPIDER_LICENCE = "MIT"
# ponytail: one local unpack, sqlite files the operator already has.
# Upgrade: a checksummed sqlite subset mirror if one is published without a login.


class HeldoutConfig(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SpiderSlice:
    questions: list[dict[str, Any]]
    db_root: Path
    sqlite_questions: int
    excluded_cloud: int
    gold_absent: int
    databases: int


def keys_present(env: Mapping[str, str] | None = None) -> bool:
    """True when an ``ov_`` key is set. The value is not returned."""
    source = os.environ if env is None else env
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = str(source.get(name, "")).strip()
        if raw.startswith("ov_"):
            return True
    return False


def live_block(env: Mapping[str, str] | None = None) -> str | None:
    """A config code when live must not start. None when the route can be called."""
    source = os.environ if env is None else env
    if not str(source.get("OPENVAULT_URL", "")).strip():
        return "openvault_url"
    if not keys_present(source):
        return "openvault_key"
    return None


def heldout_label(row: Mapping[str, Any]) -> str:
    """correct, wrong, abstain, rule_fail, error, unattributed, or unscored."""
    if row.get("contaminated"):
        return "unscored"
    if row.get("reason") == "gold_absent":
        return "unscored"
    outcome = row.get("outcome")
    path = row.get("path")
    if outcome == "WRONG":
        return "wrong"
    if outcome == "CORRECT" and path == "rule":
        return "rule_fail"
    if outcome == "CORRECT" and path == "ai":
        return "correct"
    if outcome == "CORRECT":
        return "unattributed"
    if outcome in {"ABSTAIN", "ASKBACK"}:
        return "abstain"
    if outcome == "ERROR":
        return "error"
    return "unscored"


def failure_stage(row: Mapping[str, Any]) -> str:
    """One stage token. The question text is not read."""
    label = str(row.get("heldout") or heldout_label(row))
    if label == "correct":
        return "answered"
    if label == "unscored":
        return "unscored"
    if label == "rule_fail":
        return "rule_served"
    code = row.get("stage_code")
    if code in SCHEMA:
        return "schema_retrieval"
    if code in ONTOLOGY:
        return "ontology_mapping"
    step = str(row.get("escalation") or row.get("rung") or "")
    if step in REPAIR:
        return "self_correction"
    if label == "wrong" or code in SQL_GEN:
        return "sql_generation"
    return "unknown"


def annotate(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["heldout"] = heldout_label(item)
    item["stage"] = failure_stage(item)
    return item


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """WRONG is first. Held-out correct excludes rule-served matches."""
    rows = [annotate(row) for row in records]
    labels: Counter[str] = Counter(str(row["heldout"]) for row in rows)
    stages: Counter[str] = Counter(
        str(row["stage"]) for row in rows if row["heldout"] not in {"correct", "unscored"}
    )
    official = official_report(rows)
    n_total = len(rows)
    correct = int(labels["correct"])
    databases = {str(row.get("db_id") or "") for row in rows}
    databases.discard("")
    durations = [
        float(row["seconds"])
        for row in rows
        if isinstance(row.get("seconds"), (int, float)) and not isinstance(row.get("seconds"), bool)
    ]
    costs = [
        float(row["cost"])
        for row in rows
        if isinstance(row.get("cost"), (int, float)) and not isinstance(row.get("cost"), bool)
    ]
    incomplete = official["status"] != "COMPLETE" or labels["unscored"] > 0
    return {
        "wrong": int(labels["wrong"]),
        "heldout_correct": correct,
        "rule_fail": int(labels["rule_fail"]),
        "abstain": int(labels["abstain"]),
        "error": int(labels["error"]),
        "unattributed": int(labels["unattributed"]),
        "unscored": int(labels["unscored"]),
        "n_total": n_total,
        "databases": len(databases),
        "heldout_ex": None if n_total == 0 else correct / n_total,
        "official_correct": int(official["outcomes"]["CORRECT"]),
        "official_wrong": int(official["outcomes"]["WRONG"]),
        "official_ex": official["ex"],
        "status": "INCOMPLETE" if incomplete else "COMPLETE",
        "p50": nearest_rank(durations, 50),
        "p95": nearest_rank(durations, 95),
        "cost_sum": None if not costs else sum(costs),
        "stages": {name: int(stages.get(name, 0)) for name in FAIL_STAGES},
        "pass": False,
        "target": None,
    }


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def format_heldout(summary: Mapping[str, Any]) -> str:
    stages = summary.get("stages") or {}
    stage_s = " ".join(f"{name}={int(stages.get(name, 0))}" for name in FAIL_STAGES)
    p50 = summary.get("p50")
    p50_s = "n/a" if p50 is None else f"{float(p50):.3f}"
    cost = summary.get("cost_sum")
    cost_s = "n/a" if cost is None else f"{float(cost):.6f}"
    head = (
        f"WRONG={summary.get('wrong')} "
        f"heldout_correct={summary.get('heldout_correct')} "
        f"rule_fail={summary.get('rule_fail')} "
        f"ABSTAIN={summary.get('abstain')} "
        f"ERROR={summary.get('error')} "
        f"unattributed={summary.get('unattributed')} "
        f"unscored={summary.get('unscored')} "
        f"n={summary.get('n_total')} "
        f"databases={summary.get('databases')} "
        f"heldout_ex={_num(summary.get('heldout_ex'))} "
        f"official_WRONG={summary.get('official_wrong')} "
        f"official_CORRECT={summary.get('official_correct')} "
        f"official_ex={_num(summary.get('official_ex'))} "
        f"cost_sum={cost_s} p50={p50_s} status={summary.get('status')}"
    )
    before = (
        "BEFORE official EX counts a rule-served match as CORRECT. "
        "AFTER held-out counts that match as rule_fail. WRONG is unchanged by that rule."
    )
    return head + "\n" + f"stages {stage_s}\n" + before


def _safe_name(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or name in {".", ".."} or name != Path(name).name:
        return None
    if "/" in name or "\\" in name:
        return None
    return name


def load_spider(root: Path) -> SpiderSlice:
    """SQLite slice of a local Spider 2.0-lite unpack. No download."""
    questions_path = root / "spider2-lite.jsonl"
    sqlite_root = root / "resource" / "databases" / "sqlite"
    gold_dir = root / "evaluation_suite" / "gold" / "sql"
    if not questions_path.is_file():
        raise HeldoutConfig("spider_questions_missing")
    if not sqlite_root.is_dir():
        raise HeldoutConfig("spider_sqlite_missing")
    on_disk = {path.name for path in sqlite_root.iterdir() if path.is_dir()}
    try:
        lines = questions_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise HeldoutConfig("spider_questions_missing") from None
    questions: list[dict[str, Any]] = []
    excluded = 0
    absent = 0
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            raise HeldoutConfig("spider_questions_json") from None
        if not isinstance(row, dict):
            continue
        db_name = _safe_name(row.get("db"))
        qid = _safe_name(row.get("instance_id"))
        if db_name is None or qid is None or db_name not in on_disk:
            excluded += 1
            continue
        gold_path = gold_dir / f"{qid}.sql"
        gold_sql = ""
        missing = True
        if gold_path.is_file():
            gold_sql = gold_path.read_text(encoding="utf-8").strip()
            missing = not bool(gold_sql)
        if missing:
            absent += 1
        question = row.get("question")
        questions.append(
            {
                "question_id": qid,
                "db_id": db_name,
                "question": question if isinstance(question, str) else "",
                "SQL": gold_sql,
                "gold_missing": missing,
            }
        )
    return SpiderSlice(
        questions=questions,
        db_root=sqlite_root,
        sqlite_questions=len(questions),
        excluded_cloud=excluded,
        gold_absent=absent,
        databases=len({str(item["db_id"]) for item in questions}),
    )


def unscored_record(question: Mapping[str, Any], *, arm: str) -> dict[str, Any]:
    return {
        "id": question_id(question),
        "db_id": db_id_of(question),
        "arm": arm,
        "evidence": False,
        "yes": False,
        "contaminated": False,
        "outcome": None,
        "ex": None,
        "ex_norm": None,
        "reason": "gold_absent",
        "path": "unattributed",
        "rung": "unknown",
        "http_status": None,
        "seconds": 0,
        "cost": None,
        "escalation": "unknown",
        "latency_ms": 0,
        "stage_code": None,
    }


def abstain_post(
    url: str, body: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any]]:
    """One abstain. The question string is not read. No SQL is invented."""
    del url, body, headers
    return 200, {"abstained": True, "badge": "ABSTAIN", "abstain_reason": "stub_no_model"}


def run_heldout(
    questions: Sequence[Mapping[str, Any]],
    *,
    dataset: str,
    provider: str,
    db_root: Path,
    out: Path,
    post: Callable[..., tuple[int, Any]] | None = None,
    api_base: str = "",
    ov_url: str = "",
    ov_headers: dict[str, str] | None = None,
    spaces: Mapping[str, str] | None = None,
    concurrency: int = 1,
) -> list[dict[str, Any]]:
    """Grade pending questions and overwrite ``out`` with held-out lines."""
    if provider not in {"stub", "live"}:
        raise HeldoutConfig("provider")
    arm = "baseline" if provider == "live" else "dms"
    pending = [row for row in questions if not row.get("gold_missing")]
    skipped = [unscored_record(row, arm=arm) for row in questions if row.get("gold_missing")]
    bound = dict(spaces or {})
    if provider == "stub":
        for row in pending:
            name = db_id_of(row)
            bound.setdefault(name, name)
        sender = post or abstain_post
    else:
        sender = post
    produced = run_questions(
        pending,
        arm=arm,
        db_root=db_root,
        spaces=bound,
        evidence=False,
        simulate_yes=False,
        concurrency=concurrency,
        out=out,
        post=sender,
        api_base=api_base or "http://stub",
        ov_url=ov_url,
        ov_headers=ov_headers,
        compact=provider == "live",
    )
    # run_questions already wrote the official lines. Replace them with the tally.
    annotated = [annotate(row) for row in list(skipped) + list(produced)]
    for row in annotated:
        row["dataset"] = dataset
    _write(out, annotated)
    return annotated


def _write(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = (
        "id",
        "db_id",
        "dataset",
        "arm",
        "heldout",
        "stage",
        "outcome",
        "ex",
        "path",
        "rung",
        "escalation",
        "stage_code",
        "reason",
        "seconds",
        "latency_ms",
        "cost",
        "contaminated",
        "http_status",
    )
    lines = []
    for record in records:
        item = {key: record.get(key) for key in keep}
        lines.append(json.dumps(item, sort_keys=True))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_scored(path: Path) -> dict[str, Any]:
    return summarize(load_jsonl(path))


_CELL = "RowTokenZZ"
_AI_MODEL = "copied-from-openvault"


def _sqlite(path: Path, statements: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(statements)
    con.commit()
    con.close()


def _completion(sql: str, *, cost: float | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "choices": [{"message": {"content": sql}}],
        "served_model": _AI_MODEL,
    }
    if cost is not None:
        body["usage"] = {"cost": cost}
    return body


def _queue_post(queue: list[tuple[int, dict[str, Any]]], seen: list[dict[str, Any]]):
    def post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        del url
        seen.append(body)
        if "provider" in body or "model" in body:
            raise AssertionError("provider")
        if _CELL in json.dumps(body):
            raise AssertionError("raw_cell")
        for value in headers.values():
            if str(value).startswith("ov_"):
                raise AssertionError("key")
        if not queue:
            raise AssertionError("extra_call")
        return queue.pop(0)

    return post


def run_check(root: Path) -> dict[str, Any]:
    """Four databases. Scripted answers. Not a Mini-Dev score and not live."""
    db = root / "db"
    _sqlite(
        db / "shop" / "shop.sqlite",
        f"CREATE TABLE items (n INTEGER, name TEXT); INSERT INTO items VALUES (1, '{_CELL}');",
    )
    _sqlite(
        db / "yard" / "yard.sqlite",
        f"CREATE TABLE plots (code TEXT); INSERT INTO plots VALUES ('{_CELL}');",
    )
    _sqlite(
        db / "dock" / "dock.sqlite",
        "CREATE TABLE boats (id INTEGER); INSERT INTO boats VALUES (1);",
    )
    _sqlite(
        db / "mill" / "mill.sqlite",
        "CREATE TABLE bins (k INTEGER); INSERT INTO bins VALUES (1);",
    )
    rule = {
        "badge": "L1_GOVERNED_METRIC",
        "plan_origin": "l1_governed_metric",
        "ladder_rung": "l1",
        "abstained": False,
        "rows": [{"n": 1}],
    }
    schema_abstain = {
        "abstained": True,
        "badge": "ABSTAIN",
        "abstain_reason": "sql_relation_not_granted",
    }
    onto_abstain = {
        "abstained": True,
        "badge": "ABSTAIN",
        "abstain_reason": "no_declared_measure",
    }
    askback = {
        "abstained": True,
        "badge": "ABSTAIN",
        "reconfirm": {"suggested_question": "closest count", "token": "tok-1"},
    }
    specs: list[dict[str, Any]] = [
        {
            "id": "s1",
            "db": "shop",
            "sql": "SELECT COUNT(*) FROM items",
            "arm": "baseline",
            "replies": [(200, _completion("SELECT COUNT(*) FROM items", cost=0.01))],
        },
        {
            "id": "s2",
            "db": "yard",
            "sql": "SELECT COUNT(*) FROM plots",
            "arm": "dms",
            "replies": [(200, rule)],
        },
        {
            "id": "s3",
            "db": "dock",
            "sql": "SELECT COUNT(*) FROM boats",
            "arm": "baseline",
            "replies": [(200, _completion("SELECT 0"))],
        },
        {
            "id": "s4",
            "db": "mill",
            "sql": "SELECT COUNT(*) FROM bins",
            "arm": "dms",
            "replies": [(200, schema_abstain)],
        },
        {
            "id": "s5",
            "db": "shop",
            "sql": "SELECT COUNT(*) FROM items",
            "arm": "dms",
            "replies": [(200, onto_abstain)],
        },
        {
            "id": "s6",
            "db": "yard",
            "sql": "SELECT COUNT(*) FROM plots",
            "arm": "baseline",
            "replies": [
                (429, {"detail": {"code": "429"}}),
                (200, _completion("SELECT 0")),
            ],
        },
        {
            "id": "s7",
            "db": "dock",
            "sql": "SELECT COUNT(*) FROM boats",
            "arm": "baseline",
            "replies": [(200, _completion("SELECT COUNT(*) FROM boats"))],
        },
        {
            "id": "s8",
            "db": "mill",
            "sql": "SELECT COUNT(*) FROM bins",
            "arm": "dms",
            "replies": [(200, askback)],
        },
    ]
    produced: list[dict[str, Any]] = []
    for spec in specs:
        queue = list(spec["replies"])
        seen: list[dict[str, Any]] = []
        record = grade_item(
            {
                "question_id": spec["id"],
                "db_id": spec["db"],
                "question": "count rows",
                "SQL": spec["sql"],
            },
            arm=spec["arm"],
            db_root=db,
            space_id="space",
            evidence=False,
            simulate_yes=False,
            post=_queue_post(queue, seen),
            api_base="http://stub",
            ov_url="http://ov.example",
            ov_headers={},
            compact=True,
        )
        if queue:
            raise AssertionError("unused_reply")
        produced.append(record)
    summary = summarize(produced)
    expected = {
        "wrong": 2,
        "heldout_correct": 2,
        "rule_fail": 1,
        "abstain": 3,
        "official_correct": 3,
        "official_wrong": 2,
        "databases": 4,
        "n_total": 8,
    }
    for key, value in expected.items():
        if summary[key] != value:
            raise AssertionError(key)
    if summary["stages"]["schema_retrieval"] != 1:
        raise AssertionError("schema_retrieval")
    if summary["stages"]["ontology_mapping"] != 1:
        raise AssertionError("ontology_mapping")
    if summary["stages"]["sql_generation"] != 1:
        raise AssertionError("sql_generation")
    if summary["stages"]["self_correction"] != 1:
        raise AssertionError("self_correction")
    if summary["stages"]["rule_served"] != 1:
        raise AssertionError("rule_served")
    if summary["pass"] is not False or summary["target"] is not None:
        raise AssertionError("pass")
    if summary["cost_sum"] != 0.01:
        raise AssertionError("cost")
    by_id = {row["id"]: annotate(row) for row in produced}
    if by_id["s1"]["escalation"] != "plan" or by_id["s6"]["escalation"] != "escalate":
        raise AssertionError("escalation")
    if by_id["s1"]["heldout"] != "correct" or by_id["s2"]["heldout"] != "rule_fail":
        raise AssertionError("label")
    if by_id["s3"]["heldout"] != "wrong" or by_id["s3"]["stage"] != "sql_generation":
        raise AssertionError("wrong_stage")
    blob = json.dumps(produced)
    if _CELL in blob or "SELECT" in blob:
        raise AssertionError("leak")
    return summary
