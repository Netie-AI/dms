"""End-to-end demo verification against the LIVE stack (DMS + Cortex + OpenVault).

Asserts the customer-visible artifact at the layer the customer receives it
(R-0001): the HTTP envelope from POST /v1/chat/ask and the ingest receipt from
POST /v1/studio/ingest-batch. Nothing here inspects DMS internals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

API = "http://127.0.0.1:8090"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
XLSX = Path("D:/DMS/tests/fixtures/ingest/15_q3_sales_export.xlsx")

REVENUE = "What is our total spend by supplier country?"   # needs suppliers (Finance only)
WHERE_SKU = "What is total stock value by category?"        # needs inventory (company-wide)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")


def ask(client: httpx.Client, question: str, **kw) -> tuple[int, dict]:
    r = client.post(f"{API}/v1/chat/ask", json={"question": question, **kw}, timeout=120.0)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_raw": r.text[:400]}


def has_numeric_value(env: dict) -> bool:
    for v in env.get("values") or []:
        val = v.get("value")
        if isinstance(val, (int, float)):
            return True
    return False


def envelope_compute_or_abstain(status: int, env: dict) -> bool:
    """200 + numeric answer, or honest abstain (R-0001). Silent wrong answer is not OK."""
    if status != 200:
        return False
    abstained = env.get("abstained")
    badge = env.get("badge")
    if abstained is False:
        return has_numeric_value(env)
    if abstained is True:
        return badge == "ABSTAIN"
    return False


with httpx.Client() as c:
    print("\n== health ==")
    h = c.get(f"{API}/health", timeout=30).json()
    check("ask_mode is live", h["ask_mode"] == "live", f"ask_mode={h['ask_mode']}")
    check("demo fallback is off", h["demo_fallback"] is False)
    check("cortex reachable", h["dependencies"]["cortex"]["ok"] is True)

    print("\n== spaces carry the DR-0002 names ==")
    spaces = c.get(f"{API}/v1/spaces", timeout=30).json()["spaces"]
    names = {s["name"] for s in spaces}
    check("Finance and Warehouse Ops exist", {"Finance", "Warehouse Ops"} <= names, str(names))

    print("\n== P0-DEMO-01: xlsx ingest reports the truth ==")
    files = [("files", (XLSX.name, XLSX.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))]
    r = c.post(
        f"{API}/v1/studio/ingest-batch",
        files=files,
        data={"space_id": FINANCE},
        timeout=180.0,
    )
    receipt = r.json()
    check("ingest returned 200", r.status_code == 200, f"status={r.status_code}")
    entry = (receipt.get("files") or [{}])[0]
    table = entry.get("table")
    check("receipt says ingested", receipt.get("ingested") == 1,
          f"ingested={receipt.get('ingested')} reason={entry.get('reason')}")
    check("receipt names the created table", bool(table), f"table={table}")

    if table:
        fin_bronze = c.get(f"{API}/v1/studio/bronze?space_id={FINANCE}", timeout=30).json()
        ops_bronze = c.get(f"{API}/v1/studio/bronze?space_id={WAREHOUSE_OPS}", timeout=30).json()
        fin_names = {row.get("table") for row in fin_bronze}
        ops_names = {row.get("table") for row in ops_bronze}
        check("upload visible in Finance Studio bronze", table in fin_names, str(fin_names))
        check("upload hidden from Warehouse Ops bronze", table not in ops_names, str(ops_names))

        st_fin, env_fin = ask(
            c,
            "How many units were sold?",
            grounded_tables=[table],
            space_id=FINANCE,
            session_id="ses_fin_upload",
        )
        st_ops, env_ops = ask(
            c,
            "How many units were sold?",
            grounded_tables=[table],
            space_id=WAREHOUSE_OPS,
            session_id="ses_ops_upload",
        )
        ops_detail = env_ops.get("detail", {}) if isinstance(env_ops.get("detail"), dict) else {}
        check(
            "upload groundable in Finance",
            st_fin == 200 and env_fin.get("abstained") is False,
            f"status={st_fin} badge={env_fin.get('badge')}",
        )
        check(
            "upload refused in Warehouse Ops",
            st_ops == 403 and ops_detail.get("code") == "grounding_not_grantable",
            f"status={st_ops} code={ops_detail.get('code')}",
        )

    print("\n== offline fixtures hidden in live demo ==")
    src_refs = {s.get("ref") for s in c.get(f"{API}/v1/library/sources", timeout=30).json()}
    check("company gl_export hidden from sources", "Company/finance/gl_export.csv" not in src_refs, str(src_refs))

    print("\n== P0-DEMO-03: grounding mints exactly the selection ==")
    if table:
        st, env = ask(c, "How many units were sold?", grounded_tables=[table],
                      session_id="ses_demo_ground")
        if st == 200:
            g = env.get("grounded_tables")
            check("manifest holds exactly the ticked file", g == [table], f"grounded_tables={g}")
            check("UI count equals manifest length", len(g or []) == 1, f"count={len(g or [])}")
        else:
            check("grounded ask returned 200", False, f"status={st} body={json.dumps(env)[:300]}")

    st, env = ask(c, "How many units?", grounded_tables=["bronze.never_ingested"],
                  session_id="ses_demo_refuse")
    detail = env.get("detail", {}) if isinstance(env.get("detail"), dict) else {}
    check("an ungrantable selection is refused, not widened",
          st == 403 and detail.get("code") == "grounding_not_grantable",
          f"status={st} code={detail.get('code')}")

    print("\n== DR-0002: the boundary shows both halves ==")
    st_f, env_f = ask(c, REVENUE, space_id=FINANCE, session_id="ses_fin")
    st_o, env_o = ask(c, REVENUE, space_id=WAREHOUSE_OPS, session_id="ses_ops")

    check("revenue answers in Finance",
          st_f == 200 and env_f.get("abstained") is False,
          f"status={st_f} badge={env_f.get('badge')} abstained={env_f.get('abstained')}")
    ops_refused = st_o in (403,) or env_o.get("abstained") is True
    check("revenue does NOT answer in Warehouse Ops", ops_refused,
          f"status={st_o} badge={env_o.get('badge')} abstained={env_o.get('abstained')}")
    if st_o == 200:
        check("no green badge over a refusal", env_o.get("badge") == "ABSTAIN",
              f"badge={env_o.get('badge')}")

    st_a, env_a = ask(c, WHERE_SKU, space_id=FINANCE, session_id="ses_fin2")
    st_b, env_b = ask(c, WHERE_SKU, space_id=WAREHOUSE_OPS, session_id="ses_ops2")
    check("the shared operational question answers in Finance",
          st_a == 200 and env_a.get("abstained") is False,
          f"status={st_a} badge={env_a.get('badge')}")
    check("the shared operational question answers in Warehouse Ops",
          st_b == 200 and env_b.get("abstained") is False,
          f"status={st_b} badge={env_b.get('badge')}")

    print("\n== no silent demo fallback anywhere (R-0011) ==")
    for label, e in (("finance", env_f), ("ops", env_o), ("shared", env_b)):
        check(f"{label} answer is not demo fallback",
              e.get("demo_fallback_used") in (False, None),
              f"demo_fallback_used={e.get('demo_fallback_used')} ask_mode={e.get('ask_mode')}")

    print("\n== DEMO-PATH follow-up (Cortex#19) ==")
    scalar_sessions: list[tuple[str, dict]] = []
    for sid, st, e in (("ses_fin", st_f, env_f), ("ses_fin2", st_a, env_a)):
        if st == 200 and e.get("abstained") is False and len(e.get("values") or []) == 1:
            if has_numeric_value(e):
                scalar_sessions.append((sid, e))

    st_m, env_m = ask(c, WHERE_SKU, space_id=FINANCE, session_id="ses_follow")
    multi_ok = st_m == 200 and env_m.get("abstained") is False and has_numeric_value(env_m)
    check(
        "multi-row Finance question answers (ses_follow)",
        multi_ok,
        f"status={st_m} badge={env_m.get('badge')} abstained={env_m.get('abstained')} "
        f"values={len(env_m.get('values') or [])}",
    )

    if multi_ok:
        st_avg, env_avg = ask(
            c, "average of them", space_id=FINANCE, session_id="ses_follow",
        )
        check(
            "follow-up 'average of them' compute-or-abstain",
            envelope_compute_or_abstain(st_avg, env_avg),
            f"status={st_avg} badge={env_avg.get('badge')} abstained={env_avg.get('abstained')} "
            f"values={env_avg.get('values')}",
        )
        if env_avg.get("abstained") is False:
            check(
                "follow-up not a green badge lie",
                env_avg.get("badge") != "ABSTAIN",
                f"badge={env_avg.get('badge')}",
            )
    else:
        check("follow-up 'average of them' skipped", True, "prior multi-row did not answer")

    if scalar_sessions:
        sid, prior = scalar_sessions[0]
        st_add, env_add = ask(c, "add 2000", space_id=FINANCE, session_id=sid)
        check(
            f"scalar follow-up 'add 2000' on {sid}",
            envelope_compute_or_abstain(st_add, env_add),
            f"status={st_add} badge={env_add.get('badge')} abstained={env_add.get('abstained')} "
            f"values={env_add.get('values')}",
        )
    else:
        print("  [SKIP] scalar 'add 2000' follow-up (no scalar prior session)")

failed = [r for r in results if not r[1]]
print("\n" + "=" * 60)
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("\nFAILED:")
    for n, _, d in failed:
        print(f"  - {n}: {d}")
sys.exit(1 if failed else 0)
