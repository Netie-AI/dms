"""BENCH-MATRIX-01: one command, one artifact per named dataset slice.

Datasets are loaded at run time and are never committed. The first live run is a
baseline. There is no accuracy target. Not PASS.

Flag ``DMS_BENCH_MATRIX`` defaults off. Only the digit ``1`` turns it on. Off,
this command sends no ask, writes no artifact, and does not touch prompt bytes,
the envelope, or the served route.

  python scripts/bench_matrix.py --self-check
  DMS_BENCH_MATRIX=1 python scripts/bench_matrix.py \\
      --dataset bird_minidev_sqlite --source <mini_dev_sqlite.json> \\
      --db-dir <sqlite-dir> --space <space-id> --out .tmp/bench

``--header-lang zh|ms`` re-ingests a CSV pack with headers from the operator's
mapping file. Question text is not rewritten. Gold SQL identifiers are renamed
with sqlglot from that map, then executed on the new sqlite.

Refuses to start while ``pgrep -f "[a]b_run.sh|[g]rade52.py --armed"`` matches.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for _path in (SCRIPTS, SCRIPTS / "fde", SCRIPTS / "verify", ROOT / "tests" / "fde_scenarios"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

FLAG = "DMS_BENCH_MATRIX"
TICKET = "BENCH-MATRIX-01"
KIND = "dms.bench_matrix"
ARMED_PATTERN = "[a]b_run.sh|[g]rade52.py --armed"
ARMED_ARGV = ["pgrep", "-f", ARMED_PATTERN]
ASK_ROUTE = "/v1/chat/ask"
HEADER_LANGS = frozenset({"zh", "ms"})
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3
VERDICTS = frozenset({"served_correct", "wrong", "reconfirm", "abstain", "gold_error", "error"})
CASE_KEYS = ("id", "verdict", "seconds", "reason_code")
REQUIRED = (
    "kind",
    "ticket",
    "dataset",
    "git_sha",
    "run_label",
    "n",
    "served_correct",
    "wrong",
    "reconfirm",
    "abstain",
    "gold_error",
    "error",
    "wrong_served_upper_95",
    "seconds_p50",
    "seconds_p95",
    "ov_usage_usd",
    "ov_usage_read",
    "ov_usage_reason",
    "header_lang",
    "baseline",
    "target",
    "pass",
    "cases",
)
COUNT_KEYS = ("served_correct", "wrong", "reconfirm", "abstain", "gold_error", "error")
LEAK_KEYS = frozenset(
    {
        "rows",
        "values",
        "sql",
        "gold_sql",
        "question",
        "text",
        "answer",
        "evidence",
        "api_key",
        "password",
        "secret",
        "token",
        "authorization",
        "dsn",
    }
)
DATASETS: dict[str, dict[str, str]] = {
    "bird_minidev_sqlite": {
        "family": "bird",
        "dialect": "sqlite",
        "hf_config": "mini_dev_sqlite",
    },
    "bird_minidev_mysql": {
        "family": "bird",
        "dialect": "mysql",
        "hf_config": "mini_dev_mysql",
    },
    "bird_minidev_pg": {
        "family": "bird",
        "dialect": "pg",
        "hf_config": "mini_dev_pg",
    },
    "spider2_lite_sqlite": {"family": "spider2", "dialect": "sqlite"},
    "adventureworks_pg": {"family": "adventureworks", "dialect": "pg"},
}
_SQL_DIALECT = {"pg": "postgres", "postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}
_CODE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")
# ponytail: nearest-rank percentile and one OV usage page (limit 1000).
# Upgrade: a linear percentile, and a cursor if OpenVault pages the ledger.
_USAGE_LIMIT = 1000
_MARKERS = ("SYN_RED", "SYN_BLUE", "SYN_LEAK")


class ConfigError(Exception):
    """A refusal before a score. ``code`` is a short token, never row data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RewriteError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def flag_on(env: Mapping[str, str] | None = None) -> bool:
    """True only when the flag is the digit 1. Unset, 0, and any other text are off."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip() == "1"


def pgrep_armed(run: Callable[..., Any] | None = None) -> bool:
    """True when an armed grade52 or ab_run is already in the process list.

    The pattern puts the first letter of each name in brackets so pgrep does
    not match its own command line. Stdout is never read.
    """
    runner = run or subprocess.run
    try:
        proc = runner(ARMED_ARGV, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise ConfigError("pgrep_unavailable") from None
    code = getattr(proc, "returncode", None)
    if code == 0:
        return True
    if code == 1:
        return False
    raise ConfigError("pgrep_failed")


def ask_request(question: str, space_id: str) -> tuple[dict[str, str], str]:
    """The live ask body and route. The question string is copied, not rewritten."""
    return {"question": question, "space_id": space_id}, ASK_ROUTE


def post_ask(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    """POST /v1/chat/ask through score_bird. The body is ask_request, nothing else."""
    import score_bird

    body, route = ask_request(question, space_id)
    if route != ASK_ROUTE or body != {"question": question, "space_id": space_id}:
        raise RuntimeError("ask_shape")
    env = score_bird._ask_live(base, question, space_id, timeout)
    if not isinstance(env, dict):
        raise RuntimeError("ask_shape")
    return env


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return round(xs[0], 6)
    rank = math.ceil(pct / 100.0 * len(xs)) - 1
    rank = min(max(rank, 0), len(xs) - 1)
    return round(xs[rank], 6)


def safe_reason(raw: str) -> str:
    """Keep a short reason code. Prose and cell text become ``unstructured``."""
    text = str(raw or "").strip()
    if not text:
        return ""
    import score_heldout

    head = score_heldout.reason_code(text).strip()
    if 1 <= len(head) <= 80 and set(head) <= _CODE_CHARS:
        return head
    return "unstructured"


def abstain_code(env: Mapping[str, Any]) -> str:
    for key in ("reason_code", "abstain_reason", "reason"):
        raw = env.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "abstain"


def classify(env: Mapping[str, Any], gold_rows: Sequence[Any]) -> tuple[str, str]:
    """served_correct, wrong, reconfirm, or abstain from envelope fields and rows.

    A reconfirm block is not served, even if the badge is confident. Demo
    fallback is wrong. Row compare is bird_minidev.grade_envelope.
    """
    import bird_minidev

    if env.get("demo_fallback_used"):
        return "wrong", "demo_fallback_used"
    block = env.get("reconfirm")
    badge = str(env.get("badge") or "")
    if block or env.get("needs_confirm") or badge == "reconfirm":
        code = "reconfirm"
        if isinstance(block, dict) and isinstance(block.get("reason_code"), str):
            code = str(block["reason_code"])
        return "reconfirm", safe_reason(code)
    verdict = bird_minidev.grade_envelope(env, gold_rows)
    if verdict in {"OK", "LAYER"}:
        return "served_correct", ""
    if verdict == "WRONG":
        got = bird_minidev.envelope_rows(env)
        return "wrong", safe_reason(f"rows_mismatch:{len(got)}/{len(gold_rows)}")
    return "abstain", safe_reason(abstain_code(env))


def read_only_sql(sql: str, dialect: str) -> str | None:
    """None when ``sql`` is one read-only query. Else a reason code. No SQL text returned."""
    text = sql.strip()
    if not text:
        return "gold_empty"
    read = _SQL_DIALECT.get(dialect, dialect)
    try:
        import sqlglot
        from sqlglot import exp

        trees = [tree for tree in sqlglot.parse(text, read=read) if tree is not None]
    except ImportError:
        import oracle_row_match

        body = text.rstrip(";").strip()
        if ";" in body or not oracle_row_match.is_select_sql(body):
            return "gold_not_select"
        return None
    except Exception:  # noqa: BLE001 - parser errors must not carry the SQL
        return "gold_not_select"
    if len(trees) != 1 or not isinstance(trees[0], exp.Query):
        return "gold_not_select"
    return None


def run_sqlite_gold(sql: str, db_path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    problem = read_only_sql(sql, "sqlite")
    if problem:
        return None, problem
    uri = quote(db_path.resolve().as_posix(), safe="/:")
    try:
        con = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, "sqlite_open"
    try:
        cur = con.execute(sql)
        cols = [item[0] for item in cur.description] if cur.description else []
        rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        return rows, None
    except sqlite3.Error:
        return None, "sql_error"
    finally:
        con.close()


def _forbidden(obj: Any) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in LEAK_KEYS:
                return True
            if _forbidden(value):
                return True
        return False
    if isinstance(obj, list):
        return any(_forbidden(item) for item in obj)
    return False


def artifact_problems(doc: Mapping[str, Any]) -> list[str]:
    """Schema failures as short codes. Empty means the artifact is writable."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["not_object"]
    for key in REQUIRED:
        if key not in doc:
            problems.append(f"missing:{key}")
    if doc.get("kind") != KIND:
        problems.append("kind")
    if doc.get("ticket") != TICKET:
        problems.append("ticket")
    if doc.get("target") is not None:
        problems.append("target")
    if doc.get("pass") is not False:
        problems.append("pass")
    if doc.get("baseline") not in {True, False}:
        problems.append("baseline")
    for key in COUNT_KEYS:
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append(f"count:{key}")
    n = doc.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        problems.append("n")
    elif all(isinstance(doc.get(key), int) for key in COUNT_KEYS):
        if sum(int(doc[key]) for key in COUNT_KEYS) != n:
            problems.append("count_sum")
    cases = doc.get("cases")
    if not isinstance(cases, list) or (isinstance(n, int) and len(cases) != n):
        problems.append("cases")
    elif isinstance(cases, list):
        allowed = set(CASE_KEYS)
        for case in cases:
            if not isinstance(case, dict) or set(case) != allowed:
                problems.append("case_keys")
                break
            if case.get("verdict") not in VERDICTS:
                problems.append("verdict")
                break
            seconds = case.get("seconds")
            if seconds is not None and (
                isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            ):
                problems.append("seconds")
                break
            if not isinstance(case.get("reason_code"), str):
                problems.append("reason")
                break
    upper = doc.get("wrong_served_upper_95")
    if upper is not None and (isinstance(upper, bool) or not isinstance(upper, (int, float))):
        problems.append("upper")
    for key in ("seconds_p50", "seconds_p95"):
        value = doc.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            problems.append(key)
    usd = doc.get("ov_usage_usd")
    if usd is not None and (isinstance(usd, bool) or not isinstance(usd, (int, float))):
        problems.append("usd")
    if doc.get("ov_usage_read") not in {"ok", "failed", "skipped", "fixture"}:
        problems.append("ov_read")
    if doc.get("header_lang") not in {None, "zh", "ms"}:
        problems.append("header_lang")
    if _forbidden(doc):
        problems.append("leak_key")
    return problems


def _upper(wrong: int, served: int) -> float | None:
    if served <= 0:
        return None
    import score_heldout

    raw = score_heldout.wrong_upper_pct(wrong, served)
    if raw is None:
        return None
    if raw != raw or raw == float("inf"):  # NaN
        return 100.0
    return round(float(raw), 2)


def build_artifact(
    *,
    dataset: str,
    git_sha: str,
    run_label: str,
    cases: Sequence[Mapping[str, Any]],
    usage: Mapping[str, Any],
    header_lang: str | None,
    baseline: bool,
    smoke: bool,
    blocked: bool,
    unasked: int,
    source_sha256: str | None,
    gold_db: str | None,
) -> dict[str, Any]:
    counts = {key: 0 for key in COUNT_KEYS}
    for case in cases:
        verdict = str(case.get("verdict") or "")
        if verdict in counts:
            counts[verdict] += 1
    served = counts["served_correct"] + counts["wrong"]
    times = []
    for case in cases:
        seconds = case.get("seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            times.append(float(seconds))
    doc: dict[str, Any] = {
        "kind": KIND,
        "ticket": TICKET,
        "dataset": dataset,
        "git_sha": git_sha,
        "run_label": run_label,
        "n": len(cases),
        "served_correct": counts["served_correct"],
        "wrong": counts["wrong"],
        "reconfirm": counts["reconfirm"],
        "abstain": counts["abstain"],
        "gold_error": counts["gold_error"],
        "error": counts["error"],
        "wrong_served_upper_95": _upper(counts["wrong"], served),
        "seconds_p50": percentile(times, 50),
        "seconds_p95": percentile(times, 95),
        "ov_usage_usd": usage.get("usd"),
        "ov_usage_read": usage.get("read"),
        "ov_usage_reason": usage.get("reason"),
        "header_lang": header_lang,
        "baseline": baseline,
        "target": None,
        "pass": False,
        "smoke": smoke,
        "blocked": blocked,
        "unasked": unasked,
        "source_sha256": source_sha256,
        "gold_db": gold_db,
        "cases": [dict(case) for case in cases],
    }
    return doc


def print_report(doc: Mapping[str, Any]) -> None:
    """Counts, the bound, and Not PASS. No per-case text and no cell values."""
    from score_bound import bound_line

    print(
        f"dataset={doc['dataset']} n={doc['n']} "
        f"served_correct={doc['served_correct']} WRONG={doc['wrong']} "
        f"reconfirm={doc['reconfirm']} abstain={doc['abstain']} "
        f"gold_error={doc['gold_error']} error={doc['error']}"
    )
    served = int(doc["served_correct"]) + int(doc["wrong"])
    if int(doc["wrong"]) == 0:
        print(bound_line(served))
    upper = doc.get("wrong_served_upper_95")
    upper_s = "n/a" if upper is None else f"{float(upper):.2f} pct"
    p50 = doc.get("seconds_p50")
    p95 = doc.get("seconds_p95")
    usd = doc.get("ov_usage_usd")
    p50_s = "n/a" if p50 is None else f"{float(p50):.6f}"
    p95_s = "n/a" if p95 is None else f"{float(p95):.6f}"
    usd_s = "n/a" if usd is None else f"{float(usd):.4f}"
    print(
        f"wrong_served_upper_95={upper_s} seconds_p50={p50_s} "
        f"seconds_p95={p95_s} ov_usage_usd={usd_s} ov_usage_read={doc.get('ov_usage_read')}"
    )
    if doc.get("smoke"):
        print("smoke limit set. Not a dataset score.")
    if doc.get("blocked"):
        print(f"blocked unasked={doc.get('unasked')}. No verdict invented for the rest.")
    print("Not PASS. No accuracy target. A finished run is a baseline, not a pass.")


def _event_usd(event: Mapping[str, Any]) -> float | None:
    for key in ("cost_usd", "usd", "usage_usd"):
        value = event.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    usage = event.get("usage")
    if isinstance(usage, dict):
        for key in ("cost_usd", "usd", "usage_usd"):
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _event_label(event: Mapping[str, Any]) -> str:
    for key in ("run_label", "label"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def read_ov_usage(
    url: str,
    run_label: str,
    getter: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sum USD for events whose run label matches. Key fields are never copied.

    Events with a different label are ignored. A ledger that does not name the
    label is ``no_run_label_match``, not a sum of every other run.
    """
    params = {"run_label": run_label, "limit": _USAGE_LIMIT}
    fetch = getter or _http_usage
    try:
        body = fetch(url, params)
    except Exception as exc:  # noqa: BLE001 - the class name is the only record
        return {"usd": None, "read": "failed", "reason": type(exc).__name__}
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return {"usd": None, "read": "failed", "reason": "usage_ledger_shape"}
    matched: list[float] = []
    saw = False
    for event in events:
        if not isinstance(event, dict):
            continue
        if _event_label(event) != run_label:
            continue
        saw = True
        usd = _event_usd(event)
        if usd is not None:
            matched.append(usd)
    if not saw:
        return {"usd": None, "read": "ok", "reason": "no_run_label_match"}
    if not matched:
        return {"usd": None, "read": "ok", "reason": "no_usd_field"}
    return {"usd": round(sum(matched), 6), "read": "ok", "reason": None}


def _http_usage(url: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
    import httpx

    response = httpx.get(url, params=dict(params), timeout=5.0)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("usage_ledger_shape")
    return body


def live_usage(run_label: str) -> dict[str, Any]:
    base = os.environ.get("OPENVAULT_URL", "").strip()
    if not base:
        return {"usd": None, "read": "skipped", "reason": "no_openvault_url"}
    return read_ov_usage(base.rstrip("/") + "/api/usage", run_label)


@dataclass
class Case:
    id: str
    question: str
    gold_sql: str = ""
    db_id: str = ""
    gold_rows: list[dict[str, Any]] | None = None
    gold_error: str | None = None
    from_csv: bool = False


@dataclass
class Slice:
    dataset: str
    source: str
    db_dir: Path | None = None
    csv_dir: Path | None = None
    header_lang: str | None = None
    header_map: Path | None = None
    pg_dsn_env: str = ""
    mysql_dsn_env: str = ""
    space: str = ""
    limit: int | None = None
    url: str = ""
    timeout: float = 60.0
    out: Path | None = None
    run_label: str = ""


def _question_text(item: Mapping[str, Any]) -> str:
    for key in ("question", "instruction"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
    raise ConfigError("question_field_missing")


def _case_id(item: Mapping[str, Any]) -> str:
    for key in ("instance_id", "question_id", "id"):
        if key in item and item[key] is not None:
            return str(item[key])
    raise ConfigError("id_missing")


def cases_from_items(items: Sequence[Mapping[str, Any]], root: Path | None) -> list[Case]:
    cases: list[Case] = []
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError("questions_shape")
        gold_sql = ""
        for key in ("SQL", "sql", "gold_sql"):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                gold_sql = raw
                break
        gold_rows = None
        from_csv = False
        gold_error = None
        rel = item.get("gold_csv")
        if isinstance(rel, str) and rel.strip():
            if root is None:
                raise ConfigError("gold_csv_outside")
            path = _inside(root, rel.strip())
            gold_rows, gold_error = _read_gold_csv(path)
            from_csv = True
        db_id = item.get("db_id") or item.get("db") or ""
        cases.append(
            Case(
                id=_case_id(item),
                question=_question_text(item),
                gold_sql=gold_sql,
                db_id=str(db_id),
                gold_rows=gold_rows,
                gold_error=gold_error,
                from_csv=from_csv,
            )
        )
    if not cases:
        raise ConfigError("questions_empty")
    return cases


def _inside(root: Path, rel: str) -> Path:
    path = Path(rel)
    if path.is_absolute():
        raise ConfigError("gold_csv_outside")
    full = (root / path).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        raise ConfigError("gold_csv_outside") from None
    return full


def _coerce_cell(cell: str) -> Any:
    text = cell.strip()
    if text == "":
        return None
    if text[0] in "+-" or text[0].isdigit():
        try:
            if text.isdigit() or (text[0] == "-" and text[1:].isdigit()):
                return int(text)
            return float(text) if _float_text(text) else text
        except (ValueError, OverflowError):
            return text
    return text


def _float_text(text: str) -> bool:
    if text.count(".") != 1:
        return False
    left, right = text.split(".")
    if left in {"", "-", "+"}:
        left = left + "0" if left else "0"
    return left.lstrip("+-").isdigit() and right.isdigit()


def _read_gold_csv(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not path.is_file():
        return None, "gold_csv_missing"
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return None, "gold_csv_shape"
            rows = []
            for raw in reader:
                rows.append({key: _coerce_cell(value or "") for key, value in raw.items()})
    except (OSError, csv.Error):
        return None, "gold_csv_shape"
    return rows, None


def _load_json_items(path: Path) -> tuple[list[Any], str]:
    try:
        data = path.read_bytes()
    except OSError:
        raise ConfigError("source_unreadable") from None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ConfigError("source_json") from None
    if isinstance(parsed, dict):
        items = parsed.get("questions") or parsed.get("data") or parsed.get("instances")
    else:
        items = parsed
    if not isinstance(items, list):
        raise ConfigError("questions_shape")
    import hashlib

    return items, hashlib.sha256(data).hexdigest()


def aligned_ids(questions: Sequence[Mapping[str, Any]]) -> bool:
    """BIRD Mini-Dev 2026: the three splits share question ids 0 through 499."""
    found: list[int] = []
    for item in questions:
        raw = item.get("question_id", item.get("id"))
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            return False
        try:
            found.append(int(raw))
        except (TypeError, ValueError):
            return False
    return len(found) == 500 and set(found) == set(range(500))


def load_bird(spec: Slice) -> tuple[list[Case], str | None]:
    import bird_minidev

    dest = spec.out or (ROOT / ".tmp" / "bench_matrix")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        questions, meta = bird_minidev.load_minidev_source(spec.source, dest_dir=dest)
    except Exception:  # noqa: BLE001 - do not echo the source URL or body
        raise ConfigError("source_unreadable") from None
    if spec.limit is None:
        if bird_minidev.validate_full_minidev(questions, limit=None):
            raise ConfigError("minidev_shape")
        if not aligned_ids(questions):
            raise ConfigError("aligned_ids")
    else:
        if spec.limit < 1:
            raise ConfigError("limit")
        questions = list(questions[: spec.limit])
    return cases_from_items(questions, None), str(meta.get("sha256") or "") or None


def load_spider(spec: Slice) -> tuple[list[Case], str | None]:
    source = Path(spec.source)
    if source.is_dir():
        path = source / "questions.json"
        root = source
    else:
        path = source
        root = source.parent
    items, digest = _load_json_items(path)
    if spec.limit is not None:
        if spec.limit < 1:
            raise ConfigError("limit")
        items = items[: spec.limit]
    return cases_from_items(items, root), digest


def load_questions_file(spec: Slice) -> tuple[list[Case], str | None]:
    items, digest = _load_json_items(Path(spec.source))
    if spec.limit is not None:
        if spec.limit < 1:
            raise ConfigError("limit")
        items = items[: spec.limit]
    return cases_from_items(items, Path(spec.source).parent), digest


def find_sqlite(db_dir: Path, db_id: str) -> Path | None:
    if not db_id or db_id != Path(db_id).name or db_id in {".", ".."}:
        return None
    for name in (f"{db_id}.sqlite", f"{db_id}.sqlite3", f"{db_id}.db"):
        path = db_dir / name
        if path.is_file():
            return path
    nested = db_dir / db_id / f"{db_id}.sqlite"
    if nested.is_file():
        return nested
    return None


def default_db_dir(spec: Slice) -> Path | None:
    if spec.db_dir is not None:
        return spec.db_dir
    source = Path(spec.source)
    if source.is_dir():
        nested = source / "databases"
        return nested if nested.is_dir() else source
    return None


def parse_mapping(
    doc: Mapping[str, Any], lang: str
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    if lang not in HEADER_LANGS:
        raise ConfigError("header_lang")
    tables: dict[str, str] = {}
    columns: dict[tuple[str, str], str] = {}
    raw_tables = doc.get("tables") or {}
    raw_columns = doc.get("columns") or {}
    if not isinstance(raw_tables, dict) or not isinstance(raw_columns, dict):
        raise ConfigError("header_map_shape")
    for name, entry in raw_tables.items():
        if isinstance(entry, dict) and isinstance(entry.get(lang), str) and entry[lang].strip():
            tables[str(name)] = entry[lang].strip()
    for key, entry in raw_columns.items():
        if "." not in str(key):
            raise ConfigError("header_key")
        table, column = str(key).split(".", 1)
        if isinstance(entry, dict) and isinstance(entry.get(lang), str) and entry[lang].strip():
            columns[(table, column)] = entry[lang].strip()
    if not tables and not columns:
        raise ConfigError("header_map_empty")
    return tables, columns


def rewrite_sql(
    sql: str,
    tables: Mapping[str, str],
    columns: Mapping[tuple[str, str], str],
    dialect: str,
) -> str:
    """Rename tables and columns that the mapping names. Question text is not an input."""
    import sqlglot
    from sqlglot import exp

    read = _SQL_DIALECT.get(dialect, dialect)
    try:
        tree = sqlglot.parse_one(sql, read=read)
    except Exception:  # noqa: BLE001
        raise RewriteError("sql_rewrite_failed") from None
    alias_to_real: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        alias_to_real[table.alias_or_name] = table.name
        alias_to_real[table.name] = table.name
    for table in list(tree.find_all(exp.Table)):
        new = tables.get(table.name)
        if new and new != table.name:
            table.set("this", exp.to_identifier(new, quoted=True))
    by_column: dict[str, set[str]] = {}
    for (table, column), new in columns.items():
        by_column.setdefault(column, set()).add(new)
    for col in list(tree.find_all(exp.Column)):
        real = alias_to_real.get(col.table, "")
        if real and (real, col.name) in columns:
            col.set("this", exp.to_identifier(columns[(real, col.name)], quoted=True))
            continue
        if col.table:
            continue
        hits = by_column.get(col.name) or set()
        if len(hits) > 1:
            raise RewriteError("header_ambiguous")
        if len(hits) == 1:
            col.set("this", exp.to_identifier(next(iter(hits)), quoted=True))
    return tree.sql(dialect=read)


def _read_csv_table(path: Path) -> tuple[list[str], list[list[str]]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except (OSError, csv.Error):
        raise ConfigError("csv_unreadable") from None
    if not rows or not rows[0]:
        raise ConfigError("csv_empty")
    width = len(rows[0])
    body: list[list[str]] = []
    for row in rows[1:]:
        if len(row) != width:
            raise ConfigError("csv_short_row")
        body.append(row)
    return rows[0], body


def write_sqlite(path: Path, tables: Mapping[str, tuple[list[str], list[list[Any]]]]) -> None:
    import fde_pipeline

    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        for name, (headers, rows) in tables.items():
            cols = ", ".join(fde_pipeline.q(header) for header in headers)
            con.execute(f"CREATE TABLE {fde_pipeline.q(name)} ({cols})")
            marks = ", ".join(["?"] * len(headers))
            con.executemany(
                f"INSERT INTO {fde_pipeline.q(name)} VALUES ({marks})",
                [tuple(row) for row in rows],
            )
        con.commit()
    except sqlite3.Error:
        raise ConfigError("sqlite_write") from None
    finally:
        con.close()


def reingest_csv(
    csv_dir: Path,
    mapping_path: Path,
    lang: str,
    dest: Path,
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Build ``dest`` with translated headers. Cell values are copied, not edited."""
    try:
        doc = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("header_map_unreadable") from None
    if not isinstance(doc, dict):
        raise ConfigError("header_map_shape")
    tables, columns = parse_mapping(doc, lang)
    frames: dict[str, tuple[list[str], list[list[Any]]]] = {}
    paths = sorted(csv_dir.glob("*.csv"))
    if not paths:
        raise ConfigError("csv_empty")
    for path in paths:
        headers, body = _read_csv_table(path)
        new_headers = []
        for header in headers:
            new_headers.append(columns.get((path.stem, header), header))
        if len(set(new_headers)) != len(new_headers):
            raise ConfigError("header_collision")
        new_table = tables.get(path.stem, path.stem)
        if new_table in frames:
            raise ConfigError("header_collision")
        coerced = [[_coerce_cell(cell) for cell in row] for row in body]
        frames[new_table] = (new_headers, coerced)
    write_sqlite(dest, frames)
    return tables, columns


def apply_header_lang(
    cases: Sequence[Case],
    tables: Mapping[str, str],
    columns: Mapping[tuple[str, str], str],
) -> list[Case]:
    """Rewrite gold SQL identifiers. The question string on each case stays put."""
    out: list[Case] = []
    for case in cases:
        if case.from_csv or not case.gold_sql:
            out.append(case)
            continue
        try:
            sql = rewrite_sql(case.gold_sql, tables, columns, "sqlite")
        except RewriteError as exc:
            out.append(replace(case, gold_sql="", gold_error=exc.code))
            continue
        out.append(replace(case, gold_sql=sql))
    return out


def _sqlite_gold_fn(
    db_for: Callable[[Case], Path],
) -> Callable[[Case], tuple[list[Any] | None, str | None]]:
    def gold(case: Case) -> tuple[list[Any] | None, str | None]:
        if case.gold_error:
            return None, case.gold_error
        if case.from_csv:
            return case.gold_rows or [], None
        try:
            path = db_for(case)
        except ConfigError as exc:
            return None, exc.code
        return run_sqlite_gold(case.gold_sql, path)

    return gold


def _dbapi_gold_fn(
    connect: Callable[[], Any], dialect: str
) -> Callable[[Case], tuple[list[Any] | None, str | None]]:
    import bird_minidev

    def gold(case: Case) -> tuple[list[Any] | None, str | None]:
        if case.gold_error:
            return None, case.gold_error
        if case.from_csv:
            return case.gold_rows or [], None
        problem = read_only_sql(case.gold_sql, dialect)
        if problem:
            return None, problem
        return bird_minidev.run_pg_gold(case.gold_sql, connect)

    return gold


def _probe_connect(connect: Callable[[], Any]) -> None:
    try:
        con = connect()
    except ConfigError:
        raise
    except Exception:  # noqa: BLE001
        raise ConfigError("db_connect_failed") from None
    try:
        cur = con.cursor()
        try:
            cur.execute("SELECT 1")
        finally:
            cur.close()
    except Exception:  # noqa: BLE001
        raise ConfigError("db_connect_failed") from None
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def connect_pg(env_name: str) -> Callable[[], Any]:
    if not env_name.strip():
        raise ConfigError("pg_dsn_unset")
    dsn = os.environ.get(env_name, "").strip()
    if not dsn:
        raise ConfigError("pg_dsn_unset")

    def connect() -> Any:
        try:
            import psycopg
        except ImportError:
            raise ConfigError("pg_driver_missing") from None
        return psycopg.connect(dsn)

    return connect


def connect_mysql(env_name: str) -> Callable[[], Any]:
    if not env_name.strip():
        raise ConfigError("mysql_dsn_unset")
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise ConfigError("mysql_dsn_unset")
    parsed = urlparse(raw)
    if parsed.scheme not in {"mysql", "mysql+pymysql"} or not parsed.hostname or not parsed.path:
        raise ConfigError("mysql_dsn_scheme")
    host = parsed.hostname
    port = parsed.port or 3306
    user = parsed.username or ""
    password = parsed.password or ""
    database = parsed.path.lstrip("/")

    def connect() -> Any:
        try:
            import pymysql
        except ImportError:
            raise ConfigError("mysql_driver_missing") from None
        return pymysql.connect(
            host=host, port=port, user=user, password=password, database=database
        )

    return connect


def prepare_gold(
    spec: Slice, cases: Sequence[Case], work: Path
) -> tuple[list[Case], Callable[[Case], tuple[list[Any] | None, str | None]], str | None]:
    """Return cases, a gold callable, and the gold db file name (not a full path)."""
    meta = DATASETS[spec.dataset]
    dialect = meta["dialect"]
    gold_name: str | None = None
    prepared = list(cases)
    if spec.header_lang:
        if spec.header_lang not in HEADER_LANGS:
            raise ConfigError("header_lang")
        if spec.csv_dir is None or spec.header_map is None:
            raise ConfigError("header_inputs")
        gold_name = f"{spec.dataset}.{spec.header_lang}.sqlite"
        dest = work / gold_name
        tables, columns = reingest_csv(spec.csv_dir, spec.header_map, spec.header_lang, dest)
        prepared = apply_header_lang(prepared, tables, columns)
        return prepared, _sqlite_gold_fn(lambda _case: dest), gold_name
    if dialect == "sqlite":
        db_dir = default_db_dir(spec)
        if db_dir is None:
            raise ConfigError("sqlite_missing")
        missing = 0
        resolved: dict[str, Path] = {}
        for case in prepared:
            if case.from_csv or case.gold_error:
                continue
            if not case.gold_sql:
                case.gold_error = "gold_empty"
                continue
            found = find_sqlite(db_dir, case.db_id)
            if found is None:
                missing += 1
            else:
                resolved[case.id] = found
        if missing:
            raise ConfigError("sqlite_missing")

        def db_for(case: Case) -> Path:
            try:
                return resolved[case.id]
            except KeyError:
                raise ConfigError("sqlite_missing") from None

        return prepared, _sqlite_gold_fn(db_for), None
    if dialect == "pg":
        connect = connect_pg(spec.pg_dsn_env)
        _probe_connect(connect)
        return prepared, _dbapi_gold_fn(connect, "postgres"), None
    if dialect == "mysql":
        connect = connect_mysql(spec.mysql_dsn_env)
        _probe_connect(connect)
        return prepared, _dbapi_gold_fn(connect, "mysql"), None
    raise ConfigError("dialect")


def score_cases(
    cases: Sequence[Case],
    gold_fn: Callable[[Case], tuple[list[Any] | None, str | None]],
    ask_fn: Callable[[str], Mapping[str, Any]],
    now: Callable[[], float] | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    import score_bird

    clock = now or time.perf_counter
    rows: list[dict[str, Any]] = []
    blocked = False
    for index, case in enumerate(cases):
        gold_rows, gold_err = gold_fn(case)
        if gold_err:
            rows.append(
                {
                    "id": case.id,
                    "verdict": "gold_error",
                    "seconds": None,
                    "reason_code": safe_reason(gold_err),
                }
            )
            continue
        started = clock()
        try:
            env = ask_fn(case.question)
        except Exception as exc:  # noqa: BLE001 - class name only, never the message
            elapsed = round(clock() - started, 6)
            code = type(exc).__name__
            rows.append(
                {
                    "id": case.id,
                    "verdict": "error",
                    "seconds": elapsed,
                    "reason_code": safe_reason(code),
                }
            )
            if code in score_bird.TRANSPORT_BLOCK:
                blocked = True
                break
            continue
        elapsed = round(clock() - started, 6)
        if not isinstance(env, dict):
            rows.append(
                {
                    "id": case.id,
                    "verdict": "error",
                    "seconds": elapsed,
                    "reason_code": "ask_shape",
                }
            )
            continue
        verdict, reason = classify(env, gold_rows or [])
        rows.append(
            {
                "id": case.id,
                "verdict": verdict,
                "seconds": elapsed,
                "reason_code": reason,
            }
        )
    unasked = len(cases) - len(rows)
    return rows, unasked, blocked


def _run_label(dataset: str, sha: str, requested: str) -> str:
    if requested:
        if not (1 <= len(requested) <= 80 and set(requested) <= _CODE_CHARS):
            raise ConfigError("run_label")
        return requested
    import bird_minidev

    stamp = bird_minidev.utc_now().replace("-", "").replace(":", "")
    return f"bench-matrix-{dataset}-{sha[:12]}-{stamp}"


def artifact_path(out: Path, dataset: str, header_lang: str | None) -> Path:
    name = dataset if not header_lang else f"{dataset}.{header_lang}"
    if name != Path(name).name:
        raise ConfigError("dataset")
    return out / f"{name}.json"


def write_artifact(path: Path, doc: Mapping[str, Any]) -> None:
    problems = artifact_problems(doc)
    if problems:
        raise ConfigError("artifact_schema")
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_slice(
    spec: Slice,
    *,
    ask_fn: Callable[[str], Mapping[str, Any]] | None = None,
    usage_fn: Callable[[str], Mapping[str, Any]] | None = None,
    baseline: bool = True,
) -> tuple[int, dict[str, Any]]:
    if spec.dataset not in DATASETS:
        raise ConfigError("unknown_dataset")
    if not spec.space.strip():
        raise ConfigError("space")
    if spec.out is None:
        raise ConfigError("out")
    spec.out.mkdir(parents=True, exist_ok=True)
    meta = DATASETS[spec.dataset]
    if meta["family"] == "bird":
        cases, digest = load_bird(spec)
    elif meta["family"] == "spider2":
        cases, digest = load_spider(spec)
    else:
        cases, digest = load_questions_file(spec)
    cases, gold_fn, gold_name = prepare_gold(spec, cases, spec.out)
    import score_bird

    base = ""
    if ask_fn is None:
        try:
            base = score_bird.live_url(dict(os.environ), spec.url or None)
        except ValueError:
            raise ConfigError("api_base") from None

        def ask_fn(
            question: str,
            _base: str = base,
            _space: str = spec.space,
            _timeout: float = spec.timeout,
        ) -> dict[str, Any]:
            return post_ask(_base, question, _space, _timeout)

    import bird_minidev

    sha = bird_minidev.git_sha()
    label = _run_label(spec.dataset, sha, spec.run_label)
    scored, unasked, blocked = score_cases(cases, gold_fn, ask_fn)
    usage = (usage_fn or live_usage)(label)
    doc = build_artifact(
        dataset=spec.dataset,
        git_sha=sha,
        run_label=label,
        cases=scored,
        usage=usage,
        header_lang=spec.header_lang,
        baseline=baseline,
        smoke=spec.limit is not None,
        blocked=blocked,
        unasked=unasked,
        source_sha256=digest,
        gold_db=gold_name,
    )
    path = artifact_path(spec.out, spec.dataset, spec.header_lang)
    write_artifact(path, doc)
    print_report(doc)
    if gold_name:
        print(f"gold_db={gold_name}")
    if blocked:
        return EXIT_BLOCKED, doc
    if doc["wrong"] or doc["error"]:
        return EXIT_FAIL, doc
    return EXIT_PASS, doc


def _rank(code: int) -> int:
    return {EXIT_FAIL: 3, EXIT_BLOCKED: 2, EXIT_CONFIG: 1, EXIT_PASS: 0}.get(code, 3)


def run_cli(args: argparse.Namespace) -> int:
    slices = slices_from_args(args)
    worst = EXIT_PASS
    for spec in slices:
        try:
            code, _doc = run_slice(spec)
        except ConfigError as exc:
            print(f"CONFIG: {exc.code}. Not a score.")
            code = EXIT_CONFIG
        if _rank(code) > _rank(worst):
            worst = code
    return worst


def slices_from_args(args: argparse.Namespace) -> list[Slice]:
    if args.manifest is not None:
        return _load_manifest(args)
    if not args.dataset or not args.source:
        raise ConfigError("dataset_and_source")
    return [_slice_from_args(args)]


def _slice_from_args(args: argparse.Namespace) -> Slice:
    return Slice(
        dataset=args.dataset,
        source=args.source,
        db_dir=args.db_dir,
        csv_dir=args.csv_dir,
        header_lang=args.header_lang,
        header_map=args.header_map,
        pg_dsn_env=args.pg_dsn_env,
        mysql_dsn_env=args.mysql_dsn_env,
        space=args.space,
        limit=args.limit,
        url=args.url or "",
        timeout=args.timeout,
        out=args.out,
        run_label=args.run_label or "",
    )


def _load_manifest(args: argparse.Namespace) -> list[Slice]:
    try:
        doc = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("manifest") from None
    rows = doc.get("slices") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ConfigError("manifest")
    out: list[Slice] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigError("manifest")
        dataset = str(row.get("dataset") or "")
        source = str(row.get("source") or "")
        if not dataset or not source:
            raise ConfigError("dataset_and_source")
        out.append(
            Slice(
                dataset=dataset,
                source=source,
                db_dir=Path(row["db_dir"]) if row.get("db_dir") else args.db_dir,
                csv_dir=Path(row["csv_dir"]) if row.get("csv_dir") else args.csv_dir,
                header_lang=row.get("header_lang") or args.header_lang,
                header_map=Path(row["header_map"]) if row.get("header_map") else args.header_map,
                pg_dsn_env=str(row.get("pg_dsn_env") or args.pg_dsn_env or ""),
                mysql_dsn_env=str(row.get("mysql_dsn_env") or args.mysql_dsn_env or ""),
                space=str(row.get("space") or args.space or ""),
                limit=row.get("limit", args.limit),
                url=str(row.get("url") or args.url or ""),
                timeout=float(row.get("timeout") or args.timeout),
                out=Path(row["out"]) if row.get("out") else args.out,
                run_label=str(row.get("run_label") or args.run_label or ""),
            )
        )
    return out


def _synthetic(root: Path) -> tuple[Path, Path, Path, list[Case], dict[str, str]]:
    db = root / "toy.sqlite"
    csv_dir = root / "csv"
    csv_dir.mkdir()
    mapping = {
        "tables": {"widgets": {"zh": "部件", "ms": "widgetms"}},
        "columns": {
            "widgets.id": {"zh": "编号", "ms": "nombor"},
            "widgets.name": {"zh": "名称", "ms": "nama"},
            "widgets.price": {"zh": "价格", "ms": "harga"},
        },
    }
    map_path = root / "header_map.json"
    map_path.write_text(json.dumps(mapping), encoding="utf-8")
    write_sqlite(
        db,
        {
            "widgets": (
                ["id", "name", "price"],
                [(1, "SYN_RED", 10.0), (2, "SYN_BLUE", 20.0)],
            )
        },
    )
    with (csv_dir / "widgets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name", "price"])
        writer.writerow(["1", "SYN_RED", "10.0"])
        writer.writerow(["2", "SYN_BLUE", "20.0"])
    questions = [
        ("q1", "How many widgets are in the tray?", "SELECT COUNT(*) AS n FROM widgets", "correct"),
        ("q2", "List the widget color names.", "SELECT name FROM widgets ORDER BY id", "wrong"),
        (
            "q3",
            "What is the average widget price?",
            "SELECT AVG(price) AS avg_price FROM widgets",
            "reconfirm",
        ),
        (
            "q4",
            "What is the highest widget price?",
            "SELECT MAX(price) AS max_price FROM widgets",
            "abstain",
        ),
        (
            "q5",
            "How many widgets are missing on purpose?",
            "SELECT nope FROM missing_table",
            "gold",
        ),
    ]
    cases = [
        Case(id=ident, question=text, gold_sql=sql, db_id="toy")
        for ident, text, sql, _kind in questions
    ]
    plan = {text: kind for _ident, text, _sql, kind in questions}
    return db, csv_dir, map_path, cases, plan


def _stub_ask(plan: Mapping[str, str]) -> Callable[[str], dict[str, Any]]:
    def ask(question: str) -> dict[str, Any]:
        kind = plan[question]
        if kind == "correct":
            return {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"n": 2}]}
        if kind == "wrong":
            return {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "rows": [{"name": "SYN_LEAK"}],
            }
        if kind == "reconfirm":
            return {
                "badge": "reconfirm",
                "abstained": True,
                "reconfirm": {"reason_code": "closest_question"},
            }
        return {"badge": "ABSTAIN", "abstained": True, "abstain_reason": "ungranted"}

    return ask


def _fixture_usage(url: str, params: Mapping[str, Any]) -> dict[str, Any]:
    label = str(params["run_label"])
    return {
        "events": [
            {"run_label": label, "cost_usd": 0.25, "api_key": "sk-SECRET-VALUE"},
            {"run_label": "other-run", "cost_usd": 9},
            {"label": label, "usd": 0.05},
        ]
    }


def _contains_marker(obj: Any) -> bool:
    if isinstance(obj, str):
        return any(marker in obj for marker in _MARKERS)
    if isinstance(obj, dict):
        return any(_contains_marker(key) or _contains_marker(value) for key, value in obj.items())
    if isinstance(obj, list):
        return any(_contains_marker(item) for item in obj)
    return False


def self_check() -> int:
    """Five synthetic questions, header rewrite, armed-guard, fixture usage. Not a score."""
    import tempfile

    import bird_minidev

    with tempfile.TemporaryDirectory(prefix="bench-matrix-") as tmp:
        root = Path(tmp)
        db, csv_dir, map_path, cases, plan = _synthetic(root)
        gold_fn = _sqlite_gold_fn(lambda _case: db)
        scored, unasked, blocked = score_cases(cases, gold_fn, _stub_ask(plan))
        if unasked or blocked:
            print("self-check FAIL score_loop")
            return EXIT_FAIL
        usage = read_ov_usage(
            "http://fixture.invalid/api/usage", "bench-self-check", _fixture_usage
        )
        usage = {"usd": usage["usd"], "read": "fixture", "reason": usage["reason"]}
        if usage["usd"] != 0.3 or "sk-SECRET-VALUE" in json.dumps(usage):
            print("self-check FAIL usage")
            return EXIT_FAIL
        doc = build_artifact(
            dataset="synthetic_sqlite",
            git_sha=bird_minidev.git_sha(),
            run_label="bench-self-check",
            cases=scored,
            usage=usage,
            header_lang=None,
            baseline=False,
            smoke=False,
            blocked=False,
            unasked=0,
            source_sha256=None,
            gold_db=None,
        )
        problems = artifact_problems(doc)
        expect = {
            "served_correct": 1,
            "wrong": 1,
            "reconfirm": 1,
            "abstain": 1,
            "gold_error": 1,
            "error": 0,
            "n": 5,
        }
        if problems or any(doc[key] != value for key, value in expect.items()):
            print("self-check FAIL schema")
            return EXIT_FAIL
        if _contains_marker(doc):
            print("self-check FAIL leak")
            return EXIT_FAIL
        if doc["seconds_p50"] is None or doc["seconds_p95"] is None:
            print("self-check FAIL timing")
            return EXIT_FAIL
        if float(doc["seconds_p50"]) > float(doc["seconds_p95"]):
            print("self-check FAIL timing")
            return EXIT_FAIL
        served = doc["served_correct"] + doc["wrong"]
        if doc["wrong_served_upper_95"] != _upper(doc["wrong"], served):
            print("self-check FAIL bound")
            return EXIT_FAIL
        original_rows: dict[str, list[Any]] = {}
        for case in cases:
            rows, err = gold_fn(case)
            if err:
                continue
            original_rows[case.id] = rows or []
        asked: list[str] = []

        def record(question: str) -> dict[str, Any]:
            asked.append(question)
            return _stub_ask(plan)(question)

        for lang in ("zh", "ms"):
            work = root / lang
            work.mkdir()
            tables, columns = reingest_csv(csv_dir, map_path, lang, work / "gold.sqlite")
            rewritten = apply_header_lang(cases, tables, columns)
            if [case.question for case in rewritten] != [case.question for case in cases]:
                print("self-check FAIL header_question")
                return EXIT_FAIL
            translated = _sqlite_gold_fn(lambda _case, path=work / "gold.sqlite": path)
            for case in rewritten:
                if case.id not in original_rows:
                    continue
                rows, err = translated(case)
                if err or not bird_minidev.rows_equal(original_rows[case.id], rows or []):
                    print("self-check FAIL header_values")
                    return EXIT_FAIL
            header_scored, _, _ = score_cases(rewritten, translated, record)
            if asked[-4:] != [case.question for case in cases if case.id != "q5"]:
                print("self-check FAIL header_question")
                return EXIT_FAIL
            header_doc = build_artifact(
                dataset="synthetic_sqlite",
                git_sha=doc["git_sha"],
                run_label="bench-self-check",
                cases=header_scored,
                usage=usage,
                header_lang=lang,
                baseline=False,
                smoke=False,
                blocked=False,
                unasked=0,
                source_sha256=None,
                gold_db=f"synthetic.{lang}.sqlite",
            )
            if artifact_problems(header_doc) or _contains_marker(header_doc):
                print("self-check FAIL header_artifact")
                return EXIT_FAIL
            print(f"header_lang {lang} values_match=1 questions_unchanged=1")

        class _Proc:
            def __init__(self, code: int) -> None:
                self.returncode = code

        if not pgrep_armed(run=lambda *_a, **_k: _Proc(0)):
            print("self-check FAIL armed_guard")
            return EXIT_FAIL
        if pgrep_armed(run=lambda *_a, **_k: _Proc(1)):
            print("self-check FAIL armed_guard")
            return EXIT_FAIL
        print("armed_guard refuses=1")
        print_report(doc)
        print("self-check instrument. Not a dataset score. Not PASS.")
    return EXIT_PASS


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BENCH-MATRIX-01 dataset matrix. Not a PASS.")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dataset", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--db-dir", type=Path, default=None)
    parser.add_argument("--csv-dir", type=Path, default=None)
    parser.add_argument("--header-lang", choices=sorted(HEADER_LANGS), default=None)
    parser.add_argument("--header-map", type=Path, default=None)
    parser.add_argument("--pg-dsn-env", default="")
    parser.add_argument("--mysql-dsn-env", default="")
    parser.add_argument("--space", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--run-label", default="")
    args = parser.parse_args(argv)
    if args.out is None and not args.self_check:
        args.out = ROOT / ".tmp" / "bench_matrix"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if pgrep_armed():
            print("CONFIG: armed_run_live. Refusing to start. Not a score.")
            return EXIT_CONFIG
        if args.self_check:
            return self_check()
        if not flag_on():
            print("CONFIG: DMS_BENCH_MATRIX is off. No ask was sent. Not a score.")
            return EXIT_CONFIG
        return run_cli(args)
    except ConfigError as exc:
        print(f"CONFIG: {exc.code}. Not a score.")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
