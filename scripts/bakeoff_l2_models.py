"""Bake FreeRoute models on one L2 freeform ask; promote winner.

Requires Cortex process with DMS_L2_ENABLED=1 (or runs in-process answer()).
Writes docs/L2_MODEL_BAKEOFF.md under DMS.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "L2_MODEL_BAKEOFF.md"
Q = "rank carriers by on-time percentage for hazmat only"
CONTROL = "Top 5 selling SKUs by revenue"

# Free / freemium ids that FreeRoute may accept (probe first).
CANDIDATES = [
    "auto",
    "mistral-small-latest",
    "ministral-8b-latest",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "google/gemini-2.5-flash",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
]


def ov() -> str:
    return os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000").rstrip("/")


def probe_model(client: httpx.Client, model: str) -> dict:
    t0 = time.perf_counter()
    r = client.post(
        f"{ov()}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with only: SELECT 1"}],
            "max_tokens": 32,
        },
        timeout=45.0,
    )
    ms = (time.perf_counter() - t0) * 1000
    ok = r.status_code == 200
    upstream = None
    if ok:
        try:
            upstream = (r.json() or {}).get("model")
        except Exception:  # noqa: BLE001
            upstream = None
    return {
        "model": model,
        "http": r.status_code,
        "ok": ok,
        "ms": round(ms, 1),
        "upstream": upstream,
        "err": None if ok else r.text[:160],
    }


def l2_inprocess(model: str) -> dict:
    os.environ["PACK"] = "dms"
    os.environ["DMS_L2_ENABLED"] = "1"
    os.environ["DMS_L2_MODEL"] = model
    os.environ.setdefault("OPENVAULT_URL", "http://127.0.0.1:5000")
    # Fresh import path — answer_engine reads env each call for model via sql_generator
    import packs.dms  # noqa: F401
    from CortexOS.dms.answer_engine import answer

    t0 = time.perf_counter()
    r = answer(Q)
    ms = (time.perf_counter() - t0) * 1000
    return {
        "model": model,
        "badge": r.get("badge"),
        "layer": r.get("layer"),
        "abstain": r.get("badge") == "abstain" or "can't answer" in str(r.get("answer") or "").lower(),
        "ms": round(ms, 1),
        "sql": (r.get("sql_used") or "")[:120],
        "answer": (r.get("answer") or "")[:160],
        "assumptions": (r.get("assumptions") or "")[:160],
    }


def main() -> int:
    cortex_root = Path(os.environ.get("CORTEX_ROOT", r"D:\Cortex"))
    if str(cortex_root) not in sys.path:
        sys.path.insert(0, str(cortex_root))

    rows_probe: list[dict] = []
    live: list[str] = []
    with httpx.Client() as client:
        for m in CANDIDATES:
            p = probe_model(client, m)
            rows_probe.append(p)
            print(f"PROBE {m}: http={p['http']} upstream={p.get('upstream')} ms={p['ms']}")
            if p["ok"]:
                live.append(m)

    if not live:
        print("No FreeRoute models answered SELECT 1 — abort")
        return 1

    # Prefer non-auto named models for ranking, but always include auto.
    rank_models = live[:]
    results: list[dict] = []
    for m in rank_models:
        try:
            r = l2_inprocess(m)
        except Exception as exc:  # noqa: BLE001
            r = {"model": m, "badge": "ERROR", "abstain": True, "ms": 0, "err": str(exc)[:200]}
        results.append(r)
        print(f"L2 {m}: badge={r.get('badge')} abstain={r.get('abstain')} ms={r.get('ms')}")

    winners = [r for r in results if r.get("badge") == "L2_VALIDATED" and not r.get("abstain")]
    # Prefer fastest validated named model; else auto
    winners.sort(key=lambda x: (0 if x["model"] != "auto" else 1, x.get("ms") or 9e9))
    winner = winners[0]["model"] if winners else (live[0] if live else "auto")

    lines = [
        "# L2 FreeRoute model bakeoff",
        "",
        f"Question: `{Q}`",
        f"Winner (promoted): **`{winner}`**",
        "",
        "## FreeRoute probe (SELECT 1)",
        "",
        "| model | http | upstream | ms |",
        "|---|---:|---|---:|",
    ]
    for p in rows_probe:
        lines.append(
            f"| `{p['model']}` | {p['http']} | {p.get('upstream') or '-'} | {p['ms']} |"
        )
    lines += [
        "",
        "## L2 hazmat ask",
        "",
        "| model | badge | abstain | ms | sql |",
        "|---|---|---|---:|---|",
    ]
    for r in results:
        lines.append(
            f"| `{r.get('model')}` | {r.get('badge')} | {r.get('abstain')} | {r.get('ms')} | `{(r.get('sql') or '')[:60]}` |"
        )
    lines += [
        "",
        "## Promote",
        "",
        "```powershell",
        f"D:\\DMS\\scripts\\windows\\Start-DMSStack.ps1 -StartSiblings -EnableL2 -L2Model {winner}",
        "# or: $env:DMS_L2_MODEL='" + winner + "'",
        "```",
        "",
        f"Generator default remains `auto` if unset; launcher `-L2Model` pins **{winner}**.",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(json.dumps({"winner": winner, "validated": [w["model"] for w in winners]}, indent=2))
    # write machine json next to md
    (REPO / "docs" / "L2_MODEL_BAKEOFF.json").write_text(
        json.dumps(
            {"winner": winner, "probe": rows_probe, "l2": results, "question": Q},
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if winners else 2


if __name__ == "__main__":
    raise SystemExit(main())
