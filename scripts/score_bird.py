"""SCORE-BIRD-01 -- measured live score on BIRD Space (batch bronze).

Platform SCORE-BIRD GO 2026-09-13. Real OK/LAYER/ABSTAIN/WRONG only. WRONG=0
law. First batch was gender. Bronze may grow; 75-table extract is leftover.
Keys stay in OpenVault.

GEN-01 (#179 @ a9578348) is the product path inside POST /v1/chat/ask.
This harness A/B's exact-match pack (must miss BIRD) vs that live path.
It is not GEN-02's curated coverage climb. Not EPIC-020b / #108 COMPLETE.

  python scripts/score_bird.py --self-check
  DMS_API_BASE=https://<studio>/api python scripts/score_bird.py --live
  python scripts/score_bird.py --minidev <mini_dev_postgresql.json> --live

A1-02 Mini-Dev (#264) grades /v1/chat/ask envelopes against gold SQL.
No accuracy target. Not a quoted Mini-Dev score from this tree.
No laptop default URL. Unset live env is CONFIG, not PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from score_bound import (  # noqa: E402
    bound_line,
    bound_pct,
    zero_wrong_summary_bounded,
)
from score_curated import (  # noqa: E402
    ask_error_envelope,
    judge,
    load_pack,
)

DEFAULT_PACK = ROOT / "tests" / "fixtures" / "bird_minidev" / "questions.yaml"
BIRD_SPACE = "f0da7dd3-58b3-4d15-84a8-a18f2853ed87"
TARGET_TABLES = 75
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3
TRANSPORT_BLOCK = frozenset({"ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException"})
KEEP_REFUSE = frozenset({"demo_pack_bleed", "full_extract"})


def load_bird(path: Path = DEFAULT_PACK) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    honesty = dict(data.get("honesty") or {})
    pack = load_pack(path)
    pack["honesty"] = honesty
    return pack


def as_tables(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw or []:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def bronze_stems(label: str) -> set[str]:
    s = label.lower().replace("-", "_").replace("#", ".").replace("/", ".")
    stems: set[str] = set()
    for chunk in s.split("."):
        chunk = chunk.strip()
        if not chunk:
            continue
        stems.add(chunk)
        if chunk.startswith("public_"):
            stems.add(chunk[7:])
        if chunk.startswith("bronze_"):
            stems.add(chunk[7:])
    return stems


def table_landed(need: str, names: list[str]) -> bool:
    want = need.lower().replace("-", "_")
    if not want:
        return False
    for raw in names:
        stems = bronze_stems(raw)
        if want in stems or any(part.endswith("_" + want) for part in stems):
            return True
    return False


def leftover_remaining(measured_n: int) -> int:
    return max(TARGET_TABLES - max(measured_n, 0), 0)


def honesty_ok(honesty: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    baseline = as_tables(honesty.get("baseline_tables") or honesty.get("attached_tables"))
    attached = as_tables(honesty.get("attached_tables"))
    attached_l = {t.lower() for t in attached}
    if "gender" not in {t.lower() for t in baseline}:
        errs.append("baseline_tables must include gender (first GO batch)")
    if not attached:
        errs.append("attached_tables snapshot required (growth ok)")
    missing = [t for t in baseline if t.lower() not in attached_l]
    if missing:
        errs.append(f"attached_tables must keep baseline {missing} (growth ok)")
    if int(honesty.get("leftover_full_extract_tables") or 0) != TARGET_TABLES:
        errs.append(f"leftover_full_extract_tables must be {TARGET_TABLES}")
    if int(honesty.get("source_count") or 0) != 1:
        errs.append("source_count must be 1 (one postgres source; tables may grow)")
    if str(honesty.get("space_id") or "") != BIRD_SPACE:
        errs.append("space_id must be the Platform BIRD Space")
    if str(honesty.get("data_source") or "") != "12b6f170":
        errs.append("data_source must be 12b6f170")
    blob = json.dumps(honesty)
    if any(k in blob for k in ("99.95", "99,95")):
        errs.append("honesty must not embed a high-nines percent")
    if "COMPLETE" in blob:
        errs.append("honesty must not claim COMPLETE")
    return errs


def case_expect(case: dict[str, Any], bronze_names: list[str] | None) -> str | None:
    """Yaml expect, or None to SKIP a leftover trap whose table has landed.

    No invented oracle for a newly attached Mini-Dev table. Skip is not OK.
    """
    leftover = str(case.get("leftover") or "")
    if leftover in KEEP_REFUSE:
        return "refuse"
    needs = as_tables(case.get("needs_table") or case.get("needs_tables"))
    landed = needs and bronze_names is not None
    if landed and all(table_landed(t, bronze_names) for t in needs):
        return None
    return str(case.get("expect") or "answered")


def _tally() -> dict[str, int]:
    return {"OK": 0, "ABSTAIN": 0, "LAYER": 0, "WRONG": 0}


def _path_report(name: str, tallies: dict[str, int], n: int) -> dict[str, Any]:
    wrong = tallies["WRONG"]
    answered = tallies["OK"] + tallies["LAYER"]
    precision: float | None
    if answered + wrong == 0:
        precision = None
    else:
        precision = round(100.0 * answered / (answered + wrong), 2)
    return {
        "path": name,
        "n": n,
        "ok": tallies["OK"],
        "layer": tallies["LAYER"],
        "abstain": tallies["ABSTAIN"],
        "wrong": wrong,
        "answered": answered,
        "bound_pct": bound_pct(answered),
        "precision_on_answered_pct": precision,
        "coverage_answered_pct": round(100.0 * answered / n, 2) if n else 0.0,
    }


def _miss() -> dict[str, Any]:
    return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "path miss"}


def exact_match_env(question: str, space_id: str) -> dict[str, Any]:
    """Demo pack / VQ-04 refuse only. Empty grants: BIRD is not Finance."""
    from dms_executor.demo_pack import maybe_pack_ask, maybe_uncertified_refuse_ask

    env = maybe_uncertified_refuse_ask(question, space_id=space_id)
    if env is not None:
        return env

    def _dead_submit(_sql: str) -> None:
        raise RuntimeError("exact-match BIRD lane must not submit")

    def _dead_ledger(_payload: dict[str, Any]) -> None:
        raise RuntimeError("exact-match BIRD lane must not ledger")

    env = maybe_pack_ask(
        question,
        space_id=space_id,
        grantable=set(),
        submit=_dead_submit,
        ledger_append=_dead_ledger,
    )
    return env if env is not None else _miss()


def print_honesty(
    honesty: dict[str, Any],
    *,
    measured: list[str] | None = None,
) -> None:
    baseline = as_tables(honesty.get("baseline_tables") or ["gender"])
    snapshot = as_tables(honesty.get("attached_tables"))
    names = measured if measured is not None else snapshot
    n = len(names)
    left = leftover_remaining(n)
    print(
        f"honesty baseline=[{','.join(baseline)}] "
        f"attached=[{','.join(snapshot)}] "
        f"target={TARGET_TABLES} measured={n} leftover={left} "
        f"source_count={honesty.get('source_count')} "
        f"data_source={honesty.get('data_source')}"
    )
    if measured is not None:
        print(f"bronze measured=[{','.join(names)}]")
    print("bronze grows in Platform batches; not Mini-Dev coverage; not COMPLETE")
    print("not COMPLETE: EPIC-020b #173, EPIC-020 #108, SCORE-BIRD-01")
    print("no high-nines percent claim. keys: OpenVault only. not a DB-GPT clone.")


def self_check(path: Path = DEFAULT_PACK) -> int:
    pack = load_bird(path)
    errs = honesty_ok(pack["honesty"])
    ids = [c["id"] for c in pack["questions"]]
    if len(ids) != len(set(ids)):
        errs.append("duplicate ids")
    if len(ids) < 6:
        errs.append(f"need gender hits + leftover traps, got {len(ids)}")
    expects = {str(c.get("expect") or "").lower() for c in pack["questions"]}
    if "answered" not in expects:
        errs.append("pack needs expect:answered baseline cases")
    if "refuse" not in expects:
        errs.append("pack needs expect:refuse leftover traps")
    baseline = as_tables(
        pack["honesty"].get("baseline_tables") or pack["honesty"].get("attached_tables")
    )
    attached_hits = [
        c["id"]
        for c in pack["questions"]
        if str(c.get("attached") or "").lower() in {t.lower() for t in baseline}
    ]
    leftover = [c["id"] for c in pack["questions"] if c.get("leftover")]
    if len(attached_hits) < 2:
        errs.append("need >=2 baseline-attached asks")
    if len(leftover) < 3:
        errs.append("need >=3 multi-table leftover traps")
    if not any(str(c.get("leftover") or "") == "full_extract" for c in pack["questions"]):
        errs.append("need leftover:full_extract trap (75-table invent)")
    growing = dict(pack["honesty"])
    growing["attached_tables"] = [*baseline, "schools"]
    if honesty_ok(growing):
        errs.append("honesty_ok must allow attached_tables to grow past baseline")
    spaces = pack["spaces"]
    if str(spaces.get("bird") or "") != BIRD_SPACE:
        errs.append("spaces.bird must pin Platform BIRD Space")
    blob = path.read_text(encoding="utf-8")
    if "99.95" in blob or "99,95" in blob:
        errs.append("fixture must not embed a high-nines percent")
    planted = judge(
        {"expect": "refuse"},
        {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"x": 1}]},
    )
    gender_ok = judge(
        {"expect": "answered", "min_rows": 1},
        {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"x": 1}]},
    )
    gender_abs = judge(
        {"expect": "answered", "min_rows": 1},
        {"badge": "ABSTAIN", "abstained": True, "rows": []},
    )
    empty_green = judge(
        {"expect": "answered", "min_rows": 1},
        {"badge": "L2_VALIDATED", "abstained": False, "rows": []},
    )
    if planted != "WRONG" or gender_ok != "OK" or gender_abs != "ABSTAIN":
        errs.append("judge plant")
    if empty_green != "WRONG":
        errs.append("confident empty gender answer must be WRONG")
    if not zero_wrong_summary_bounded(pass_lines(0, 0, 0)):
        errs.append("WRONG=0 summary must carry answered= and bound (A1-01)")
    if not zero_wrong_summary_bounded(pass_lines(9, 4, 0)):
        errs.append("WRONG=0 summary must carry answered= and bound (A1-01)")
    if zero_wrong_summary_bounded(["PASS: WRONG=0 on both paths. leftover=0/75 tables."]):
        errs.append("a bare WRONG=0 line must fail the bound check (R-0007)")
    if errs:
        print("FAIL: " + "; ".join(errs))
        return EXIT_FAIL
    from bird_minidev import minidev_self_check

    mini_errs = minidev_self_check()
    if mini_errs:
        print("FAIL: " + "; ".join(mini_errs))
        return EXIT_FAIL
    print(
        f"PASS: bird pack {len(ids)} cases, leftover traps {len(leftover)}, "
        "judge fail-closed. bronze may grow. not a live measurement."
    )
    print("PASS: minidev grader plants. not a live Mini-Dev score.")
    print_honesty(pack["honesty"])
    return EXIT_PASS


def _forbid_wildcard(label: str, url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in {"0.0.0.0", "*", "::", "[::]"}:
        raise ValueError(f"{label} hostname {host!r} is a public bind. DMS :8090 stays 127.0.0.1.")


def live_url(env: dict[str, str], url_arg: str | None) -> str:
    raw = (url_arg or env.get("DMS_API_BASE") or env.get("BIRD_SCORE_URL") or "").strip()
    if not raw:
        raise ValueError(
            "DMS_API_BASE (or BIRD_SCORE_URL / --url) is required for --live. "
            "No 127.0.0.1:8090 default. Unset is CONFIG, not PASS."
        )
    _forbid_wildcard("DMS_API_BASE", raw)
    return raw.rstrip("/")


def _ask_live(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    import httpx

    resp = httpx.post(
        f"{base}/v1/chat/ask",
        json={"question": question, "space_id": space_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("ask response is not an object")
    return body


def _blocked_kind(exc: BaseException) -> str | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 404:
        return "space_not_found"
    name = type(exc).__name__
    if name in TRANSPORT_BLOCK:
        return "transport"
    return None


def _blocked_envelope_kind(env: dict[str, Any]) -> str | None:
    """``space_not_found`` / ``space_id_empty`` ABSTAIN envelopes block the run.

    SPACE-GEN-01 round 2: an unknown or empty Space id is a named ABSTAIN
    envelope (200), no longer a 404.
    """
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    for kind in ("space_not_found", "space_id_empty"):
        if f"ABSTAIN reason: {kind}" in notes:
            return kind
    return None


def fetch_bronze(base: str, space_id: str, timeout: float) -> tuple[str, list[str]]:
    """Measured bronze names for the Space. Snapshot on failure, never invent 75."""
    import httpx

    try:
        resp = httpx.get(
            f"{base}/v1/studio/bronze",
            params={"space_id": space_id},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"bronze\tBLOCKED\t{type(exc).__name__}: using pack snapshot")
        return "snapshot", []
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("tables")
    else:
        rows = None
    if not isinstance(rows, list):
        print("bronze\tBLOCKED\tnot a list: using pack snapshot")
        return "snapshot", []
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = ""
        if isinstance(row, dict):
            label = str(row.get("table") or row.get("name") or "").strip()
        elif row:
            label = str(row).strip()
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        names.append(label)
    return "ok", names


def run_exact(
    pack: dict[str, Any],
    space_id: str,
    bronze_names: list[str] | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]], int]:
    tallies = _tally()
    rows: list[dict[str, Any]] = []
    skipped = 0
    for case in pack["questions"]:
        qid = str(case["id"])
        expect = case_expect(case, bronze_names)
        if expect is None:
            skipped += 1
            print(f"{qid}\texact\tSKIP\tlanded leftover trap (no invented oracle)")
            rows.append({"id": qid, "exact": "SKIP"})
            continue
        scored = {**case, "expect": expect}
        env = exact_match_env(str(case["question"]), space_id)
        verdict = judge(scored, env)
        tallies[verdict] += 1
        rows.append(
            {
                "id": qid,
                "expect": expect,
                "exact": verdict,
                "exact_badge": env.get("badge"),
            }
        )
        print(f"{qid}\texact\t{verdict}\t{env.get('badge')}\texpect={expect}")
    return tallies, rows, skipped


def run_live(
    pack: dict[str, Any],
    space_id: str,
    url: str,
    timeout: float,
    bronze_names: list[str] | None,
) -> tuple[str, dict[str, int], list[dict[str, Any]], int]:
    """Return (ok|blocked, tallies, per-case, skipped). blocked does not invent PASS."""
    tallies = _tally()
    rows: list[dict[str, Any]] = []
    skipped = 0
    for case in pack["questions"]:
        qid = str(case["id"])
        expect = case_expect(case, bronze_names)
        if expect is None:
            skipped += 1
            print(f"{qid}\tlive\tSKIP\tlanded leftover trap (no invented oracle)")
            rows.append({"id": qid, "generative": "SKIP"})
            continue
        scored = {**case, "expect": expect}
        try:
            env = _ask_live(url, str(case["question"]), space_id, timeout)
        except Exception as exc:  # noqa: BLE001
            blocked = _blocked_kind(exc)
            if blocked:
                print(f"{qid}\tlive\tBLOCKED\terror.type={blocked}\t{type(exc).__name__}")
                return "blocked", tallies, rows, skipped
            env = ask_error_envelope(exc)
            if env is None:
                print(f"{qid}\tlive\tERROR\t{type(exc).__name__}: {exc}")
                tallies["WRONG"] += 1
                rows.append({"id": qid, "generative": "WRONG", "generative_badge": "ERROR"})
                continue
            print(f"{qid}\tlive\tGRANT_REFUSE\t{type(exc).__name__}")
        blocked_env = _blocked_envelope_kind(env)
        if blocked_env:
            # The Space is missing: every case would ABSTAIN for the same
            # reason, which is a blocked run, not 500 scored refusals.
            print(f"{qid}\tlive\tBLOCKED\terror.type={blocked_env}\tenvelope")
            return "blocked", tallies, rows, skipped
        verdict = judge(scored, env)
        tallies[verdict] += 1
        n = len(env.get("rows") or env.get("values") or [])
        rows.append(
            {
                "id": qid,
                "generative": verdict,
                "generative_badge": env.get("badge"),
                "rows": n,
                "expect": expect,
            }
        )
        print(f"{qid}\tlive\t{verdict}\t{env.get('badge')}\trows={n}\texpect={expect}")
    return "ok", tallies, rows, skipped


def pass_lines(exact_answered: int, gen_answered: int, leftover: int) -> list[str]:
    """The WRONG=0 verdict plus its n and rule-of-three bound (A1-01, NETIE.md rule 7).

    n is answered (OK+LAYER) across both paths. Below n=300 the bound is
    above one percent and the line says so; nothing answered is n/a, never 0.
    """
    answered = exact_answered + gen_answered
    return [
        f"PASS: WRONG=0 on both paths. Not EPIC-020b COMPLETE. "
        f"leftover={leftover}/{TARGET_TABLES} tables.",
        f"  {bound_line(answered)}; exact_match answered={exact_answered} "
        f"generative_live answered={gen_answered}.",
    ]


def _write_artifact(report: dict[str, Any]) -> None:
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "cases"}
    # A1-01: answered (OK+LAYER over measured paths) and its rule-of-three bound.
    answered = sum(
        int(slim[k].get("answered") or 0)
        for k in ("exact_match", "generative")
        if isinstance(slim.get(k), dict)
    )
    slim["answered"] = answered
    slim["bound_pct"] = bound_pct(answered)
    (art / "score_bird.json").write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")


def print_paths(*rows: dict[str, Any]) -> None:
    print(f"{'path':<22} n ok layer abstain wrong answered precision")
    for row in rows:
        prec = row.get("precision_on_answered_pct")
        prec_s = "n/a" if prec is None else f"{prec:.2f} pct"
        print(
            f"{row['path']:<22} {row['n']} {row['ok']} {row['layer']} "
            f"{row['abstain']} {row['wrong']} {row['answered']} {prec_s}"
        )


def _scored_n(pack_n: int, skipped: int) -> int:
    return max(pack_n - skipped, 0)


def ab_offline() -> int:
    pack = load_bird()
    space = os.environ.get("BIRD_SPACE_ID", "").strip() or BIRD_SPACE
    snapshot = as_tables(pack["honesty"].get("attached_tables"))
    print("SCORE-BIRD-01 A/B exact-match only (no live Studio). generative=BLOCKED.")
    print_honesty(pack["honesty"], measured=snapshot)
    exact_t, cases, skipped = run_exact(pack, space, snapshot)
    n = _scored_n(len(pack["questions"]), skipped)
    exact_r = _path_report("exact_match", exact_t, n)
    exact_r["skipped"] = skipped
    print_paths(exact_r)
    if skipped:
        print(f"skipped {skipped} leftover traps (table now in bronze snapshot)")
    print("generative_live BLOCKED error.type=env.unset owner=Platform/studio")
    print("Run --live on prove/Studio for GEN-01 product path counts.")
    report = {
        "kind": "dms.score_bird",
        "pack": "bird_minidev",
        "space_id": space,
        "honesty": pack["honesty"],
        "measured_tables": snapshot,
        "leftover_remaining": leftover_remaining(len(snapshot)),
        "skipped": skipped,
        "exact_match": exact_r,
        "generative": {"path": "generative_live", "blocked": True},
        "wrong": exact_r["wrong"],
        "passed": False,
        "complete": False,
        "cases": cases,
    }
    _write_artifact(report)
    if exact_r["wrong"]:
        print("FAIL: exact-match WRONG>0")
        return EXIT_FAIL
    print("VERDICT: BLOCKED (generative not measured). Not COMPLETE.")
    return EXIT_BLOCKED


def live(url: str, timeout: float, space_id: str) -> int:
    pack = load_bird()
    snapshot = as_tables(pack["honesty"].get("attached_tables"))
    print("SCORE-BIRD-01 live A/B: exact-match pack vs POST /v1/chat/ask (GEN-01).")
    print(f"space_id={space_id}")
    print(f"DMS_API_BASE={url}")
    bronze_status, measured = fetch_bronze(url, space_id, timeout)
    names = measured if bronze_status == "ok" and measured else snapshot
    print_honesty(pack["honesty"], measured=names)
    exact_t, exact_cases, skip_e = run_exact(pack, space_id, names)
    status, live_t, live_cases, skip_g = run_live(pack, space_id, url, timeout, names)
    pack_n = len(pack["questions"])
    exact_r = _path_report("exact_match", exact_t, _scored_n(pack_n, skip_e))
    exact_r["skipped"] = skip_e
    by_id = {row["id"]: dict(row) for row in exact_cases}
    for row in live_cases:
        by_id.setdefault(row["id"], {}).update(row)
    leftover = leftover_remaining(len(names))
    if status == "blocked":
        print_paths(exact_r)
        print("generative_live BLOCKED. Do not invent OK/LAYER/ABSTAIN/WRONG.")
        report = {
            "kind": "dms.score_bird",
            "pack": "bird_minidev",
            "space_id": space_id,
            "honesty": pack["honesty"],
            "measured_tables": names,
            "leftover_remaining": leftover,
            "exact_match": exact_r,
            "generative": {"path": "generative_live", "blocked": True},
            "wrong": exact_r["wrong"],
            "passed": False,
            "complete": False,
            "cases": list(by_id.values()),
        }
        _write_artifact(report)
        print("VERDICT: BLOCKED. Not COMPLETE.")
        return EXIT_BLOCKED
    gen_r = _path_report("generative_live", live_t, _scored_n(pack_n, skip_g))
    gen_r["skipped"] = skip_g
    print_paths(exact_r, gen_r)
    if skip_g:
        print(f"skipped {skip_g} leftover traps (table now in bronze; no invented oracle)")
    wrong = exact_r["wrong"] + gen_r["wrong"]
    report = {
        "kind": "dms.score_bird",
        "pack": "bird_minidev",
        "space_id": space_id,
        "honesty": pack["honesty"],
        "measured_tables": names,
        "leftover_remaining": leftover,
        "exact_match": exact_r,
        "generative": gen_r,
        "wrong": wrong,
        "passed": wrong == 0,
        "complete": False,
        "cases": list(by_id.values()),
    }
    _write_artifact(report)
    if wrong:
        print("FAIL: WRONG>0 (confidently wrong or transport error)")
        return EXIT_FAIL
    for line in pass_lines(exact_r["answered"], gen_r["answered"], leftover):
        print(line)
    return EXIT_PASS


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--ab", action="store_true")
    p.add_argument("--url", default=None)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--space", default=None)
    p.add_argument(
        "--minidev",
        default=None,
        metavar="JSON",
        help="BIRD Mini-Dev JSON path or URL (500 questions). Never committed.",
    )
    p.add_argument(
        "--with-evidence",
        action="store_true",
        help="Append BIRD evidence text to the question (reported separately).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Smoke slice. Printed. Not a Mini-Dev score.",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="No Cortex. Skips FreeRoute freeze. Synthetic/--self-check path.",
    )
    p.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="Mini-Dev: seconds to wait between questions (provider rate limits).",
    )
    p.add_argument(
        "--provider-attempts",
        type=int,
        default=3,
        help="Mini-Dev: attempts per question on HTTP 429/5xx/timeout before "
        "PROVIDER_ERROR (excluded from n, never RIGHT).",
    )
    p.add_argument(
        "--compare",
        nargs=2,
        metavar=("A", "B"),
        help="Compare two Mini-Dev artifacts. Refuses different setup fingerprints.",
    )
    p.add_argument(
        "--force-cross-setup",
        action="store_true",
        help="Allow a labeled cross-setup compare. Never implied same-setup.",
    )
    args = p.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.minidev or args.compare:
        from bird_minidev import run_minidev_cli

        return run_minidev_cli(args, dict(os.environ))
    space = (args.space or os.environ.get("BIRD_SPACE_ID") or BIRD_SPACE).strip()
    if args.live:
        try:
            url = live_url(dict(os.environ), args.url)
        except ValueError as exc:
            print(f"CONFIG: {exc}")
            print("VERDICT: CONFIG")
            return EXIT_CONFIG
        return live(url, args.timeout, space)
    if args.ab:
        return ab_offline()
    print(
        "usage: python scripts/score_bird.py --self-check | --ab | --live | "
        "--minidev JSON --live | --compare A B\n"
        "--live requires DMS_API_BASE (A/B exact vs GEN-01 product path)."
    )
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
