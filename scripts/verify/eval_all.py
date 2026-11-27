"""R1-EVAL-01: one report for the 52, pack H, BIRD mini-dev 2026, and the ambiguity pack.

  python scripts/verify/eval_all.py --stub --out .tmp/eval_all
  python scripts/verify/eval_all.py --armed --out .tmp/eval_all
  python scripts/verify/eval_all.py --download-bird --url URL --dest /tmp/bird-minidev

Stub never calls Cortex or OpenVault, never opens pack H, and never downloads
BIRD. It writes report.md and report.json. Armed is the manual run on the box.
A missing setting is a named refusal, not a score.

No serving flag. This file does not change POST /v1/chat/ask.
Model ids are recorded only from an OpenVault hop stamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
VERIFY = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
for entry in (str(VERIFY), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import ambig_pack  # noqa: E402
import grade52  # noqa: E402

# Scripts and CI only. There is no DMS_* flag on the ask path.
SERVING_FLAG = None
TICKET = "R1-EVAL-01"
STUB_LIMIT_S = 600
SHA_FILE = VERIFY / "bird_minidev_2026.sha256"
UNPINNED = "UNPINNED"
# Named so an armed run can be pointed at it. Stub never opens this path.
PACK_H = Path("/workspace/dms-demo-20261127/heldout/pack_h")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PUBLIC_TARGET = (
    "DMS on BIRD mini-dev 2026 PostgreSQL: {ex} execution accuracy through the "
    "full product path, with 0 wrong answers served on our own held-out pack; "
    "everything else asks back or declines with a reason. Phase 1 gate X >= 80."
)
COLUMNS = (
    "correct",
    "WRONG",
    "refusal_wrong",
    "reconfirm",
    "clarify",
    "abstain",
    "AI",
    "rule",
    "unattributed",
    "p50_s",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pin(path: Path = SHA_FILE) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    raise SystemExit("eval_all: checksum pin file is empty")


def assert_outside_repo(dest: Path) -> None:
    resolved = dest.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return
    raise SystemExit("eval_all: BIRD data must stay outside the repo")


def verify_corpus(path: Path, pin_file: Path = SHA_FILE) -> str:
    """Match the file to the pin. UNPINNED is a refusal, not a score."""
    if not path.is_file():
        raise SystemExit("eval_all: BIRD corpus file is missing")
    assert_outside_repo(path)
    pin = read_pin(pin_file)
    digest = sha256_file(path)
    if pin == UNPINNED:
        raise SystemExit(
            f"eval_all: BIRD checksum is UNPINNED. computed sha256={digest}. not a score"
        )
    if len(pin) != 64 or any(char not in "0123456789abcdef" for char in pin) or pin != digest:
        raise SystemExit("eval_all: BIRD checksum mismatch. not a score")
    return digest


def download_bird(url: str, dest: Path) -> Path:
    """Fetch the corpus to dest (outside the repo). Does not score it."""
    import httpx

    assert_outside_repo(dest)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "mini_dev.json"
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    return target


def ontology_from_sqlite(path: Path) -> dict[str, Any]:
    """Tables and columns only. Row values never enter the ontology."""
    import sqlite3

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables: list[dict[str, Any]] = []
        for name in names:
            if not isinstance(name, str) or not _IDENT.match(name):
                continue
            columns = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{name}")')
            ]
            tables.append({"name": name, "columns": columns})
    finally:
        connection.close()
    return {"tables": tables}


def bird_outcome(env: Mapping[str, Any], gold_rows: Sequence[Any]) -> str:
    """EX, WRONG, clarify, reconfirm, or abstain. Clarify wins over a row match."""
    from bird_minidev import grade_envelope

    if grade52._is_clarify(env):
        return "clarify"
    block = env.get("reconfirm")
    if isinstance(block, Mapping) and "suggested_question" in block:
        return "reconfirm"
    raw = grade_envelope(env, gold_rows)
    if raw in {"OK", "LAYER"}:
        return "EX"
    if raw == "WRONG":
        return "WRONG"
    return "abstain"


def bird_tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """EX rate and the DMS counts, overall and per dialect. Empty is not a rate."""

    def one(subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        n = len(subset)
        counts = {key: 0 for key in ("EX", "WRONG", "clarify", "reconfirm", "abstain")}
        for row in subset:
            outcome = str(row.get("outcome") or "")
            if outcome in counts:
                counts[outcome] += 1
        rate = None if n == 0 else counts["EX"] / n
        return {"n": n, "ex_rate": rate, **counts}

    dialects = sorted({str(row.get("dialect") or "") for row in rows})
    return {
        "overall": one(rows),
        "by_dialect": {name: one([row for row in rows if str(row.get("dialect") or "") == name]) for name in dialects},
    }


def _p50(report: Mapping[str, Any]) -> Any:
    totals: list[float] = []
    for case in report.get("cases") or []:
        if not isinstance(case, Mapping):
            continue
        timings = case.get("timings")
        if not isinstance(timings, Mapping):
            continue
        spans = timings.get("spans_ms")
        raw = spans.get("total") if isinstance(spans, Mapping) else None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        totals.append(float(raw) / 1000.0)
    if not totals:
        return "unmeasured (stub)" if report.get("mode") != grade52.MODE_ARMED else "unmeasured"
    return round(grade52._percentile(totals, 50), 3)


def pack_row(report: Mapping[str, Any]) -> dict[str, Any]:
    """The founder table. WRONG is grade52's headline (bucket wrong + refusal_wrong)."""
    split = grade52.route_split(report["cases"])
    pin = grade52.pin_split(report["cases"])
    hops = sum(1 for case in report["cases"] if str(case.get("hop_model") or "").strip())
    row = {
        "mode": report.get("mode"),
        "n": report.get("n"),
        "correct": report.get("correct"),
        "WRONG": grade52.headline_wrong(report),
        "refusal_wrong": report.get("refusal_wrong"),
        "reconfirm": report.get("reconfirm"),
        "clarify": int(report.get("clarify") or 0),
        "abstain": report.get("abstain"),
        "AI": split["ai"],
        "rule": split["rule"],
        "unattributed": split["unattributed"],
        "p50_s": _p50(report),
        "refusal_ok": report.get("refusal_ok"),
        "gold_broken": report.get("gold_broken"),
        "empty_gold": report.get("empty_gold"),
        "pin": pin,
        "hop_stamps": hops,
    }
    if hops == 0:
        row["pin_status"] = (
            "unmeasured (stub)" if report.get("mode") != grade52.MODE_ARMED else "unmeasured"
        )
    else:
        row["pin_status"] = "measured"
    return row


def _unmeasured(kind: str) -> dict[str, str]:
    return {"status": "unmeasured (stub)", "kind": kind}


def assemble(
    off: Mapping[str, Any],
    on: Mapping[str, Any],
    *,
    elapsed_s: float,
    mode: str = "stub",
) -> dict[str, Any]:
    errors = ambig_pack.validate_pack()
    if errors:
        raise SystemExit("eval_all: ambiguity pack: " + "; ".join(errors))
    cases = ambig_pack.load_cases()
    return {
        "ticket": TICKET,
        "mode": mode,
        "serving_flag": SERVING_FLAG,
        "dms_sha": off.get("dms_sha"),
        "elapsed_s": round(elapsed_s, 3),
        "note": (
            "stub-exec is not an armed score. Pack H, BIRD execution accuracy, "
            "and hop-stamp pin splits are unmeasured (stub) until a box run."
            if mode == "stub"
            else "Armed 52 is the live ask. BIRD EX stays unmeasured until the checksum is pinned and a live run returns it."
        ),
        "public_target": PUBLIC_TARGET.format(
            ex="unmeasured (stub)" if mode == "stub" else "unmeasured"
        ),
        "phase_1_gate": "X >= 80",
        "pack_52": {
            "flag_off": pack_row(off),
            "flag_on": pack_row(on),
            "flag_on_note": (
                "R1-EVAL-01 adds no serving flag. flag_on is the existing "
                "grade52 --main --ai-first stub (DMS_AI_FIRST=1), measured here "
                "so the split is on this SHA. It is not an armed score."
            ),
        },
        "pack_h": _unmeasured("pack_h"),
        "bird": {
            **_unmeasured("bird_minidev_2026"),
            "ex_overall": "unmeasured (stub)",
            "ex_postgresql": "unmeasured (stub)",
            "ex_sqlite": "unmeasured (stub)",
            "WRONG": "unmeasured (stub)",
            "clarify": "unmeasured (stub)",
            "reconfirm": "unmeasured (stub)",
            "checksum": read_pin(),
        },
        "ambig": {
            "n": len(cases),
            "by_kind": ambig_pack.counts_by_kind(cases),
            "clarify_precision": "unmeasured (stub)",
            "clarify_recall": "unmeasured (stub)",
            "schema": "ok",
        },
        "columns": list(COLUMNS),
    }


def render_md(report: Mapping[str, Any]) -> str:
    def cells(row: Mapping[str, Any]) -> str:
        return " | ".join(str(row.get(key, "")) for key in COLUMNS)

    lines = [
        f"# {report['ticket']} {report['mode']}",
        "",
        f"sha: {report.get('dms_sha')}",
        f"serving flag: {report.get('serving_flag')}",
        "",
        str(report.get("public_target") or ""),
        "",
    ]
    pack = report.get("pack_52")
    if isinstance(pack, Mapping) and "flag_off" in pack and "flag_on" in pack:
        header = " | ".join(COLUMNS)
        rule = " | ".join("---" for _ in COLUMNS)
        lines.extend(
            [
                "52 stub-exec. Not an armed score."
                if report.get("mode") == "stub"
                else "52 armed.",
                "",
                f"| run | {header} |",
                f"| --- | {rule} |",
                f"| flag off | {cells(pack['flag_off'])} |",
                f"| flag on (ai-first stub) | {cells(pack['flag_on'])} |",
                "",
                str(pack.get("flag_on_note") or ""),
                "",
            ]
        )
    else:
        reason = pack.get("reason") if isinstance(pack, Mapping) else "unmeasured"
        lines.extend([f"52: unmeasured. {reason}", ""])
    pack_h = report.get("pack_h") if isinstance(report.get("pack_h"), Mapping) else {}
    bird = report.get("bird") if isinstance(report.get("bird"), Mapping) else {}
    ambig = report.get("ambig") if isinstance(report.get("ambig"), Mapping) else {}
    lines.extend(
        [
            f"pack H: {pack_h.get('status')}",
            f"BIRD EX: {bird.get('ex_overall')} (checksum {bird.get('checksum')})",
            (
                f"ambiguity pack: n={ambig.get('n')} "
                f"precision={ambig.get('clarify_precision')} "
                f"recall={ambig.get('clarify_recall')}"
            ),
            "",
            "Pin: scored-on-pin counts only an OpenVault hop stamp. "
            "A stub run has no hop stamps, so that split is unmeasured (stub).",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(report: Mapping[str, Any], out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "report.json"
    md_path = out / "report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    return md_path, json_path


def run_stub(out: Path) -> int:
    started = time.perf_counter()
    off = grade52.grade_main()
    on = grade52.grade_main(ai_first=True)
    elapsed = time.perf_counter() - started
    if elapsed > STUB_LIMIT_S:
        raise SystemExit(f"eval_all: stub took {elapsed:.0f}s; limit is {STUB_LIMIT_S}s")
    report = assemble(off, on, elapsed_s=elapsed, mode="stub")
    md_path, json_path = write_outputs(report, out)
    print(f"eval_all stub wrote {md_path}")
    print(f"eval_all stub wrote {json_path}")
    print(f"eval_all stub elapsed_s={report['elapsed_s']}")
    return 0


def run_armed(out: Path) -> int:
    """Refuse unless the box settings are present. Never invents a BIRD number."""
    import os

    missing = [
        name
        for name in (grade52.ARMED_CORTEX_ENV, grade52.ARMED_KEY_ENV, grade52.ARMED_SHA_ENV)
        if not str(os.environ.get(name) or "").strip()
    ]
    if missing:
        reason = "eval_all armed: REFUSED " + ",".join(missing)
        report = {
            "ticket": TICKET,
            "mode": "armed",
            "serving_flag": SERVING_FLAG,
            "public_target": PUBLIC_TARGET.format(ex="unmeasured"),
            "pack_52": {"status": "unmeasured", "reason": reason},
            "pack_h": {"status": "unmeasured", "reason": reason},
            "bird": {"status": "unmeasured", "ex_overall": "unmeasured", "reason": reason},
            "ambig": {"clarify_precision": "unmeasured", "clarify_recall": "unmeasured"},
        }
        write_outputs(report, out)
        print(reason)
        return 2
    started = time.perf_counter()
    try:
        grade52.armed_config()
        off = grade52.grade_armed("copy-only")
        on = grade52.grade_armed("copy-only", profile=grade52.PROFILE_AI_FIRST)
    except SystemExit as exc:
        report = {
            "ticket": TICKET,
            "mode": "armed",
            "serving_flag": SERVING_FLAG,
            "public_target": PUBLIC_TARGET.format(ex="unmeasured"),
            "pack_52": {"status": "unmeasured", "reason": str(exc)},
            "pack_h": {"status": "unmeasured", "reason": str(exc)},
            "bird": {"status": "unmeasured", "ex_overall": "unmeasured", "reason": str(exc)},
            "ambig": {"clarify_precision": "unmeasured", "clarify_recall": "unmeasured"},
        }
        write_outputs(report, out)
        print(str(exc))
        return 2
    report = assemble(off, on, elapsed_s=time.perf_counter() - started, mode="armed")
    report["bird"] = {
        "status": "unmeasured",
        "ex_overall": "unmeasured",
        "ex_postgresql": "unmeasured",
        "ex_sqlite": "unmeasured",
        "WRONG": "unmeasured",
        "clarify": "unmeasured",
        "reconfirm": "unmeasured",
        "checksum": read_pin(),
        "reason": "BIRD EX is recorded only after the checksum pin matches a corpus outside the repo",
    }
    report["pack_h"] = {
        "status": "unmeasured",
        "reason": "grade pack H with grade52.py --armed --pack on the box. This process does not open it.",
    }
    md_path, json_path = write_outputs(report, out)
    print(f"eval_all armed wrote {md_path}")
    print(f"eval_all armed wrote {json_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / ".tmp" / "eval_all")
    parser.add_argument("--download-bird", action="store_true")
    parser.add_argument("--url", default="")
    parser.add_argument("--dest", type=Path)
    args = parser.parse_args(argv)
    if args.download_bird:
        if not args.url or args.dest is None:
            raise SystemExit("eval_all: --download-bird needs --url and --dest")
        path = download_bird(args.url, args.dest)
        print(f"downloaded {path} sha256={sha256_file(path)}")
        print("not a score until scripts/verify/bird_minidev_2026.sha256 pins that digest")
        return 0
    if args.stub == args.armed:
        raise SystemExit("eval_all: pass exactly one of --stub or --armed")
    if args.armed:
        return run_armed(args.out)
    return run_stub(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
