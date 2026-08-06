"""Run playground questions against a live DMS Space.

Edit ``playground/my_questions.yaml`` (your copy) — tweak ``prompt`` lines,
keep ``id`` stable. Seed bank: ``playground/questions.yaml``.

  python scripts/gen_playground_data.py
  python scripts/playground_ask.py --dry
  python scripts/playground_ask.py --space <space_id>
  python scripts/playground_ask.py --space <space_id> --only L2_encoding_beta
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_Q = ROOT / "playground" / "my_questions.yaml"
SEED_Q = ROOT / "playground" / "questions.yaml"
DEFAULT_URL = "http://127.0.0.1:8090"


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required: pip install pyyaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = list(data.get("questions") or [])
    if not items:
        raise SystemExit(f"no questions in {path}")
    return items


def _expect(case: dict[str, Any]) -> str:
    return str(case.get("expect_layer") or case.get("ladder_today") or "?")


def _aspiration(case: dict[str, Any]) -> str:
    return str(case.get("aspiration") or case.get("expect_layer") or "")


def _ask(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    import httpx

    resp = httpx.post(
        f"{base.rstrip('/')}/v1/chat/ask",
        json={"question": question, "space_id": space_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    return dict(resp.json())


def _layer_guess(env: dict[str, Any]) -> str:
    badge = str(env.get("badge") or "")
    route = str(env.get("route") or "").lower()
    if env.get("abstained") or badge == "ABSTAIN":
        return "ABSTAIN"
    if badge.startswith("L0") or "certified" in route:
        return "L0"
    if badge.startswith("L1") or "governed" in route or "metric" in route:
        return "L1"
    if "doc" in route or "rag" in route:
        return "L3"
    if badge.startswith("L2") or "query" in route or "session" in route:
        return "L2"
    return badge or "UNKNOWN"


def _print_case(case: dict[str, Any], env: dict[str, Any] | None, err: str | None) -> None:
    qid = case.get("id")
    expect = _expect(case)
    asp = _aspiration(case)
    print(f"\n=== {qid}  today={expect}  aspire={asp} ===")
    print(f"prompt: {case.get('prompt')}")
    if case.get("note"):
        print(f"note:   {case.get('note')}")
    if err:
        print(f"ERROR:  {err}")
        return
    assert env is not None
    got = _layer_guess(env)
    print(
        f"got:    layer~{got}  badge={env.get('badge')}  "
        f"abstained={env.get('abstained')}  route={env.get('route')}"
    )
    text = str(env.get("text") or "")
    print(f"text:   {text[:240]}{'...' if len(text) > 240 else ''}")
    rows = env.get("rows") or []
    if rows:
        print(f"rows:   {json.dumps(rows[:5], default=str)}")
    sql = env.get("sql_used")
    if sql:
        print(f"sql:    {str(sql)[:200]}")

    if got == "ABSTAIN" and expect in ("L2", "L3", "L4", "L5"):
        judge = "ABSTAIN-ok (better than invent)"
    elif expect in ("L4", "L5") and got in ("L0", "L1", "L2"):
        judge = f"aspirational-landed-as-{got}"
    elif got == expect:
        judge = "OK"
    else:
        judge = "CHECK"
    print(f"judge:  {judge}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    default_q = DEFAULT_Q if DEFAULT_Q.is_file() else SEED_Q
    ap.add_argument("--questions", type=Path, default=default_q)
    ap.add_argument("--space", help="Space id (required unless --dry)")
    ap.add_argument("--only", help="run a single question id")
    ap.add_argument("--dry", "--list", action="store_true", help="print prompts only")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--url", default=os.environ.get("DMS_URL", DEFAULT_URL))
    args = ap.parse_args(argv)

    cases = _load_yaml(args.questions)
    if args.only:
        cases = [c for c in cases if c.get("id") == args.only]
        if not cases:
            raise SystemExit(f"unknown id {args.only!r}")

    if args.dry:
        print(f"# from {args.questions}")
        for c in cases:
            print(f"{c.get('id'):<32} today={_expect(c):<4} aspire={_aspiration(c):<4}  {c.get('prompt')}")
        return 0

    if not args.space:
        ap.error("--space is required unless --dry")

    errors = 0
    for case in cases:
        try:
            env = _ask(args.url, str(case["prompt"]).strip(), str(args.space), args.timeout)
            _print_case(case, env, None)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            _print_case(case, None, f"{type(exc).__name__}: {exc}")

    print(f"\nDone. {len(cases)} asked, {errors} errors.")
    print("Tweak prompts in playground/my_questions.yaml and re-run --only <id>.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
