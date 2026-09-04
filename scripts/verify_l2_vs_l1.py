"""Compare L0/L1 vs L2 freeform on a live DMS stack; rotate FreeRoute models on L2 fail.

Requires: OpenVault :5000, Cortex :8010 with DMS_L2_ENABLED=1, DMS API :8090.
Exit 0 if L0/L1 stay certified/governed AND L2 freeform is L2_VALIDATED (or all models abstain honestly).
Exit 1 if L0/L1 regress or L2 invents / wrong badge / crashes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

# Corrected warehouse ranks (dms#39). Naive JOIN inventory inflates ~14.8x.
_VQ01_ORACLE = [
    ("ELECTRONICS", 8_953_922.60),
    ("CHEMICALS", 8_799_446.70),
    ("FOOD_COLD", 8_754_427.11),
]

# L0/L1 control — must NOT become L2 when templates match.
L0_L1_CASES = [
    {
        "id": "top5_revenue",
        "question": "Top 5 selling SKUs by revenue",
        "expect_badges": {"L0_CERTIFIED", "L1_GOVERNED_METRIC"},
        "must_not_abstain": True,
    },
    {
        "id": "categoty_top3",
        "question": "show top 3 categoty sales",
        "expect_badges": {"L0_CERTIFIED", "L1_GOVERNED_METRIC"},
        "must_not_abstain": True,
        "expect_ranks": _VQ01_ORACLE,
    },
]

# Outside certified templates — L2 or honest abstain.
L2_CASES = [
    {
        "id": "hazmat_carriers",
        "question": "rank carriers by on-time percentage for hazmat only",
        "expect_badges": {"L2_VALIDATED"},
        "allow_abstain": True,
    },
    {
        "id": "top11_sales",
        "question": "top 11 selling SKUs by revenue",
        "expect_badges": {"L2_VALIDATED", "L0_CERTIFIED", "L1_GOVERNED_METRIC"},
        "allow_abstain": True,
    },
]

# Rotate when L2 fails gate / wrong path (OpenVault FreeRoute ids).
DEFAULT_MODELS = [
    "auto",
    "mistral-small-latest",
    "llama-3.3-70b-versatile",
    "deepseek/deepseek-chat",
    "qwen/qwen-2.5-72b-instruct",
]


def _dms() -> str:
    return os.environ.get("DMS_URL", "http://127.0.0.1:8090").rstrip("/")


def _ov() -> str:
    return os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000").rstrip("/")


def ask(client: httpx.Client, question: str) -> dict[str, Any]:
    r = client.post(
        f"{_dms()}/v1/chat/ask",
        json={"question": question},
        timeout=120.0,
    )
    if r.status_code == 403:
        try:
            detail = r.json().get("detail") or r.json()
        except Exception:  # noqa: BLE001
            detail = r.text[:300]
        return {
            "badge": "ERROR",
            "abstained": True,
            "text": f"HTTP 403: {detail}",
            "sql_used": None,
            "_http_status": 403,
            "_detail": detail,
        }
    r.raise_for_status()
    return r.json()


def set_cortex_model_hint(model: str) -> None:
    """Document-only: model is on Cortex process env. Print rotate instruction."""
    print(f"  [rotate] set DMS_L2_MODEL={model} on Cortex and restart :8010 if still failing")


def summarize(env: dict[str, Any]) -> str:
    badge = env.get("badge")
    abstained = bool(env.get("abstained"))
    text = (env.get("text") or env.get("answer") or "")[:120]
    sql = (env.get("sql_used") or "")[:80]
    return f"badge={badge} abstain={abstained} sql={sql!r} text={text!r}"


def run_l0_l1(client: httpx.Client) -> list[str]:
    fails: list[str] = []
    for case in L0_L1_CASES:
        env = ask(client, case["question"])
        print(f"[L0/L1] {case['id']}: {summarize(env)}")
        badge = env.get("badge")
        if case["must_not_abstain"] and env.get("abstained"):
            fails.append(f"{case['id']}: unexpectedly abstained")
        if badge not in case["expect_badges"]:
            fails.append(
                f"{case['id']}: badge {badge} not in {sorted(case['expect_badges'])}"
            )
        if badge == "L2_VALIDATED":
            fails.append(f"{case['id']}: L0/L1 question wrongly answered as L2")
        ranks = case.get("expect_ranks")
        if ranks and not env.get("abstained"):
            rows = list(env.get("rows") or [])
            got = [
                (
                    str(r.get("category") or ""),
                    round(float(r.get("sales_value_myr") or r.get("value") or 0), 2),
                )
                for r in rows[: len(ranks)]
            ]
            want = [(c, round(float(v), 2)) for c, v in ranks]
            if got != want:
                fails.append(f"{case['id']}: ranks {got} != oracle {want}")
    return fails


def run_l2_with_rotate(
    client: httpx.Client, models: list[str]
) -> tuple[list[str], list[str]]:
    """Returns (fails, notes). Tries each L2 case; on fail prints model rotate hints."""
    fails: list[str] = []
    notes: list[str] = []
    for case in L2_CASES:
        env = ask(client, case["question"])
        print(f"[L2] {case['id']}: {summarize(env)}")
        badge = env.get("badge")
        abstained = bool(env.get("abstained"))
        if abstained and case.get("allow_abstain"):
            notes.append(f"{case['id']}: honest abstain (try model rotate)")
            for m in models[1:]:
                set_cortex_model_hint(m)
            continue
        if badge in case["expect_badges"] and not abstained:
            notes.append(f"{case['id']}: ok under {badge}")
            continue
        fails.append(f"{case['id']}: badge={badge} abstain={abstained}")
        for m in models:
            set_cortex_model_hint(m)
    return fails, notes


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated FreeRoute model ids for rotate hints",
    )
    p.add_argument("--json-out", default="", help="optional path for machine-readable report")
    args = p.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    report: dict[str, Any] = {"ok": False, "l0_l1_fails": [], "l2_fails": [], "notes": []}
    try:
        with httpx.Client() as client:
            # health
            for name, url in (
                ("dms", f"{_dms()}/health"),
                ("ov", f"{_ov()}/api/healthz"),
            ):
                try:
                    hr = client.get(url, timeout=5.0)
                    print(f"[health] {name}: {hr.status_code}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[health] {name}: DOWN ({exc})")
                    report["l0_l1_fails"].append(f"{name} down")
                    return 1

            report["l0_l1_fails"] = run_l0_l1(client)
            l2_fails, notes = run_l2_with_rotate(client, models)
            report["l2_fails"] = l2_fails
            report["notes"] = notes
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}", file=sys.stderr)
        report["fatal"] = str(exc)
        if args.json_out:
            from pathlib import Path

            Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1

    report["ok"] = not report["l0_l1_fails"] and not report["l2_fails"]
    print("---")
    print(json.dumps(report, indent=2))
    if args.json_out:
        from pathlib import Path

        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Soft: L2 abstain-only is exit 2 (stack ok, FreeRoute/gate not producing SQL yet)
    if report["l0_l1_fails"]:
        return 1
    if report["l2_fails"]:
        return 1
    if any("honest abstain" in n for n in notes):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
