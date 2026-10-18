"""Held-out n=348 comparison of this vote arm against a main arm.

The questions and the Mini-Dev databases are not in this repo. Without the
env vars below, this script prints STUB and does not score. Not PASS.

Live command, from the repo root, with a vote API and a main API already up:

    BIRD_MINIDEV_DIR=/path/to/minidev/dev_databases \\
    BIRD_HELD_OUT=/path/to/heldout_348.jsonl \\
    DMS_API_BASE=http://127.0.0.1:8090 \\
    DMS_API_BASE_MAIN=http://127.0.0.1:8091 \\
    CORTEX_FREEROUTE_LEARN=0 \\
    CORTEX_ROUTE_STORE=/path/to/empty-route-store \\
    python scripts/verify/sql_vote_heldout.py --live

``BIRD_HELD_OUT`` is JSONL, one object per line, exactly 348 lines:
``id``, ``question``, ``db_id``, ``gold_sql``.
``BIRD_MINIDEV_DIR`` holds ``{db_id}/{db_id}.sqlite`` (or ``.sqlite3`` / ``.db``).
``DMS_API_BASE`` is this branch. ``DMS_API_BASE_MAIN`` is main, vote off.
``CORTEX_FREEROUTE_LEARN`` must be ``0``. ``CORTEX_ROUTE_STORE`` must exist
and be empty. Optional: ``BIRD_SPACE_ID``, ``BIRD_ASK_TIMEOUT`` (seconds).

The insights wire has ``model_preference=free+normal`` and no temperature
or seed. This harness does not set a second model.

    python scripts/verify/sql_vote_heldout.py --self-check
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
HELD_OUT_N = 348
LEARN_ENV = "CORTEX_FREEROUTE_LEARN"
STORE_ENV = "CORTEX_ROUTE_STORE"
REQUIRED = (
    "BIRD_MINIDEV_DIR",
    "BIRD_HELD_OUT",
    "DMS_API_BASE",
    "DMS_API_BASE_MAIN",
    LEARN_ENV,
    STORE_ENV,
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _score():
    return _load(SCRIPTS / "score_sql_vote.py", "score_sql_vote_heldout")


def _rows_equal():
    vote = _load(
        ROOT / "packages" / "executor" / "dms_executor" / "sql_vote.py",
        "sql_vote_heldout",
    )
    return vote.rows_equal


def live_blockers(env: dict[str, str]) -> list[str]:
    """Names of what is missing. Empty means the live comparison can start."""
    missing = [name for name in REQUIRED if not str(env.get(name) or "").strip()]
    blockers = [f"missing {name}" for name in missing]
    learn = str(env.get(LEARN_ENV) or "").strip()
    if learn and learn != "0":
        blockers.append(f"{LEARN_ENV} must be 0, got {learn}")
    store_text = str(env.get(STORE_ENV) or "").strip()
    if store_text:
        store = Path(store_text)
        if not store.exists():
            blockers.append(f"{STORE_ENV} does not exist: {store}")
        elif store.is_file() and store.stat().st_size != 0:
            blockers.append(f"{STORE_ENV} is not an empty file")
        elif store.is_dir() and any(store.iterdir()):
            blockers.append(f"{STORE_ENV} is not an empty directory")
    db_text = str(env.get("BIRD_MINIDEV_DIR") or "").strip()
    if db_text and not Path(db_text).is_dir():
        blockers.append(f"BIRD_MINIDEV_DIR is not a directory: {db_text}")
    held = str(env.get("BIRD_HELD_OUT") or "").strip()
    if held and not Path(held).is_file():
        blockers.append(f"BIRD_HELD_OUT is not a file: {held}")
    return blockers


def db_file(root: Path, db_id: str) -> Path | None:
    names = (
        root / db_id / f"{db_id}.sqlite",
        root / db_id / f"{db_id}.sqlite3",
        root / db_id / f"{db_id}.db",
        root / f"{db_id}.sqlite",
    )
    for path in names:
        if path.is_file():
            return path
    return None


def load_held_out(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def gold_rows(database: Path, sql: str) -> list[dict[str, Any]]:
    uri = f"file:{database}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute(sql)
        cols = [str(item[0]) for item in (cur.description or [])]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def ask(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    body = json.dumps({"question": question, "space_id": space_id or None}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/ask",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("ask response was not an object")
    return payload


def verdict(envelope: dict[str, Any], gold: list[dict[str, Any]], same) -> str:
    if isinstance(envelope.get("reconfirm"), dict):
        return "reconfirm"
    if envelope.get("abstained") or envelope.get("badge") == "ABSTAIN":
        return "abstain"
    rows = envelope.get("rows")
    served = rows if isinstance(rows, list) else []
    if not served and not gold:
        return "correct"
    if same(served, gold):
        return "correct"
    return "wrong"


def run_live(env: dict[str, str]) -> int:
    blockers = live_blockers(env)
    if blockers:
        for item in blockers:
            print(f"CONFIG: {item}")
        print("STUB held-out n=348 was not run. Not a score. Not PASS.")
        return 2
    questions = load_held_out(Path(env["BIRD_HELD_OUT"]))
    if len(questions) != HELD_OUT_N:
        print(f"CONFIG: BIRD_HELD_OUT has {len(questions)} rows, expected {HELD_OUT_N}")
        print("Not a score. Not PASS.")
        return 2
    root = Path(env["BIRD_MINIDEV_DIR"])
    same = _rows_equal()
    score = _score()
    space = str(env.get("BIRD_SPACE_ID") or "").strip()
    timeout = float(env.get("BIRD_ASK_TIMEOUT") or 180)
    cases: list[dict[str, str]] = []
    for row in questions:
        ident = str(row.get("id") or "")
        question = str(row.get("question") or "")
        db_id = str(row.get("db_id") or "")
        gold_sql = str(row.get("gold_sql") or "")
        database = db_file(root, db_id)
        if database is None or not question or not gold_sql:
            print(f"CONFIG: held-out row {ident or '?'} has no database or gold SQL")
            print("Not a score. Not PASS.")
            return 2
        try:
            gold = gold_rows(database, gold_sql)
            main_env = ask(env["DMS_API_BASE_MAIN"], question, space, timeout)
            vote_env = ask(env["DMS_API_BASE"], question, space, timeout)
        except (
            OSError,
            sqlite3.Error,
            urllib.error.URLError,
            RuntimeError,
            json.JSONDecodeError,
        ) as exc:
            print(f"CONFIG: row {ident or '?'} failed: {type(exc).__name__}: {exc}")
            print("Not a score. Not PASS.")
            return 2
        cases.append(
            {
                "id": ident,
                "main": verdict(main_env, gold, same),
                "new": verdict(vote_env, gold, same),
            }
        )
    report = score.paired_score(cases)
    report["live"] = True
    score._print_report(report, label="held-out")
    print(
        "live n=348 against main. The insights wire has no second model, "
        "temperature, or seed. Not PASS."
    )
    return 0


def self_check() -> int:
    score = _score()
    code = score.self_check()
    blockers = live_blockers({})
    if len(blockers) != len(REQUIRED):
        print(f"self-check mismatch blockers: {blockers!r}")
        return 1
    if code:
        return code
    print("harness self-check: env absent, live comparison not started. Not PASS.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check or not args.live:
        code = self_check()
        if not args.live:
            print(
                "STUB held-out n=348 was not run. Set BIRD_MINIDEV_DIR, "
                "BIRD_HELD_OUT, DMS_API_BASE, DMS_API_BASE_MAIN, "
                "CORTEX_FREEROUTE_LEARN=0, CORTEX_ROUTE_STORE, then "
                "python scripts/verify/sql_vote_heldout.py --live"
            )
            print("Not a score. Not PASS.")
        return code
    return run_live(dict(os.environ))


if __name__ == "__main__":
    sys.exit(main())
