"""BIRD Mini-Dev command.

  python scripts/bench/bird/__main__.py self-check
  DMS_BIRD_HARNESS=1 python scripts/bench/bird/__main__.py fetch --dest "$RUNNER_TEMP/bird"
  DMS_BIRD_HARNESS=1 python scripts/bench/bird/__main__.py run --arm dms ...

``self-check`` and ``score`` do not need the flag. ``fetch``, ``ingest``,
``run``, and ``nightly`` do. Off, they send no ask and write no artifact.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bench.bird.corpus import fetch_release, load_questions, validate_full  # noqa: E402
from bench.bird.ex import self_check as ex_self_check  # noqa: E402
from bench.bird.gold import execute_readonly  # noqa: E402
from bench.bird.ingest import ingest_root, read_spaces, write_spaces  # noqa: E402
from bench.bird.run import (  # noqa: E402
    EXIT_CONFIG,
    EXIT_FAIL,
    EXIT_PASS,
    FLAG,
    enabled,
    load_jsonl,
    post_json,
    run_questions,
    surface,
)
from bench.bird.score import format_report, report  # noqa: E402


def _config(message: str) -> int:
    print(f"CONFIG: {message}")
    return EXIT_CONFIG


def _need_flag() -> int | None:
    if enabled():
        return None
    print(f"CONFIG: {FLAG} is off. No ask was sent. Not a score.")
    return EXIT_CONFIG


def _ov_headers() -> dict[str, str] | None:
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if raw.startswith("ov_"):
            return {"Authorization": "Bearer " + raw}
    return None


def _post_api(base: str):
    def post(path: str, body: dict) -> tuple[int, object]:
        return post_json(base.rstrip("/") + path, body, {})

    return post


def cmd_self_check() -> int:
    ex_self_check()
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory(prefix="bird-self-") as tmp:
        path = Path(tmp) / "shop.sqlite"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE items (n INTEGER, name TEXT)")
        con.execute("INSERT INTO items VALUES (1, 'Alpha'), (1, 'Alpha')")
        con.commit()
        con.close()
        rows, err = execute_readonly(path, "SELECT n, name FROM items")
        assert err is None and rows is not None
        from bench.bird.ex import calculate_ex

        assert calculate_ex(rows, [(1, "Alpha")]) == 1
        _rows, write_err = execute_readonly(path, "INSERT INTO items VALUES (2, 'Beta')")
        assert write_err == "destructive_sql"
        check = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        assert check.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
        check.close()
    assert surface() == {"prompt": None, "envelope": None, "route": None}
    print("bird self-check ok")
    return EXIT_PASS


def cmd_fetch(args: argparse.Namespace) -> int:
    blocked = _need_flag()
    if blocked is not None:
        return blocked
    dest = Path(args.dest)
    questions, db_root = fetch_release(dest, args.url)
    print(f"questions={questions}")
    print(f"db_root={db_root}")
    return EXIT_PASS


def cmd_ingest(args: argparse.Namespace) -> int:
    blocked = _need_flag()
    if blocked is not None:
        return blocked
    base = (args.api or os.environ.get("DMS_API_BASE") or "").strip()
    root_env = os.environ.get("DMS_SQLITE_SOURCE_ROOT", "").strip()
    if not base:
        return _config("DMS_API_BASE is required. No default host.")
    if not root_env:
        return _config("DMS_SQLITE_SOURCE_ROOT is required on this process and on the API.")
    db_root = Path(args.db_root)
    try:
        spaces = ingest_root(_post_api(base), db_root)
    except Exception as exc:  # noqa: BLE001 - the code is the message
        print(f"FAIL: {getattr(exc, 'code', type(exc).__name__)}")
        return EXIT_FAIL
    write_spaces(Path(args.out), spaces)
    print(f"spaces={len(spaces)} out={args.out}")
    return EXIT_PASS


def cmd_run(args: argparse.Namespace) -> int:
    blocked = _need_flag()
    if blocked is not None:
        return blocked
    questions_path = Path(args.questions)
    if not questions_path.is_file():
        return _config("questions file is missing")
    questions = load_questions(questions_path)
    problem = validate_full(questions, limit=args.limit)
    if problem:
        return _config(problem + ". Pass --limit for a smoke run. Not a Mini-Dev score.")
    if args.limit is not None:
        questions = questions[: args.limit]
    api_base = (args.api or os.environ.get("DMS_API_BASE") or "").strip()
    ov_url = (args.openvault or os.environ.get("OPENVAULT_URL") or "").strip()
    headers: dict[str, str] = {}
    if args.arm == "dms" and not api_base:
        return _config("DMS_API_BASE is required for the dms arm. No default host.")
    if args.arm == "baseline":
        if not ov_url:
            return _config("OPENVAULT_URL is required for the baseline arm. No default host.")
        found = _ov_headers()
        if not found:
            return _config("an ov_ OpenVault key is required for the baseline arm.")
        headers = found
    spaces = read_spaces(Path(args.spaces)) if args.spaces else {}
    out = Path(args.out)
    prior = load_jsonl(out)
    evidence = args.evidence == "on"
    run_questions(
        questions,
        arm=args.arm,
        db_root=Path(args.db_root),
        spaces=spaces,
        evidence=evidence,
        simulate_yes=not args.no_yes,
        concurrency=args.concurrency,
        out=out,
        api_base=api_base,
        ov_url=ov_url,
        ov_headers=headers,
        prior=prior,
    )
    summary = report(load_jsonl(out))
    print(format_report(summary))
    if args.limit is not None:
        print("smoke, not a Mini-Dev score")
    print("pass=false target=null")
    return EXIT_PASS


def cmd_heldout_check() -> int:
    import tempfile

    from bench.bird.heldout import format_heldout, keys_present, run_check

    with tempfile.TemporaryDirectory(prefix="heldout-check-") as tmp:
        try:
            summary = run_check(Path(tmp))
        except AssertionError as exc:
            print(f"FAIL: {exc}")
            return EXIT_FAIL
    print(format_heldout(summary))
    keys = "present" if keys_present() else "absent"
    print(f"provider=stub live=not_run keys={keys}")
    print("pass=false target=null")
    print("heldout-check ok")
    return EXIT_PASS


def cmd_heldout(args: argparse.Namespace) -> int:
    from bench.bird.corpus import validate_full
    from bench.bird.heldout import (
        HeldoutConfig,
        format_heldout,
        live_block,
        load_spider,
        run_heldout,
        summarize,
    )

    blocked = _need_flag()
    if blocked is not None:
        return blocked
    if args.provider == "live":
        code = live_block()
        if code == "openvault_url":
            return _config("live needs OPENVAULT_URL. No score. Not a live pass.")
        if code == "openvault_key":
            return _config("live needs an ov_ key. No score. Not a live pass.")
        found = _ov_headers()
        if not found:
            return _config("live needs an ov_ key. No score. Not a live pass.")
        headers = found
        ov_url = (args.openvault or os.environ.get("OPENVAULT_URL") or "").strip()
    else:
        headers = {}
        ov_url = ""
    try:
        if args.dataset == "spider2":
            if not args.root:
                return _config("spider2 needs --root. No download and no score.")
            loaded = load_spider(Path(args.root))
            if loaded.sqlite_questions == 0:
                return _config("spider2 sqlite slice is empty. No score.")
            questions = loaded.questions
            db_root = loaded.db_root
            print(
                f"spider2 licence=MIT sqlite={loaded.sqlite_questions} "
                f"databases={loaded.databases} gold_absent={loaded.gold_absent} "
                f"excluded_cloud={loaded.excluded_cloud}"
            )
        else:
            if not args.questions or not args.db_root:
                return _config("bird needs --questions and --db-root.")
            from bench.bird.corpus import load_questions

            questions_path = Path(args.questions)
            if not questions_path.is_file():
                return _config("questions file is missing")
            questions = load_questions(questions_path)
            problem = validate_full(questions, limit=args.limit)
            if problem:
                return _config(problem + ". Pass --limit for a smoke run. Not a Mini-Dev score.")
            if args.limit is not None:
                questions = questions[: args.limit]
            db_root = Path(args.db_root)
    except HeldoutConfig as exc:
        return _config(str(exc.code) + ". No score.")
    out = Path(args.out)
    try:
        rows = run_heldout(
            questions,
            dataset=args.dataset,
            provider=args.provider,
            db_root=db_root,
            out=out,
            api_base="http://stub",
            ov_url=ov_url,
            ov_headers=headers,
            concurrency=args.concurrency,
        )
    except HeldoutConfig as exc:
        return _config(str(exc.code) + ". No score.")
    print(format_heldout(summarize(rows)))
    sent = "false"
    print(f"provider={args.provider} route=openvault provider_sent={sent}")
    if args.limit is not None or args.dataset == "spider2":
        print("not a full Mini-Dev score")
    print("pass=false target=null")
    return EXIT_PASS


def cmd_score(args: argparse.Namespace) -> int:
    summary = report(load_jsonl(Path(args.jsonl)))
    print(format_report(summary))
    print("pass=false target=null")
    return EXIT_PASS


def cmd_nightly(args: argparse.Namespace) -> int:
    """Fetch and score only when the flag is on and the arm has its settings.

    Missing settings exit 2 before any download. A sqlite root that is not the
    unpacked database directory exits 2 after the download and does not score.
    """
    blocked = _need_flag()
    if blocked is not None:
        return blocked
    api = (os.environ.get("DMS_API_BASE") or "").strip()
    ov_url = (os.environ.get("OPENVAULT_URL") or "").strip()
    headers: dict[str, str] = {}
    if args.arm == "dms" and not api:
        return _config("nightly dms arm needs DMS_API_BASE. No download and no score.")
    if args.arm == "baseline":
        if not ov_url:
            return _config("nightly baseline arm needs OPENVAULT_URL. No download and no score.")
        found = _ov_headers()
        if not found:
            return _config("nightly baseline arm needs an ov_ key. No download and no score.")
        headers = found
    dest = Path(args.dest)
    questions_path, db_root = fetch_release(dest)
    print(f"questions={questions_path}")
    print(f"db_root={db_root}")
    spaces: dict[str, str] = {}
    if args.arm == "dms":
        configured = (os.environ.get("DMS_SQLITE_SOURCE_ROOT") or "").strip()
        if not configured or Path(configured).resolve() != db_root.resolve():
            return _config(
                "DMS_SQLITE_SOURCE_ROOT must be the unpacked dev_databases directory. No score."
            )
        try:
            written = ingest_root(_post_api(api), db_root)
        except Exception as exc:  # noqa: BLE001 - the code is the message
            print(f"FAIL: {getattr(exc, 'code', type(exc).__name__)}")
            return EXIT_FAIL
        spaces_path = dest / "spaces.json"
        write_spaces(spaces_path, written)
        spaces = read_spaces(spaces_path)
    questions = load_questions(questions_path)
    problem = validate_full(questions, limit=None)
    if problem:
        return _config(problem)
    out = dest / f"{args.arm}.jsonl"
    run_questions(
        questions,
        arm=args.arm,
        db_root=db_root,
        spaces=spaces,
        evidence=args.evidence == "on",
        simulate_yes=True,
        concurrency=args.concurrency,
        out=out,
        api_base=api,
        ov_url=ov_url,
        ov_headers=headers,
        prior=load_jsonl(out),
    )
    print(format_report(report(load_jsonl(out))))
    print("pass=false target=null")
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("self-check")

    fetch = sub.add_parser("fetch")
    fetch.add_argument("--dest", required=True)
    fetch.add_argument("--url", default=None)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--db-root", required=True)
    ingest.add_argument("--out", required=True)
    ingest.add_argument("--api", default=None)

    run = sub.add_parser("run")
    run.add_argument("--arm", choices=("dms", "baseline"), required=True)
    run.add_argument("--questions", required=True)
    run.add_argument("--db-root", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--spaces", default=None)
    run.add_argument("--api", default=None)
    run.add_argument("--openvault", default=None)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--evidence", choices=("off", "on"), default="off")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--no-yes", action="store_true")

    score = sub.add_parser("score")
    score.add_argument("--jsonl", required=True)

    sub.add_parser("heldout-check")

    held = sub.add_parser("heldout")
    held.add_argument("--dataset", choices=("bird", "spider2"), required=True)
    held.add_argument("--provider", choices=("stub", "live"), required=True)
    held.add_argument("--questions", default=None)
    held.add_argument("--db-root", default=None)
    held.add_argument("--root", default=None)
    held.add_argument("--out", required=True)
    held.add_argument("--limit", type=int, default=None)
    held.add_argument("--openvault", default=None)
    held.add_argument("--concurrency", type=int, default=1)

    nightly = sub.add_parser("nightly")
    nightly.add_argument("--dest", required=True)
    nightly.add_argument("--arm", choices=("dms", "baseline"), default="dms")
    nightly.add_argument("--evidence", choices=("off", "on"), default="off")
    nightly.add_argument("--concurrency", type=int, default=1)

    args = parser.parse_args(argv)
    if args.cmd == "fetch" and args.url is None:
        from bench.bird.corpus import ZIP_URL

        args.url = ZIP_URL
    commands = {
        "self-check": lambda: cmd_self_check(),
        "fetch": lambda: cmd_fetch(args),
        "ingest": lambda: cmd_ingest(args),
        "run": lambda: cmd_run(args),
        "score": lambda: cmd_score(args),
        "nightly": lambda: cmd_nightly(args),
        "heldout-check": lambda: cmd_heldout_check(),
        "heldout": lambda: cmd_heldout(args),
    }
    return commands[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
