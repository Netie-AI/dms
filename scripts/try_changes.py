"""Exercise every change from the recovery + SCORE-03 wave, and say what each one proves.

This is a *demonstration* harness, not a second test suite. pytest already
asserts these things; what pytest does not do is explain, in one screen, what
changed and why you would care. Each check below prints the claim it is making
and - just as important - the claim it is *not* making.

    python scripts/try_changes.py              # offline; no stack, no Postgres
    python scripts/try_changes.py --live       # adds checks that need the stack up
    python scripts/try_changes.py --list       # just the inventory, run nothing

Exit code is 0 only if every check that ran passed. A check that cannot run
says so and does not silently count as a pass (R-0002).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "executor"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

API = os.environ.get("DMS_URL", "http://127.0.0.1:8090").rstrip("/")

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    GREEN = RED = DIM = BOLD = OFF = ""


class Result:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.blocked = 0
        self.failures: list[str] = []


R = Result()


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        R.passed += 1
        print(f"    {GREEN}PASS{OFF}  {label}")
    else:
        R.failed += 1
        R.failures.append(label)
        print(f"    {RED}FAIL{OFF}  {label}")
    if detail:
        print(f"          {DIM}{detail}{OFF}")


def blocked(label: str, why: str) -> None:
    R.blocked += 1
    print(f"    {RED}BLOCKED{OFF}  {label}")
    print(f"          {DIM}{why}{OFF}")


def section(num: str, title: str, *, proves: str, not_proves: str) -> None:
    print(f"\n{BOLD}[{num}] {title}{OFF}")
    print(f"    {DIM}proves:     {proves}{OFF}")
    print(f"    {DIM}stops at:   {not_proves}{OFF}")


# ---------------------------------------------------------------------------


def c1_composition_root() -> None:
    section(
        "1",
        "The API composition root is back",
        proves="every route that reaches the executor imports and mounts again",
        not_proves="that those routes return correct data - only that they exist",
    )
    try:
        from dms_api.app import create_app
    except Exception as exc:  # noqa: BLE001
        blocked("dms_api imports", f"{type(exc).__name__}: {exc}")
        return

    app = create_app()
    # Read the OpenAPI schema rather than walking app.routes: this FastAPI
    # version wraps included routers in objects with no .path, so the naive walk
    # silently found nothing and reported four failures for routes that were
    # mounted the whole time. The schema is also the surface a client actually
    # sees, which is the thing worth asserting on (R-0001).
    routes = set(app.openapi().get("paths", {}))
    for path in (
        "/v1/library/chunks/search",
        "/v1/studio/chunks",
        "/v1/library/reveal",
        "/v1/chat/ask",
    ):
        check(f"route mounted: {path}", path in routes)

    from dms_api import wiring

    for fn in (
        "reveal_origin_uri",
        "search_document_chunks",
        "list_document_chunks",
        "warehouse_tables",
    ):
        check(f"wiring exposes {fn}()", callable(getattr(wiring, fn, None)))

    import inspect

    sig = inspect.signature(wiring.warehouse_tables)
    check(
        "warehouse_tables takes space_id",
        "space_id" in sig.parameters,
        "Library reads the active Space, not the whole warehouse",
    )


def c2_space_filter_in_sql() -> None:
    section(
        "2",
        "Chunk search filters by Space in SQL, not after the fetch",
        proves="the WHERE clause carries space_id, so a forgotten post-filter cannot leak",
        not_proves="the live boundary - tests/control_plane/test_rag_space_boundary.py does that against Postgres",
    )
    src = (ROOT / "packages/core/dms_core/control_plane/document_chunks.py").read_text(
        encoding="utf-8"
    )
    for fn in ("search_chunks", "list_chunks"):
        body = src.split(f"def {fn}(", 1)[-1].split("\ndef ", 1)[0]
        check(
            f"{fn}() constrains space_id in SQL",
            "space_id" in body and "WHERE" in body.upper(),
            "scope is enforced by the query, not by filtering rows afterwards",
        )


def c3_reveal_contract() -> None:
    section(
        "3",
        "Reveal reports the file you asked for, on every OS",
        proves="`path` is the requested file on Windows, Linux and macOS; only `opened` varies",
        not_proves="that Explorer actually pops up - the subprocess call is stubbed here",
    )
    import dms_executor.reveal as rv

    class _OsShim:
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        def __getattr__(self, attr: str) -> Any:
            return getattr(os, attr)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        target = root / "quarterly_sales.csv"
        target.write_text("a,b\n1,2\n", encoding="utf-8")
        os.environ["DMS_REVEAL_ROOTS"] = str(root)

        calls: list[Any] = []
        real_popen, real_os, real_platform = rv.subprocess.Popen, rv.os, sys.platform
        rv.subprocess.Popen = lambda cmd, *a, **k: calls.append(cmd)  # type: ignore[assignment]
        try:
            for os_name, platform, want_action in (
                ("nt", "win32", "explorer_select"),
                ("posix", "linux", "xdg_or_open"),
                ("posix", "darwin", "xdg_or_open"),
            ):
                calls.clear()
                rv.os = _OsShim(os_name)  # type: ignore[assignment]
                sys.platform = platform  # type: ignore[misc]
                try:
                    res = rv.reveal_path(str(target))
                finally:
                    rv.os, sys.platform = real_os, real_platform  # type: ignore[misc]
                want_opened = str(target) if os_name == "nt" else str(target.parent)
                check(
                    f"{platform}: path is the requested file",
                    res.get("path") == str(target),
                    f"path={res.get('path')}",
                )
                check(
                    f"{platform}: opened={'file' if os_name == 'nt' else 'folder'}"
                    f" via {want_action}",
                    res.get("opened") == want_opened and res.get("action") == want_action,
                    f"opened={res.get('opened')}",
                )
        finally:
            rv.subprocess.Popen = real_popen  # type: ignore[assignment]
            os.environ.pop("DMS_REVEAL_ROOTS", None)

        outside = rv.reveal_path(str(Path.cwd() / "not_allowlisted.csv"), open_explorer=False)
        check(
            "a path outside the allowlist is refused",
            outside.get("ok") is False and outside.get("error") == "path_not_allowlisted",
            f"error={outside.get('error')}",
        )


def c4_f32_scope_trap() -> None:
    section(
        "4",
        "SCORE-03: the F32 ambiguous-scope trap is armed",
        proves="Sales and Wide_Fill disagree on rank AND magnitude, and the judge catches the wrong one",
        not_proves="that the product answers it correctly - it does not yet; VQ-01 (#39) owns that",
    )
    from score_answers import judge, oracle_top_n

    book = ROOT / "tests/fixtures/hostile_score/f32_ambiguous_scope.xlsx"
    if not book.is_file():
        blocked("F32 fixture", f"missing {book}; run scripts/gen_hostile_score_fixtures.py")
        return

    sales = oracle_top_n(book, "Sales", "category", "sales_value_myr", 3)
    wide = oracle_top_n(book, "Wide_Fill", "category", "sales_value_myr", 3)
    print(f"          {DIM}Sales truth : {'  '.join(f'{k}={v:,.2f}' for k, v in sales)}{OFF}")
    print(f"          {DIM}Wide_Fill   : {'  '.join(f'{k}={v:,.2f}' for k, v in wide)}{OFF}")

    check(
        "the two sheets rank categories differently",
        [k for k, _ in sales] != [k for k, _ in wide],
        "so picking the wrong sheet cannot accidentally score correct",
    )
    check(
        "the two sheets disagree on magnitude too",
        [v for _, v in sales] != [v for _, v in wide],
    )

    def env(rows: list[tuple[str, float]] | None, *, abstained: bool = False) -> dict[str, Any]:
        return {
            "badge": "ABSTAIN" if abstained else "L2_VALIDATED",
            "abstained": abstained,
            "rows": [{"category": k, "sales_value_myr": v} for k, v in (rows or [])],
            "text": "",
        }

    outcome, detail = judge(env(wide), sales)
    check(
        "answering from Wide_Fill under a green badge grades WRONG",
        outcome == "WRONG",
        detail,
    )
    outcome, _ = judge(env(sales), sales)
    check("answering from Sales grades correct (R-0005: no false refusal)", outcome == "correct")
    outcome, _ = judge(env(None, abstained=True), sales)
    check("abstaining costs coverage only, never precision", outcome == "abstained")


def c5_blank_rows() -> None:
    section(
        "5",
        "SCORE-03: blank and hanging rows do not move a total",
        proves="a messy sheet and a clean one holding the same data agree exactly",
        not_proves="anything about the live SQL path - this is the openpyxl oracle only",
    )
    from score_answers import grouped_totals, oracle_top_n

    book = ROOT / "tests/fixtures/hostile_score/blank_rows_hanging.xlsx"
    if not book.is_file():
        blocked("blank-rows fixture", f"missing {book}")
        return

    messy = grouped_totals(book, "Sales", "category", "sales_value_myr")
    clean = grouped_totals(book, "Sales_Clean", "category", "sales_value_myr")
    check("messy sheet totals == clean sheet totals", messy == clean, f"{messy}")
    check("the SUM footer was not counted as data", "Total" not in messy)
    check(
        "a hanging row (category, no measure) contributes nothing",
        round(messy.get("Home", 0), 2) == 899.45,
    )
    check(
        "top-3 is identical either way",
        oracle_top_n(book, "Sales", "category", "sales_value_myr", 3)
        == oracle_top_n(book, "Sales_Clean", "category", "sales_value_myr", 3),
    )


def c6_e9_boundary() -> None:
    section(
        "6",
        "E9: no executed query means no stated figure",
        proves="prose that invents totals without SQL is demoted to abstain before it reaches you",
        not_proves="that every live ask path routes through this - the invariant suite pins that",
    )
    try:
        from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
    except Exception as exc:  # noqa: BLE001
        blocked("envelope import", f"{type(exc).__name__}: {exc}")
        return

    invented = build_answer_envelope(
        answer_id="ans_demo_e9",
        text=(
            "Based on the documents, the top categories are Home 383,803.56, "
            "Sports 242,755.97 and Misc 228,548.84."
        ),
        badge="L2_VALIDATED",
        abstained=False,
        values=[],
        sql_used=None,
        rows=[],
    )
    check(
        "uncited totals with no SQL are demoted",
        invented.get("abstained") is True or invented.get("badge") == "ABSTAIN",
        f"badge={invented.get('badge')} abstained={invented.get('abstained')}",
    )
    try:
        assert_envelope_valid(invented)
        check("the demoted envelope still validates E1-E9", True)
    except Exception as exc:  # noqa: BLE001
        check("the demoted envelope still validates E1-E9", False, str(exc)[:160])


def c7_playground_ladder() -> None:
    section(
        "7",
        "The playground bank still spans the whole ladder",
        proves="L0 through L5 are all represented, and every question is answerable-shaped",
        not_proves="that the stack reaches those layers - L4/L5 are aspiration labels (P-DMS-33)",
    )
    import yaml

    pack = yaml.safe_load((ROOT / "playground/questions.yaml").read_text(encoding="utf-8"))
    qs = pack["questions"]
    layers = {str(q.get("expect_layer") or "").upper() for q in qs}
    check(f"{len(qs)} questions in the bank", len(qs) >= 10)
    for rung in ("L0", "L2", "L3", "L4", "L5"):
        check(f"ladder rung {rung} present", rung in layers)
    check(
        "the F26 doc-RAG sum trap is still in the bank",
        "l3_rag_sum_trap" in {str(q["id"]).lower() for q in qs},
    )


def c8_live_stack() -> None:
    section(
        "8",
        "Live stack (needs Start-DMSStack.ps1 running)",
        proves="the running product agrees with everything above",
        not_proves="cold-start behaviour - see #43, the first ask after a fresh start can 403",
    )
    try:
        import httpx
    except ImportError:
        blocked("httpx not installed", "pip install httpx")
        return
    try:
        health = httpx.get(f"{API}/health", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        blocked(
            "stack unreachable",
            f"{API} - start it with scripts/windows/Start-DMSStack.ps1 "
            f"-StartSiblings -EnableL2  ({type(exc).__name__})",
        )
        return

    # Read the keys /health actually publishes. An earlier version of this check
    # asked for dms_ask_mode / dms_demo_fallback, which do not exist; both came
    # back None, and None was in the accepted set - so the fallback check passed
    # without ever looking at the fallback. A check that cannot fail is not a
    # check, and this one guards R-0011 ("a silent fallback is a lie"), which is
    # exactly the thing you do not want quietly unguarded.
    for key in ("ask_mode", "demo_fallback"):
        if key not in health:
            blocked(
                f"/health does not publish {key!r}",
                f"cannot judge demo-fallback state; keys are {sorted(health)}",
            )
            return

    check("API is up", health.get("status") == "ok", f"ask_mode={health['ask_mode']}")
    check(
        "ask mode is live, not demo",
        health["ask_mode"] == "live",
        f"ask_mode={health['ask_mode']}",
    )
    check(
        "no silent demo fallback (R-0011)",
        health["demo_fallback"] is False,
        f"demo_fallback={health['demo_fallback']}",
    )
    cortex_ok = bool((health.get("dependencies", {}).get("cortex") or {}).get("ok"))
    check("Cortex reachable", cortex_ok)

    rc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_demo_live.py")],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    tail = [ln for ln in rc.stdout.strip().splitlines() if "checks passed" in ln]
    check(
        "verify_demo_live.py is green",
        rc.returncode == 0,
        tail[-1].strip() if tail else rc.stdout.strip()[-200:],
    )


CHECKS: list[tuple[str, str, Callable[[], None]]] = [
    ("1", "API composition root restored", c1_composition_root),
    ("2", "Space filter lives in SQL", c2_space_filter_in_sql),
    ("3", "Reveal path contract is cross-platform", c3_reveal_contract),
    ("4", "F32 ambiguous-scope trap armed", c4_f32_scope_trap),
    ("5", "Blank/hanging rows do not move totals", c5_blank_rows),
    ("6", "E9 numeric-answer boundary", c6_e9_boundary),
    ("7", "Playground ladder intact", c7_playground_ladder),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also check against a running stack")
    ap.add_argument("--list", action="store_true", help="print the inventory and stop")
    ap.add_argument("--only", help="run one check by number, e.g. --only 4")
    args = ap.parse_args(argv)

    if args.list:
        for num, title, _ in CHECKS:
            print(f"  {num}. {title}")
        print("  8. Live stack (with --live)")
        return 0

    print(f"{BOLD}What changed, and how you can see it{OFF}")
    print(f"{DIM}repo {ROOT}{OFF}")

    todo = [c for c in CHECKS if not args.only or c[0] == args.only]
    if args.only and not todo:
        print(f"no check numbered {args.only}")
        return 2

    for _, _, fn in todo:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            blocked(fn.__name__, f"raised {type(exc).__name__}: {exc}")

    if args.live and not args.only:
        try:
            c8_live_stack()
        except Exception as exc:  # noqa: BLE001
            blocked("live stack", f"{type(exc).__name__}: {exc}")
    elif not args.only:
        print(f"\n{DIM}[8] Live stack checks skipped - pass --live with the stack running.{OFF}")

    print(f"\n{'=' * 62}")
    print(f"  {GREEN}{R.passed} passed{OFF}   {RED}{R.failed} failed{OFF}   {R.blocked} blocked")
    if R.failed:
        print(f"\n  {RED}Failures:{OFF}")
        for f in R.failures:
            print(f"    - {f}")
    if R.blocked:
        print(f"\n  {DIM}Blocked checks did not run. They are not passes.{OFF}")
    return 1 if (R.failed or R.blocked) else 0


if __name__ == "__main__":
    raise SystemExit(main())
