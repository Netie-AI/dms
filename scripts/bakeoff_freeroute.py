"""SCALE-FREE-AI-01 — OpenVault FreeRoute consumption harness.

Resolves free+normal providers through OpenVault API only.
Does not POST chat completions, scrape a local vault directory, or print tokens.
Exit 0 on --self-check. Live OV leftover is Platform.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dms_core.freeroute import (
    FREEROUTE_PREFERENCE,
    WRONG_DISCIPLINE,
    plan_free_providers,
    records_from_payloads,
    render_harness_md,
)

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "FREEROUTE_PROVIDERS.md"

_FIXTURE: dict[str, Any] = {
    "status": {
        "ok": True,
        "hops": [
            {"label": "Groq", "provider": "groq", "role": "free", "tier": "freemium"},
            {"label": "Groq", "provider": "groq", "role": "free", "tier": "freemium"},
        ],
        "spendable": [
            {
                "id": "groq",
                "name": "Groq",
                "tier": "freemium",
                "default_role": "free",
                "openai_compatible": True,
                "chat_models": ["llama-3.3-70b-versatile"],
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "tier": "paid",
                "default_role": "primary",
                "openai_compatible": True,
                "chat_models": ["gpt-4o-mini"],
            },
        ],
    },
    "onboard": {
        "providers": [
            {"id": "groq", "label": "Groq", "tier": "freemium", "role": "free"},
            {"id": "google", "label": "Google AI Studio", "tier": "freemium", "role": "free"},
        ]
    },
    "register": {"providers": []},
    "keys": [
        {"label": "Groq", "provider": "groq", "role": "free"},
        {"label": "site-password", "provider": "custom", "role": "primary", "tier": "paid"},
    ],
}


def _ov() -> str:
    return os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000").rstrip("/")


def self_check() -> int:
    records = records_from_payloads(
        status=_FIXTURE["status"],
        onboard=_FIXTURE["onboard"],
        register=_FIXTURE["register"],
        keys=_FIXTURE["keys"],
    )
    plan = plan_free_providers(records)
    labels = [row["label"] for row in plan["attempted"]]
    reasons = {row["reason"] for row in plan["skipped"]}
    assert plan["preference"] == FREEROUTE_PREFERENCE
    assert plan["wrong_discipline"] == WRONG_DISCIPLINE
    assert plan["chat_tokens"] is False
    assert plan["scrape_local_vault"] is False
    assert plan["second_vault"] is False
    assert "Groq" in labels
    assert "Google AI Studio" in labels
    assert "dup_label" in reasons
    assert "paid_not_free_normal" in reasons
    assert plan["dup_labels_skipped"] >= 1
    md = render_harness_md(plan)
    assert "dup_label" in md
    assert "LIVE_KEY" not in md
    print("PASS: SCALE-FREE-AI-01 self-check (dup labels skipped, paid skipped, no tokens)")
    return 0


def live(base: str) -> int:
    from dms_api.freeroute_client import consume_freeroute_plan

    print(f"OPENVAULT_URL={base}")
    plan = consume_freeroute_plan(base)
    md = render_harness_md(plan)
    print(md)
    out_json = REPO / ".tmp" / "freeroute_providers.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Wrote {out_json}")
    if not plan.get("openvault_ok"):
        print("FAIL OpenVault catalog unreachable (honest empty plan, no invent)")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    args = p.parse_args(argv)
    if args.self_check:
        return self_check()
    return live(_ov())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
