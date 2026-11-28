"""One generality scoreboard.

Reuses ``scripts/fde/eval_pack.py`` to execute gold SQL and compare result
sets, and ``scripts/bench/bird/baseline.py`` for the plain one-shot control
(role ``sql``, schema, no DMS, no model id). It does not keep a second grader
and it does not read a stored answer number.

Rows are DMS under each digit-1 flag profile, plus ``control``.
Columns are correct, wrong, abstain, unsafe, rule-served, p50, p95, and cost.
rule-served is a failure. A stored alternate reading is ``correct_alt``:
not a pass, and not wrong.

``DMS_GENERALITY`` defaults off. Only the digit ``1`` runs the live job.
The offline slice uses recorded fixtures and does not call a provider.

    python scripts/verify/generality.py offline --out generality-out
    DMS_GENERALITY=1 python scripts/verify/generality.py live \\
        --budget-usd 2 --max-usd-per-call 0.05 --out generality-out
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
for _entry in (SCRIPTS, SCRIPTS / "fde", SCRIPTS / "verify"):
    _text = str(_entry)
    if _text not in sys.path:
        sys.path.insert(0, _text)

import eval_pack  # noqa: E402
import grade52  # noqa: E402
from bench.bird.baseline import baseline_body, copied_model, reject_model_keys, sql_from_reply  # noqa: E402
from bench.bird.corpus import (  # noqa: E402
    asked_text,
    db_id_of,
    gold_sql_of,
    load_questions,
    locate_release,
    question_id,
)
from bench.bird.ex import nearest_rank  # noqa: E402
from bench.bird.gold import locate_db, schema_block  # noqa: E402
from bench.bird.run import post_json  # noqa: E402

FLAG = "DMS_GENERALITY"
SLICE = ROOT / "tests" / "fixtures" / "generality" / "slice.json"
BIRD_MIN_Q = 30
BIRD_MIN_DB = 10
EVERYDAY_N = 172
BUCKETS = ("correct", "wrong", "abstain", "unsafe", "rule_served", "correct_alt")
PACK_KINDS = ("bird", "everyday", "pharmacy", "harborline")
_SQLITE = {".sqlite", ".sqlite3", ".db"}


class GeneralityError(ValueError):
    """The pack or the budget cannot be scored as specified."""


class ShortPack(GeneralityError):
    """The file is not the full pack. Nothing was graded."""


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_GENERALITY`` is the digit 1. Unset is off."""
    raw = (env if env is not None else os.environ).get(FLAG, "")
    return str(raw).strip() == "1"


def digit_one(flags: Mapping[str, str]) -> dict[str, str]:
    """Keep flags whose value is the digit 1. Anything else stays off."""
    return {str(key): "1" for key, value in flags.items() if str(value).strip() == "1"}


def profiles() -> tuple[tuple[str, dict[str, str]], ...]:
    """``off``, the digit-1 ai-first set, then each of those flags alone."""
    flags = digit_one(grade52.AI_FIRST_FLAGS)
    rows: list[tuple[str, dict[str, str]]] = [("off", {}), ("ai-first", dict(flags))]
    for name in sorted(flags):
        rows.append((name, {name: "1"}))
    return tuple(rows)


def profile_names() -> tuple[str, ...]:
    return tuple(name for name, _flags in profiles())


def head_sha(root: Path = ROOT) -> str:
    """This checkout's HEAD. Empty when git cannot say. Never a guessed sha."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return ""
    if len(out) != 40 or any(ch not in "0123456789abcdef" for ch in out):
        return ""
    return out


def control_request(question: str, schema: str) -> dict[str, Any]:
    """The bird baseline body. One shot, role ``sql``, no DMS and no model id."""
    return baseline_body(question, schema)


def dms_request(question: str, space_id: str = "") -> dict[str, Any]:
    """Product ask body. No model id. Flags are the server process, not this body."""
    body: dict[str, Any] = {"question": question}
    if space_id:
        body["space_id"] = space_id
    reject_model_keys(body)
    return body


def usage_cost(payload: Any) -> float | None:
    """A cost the provider actually returned. Missing stays unknown."""
    if not isinstance(payload, Mapping):
        return None
    usage = payload.get("usage")
    found: Any = None
    if isinstance(usage, Mapping):
        for key in ("cost_usd", "cost", "total_cost"):
            if key in usage:
                found = usage[key]
                break
    if found is None and "cost_usd" in payload:
        found = payload.get("cost_usd")
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return None
    return float(found)


class Budget:
    """Hard cap. The next call is refused when it could cross the cap."""

    def __init__(self, cap: float, per_call: float) -> None:
        if cap <= 0 or per_call <= 0 or per_call > cap:
            raise GeneralityError(
                "budget cap must be positive and the per-call max must fit under it"
            )
        self.cap = float(cap)
        self.per_call = float(per_call)
        self.spent = 0.0
        self.stop: str | None = None

    def allow(self) -> bool:
        if self.stop:
            return False
        if self.spent + self.per_call > self.cap:
            self.stop = "budget"
            return False
        return True

    def charge(self, cost: float | None) -> None:
        """Record a call that already happened. Unknown or over-cap cost stops."""
        if self.stop:
            return
        if cost is None:
            self.stop = "cost_unmeasured"
            return
        amount = float(cost)
        if amount > self.per_call or self.spent + amount > self.cap:
            self.spent += amount
            self.stop = "over_budget"
            return
        self.spent += amount

    def as_dict(self) -> dict[str, Any]:
        return {
            "cap_usd": self.cap,
            "max_usd_per_call": self.per_call,
            "spent_usd": round(self.spent, 6),
            "stop": self.stop,
        }


def _predict(con: Any, sql: str | None, dialect: str) -> tuple[str, list[tuple[Any, ...]] | None]:
    """Execute one reply. A write is unsafe and is not run. Prose is abstain."""
    if not isinstance(sql, str) or not sql.strip():
        return "missing", None
    try:
        _cols, rows = eval_pack.run_select(con, sql, dialect=dialect)
    except eval_pack.EvalPackError as exc:
        text = str(exc)
        if "did not parse" in text:
            return "abstain", None
        if "read-only" in text or "only one statement" in text:
            return "unsafe", None
        return "abstain", None
    return "rows", rows


def classify_case(
    item: Mapping[str, Any],
    reply: Mapping[str, Any],
    con: Any,
    *,
    control: bool,
) -> str:
    """One bucket. Gold rows are already on ``item`` from executed gold SQL.

    When the reply has SQL, that SQL is executed and any ``rows`` on the reply
    are ignored. A rule path that served a result is ``rule_served``.
    """
    decision = str(reply.get("decision") or "answer")
    if decision in ("abstain", "reconfirm"):
        return "abstain"
    if decision != "answer":
        return "abstain"
    sql = reply.get("sql") if isinstance(reply.get("sql"), str) else None
    if sql and sql.strip():
        kind, rows = _predict(con, sql, str(item.get("dialect") or "sqlite"))
        if kind == "unsafe":
            return "unsafe"
        if kind != "rows":
            return "abstain"
    else:
        raw_rows = reply.get("rows")
        if raw_rows is None:
            return "wrong"
        rows = raw_rows
    bucket = eval_pack.grade_rows(item, rows, "answer")
    if not control:
        path = str(reply.get("serve_path") or "unattributed")
        if eval_pack.path_group(path) == "rule" and bucket in ("correct", "correct_alt", "WRONG"):
            return "rule_served"
    if bucket == "correct":
        return "correct"
    if bucket == "correct_alt":
        return "correct_alt"
    if bucket == "WRONG":
        return "wrong"
    return "abstain"


def _prepare(
    questions: Sequence[Mapping[str, Any]], cons: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Execute every gold SQL. A gold that does not run is not a model result."""
    ready: list[dict[str, Any]] = []
    broken: list[str] = []
    for question in questions:
        con = cons[str(question["db_id"])]
        dialect = str(question.get("dialect") or "sqlite")
        try:
            body = eval_pack.tie_aware(
                con, str(question["gold_sql"]), ordered=False, dialect=dialect
            )
        except eval_pack.EvalPackError:
            broken.append(str(question["id"]))
            continue
        item = dict(question)
        item.update(body)
        alt_sql = question.get("alt_sql")
        if isinstance(alt_sql, str) and alt_sql.strip():
            alt = eval_pack.tie_aware(con, alt_sql, ordered=False, dialect=dialect)
            item["alt"] = [{"name": "alt", "sentence": "stored alternate", **alt}]
        else:
            item["alt"] = []
        ready.append(item)
    return ready, broken


def _tally(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {name: 0 for name in BUCKETS}
    times: list[float] = []
    costs: list[float] = []
    time_known = True
    cost_known = True
    for case in cases:
        bucket = str(case["bucket"])
        if bucket not in counts:
            raise GeneralityError(f"unknown bucket: {bucket}")
        counts[bucket] += 1
        raw_t = case.get("t_ms")
        if isinstance(raw_t, bool) or not isinstance(raw_t, (int, float)):
            time_known = False
        else:
            times.append(float(raw_t))
        raw_c = case.get("cost_usd")
        if isinstance(raw_c, bool) or not isinstance(raw_c, (int, float)):
            cost_known = False
        else:
            costs.append(float(raw_c))
    n = len(cases)
    if sum(counts.values()) != n:
        raise GeneralityError("buckets do not add up to n")
    row = {
        "status": "scored",
        "n": n,
        **counts,
        "failure": counts["wrong"] + counts["unsafe"] + counts["rule_served"],
        "p50_ms": nearest_rank(times, 50) if time_known else None,
        "p95_ms": nearest_rank(times, 95) if time_known else None,
        "cost_usd": round(sum(costs), 6) if cost_known else None,
        "cases": [dict(case) for case in cases],
    }
    return row


def score_row(
    items: Sequence[Mapping[str, Any]],
    cons: Mapping[str, Any],
    reply_for: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    control: bool,
) -> dict[str, Any]:
    cases = []
    for item in items:
        reply = reply_for(item)
        bucket = classify_case(item, reply, cons[str(item["db_id"])], control=control)
        cases.append(
            {
                "id": item["id"],
                "bucket": bucket,
                "t_ms": reply.get("t_ms"),
                "cost_usd": reply.get("cost_usd"),
            }
        )
    return _tally(cases)


def _blank(pack: str, row: str, reason: str) -> dict[str, Any]:
    """A row that was not run. Nulls, not zeros."""
    body: dict[str, Any] = {
        "pack": pack,
        "row": row,
        "status": "not_run",
        "reason": reason,
        "n": None,
        "failure": None,
        "p50_ms": None,
        "p95_ms": None,
        "cost_usd": None,
        "cases": [],
    }
    for name in BUCKETS:
        body[name] = None
    return body


def _open_sqlite(path: Path) -> sqlite3.Connection:
    spec = eval_pack.SourceSpec(kind="sqlite", database=str(path))
    con = eval_pack._sqlite_ro(spec)
    return con


def _materialize(databases: Mapping[str, str], dest: Path) -> dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for db_id, script in databases.items():
        if not db_id or "/" in db_id or "\\" in db_id:
            raise GeneralityError("bad database id")
        path = dest / f"{db_id}.sqlite"
        con = sqlite3.connect(path)
        try:
            con.executescript(script)
            con.commit()
        finally:
            con.close()
        paths[db_id] = path
    return paths


def load_harborline_questions(document: Mapping[str, Any]) -> list[dict[str, str]]:
    """Question, id, and gold SQL. Stored ``answer`` and ``rows`` are not read."""
    rows = document.get("questions")
    if not isinstance(rows, list) or not rows:
        raise GeneralityError("harborline gold has no questions")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise GeneralityError("harborline question must be an object")
        qid = str(row.get("id") or "")
        buyer = row.get("buyer")
        question = buyer if isinstance(buyer, str) and buyer.strip() else row.get("question")
        sql = row.get("sql") if isinstance(row.get("sql"), str) else row.get("gold_sql")
        if not qid or qid in seen or not isinstance(question, str) or not question.strip():
            raise GeneralityError("harborline question needs an id and a buyer question")
        if not isinstance(sql, str) or not sql.strip():
            raise GeneralityError("harborline question needs gold sql")
        seen.add(qid)
        out.append({"id": qid, "question": question.strip(), "gold_sql": sql.strip()})
    return out


def questions_from_slice(spec: Mapping[str, Any], pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Slice questions. Harborline goes through the gold.json loader, decoy and all."""
    raw = spec.get("questions")
    if not isinstance(raw, list) or not raw:
        raise GeneralityError("slice has no questions")
    if pack.get("kind") != "harborline":
        return [dict(row) for row in raw if isinstance(row, Mapping)]
    document = {
        "questions": [
            {
                "id": row["id"],
                "buyer": row["question"],
                "sql": row["gold_sql"],
                "answer": pack.get("decoy_answer", "999"),
                "rows": pack.get("decoy_rows", [{"n": 999}]),
            }
            for row in raw
            if isinstance(row, Mapping)
        ]
    }
    loaded = {row["id"]: row for row in load_harborline_questions(document)}
    out = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        gold = loaded[str(row["id"])]
        item = dict(row)
        item["question"] = gold["question"]
        item["gold_sql"] = gold["gold_sql"]
        out.append(item)
    return out


def _reply(question: Mapping[str, Any], row: str) -> dict[str, Any]:
    if row == "control":
        body = dict(question.get("control") or {})
    else:
        name = row.removeprefix("dms:")
        dms = question.get("dms") or {}
        body = dict(dms.get(name) or {})
    body.setdefault("decision", "answer")
    body.setdefault("t_ms", question.get("t_ms"))
    body.setdefault("cost_usd", question.get("cost_usd"))
    return body


def _bind(
    questions: Sequence[Mapping[str, Any]], paths: Mapping[str, Path], dialect: str
) -> list[dict[str, Any]]:
    bound = []
    for question in questions:
        db_id = str(question.get("db") or "")
        if db_id not in paths:
            raise GeneralityError(f"question {question.get('id')} has no database")
        item = dict(question)
        item["db_id"] = db_id
        item["db_path"] = str(paths[db_id])
        item["dialect"] = dialect
        bound.append(item)
    return bound


def build_offline(spec_path: Path = SLICE, work: Path | None = None) -> dict[str, Any]:
    """Grade the recorded slice. Does not call a provider."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    databases = spec.get("databases")
    if not isinstance(databases, Mapping) or not databases:
        raise GeneralityError("slice has no databases")
    owned = work is None
    temp = tempfile.TemporaryDirectory() if owned else None
    dest = Path(temp.name) if temp is not None else work
    assert dest is not None
    paths = _materialize({str(k): str(v) for k, v in databases.items()}, dest)
    cons = {db_id: _open_sqlite(path) for db_id, path in paths.items()}
    try:
        packs_out = []
        rows_out = []
        for pack in spec.get("packs") or []:
            if not isinstance(pack, Mapping):
                continue
            questions = _bind(questions_from_slice(spec, pack), paths, "sqlite")
            items, broken = _prepare(questions, cons)
            pack_id = str(pack["id"])
            packs_out.append(
                {
                    "id": pack_id,
                    "kind": pack.get("kind"),
                    "status": "slice",
                    "full": False,
                    "n_questions": len(items),
                    "n_databases": len({item["db_id"] for item in items}),
                    "gold_error": broken,
                }
            )
            names = [f"dms:{name}" for name in profile_names()] + ["control"]
            for name in names:
                control = name == "control"
                scored = score_row(
                    items,
                    cons,
                    lambda item, row=name: _reply(item, row),
                    control=control,
                )
                scored["pack"] = pack_id
                scored["row"] = name
                rows_out.append(scored)
    finally:
        for con in cons.values():
            con.close()
        if temp is not None:
            temp.cleanup()
    report = _shell(
        mode="offline-slice",
        full_packs=False,
        complete=True,
        reason="recorded fixtures, not a DMS measurement",
        packs=packs_out,
        rows=rows_out,
        budget=None,
    )
    return report


def _read_json_questions(path: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise GeneralityError(f"{path.name} root must be an object")
    rows = data.get("questions")
    if not isinstance(rows, list) or not rows:
        raise GeneralityError(f"{path.name} has no questions")
    base = path.parent
    named: dict[str, Path] = {}
    databases = data.get("databases")
    if isinstance(databases, Mapping):
        for key, rel in databases.items():
            named[str(key)] = (base / str(rel)).resolve()
    default = ""
    if data.get("database"):
        default = "main"
        named[default] = (base / str(data["database"])).resolve()
    questions = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise GeneralityError("each question must be an object")
        qid = str(row.get("id") or "")
        text = row.get("question")
        sql = row.get("gold_sql") or row.get("sql") or row.get("SQL")
        db_id = str(row.get("db") or default)
        if not qid or not isinstance(text, str) or not text.strip():
            raise GeneralityError("question needs an id and text")
        if not isinstance(sql, str) or not sql.strip() or db_id not in named:
            raise GeneralityError("question needs gold sql and a database")
        questions.append(
            {
                "id": qid,
                "question": text.strip(),
                "gold_sql": sql.strip(),
                "db": db_id,
                "alt_sql": row.get("alt_sql"),
            }
        )
    for db_path in named.values():
        eval_pack.refuse_lake(str(db_path))
        if not db_path.is_file():
            raise GeneralityError(f"database file is missing: {db_path.name}")
    return questions, named


def load_everyday(path: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    questions, paths = _read_json_questions(path)
    if len(questions) != EVERYDAY_N:
        raise ShortPack(f"everyday pack has {len(questions)} questions, need {EVERYDAY_N}")
    return questions, paths


def load_pharmacy(path: Path, repo: Path = ROOT) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    resolved = path.resolve()
    root = repo.resolve()
    if resolved == root or root in resolved.parents:
        raise GeneralityError("pharmacy pack must stay outside the repo")
    questions, paths = _read_json_questions(path)
    return questions, paths


def _bird_rows(path: Path) -> tuple[list[Mapping[str, Any]], Path]:
    try:
        qpath, db_root = locate_release(path)
        return load_questions(qpath), db_root
    except FileNotFoundError:
        pass
    for name in ("questions.jsonl", "questions.json", "heldout.jsonl"):
        candidate = path / name
        if candidate.is_file():
            return load_questions(candidate) if name.endswith(".json") else _jsonl(candidate), path
    raise GeneralityError("bird questions file is missing")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_bird(path: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    """Held-out or Mini-Dev sqlite. Refuses a file under 30 questions or 10 databases."""
    rows, db_root = _bird_rows(path)
    questions: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        db_id = db_id_of(row) or str(row.get("db") or "")
        sql = gold_sql_of(row)
        qid = question_id(row)
        text = asked_text(row, evidence=False)
        if not qid or not db_id or not sql or not text:
            raise GeneralityError("bird row needs id, db_id, question, and gold sql")
        if db_id not in paths:
            paths[db_id] = locate_db(db_root, db_id)
        questions.append({"id": qid, "question": text, "gold_sql": sql, "db": db_id})
    if len(questions) < BIRD_MIN_Q or len(paths) < BIRD_MIN_DB:
        raise ShortPack(
            f"bird has {len(questions)} questions and {len(paths)} databases, "
            f"need {BIRD_MIN_Q} and {BIRD_MIN_DB}"
        )
    return questions, paths


def open_harborline(
    gold_path: Path, database: Path | None, source: Path | None
) -> tuple[list[dict[str, Any]], dict[str, Any], str, Any]:
    """Open the harborline database. Caller closes ``closer``.

    Stored answer text and stored rows in the gold file are not read.
    """
    document = json.loads(gold_path.read_text(encoding="utf-8"))
    loaded = load_harborline_questions(document)
    if source is not None:
        spec = eval_pack.load_source(source)
        ctx = eval_pack.open_source(spec)
        con = ctx.__enter__()
        dialect = eval_pack.DIALECT[spec.kind]
        questions = [
            {**row, "db": "main", "db_id": "main", "dialect": dialect, "db_path": ""}
            for row in loaded
        ]
        return questions, {"main": con}, dialect, ctx
    if database is None or database.suffix.lower() not in _SQLITE:
        raise GeneralityError("harborline needs a sqlite file or a source yaml")
    eval_pack.refuse_lake(str(database))
    if not database.is_file():
        raise GeneralityError("harborline database is missing")
    con = _open_sqlite(database)
    questions = [
        {
            **row,
            "db": "main",
            "db_id": "main",
            "dialect": "sqlite",
            "db_path": str(database),
        }
        for row in loaded
    ]
    return questions, {"main": con}, "sqlite", con


def _shell(
    *,
    mode: str,
    full_packs: bool,
    complete: bool,
    reason: str,
    packs: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    budget: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "sha": head_sha(),
        "flag": FLAG,
        "flag_on": enabled(),
        "full_packs": full_packs,
        "complete": complete,
        "reason": reason,
        "not_pass": True,
        "grader": "scripts/fde/eval_pack.py",
        "control": "scripts/bench/bird/baseline.py",
        "profiles": [{"name": name, "flags": flags} for name, flags in profiles()],
        "packs": packs,
        "rows": rows,
        "budget": budget,
    }


def _fmt(value: Any, places: int) -> str:
    if value is None:
        return "unknown"
    return f"{float(value):.{places}f}"


def render_md(report: Mapping[str, Any]) -> str:
    """Markdown scoreboard. No question text and no gold SQL."""
    lines = [
        "# Generality scoreboard",
        "",
        f"mode: {report['mode']}",
        f"sha: {report['sha'] or 'unknown'}",
        "grader: scripts/fde/eval_pack.py (gold SQL is executed, result sets compared)",
        "control: scripts/bench/bird/baseline.py (one-shot role sql, same schema, no DMS)",
        "rule-served counts as failure. failure = wrong + unsafe + rule-served.",
        "correct_alt is a stored alternate reading. It is not a pass and it is not wrong.",
        "A flag is on only when its value is the digit 1. Unset is off.",
        "Not PASS.",
        "",
    ]
    if report["mode"] == "offline-slice":
        lines.append(
            "Offline slice: recorded provider fixtures on synthetic sqlite. "
            "Not a DMS measurement. The full packs are not in this file."
        )
        lines.append(
            "Full packs, when a live run is pointed at them: "
            f"bird held-out ({BIRD_MIN_Q}+ questions, {BIRD_MIN_DB}+ sqlite databases), "
            f"everyday ({EVERYDAY_N} questions), "
            "pharmacy (a file outside this repo), "
            "harborline (PR 659 gold.json, graded by executing its SQL)."
        )
        lines.append("")
    lines.append(f"complete: {str(report['complete']).lower()}")
    if report.get("reason"):
        lines.append(f"reason: {report['reason']}")
    lines.append("")
    lines.append("Packs")
    lines.append("")
    for pack in report["packs"]:
        lines.append(
            f"- {pack['id']}: status={pack['status']} full={str(pack.get('full')).lower()} "
            f"questions={pack.get('n_questions')} databases={pack.get('n_databases')}"
        )
    lines.append("")
    lines.append(
        "| pack | row | n | correct | wrong | abstain | unsafe | rule-served | "
        "correct_alt | p50 ms | p95 ms | cost usd |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in report["rows"]:
        if row["status"] != "scored":
            lines.append(
                f"| {row['pack']} | {row['row']} | {row['status']} | | | | | | | | | |"
            )
            continue
        lines.append(
            "| {pack} | {row} | {n} | {correct} | {wrong} | {abstain} | {unsafe} | "
            "{rule_served} | {correct_alt} | {p50} | {p95} | {cost} |".format(
                pack=row["pack"],
                row=row["row"],
                n=row["n"],
                correct=row["correct"],
                wrong=row["wrong"],
                abstain=row["abstain"],
                unsafe=row["unsafe"],
                rule_served=row["rule_served"],
                correct_alt=row["correct_alt"],
                p50=_fmt(row["p50_ms"], 3),
                p95=_fmt(row["p95_ms"], 3),
                cost=_fmt(row["cost_usd"], 6),
            )
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def write_outputs(directory: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "generality.json"
    md_path = directory / "generality.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    return json_path, md_path


def _control_schema(item: Mapping[str, Any], con: Any, dialect: str) -> str:
    """Schema text for the one-shot control. Sqlite uses the bird sample block."""
    path = item.get("db_path")
    if dialect == "sqlite" and path:
        return schema_block(Path(str(path)))
    kind = {"postgres": "postgresql", "tsql": "sqlserver"}.get(dialect, dialect)
    if kind not in ("sqlite", "postgresql", "mysql", "sqlserver"):
        kind = "sqlite"
    return eval_pack.schema_text(con, kind)


def _run_calls(
    items: Sequence[Mapping[str, Any]],
    cons: Mapping[str, Any],
    budget: Budget,
    caller: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    *,
    control: bool,
    pack: str,
    row: str,
) -> dict[str, Any]:
    cases = []
    for item in items:
        reply = caller(item)
        if reply is None:
            break
        cases.append(
            {
                "id": item["id"],
                "bucket": classify_case(item, reply, cons[str(item["db_id"])], control=control),
                "t_ms": reply.get("t_ms"),
                "cost_usd": reply.get("cost_usd"),
            }
        )
        if budget.stop:
            break
    if not cases:
        return _blank(pack, row, budget.stop or "not_called")
    scored = _tally(cases)
    scored["pack"] = pack
    scored["row"] = row
    if len(cases) != len(items) or budget.stop:
        scored["status"] = "partial"
    return scored


def _one_call(
    item: Mapping[str, Any],
    con: Any,
    dialect: str,
    budget: Budget,
    *,
    transport: Callable[[Mapping[str, Any]], Any] | None = None,
    dms_post: Callable[[str, dict[str, Any]], tuple[int, Any]] | None = None,
    space_id: str = "",
) -> Mapping[str, Any] | None:
    """One provider call. None means the budget or the transport stopped first."""
    if not budget.allow():
        return None
    started = time.perf_counter()
    if transport is not None:
        body = control_request(str(item["question"]), _control_schema(item, con, dialect))
        try:
            payload = transport(body)
        except eval_pack.EvalPackError:
            budget.stop = budget.stop or "transport"
            return None
        cost = usage_cost(payload)
        budget.charge(cost)
        sql = sql_from_reply(payload)
        return {
            "decision": "answer" if sql.strip() else "abstain",
            "sql": sql,
            "t_ms": (time.perf_counter() - started) * 1000.0,
            "cost_usd": cost,
            "model": copied_model(payload),
        }
    if dms_post is None:
        return None
    status, payload = dms_post("/v1/chat/ask", dms_request(str(item["question"]), space_id))
    cost = usage_cost(payload)
    budget.charge(cost)
    elapsed = (time.perf_counter() - started) * 1000.0
    if status != 200 or not isinstance(payload, Mapping):
        return {"decision": "abstain", "t_ms": elapsed, "cost_usd": cost}
    shaped = eval_pack.from_envelope(payload, t_ms=elapsed, qid=str(item["id"]))
    sql_used = payload.get("sql_used") if isinstance(payload.get("sql_used"), str) else ""
    reply: dict[str, Any] = {
        "decision": shaped["decision"],
        "sql": sql_used,
        "serve_path": shaped["serve_path"],
        "t_ms": elapsed,
        "cost_usd": cost,
    }
    if not sql_used:
        reply["rows"] = shaped.get("rows")
    return reply


def live_report(
    *,
    budget: Budget,
    transport: Callable[[Mapping[str, Any]], Any],
    dms_post: Callable[[str, dict[str, Any]], tuple[int, Any]] | None,
    packs: Sequence[tuple[str, str, list[dict[str, Any]], dict[str, Any], str]],
    space_id: str = "",
    absent: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Score loaded packs until the budget stops.

    Each entry is ``(pack_id, kind, questions, cons, dialect)``.
    Control is one OpenVault shot. DMS is one server process, so flag profiles
    stay ``not_run`` (the offline slice is where each profile is graded).
    """
    pack_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    reasons: list[str] = [f"{pack_id}:{why}" for pack_id, why in absent]
    for pack_id, why in absent:
        pack_rows.append(
            {
                "id": pack_id,
                "kind": pack_id,
                "status": "absent",
                "full": False,
                "n_questions": 0,
                "n_databases": 0,
                "gold_error": [],
                "reason": why,
            }
        )
        for name in [f"dms:{n}" for n in profile_names()] + ["control", "dms:server"]:
            score_rows.append(_blank(pack_id, name, why))

    calls_ok = True
    for pack_id, kind, questions, cons, dialect in packs:
        if budget.stop:
            calls_ok = False
            break
        prepared, broken = _prepare(questions, cons)
        pack_body = {
            "id": pack_id,
            "kind": kind,
            "status": "scored" if prepared else "no_grade",
            "full": bool(prepared) and not broken,
            "n_questions": len(prepared),
            "n_databases": len({str(item["db_id"]) for item in prepared}),
            "gold_error": broken,
        }
        if broken:
            reasons.append(f"{pack_id}:gold_error")
            pack_body["full"] = False
        pack_rows.append(pack_body)
        for name in profile_names():
            why = "dms_api_missing" if dms_post is None else "server_process"
            score_rows.append(_blank(pack_id, f"dms:{name}", why))
        if not prepared:
            score_rows.append(_blank(pack_id, "control", "no_grade"))
            score_rows.append(_blank(pack_id, "dms:server", "no_grade"))
            calls_ok = False
            continue

        def _control(
            item: Mapping[str, Any],
            cons: Mapping[str, Any] = cons,
            dialect: str = dialect,
        ) -> Mapping[str, Any] | None:
            return _one_call(
                item,
                cons[str(item["db_id"])],
                dialect,
                budget,
                transport=transport,
            )

        control_row = _run_calls(
            prepared, cons, budget, _control, control=True, pack=pack_id, row="control"
        )
        score_rows.append(control_row)
        if control_row["status"] != "scored":
            calls_ok = False
        if budget.stop or dms_post is None:
            if dms_post is None:
                score_rows.append(_blank(pack_id, "dms:server", "dms_api_missing"))
            else:
                score_rows.append(_blank(pack_id, "dms:server", budget.stop or "not_called"))
            calls_ok = False
            continue

        def _dms(
            item: Mapping[str, Any],
            cons: Mapping[str, Any] = cons,
            dialect: str = dialect,
        ) -> Mapping[str, Any] | None:
            return _one_call(
                item,
                cons[str(item["db_id"])],
                dialect,
                budget,
                dms_post=dms_post,
                space_id=space_id,
            )

        server_row = _run_calls(
            prepared, cons, budget, _dms, control=False, pack=pack_id, row="dms:server"
        )
        score_rows.append(server_row)
        if server_row["status"] != "scored":
            calls_ok = False
    complete = calls_ok and not reasons and budget.stop is None
    reason = budget.stop or ("; ".join(dict.fromkeys(reasons)) if reasons else "")
    if not complete and not reason:
        reason = "incomplete"
    return _shell(
        mode="live",
        full_packs=complete,
        complete=complete,
        reason=reason,
        packs=pack_rows,
        rows=score_rows,
        budget=budget.as_dict(),
    )


def _sqlite_pack(
    questions: list[dict[str, Any]], paths: dict[str, Path]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cons = {db_id: _open_sqlite(path) for db_id, path in paths.items()}
    bound = _bind(questions, paths, "sqlite")
    return bound, cons


def run_live(args: argparse.Namespace) -> int:
    if not enabled():
        print(f"CONFIG: {FLAG} is off. No call was sent. Not a score.")
        return 2
    try:
        budget = Budget(float(args.budget_usd), float(args.max_usd_per_call))
    except (TypeError, ValueError, GeneralityError) as exc:
        print(f"CONFIG: {exc}")
        return 2
    url = (args.openvault_url or os.environ.get("OPENVAULT_URL", "")).strip()
    if not url:
        print("CONFIG: OPENVAULT_URL is required. No call was sent.")
        return 2
    key = os.environ.get("OPENVAULT_API_KEY", "").strip()
    key = key or os.environ.get("CORTEX_API_KEY", "").strip()
    if not key.startswith("ov_"):
        print("CONFIG: an ov_ OpenVault key is required. No call was sent.")
        return 2
    if args.fetch_bird:
        from bench.bird.corpus import fetch_release

        fetch_release(Path(args.fetch_bird))
    absent: list[tuple[str, str]] = []
    opened: list[tuple[str, str, list[dict[str, Any]], dict[str, Any], str]] = []
    closers: list[Any] = []
    try:
        if args.bird:
            bird_path: Path | None = Path(args.bird)
        elif args.fetch_bird:
            bird_path = Path(args.fetch_bird)
        else:
            bird_path = None
        if bird_path is None:
            absent.append(("bird", "missing"))
        else:
            try:
                questions, paths = load_bird(bird_path)
                bound, cons = _sqlite_pack(questions, paths)
                opened.append(("bird", "bird", bound, cons, "sqlite"))
                closers.extend(cons.values())
            except ShortPack as exc:
                absent.append(("bird", str(exc)))
            except (GeneralityError, FileNotFoundError, OSError) as exc:
                absent.append(("bird", str(exc)))
        if not args.everyday:
            absent.append(("everyday", "missing"))
        else:
            try:
                questions, paths = load_everyday(Path(args.everyday))
                bound, cons = _sqlite_pack(questions, paths)
                opened.append(("everyday", "everyday", bound, cons, "sqlite"))
                closers.extend(cons.values())
            except (GeneralityError, OSError) as exc:
                absent.append(("everyday", str(exc)))
        if not args.pharmacy:
            absent.append(("pharmacy", "missing"))
        else:
            try:
                questions, paths = load_pharmacy(Path(args.pharmacy))
                bound, cons = _sqlite_pack(questions, paths)
                opened.append(("pharmacy", "pharmacy", bound, cons, "sqlite"))
                closers.extend(cons.values())
            except (GeneralityError, OSError) as exc:
                absent.append(("pharmacy", str(exc)))
        if not args.harborline_gold:
            absent.append(("harborline", "missing"))
        else:
            try:
                source = Path(args.harborline_source) if args.harborline_source else None
                database = Path(args.harborline_db) if args.harborline_db else None
                questions, cons, dialect, closer = open_harborline(
                    Path(args.harborline_gold), database, source
                )
                opened.append(("harborline", "harborline", questions, cons, dialect))
                closers.append(closer)
            except (GeneralityError, OSError) as exc:
                absent.append(("harborline", str(exc)))
        dms_base = (args.dms_api or os.environ.get("DMS_API_BASE", "")).strip().rstrip("/")
        dms_post = None
        if dms_base:
            def dms_post(path: str, body: dict[str, Any], base: str = dms_base) -> tuple[int, Any]:
                return post_json(base + path, body, {})

        report = live_report(
            budget=budget,
            transport=eval_pack.openvault_transport(url),
            dms_post=dms_post,
            packs=opened,
            space_id=args.space_id or "",
            absent=absent,
        )
    finally:
        for closer in closers:
            try:
                if hasattr(closer, "__exit__"):
                    closer.__exit__(None, None, None)
                else:
                    closer.close()
            except Exception:
                pass
    write_outputs(Path(args.out), report)
    print(render_md(report))
    return 0 if report["complete"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    offline = sub.add_parser("offline", help="grade the recorded slice")
    offline.add_argument("--out", type=Path, required=True)
    offline.add_argument("--slice", type=Path, default=SLICE)
    live = sub.add_parser("live", help="full packs through OpenVault, under a hard budget")
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--budget-usd", required=True)
    live.add_argument("--max-usd-per-call", required=True)
    live.add_argument("--openvault-url", default="")
    live.add_argument("--bird", default="")
    live.add_argument("--fetch-bird", default="")
    live.add_argument("--everyday", default="")
    live.add_argument("--pharmacy", default="")
    live.add_argument("--harborline-gold", default="")
    live.add_argument("--harborline-db", default="")
    live.add_argument("--harborline-source", default="")
    live.add_argument("--dms-api", default="")
    live.add_argument("--space-id", default="")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "offline":
            report = build_offline(args.slice)
            write_outputs(args.out, report)
            print(render_md(report))
            return 0 if report["complete"] else 1
        return run_live(args)
    except GeneralityError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
