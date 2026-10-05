"""Red-team runner: cases -> real DMS ask route (Cortex + model stubbed) -> JSONL + judge packets.

    python scripts/redteam_run.py --cases tests/redteam/sample_cases.yaml --out <dir> --run-id r1 --print
    python scripts/redteam_run.py --cases <dir> --family a --out <dir> --run-id a1 --judge-packets
    python scripts/redteam_run.py --results <dir>/r1/results.jsonl --judge-packets --print

No network, no OpenVault, no model, no key. Strictly sequential. See tests/redteam/harness.py.
Set PYTHONPATH to the worktree packages (this script also puts them first) and run from the
worktree root, never from a directory that holds a .env.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests" / "redteam"))

import harness  # noqa: E402  (ensure_paths() runs at import: worktree packages first)
from rt_cases import CaseError  # noqa: E402


def _read_results(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--cases", help="a case YAML file or a directory of family YAML files")
    ap.add_argument("--family", help="run only this family letter (a-e)")
    ap.add_argument("--out", help="output directory (a run folder <out>/<run-id>/ is created)")
    ap.add_argument("--run-id", help="run id (default: random)")
    ap.add_argument("--ext", help="extension SQL file (default: <family>.ext.sql next to the YAML)")
    ap.add_argument("--judge-packets", action="store_true", help="also write blind judge packets")
    ap.add_argument("--results", help="build packets from an existing results.jsonl (no run)")
    ap.add_argument(
        "--no-harness-paths",
        action="store_true",
        help="server flag DMS_HARNESS_ASK_PATHS off: ask_path generative returns HTTP 400",
    )
    ap.add_argument(
        "--print", dest="print_", action="store_true", help="print JSONL / packets to stdout"
    )
    args = ap.parse_args(argv)

    if args.results:
        records = _read_results(Path(args.results))
        packets, key = harness.build_packets(records)
        out_root = Path(args.out) if args.out else Path(args.results).parent
        pp, kp = harness.write_packets(records, out_root)
        print(f"wrote {pp} ({len(packets)} packets) and {kp}", file=sys.stderr)
        if args.print_:
            for p in packets:
                print(json.dumps(p, ensure_ascii=False))
        return 0

    if not args.cases or not args.out:
        ap.error("--cases and --out are required (or use --results)")
    try:
        res = harness.run_cases(
            args.cases,
            out_dir=args.out,
            run_id=args.run_id,
            family=args.family,
            ext_sql=args.ext,
            harness_ask_paths=not args.no_harness_paths,
            judge_packets=args.judge_packets,
        )
    except CaseError as exc:
        print(f"case file error: {exc}", file=sys.stderr)
        return 2
    print(f"run {res.run_id}: {res.footer['cases']} case(s) -> {res.results_path}", file=sys.stderr)
    print(
        f"verdicts: {res.footer['verdicts']}  badge_label_violations: "
        f"{res.footer['badge_label_violations']}  gold_pristine: {res.footer['gold_pristine']}  "
        f"network_attempts: {len(res.footer['network_attempts'])}",
        file=sys.stderr,
    )
    if args.print_:
        for line in res.lines():
            print(json.dumps(harness._jsonable(line), ensure_ascii=False))
        if res.packets_path:
            print("--- judge packets ---")
            for p in res.packets_path.read_text(encoding="utf-8").splitlines():
                print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
