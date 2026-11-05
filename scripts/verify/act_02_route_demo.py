"""ACT-02 proof: the governed action routes over HTTP, end to end, refused eight ways.

Run from a worktree ``$W`` with Postgres up::

    cd $W && PYTHONPATH="$W/apps/api;$W/packages/core;$W/packages/cortex_client;\
$W/packages/executor;$W/packages/ledger;$W/packages/ao_vendor" \
python scripts/verify/act_02_route_demo.py

The database is ``dms_act_demo`` on the server named by ``ACT02_DEMO_URL``, which
must be set: there is no default, as in ACT-01, because the usual 127.0.0.1:5432
server is shared. It is created and migrated when missing and dropped at the end
unless ``--keep`` is passed. A
database not named ``dms_act_demo*`` is refused, and so is ``dms``. See
``act_01_writeback_demo.py`` for why the database is dropped (cluster-wide roles).

``DMS_ACTIONS=1`` and a seeded, non-default steward actor. The app is
``TestClient(create_app())``. The Cortex F5 gate is an allow-recorder and the
Cortex ledger is an in-process, hash-linked fake: **live Cortex is not
exercised**, and the script says so. The warehouse is a fresh demo DuckDB file
in a temp directory. Exits non-zero on any mismatch; prints
``ACT-02 PROOF OK @<sha>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import act_01_writeback_demo as act01  # noqa: E402

A = "/v1/amend/actions"
KEY = {"sku": "SKU-BETA", "location_id": "WH-B"}
QUESTION = "Which SKUs are below reorder level?"

_failures: list[str] = []


def check(label: str, got: Any, want: Any) -> None:
    ok = got == want
    print(f"  [{'ok' if ok else 'MISMATCH'}] {label}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        _failures.append(label)


def show(title: str, obj: Any) -> None:
    print(f"--- {title}")
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


class ChainedFakeLedger:
    """Each entry's hash covers the previous hash and the payload; verify recomputes it."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.fail_next = 0

    def _link(self, prev: str, event_type: str, payload: dict[str, Any], actor: str | None) -> str:
        text = json.dumps(
            {"prev": prev, "event_type": event_type, "payload": payload, "actor": actor},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(text.encode()).hexdigest()

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("fake ledger: append refused")
        prev = self.entries[-1]["hash"] if self.entries else "0" * 64
        digest = self._link(prev, req.event_type, req.payload, req.actor)
        n = len(self.entries) + 1
        self.entries.append(
            {
                "id": f"led_demo_{n}",
                "seq": n,
                "prev": prev,
                "hash": digest,
                "event_type": req.event_type,
                "payload": req.payload,
                "actor": req.actor,
            }
        )
        return LedgerAppendResponse(entry_id=f"led_demo_{n}", hash=digest, seq=n)

    def verify_ledger(self) -> Any:
        from cortex_client.models import LedgerVerifyResponse

        prev = "0" * 64
        for e in self.entries:
            if (
                e["prev"] != prev
                or self._link(prev, e["event_type"], e["payload"], e["actor"]) != e["hash"]
            ):
                return LedgerVerifyResponse(ok=False, first_break=str(e["seq"]))
            prev = e["hash"]
        return LedgerVerifyResponse(ok=True, first_break=None, checked=len(self.entries))


def main() -> int:
    url = os.environ.get("ACT02_DEMO_URL", "").strip()
    if not url:
        print("REFUSED: set ACT02_DEMO_URL to a scratch server's dms_act_demo database.")
        return 2
    keep = "--keep" in sys.argv[1:]
    name = act01._db_name(url)
    if name == "dms" or not name.startswith("dms_act_demo"):
        print(f"REFUSED: database {name!r} is not a dms_act_demo* demo database.")
        return 2
    try:
        return _run(url)
    finally:
        if not keep:
            act01.drop_database(url)


def _seed(url: str) -> dict[str, Any]:
    import psycopg

    tid, uid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    with psycopg.connect(url) as c:
        c.execute(
            "INSERT INTO dms.tenants (id, slug, name) VALUES (%s, %s, 'Act Demo')",
            (tid, f"act-demo-{suffix}"),
        )
        c.execute(
            "INSERT INTO dms.users (id, email, display_name) VALUES (%s, %s, 'Steward')",
            (uid, f"steward-{suffix}@example.com"),
        )
        role = c.execute("SELECT id FROM dms.roles WHERE name = 'steward'").fetchone()
        assert role is not None
        c.execute(
            "INSERT INTO dms.memberships (tenant_id, user_id, role_id) VALUES (%s, %s, %s)",
            (tid, uid, role[0]),
        )
        c.execute(
            "INSERT INTO dms.spaces (id, tenant_id, name, created_by) VALUES (%s, %s, %s, %s)",
            (sid, tid, f"ops-{suffix}", uid),
        )
        for entry, badge, rows in (
            ("ask_demo_ok", "validated", 2),
            ("ask_demo_abstain", "abstain", 0),
        ):
            c.execute(
                """
                INSERT INTO dms.ask_audit (
                  ask_id, tenant_id, asked_at, actor, actor_kind, space_id, question,
                  executed_sql, tables_read, badge, row_count, cortex_entry_id
                ) VALUES (%s, %s, now(), %s, 'deployment', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    tid,
                    str(uid),
                    str(sid),
                    QUESTION,
                    "SELECT sku, location_id FROM inventory WHERE quantity_kg < reorder_level_kg"
                    if badge == "validated"
                    else "",
                    ["inventory"],
                    badge,
                    rows,
                    entry,
                ),
            )
        c.commit()
    return {"tenant": tid, "user": uid, "space": sid}


def _run(url: str) -> int:
    sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    import dms_api
    import dms_core

    for mod in (dms_api, dms_core):
        f = Path(mod.__file__).resolve()
        print(f"{mod.__name__:<10} {f}")
        if ROOT not in f.parents:
            print(f"MISMATCH: {mod.__name__} is not under {ROOT}; set PYTHONPATH to this worktree.")
            return 3
    print(f"git HEAD   {sha}")

    act01.ensure_database(url)
    head = act01.upgrade_head(url)
    print(f"database   {act01._db_name(url)} @ alembic head {head}")
    seeded = _seed(url)
    space = str(seeded["space"])

    os.environ.update(
        {
            "DMS_ACTIONS": "1",
            "DATABASE_URL": url,
            "DMS_TENANT_ID": str(seeded["tenant"]),
            "DMS_ACTOR_USER_ID": str(seeded["user"]),
            "DMS_ACTOR_ROLE": "steward",
            "DMS_WAREHOUSE_DB": str(Path(tempfile.mkdtemp(prefix="act02-wh-")) / "demo.duckdb"),
        }
    )
    os.environ.pop("DMS_ONTO_PG", None)
    from cortex_client.gate import ComplianceDecision
    from dms_api.settings import get_settings

    get_settings.cache_clear()

    gate_log: list[dict[str, Any]] = []
    refuse: dict[str, str] = {}

    def recorder(
        *,
        action: str,
        actor: Any = None,
        resource: Any = None,
        metadata: Any = None,
        client: Any = None,
    ) -> ComplianceDecision:
        gate_log.append({"task_id": action, "resource": resource})
        reason = refuse.get(action)
        return ComplianceDecision(allowed=reason is None, reason=reason or "ok", action=action)

    import dms_api.routes.actions as actions_routes
    import dms_api.routes.amend as amend_routes

    actions_routes.compliance_gate = recorder  # type: ignore[assignment]
    amend_routes.compliance_gate = recorder  # type: ignore[assignment]
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    ledger = ChainedFakeLedger()
    app.state.cortex = ledger
    client = TestClient(app)
    print("ledger is an in-process fake; live Cortex not exercised")
    print("gate is an allow-recorder; Cortex F5 not exercised")

    def propose(value: str, **over: Any) -> Any:
        body = {
            "space_id": space,
            "action_type": "inventory.set_reorder_level",
            "object_key": dict(KEY),
            "value": value,
            **over,
        }
        return client.post(f"{A}/proposals", json=body)

    def apply(p: dict[str, Any], ref: str) -> Any:
        return client.post(
            f"{A}/proposals/{p['proposal_id']}/apply",
            json={"idempotency_token": p["idempotency_token"], "approval_ref": ref},
        )

    def objects() -> Any:
        return client.get(
            f"{A}/objects", params={"object_type": "inventory_item", "space_id": space}
        ).json()

    def code(r: Any) -> Any:
        d = r.json().get("detail")
        return d.get("code") if isinstance(d, dict) else d

    print("1. rule 12: 'BETA' is not the source encoding")
    r = propose("450", object_key={"sku": "BETA", "location_id": "WH-B"})
    show("422 object_key_not_found", {"status": r.status_code, **r.json()})
    check("status", r.status_code, 422)
    check("code", code(r), "object_key_not_found")
    check("candidate SKU-BETA@WH-B", KEY in r.json()["detail"]["candidates"], True)

    print("2. propose citing a validated governed answer")
    r = propose("450", evidence_audit_id="ask_demo_ok", reason="lead time doubled")
    a = r.json()
    show("proposal A", a)
    check("status", r.status_code, 200)
    check(
        "evidence",
        a["diff"]["evidence"],
        {"audit_id": "ask_demo_ok", "question": QUESTION, "badge": "validated", "row_count": 2},
    )
    check(
        "summary",
        a["diff"]["summary"],
        "Set reorder_level_kg for inventory_item SKU-BETA @ WH-B: 400 (source) -> 450",
    )
    check("mutation.executed", a["mutation"]["executed"], False)
    b = propose("500").json()  # made from the same before-state, applied later

    print("3. apply A with approval_ref DEMO-2")
    r = apply(a, "DEMO-2")
    receipt = r.json()
    show("receipt", receipt)
    check("status", r.status_code, 200)
    check("after.value", receipt.get("after", {}).get("value"), "450")
    check("verified", receipt.get("verified"), True)
    check("rows_changed", receipt.get("rows_changed"), 1)
    check("source_system_changed", receipt.get("mutation", {}).get("source_system_changed"), False)
    check(
        "separation_of_duties.proven", receipt.get("separation_of_duties", {}).get("proven"), False
    )
    check("chain.ok", receipt.get("chain", {}).get("ok"), True)

    print("4. GET /objects")
    rows = objects()
    show("objects", rows)
    check(
        "objects",
        [(o["object_key"], o["value"], o["row_version"]) for o in rows],
        [(KEY, "450", 1)],
    )

    print("5. recorded ledger payload")
    show("ledger entry 1", ledger.entries[0])
    check("event", ledger.entries[0]["event_type"], "action.apply")
    check(
        "payload hashes",
        (ledger.entries[0]["payload"]["before_hash"], ledger.entries[0]["payload"]["after_hash"]),
        (receipt.get("before_hash"), receipt.get("after_hash")),
    )
    check(
        "payload evidence_audit_id",
        ledger.entries[0]["payload"]["evidence_audit_id"],
        "ask_demo_ok",
    )

    print("6. refusals")
    r = apply(a, "DEMO-2")
    print(f"  replay              {r.status_code} {r.json()['detail']}")
    check("replay", (r.status_code, code(r)), (409, "conflict"))
    r = apply(b, "DEMO-3")
    print(f"  stale               {r.status_code} {r.json()['detail']}")
    check(
        "stale",
        (r.status_code, code(r), r.json()["detail"].get("current_value")),
        (409, "object_changed", "450"),
    )
    r = propose("550", evidence_audit_id="ask_demo_abstain")
    print(f"  abstain evidence    {r.status_code} {r.json()['detail']}")
    check("abstain evidence", (r.status_code, code(r)), (422, "evidence_not_validated"))
    r = client.post(
        f"/v1/amend/proposals/{b['proposal_id']}/confirm",
        json={"idempotency_token": b["idempotency_token"]},
    )
    print(f"  amend-confirm bypass {r.status_code} {r.json()['detail']}")
    check("amend bypass", (r.status_code, r.json()["detail"]), (409, "action_requires_apply"))

    print("7. revert A, then apply the revert")
    r = client.post(
        f"{A}/decisions/{receipt['decision_id']}/revert", json={"reason": "demo revert"}
    )
    rev = r.json()
    show("revert proposal", rev)
    check(
        "revert op", (r.status_code, rev["diff"]["op"], rev["diff"]["value"]), (200, "clear", None)
    )
    refuse["action.apply"] = "denied"
    r = apply(rev, "DEMO-4")
    print(f"  gate refusal        {r.status_code} {r.json()['detail']}")
    check("gate", (r.status_code, r.json()["detail"]), (403, "denied"))
    refuse.clear()
    ledger.fail_next = 1
    r = apply(rev, "DEMO-4")
    print(f"  ledger raise        {r.status_code} {r.json()['detail']}")
    check("ledger raise", (r.status_code, code(r)), (503, "ledger_append_failed"))
    r = apply(rev, "DEMO-4")
    print(f"  retry               {r.status_code} after={r.json().get('after')}")
    check(
        "retry",
        (
            r.status_code,
            r.json().get("after", {}).get("value"),
            r.json().get("after", {}).get("row_version"),
        ),
        (200, None, 2),
    )
    rows = objects()
    show("objects after revert", rows)
    check("objects after revert", [(o["value"], o["row_version"]) for o in rows], [(None, 2)])
    decisions = client.get(f"{A}/decisions", params={"space_id": space}).json()
    check("decisions", sorted(d["decision"] for d in decisions), ["applied", "applied"])

    print("8. gate call log")
    for call in gate_log:
        print(f"  {call['task_id']:<16} {call['resource']}")
    chain = ledger.verify_ledger()
    check("fake chain verifies", chain.ok, True)
    check(
        "ledger events", [e["event_type"] for e in ledger.entries], ["action.apply", "action.apply"]
    )

    if _failures:
        print(f"ACT-02 PROOF FAILED @{sha}: {', '.join(_failures)}")
        return 1
    print(f"ACT-02 PROOF OK @{sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
