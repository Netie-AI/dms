"""CLI. Exit codes are typed outcomes, never a WRONG-count guess.

``DMS_API_BASE`` is the server. ``DMS_API_TOKEN`` is the bearer when set.
The token is not printed. ``--json`` writes the envelope to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dms_sdk.client import DmsClient
from dms_sdk.outcome import exit_code

ClientFactory = Callable[..., DmsClient]


def _default_factory(base_url: str) -> DmsClient:
    token = os.environ.get("DMS_API_TOKEN", "").strip() or None
    return DmsClient(base_url=base_url, token=token)


def _print(body: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(body, sort_keys=True, default=str) + "\n")


def _read_document(path: str) -> dict[str, Any] | None:
    """JSON object from ``path``. None on any read or parse failure.

    The exception text is dropped so a file path or a fragment is not printed.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        body = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def main(argv: list[str] | None = None, *, client_factory: ClientFactory | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dms")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DMS_API_BASE", "http://127.0.0.1:8000"),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask")
    ask.add_argument("--space", required=True)
    ask.add_argument("--question", required=True)
    ask.add_argument("--json", action="store_true")

    bench = sub.add_parser("bench")
    bench_sub = bench.add_subparsers(dest="bench_cmd", required=True)
    run = bench_sub.add_parser("run")
    run.add_argument("--space", required=True)
    run.add_argument("--json", action="store_true")

    imp = sub.add_parser("import")
    imp.add_argument("--space", required=True)
    imp.add_argument("--file", required=True)
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    factory = client_factory or _default_factory
    client = factory(base_url=args.base_url)
    try:
        if args.cmd == "ask":
            body = client.ask(space_id=args.space, question=args.question)
        elif args.cmd == "bench":
            body = client.benchmark_run(space_id=args.space)
        else:
            document = _read_document(args.file)
            if document is None:
                body = {"outcome": "error", "route": None, "code": "import_file"}
            else:
                body = client.semantic_import(
                    space_id=args.space,
                    document=document,
                    dry_run=bool(args.dry_run),
                )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    if args.json or args.cmd == "import":
        _print(body)
    elif args.cmd == "bench":
        _print(body)
    else:
        _print(body)
    return exit_code(body)
