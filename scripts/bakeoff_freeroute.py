"""OpenVault FreeRoute / key bake-off — precheck + optional chat probe.

Does not store secrets. Uses OpenVault :5000 as SoT.
Exit 0 always when OV is reachable (reports empty vault honestly).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

PROMPT = "In one sentence: what is 2+2? Reply with only the number."


def _ov() -> str:
    return os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000").rstrip("/")


def precheck_all(client: httpx.Client, base: str) -> dict[str, Any]:
    r = client.post(f"{base}/api/keys/precheck-all")
    if r.status_code == 404:
        return {"ok": False, "error": "precheck-all not found", "status_code": 404}
    if r.status_code >= 400:
        return {"ok": False, "error": r.text[:400], "status_code": r.status_code}
    return {"ok": True, "body": r.json()}


def list_keys(client: httpx.Client, base: str) -> list[dict[str, Any]]:
    r = client.get(f"{base}/api/keys")
    if r.status_code >= 400:
        return []
    body = r.json()
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ("keys", "items", "data"):
            if isinstance(body.get(k), list):
                return body[k]
    return []


def freeroute_chat(client: httpx.Client, base: str) -> dict[str, Any]:
    """One FreeRoute completion — uses vault fallback chain (not parallel providers)."""
    started = time.perf_counter()
    try:
        r = client.post(
            f"{base}/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": 32,
            },
            timeout=45.0,
        )
        ms = (time.perf_counter() - started) * 1000
        text = ""
        try:
            data = r.json()
            choices = data.get("choices") or []
            if choices:
                text = (choices[0].get("message") or {}).get("content") or ""
        except Exception:  # noqa: BLE001
            text = r.text[:200]
        return {
            "status_code": r.status_code,
            "latency_ms": round(ms, 1),
            "answer_preview": (text or "")[:240],
            "error": None if r.status_code < 400 else r.text[:300],
            "note": "Sequential FreeRoute fallback — not a parallel OmniRoute Electron bake-off.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status_code": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "answer_preview": "",
            "error": str(exc)[:300],
        }


def main() -> int:
    base = _ov()
    out_dir = Path(__file__).resolve().parents[1] / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "BAKEOFF_FREEROUTE.md"
    print(f"OPENVAULT_URL={base}")

    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=20.0) as client:
        try:
            hz = client.get(f"{base}/api/healthz")
            if hz.status_code >= 400:
                print(f"FAIL OpenVault health {hz.status_code}")
                md_path.write_text(
                    f"# FreeRoute bake-off\n\nOpenVault unreachable at {base}\n",
                    encoding="utf-8",
                )
                return 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL OpenVault: {exc}")
            md_path.write_text(
                f"# FreeRoute bake-off\n\nOpenVault unreachable: {exc}\n",
                encoding="utf-8",
            )
            return 1

        pre = precheck_all(client, base)
        keys = list_keys(client, base)
        chat = freeroute_chat(client, base)

    # Normalize precheck rows if present
    pre_body = pre.get("body")
    if isinstance(pre_body, list):
        rows = pre_body
    elif isinstance(pre_body, dict):
        for k in ("results", "keys", "items"):
            if isinstance(pre_body.get(k), list):
                rows = pre_body[k]
                break

    lines = [
        "# FreeRoute bake-off",
        "",
        f"**When:** {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"**OpenVault:** `{base}`",
        "",
        "## Notes",
        "",
        "- Secrets stay in OpenVault (`keys.db`). DMS never stores API keys.",
        "- FreeRoute `POST /v1/chat/completions` uses role-ordered fallback (primary→backup→cheap→free).",
        "- Anthropic may be skipped by the `/v1` proxy today — record honestly.",
        "- Parallel OmniRoute Electron bake-off is **not** shipped; use precheck latency + one FreeRoute call.",
        "- Add keys via UI `http://127.0.0.1:3010/vault` or `POST /api/vault/ingest-env`.",
        "",
        "## Precheck-all",
        "",
        f"- ok: `{pre.get('ok')}`",
        f"- vault key records listed: **{len(keys)}**",
        f"- precheck result rows: **{len(rows)}**",
        "",
    ]
    if rows:
        lines += [
            "| provider / label | status | latency_ms | error |",
            "|---|---|---|---|",
        ]
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = row.get("label") or row.get("provider") or row.get("id") or "?"
            status = row.get("status") or row.get("precheck_status") or row.get("ok")
            lat = row.get("latency_ms") or row.get("latency") or ""
            err = (row.get("error") or row.get("detail") or "")[:80]
            lines.append(f"| {label} | {status} | {lat} | {err} |")
        lines.append("")
    else:
        lines += [
            "_No precheck rows — vault may be empty. Seed keys then re-run._",
            "",
            "```powershell",
            'curl -X POST http://127.0.0.1:5000/api/vault/ingest-env -H "Content-Type: application/json" -d \'{"dry_run": false}\'',
            "curl -X POST http://127.0.0.1:5000/api/keys/precheck-all",
            "```",
            "",
        ]

    lines += [
        "## FreeRoute chat probe",
        "",
        f"- prompt: `{PROMPT}`",
        f"- status: `{chat.get('status_code')}`",
        f"- latency_ms: `{chat.get('latency_ms')}`",
        f"- preview: `{chat.get('answer_preview')}`",
        f"- error: `{chat.get('error')}`",
        f"- note: {chat.get('note')}",
        "",
        "## Escalation",
        "",
        "If free tiers suck, add Claude / OpenAI / DeepSeek into OpenVault (not into DMS `.env`),",
        "then re-run `python scripts/bakeoff_freeroute.py`. Warehouse answers still route via Cortex.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"precheck_ok": pre.get("ok"), "keys": len(keys), "chat": chat}, indent=2)[:1200])
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
