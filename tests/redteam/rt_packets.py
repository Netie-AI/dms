"""Judge packets: what a blind judge may see, and nothing else.

Included: opaque packet id, question, badge, abstained, route, text, rows, values,
gold_rows, gold_sql (labelled "reference answer, may itself be wrong").

Excluded on purpose: model_sql, trap, gold_notes, the served sql_used, family, expect,
control flag, the case id (its letter names the family), mechanical verdict, stub trace.
A judge that can see the attack design stops judging the answer.
"""

from __future__ import annotations

import hashlib
from typing import Any

REFERENCE_LABEL = "reference answer, may itself be wrong"


def packet_id(run_id: str, case_id: str) -> str:
    return "P-" + hashlib.sha256(f"{run_id}|{case_id}".encode()).hexdigest()[:8]


def _served_text(status: int | None, env: Any) -> str:
    if isinstance(env, dict) and "text" in env:
        return str(env.get("text") or "")
    if status is not None and status != 200:
        detail = env.get("detail") if isinstance(env, dict) else None
        if isinstance(detail, dict):
            msg = detail.get("message") or detail.get("code") or ""
        else:
            msg = detail or ""
        return f"[HTTP {status}] {msg}".strip()
    return ""


def judge_packet(record: dict[str, Any]) -> dict[str, Any]:
    env = record.get("envelope")
    env_d = env if isinstance(env, dict) and record.get("http_status") == 200 else {}
    return {
        "id": packet_id(str(record.get("run_id")), str(record.get("case_id"))),
        "question": record.get("question"),
        "badge": env_d.get("badge"),
        "abstained": env_d.get("abstained"),
        "route": env_d.get("route"),
        "text": _served_text(record.get("http_status"), env),
        "rows": env_d.get("rows", []),
        "values": env_d.get("values", []),
        "gold_rows": record.get("gold_rows"),
        "gold_sql": record.get("gold_sql"),
        "gold_label": REFERENCE_LABEL,
    }


def build_packets(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(packets, key) where key maps packet id -> case id. Keep the key away from judges."""
    packets: list[dict[str, Any]] = []
    key: dict[str, str] = {}
    for rec in records:
        if rec.get("type", "case") != "case":
            continue
        pkt = judge_packet(rec)
        packets.append(pkt)
        key[pkt["id"]] = str(rec.get("case_id"))
    return packets, key
