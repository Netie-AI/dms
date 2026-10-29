"""ACT-01 proof: one governed action, applied, re-read, refused four ways.

Run from a worktree ``$W`` with Postgres up::

    cd $W && PYTHONPATH="$W/packages/core;$W/apps/api;$W/packages/cortex_client;\
$W/packages/ledger;$W/packages/executor" python scripts/verify/act_01_writeback_demo.py

The database is ``dms_act_demo`` on the server named by ``ACT01_DEMO_URL``
(default ``postgresql://dms:dms@127.0.0.1:5432/dms_act_demo``). A database named
``dms`` is refused: the demo never touches the live control plane. The script
creates the database when it is missing and runs ``alembic upgrade head``.

Every run seeds a fresh tenant, so reruns do not see each other. The Cortex
ledger entry ids are fake (``led_demo_*``): ACT-01 has no route and no ledger
append; ACT-02 does. Exits non-zero on any mismatch.

The demo database is dropped at the end unless ``--keep`` is passed. Migration
0001 grants the cluster-wide ``dms_viewer`` / ``dms_steward`` / ``dms_admin``
roles in every database it runs in, and ``tests/control_plane/conftest.py``
drops those roles before each session. A leftover demo database holding grants
makes that DROP ROLE fail, which errors every control-plane test on the same
server. Point ``ACT01_DEMO_URL`` at a scratch server when other sessions share
the default one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "postgresql://dms:dms@127.0.0.1:5432/dms_act_demo"

_failures: list[str] = []


def check(label: str, got: Any, want: Any) -> None:
    ok = got == want
    print(f"  [{'ok' if ok else 'MISMATCH'}] {label}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        _failures.append(label)


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def ensure_database(url: str) -> None:
    import psycopg
    from psycopg import sql

    name = _db_name(url)
    with psycopg.connect(_with_db(url, "postgres"), autocommit=True, connect_timeout=5) as c:
        exists = c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists is None:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"created database {name}")


def upgrade_head(url: str) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from alembic import command

    os.environ["DATABASE_URL"] = url
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    return ",".join(heads)


def drop_database(url: str) -> None:
    import psycopg
    from psycopg import sql

    name = _db_name(url)
    with psycopg.connect(_with_db(url, "postgres"), autocommit=True, connect_timeout=5) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    print(f"dropped database {name} (pass --keep to keep it)")


def main() -> int:
    url = os.environ.get("ACT01_DEMO_URL", DEFAULT_URL).strip()
    keep = "--keep" in sys.argv[1:]
    name = _db_name(url)
    if name == "dms":
        print("REFUSED: the demo does not run against the database named 'dms'.")
        return 2
    if not name.startswith("dms_act_demo"):
        # The demo creates and drops its database; it only does that to its own.
        print(f"REFUSED: database {name!r} is not a dms_act_demo* demo database.")
        return 2
    try:
        return _run(url)
    finally:
        if not keep:
            drop_database(url)


def _run(url: str) -> int:
    sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    import dms_core

    core_file = Path(dms_core.__file__).resolve()
    print(f"git HEAD      {sha}")
    print(f"dms_core      {core_file}")
    if ROOT not in core_file.parents:
        print(f"MISMATCH: dms_core is not under {ROOT}; set PYTHONPATH to this worktree.")
        return 3

    ensure_database(url)
    head = upgrade_head(url)
    print(f"database      {_db_name(url)} @ alembic head {head}")
    check("alembic head", head, "0009_action_writeback")

    os.environ["DMS_ACTIONS"] = "1"

    import psycopg
    from dms_core.control_plane.action_types import state_hash, validate_spec
    from dms_core.control_plane.actions import (
        PreconditionFailed,
        TokenDiffMismatch,
        apply_action_version,
        decline_action_version,
        propose_action,
        record_decision,
    )
    from dms_core.control_plane.proposals import ConflictError
    from dms_core.control_plane.session import set_tenant_context

    tid, uid = uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    key = {"sku": "SKU-BETA", "location_id": "WH-B"}

    def spec(value: str) -> Any:
        return validate_spec(
            {
                "action_type": "inventory.set_reorder_level",
                "object_key": key,
                "value": value,
                "reason": "supplier lead time doubled",
            }
        )

    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO dms.tenants (id, slug, name) VALUES (%s, %s, 'ACT-01 demo')",
            (tid, f"act01-{suffix}"),
        )
        conn.execute(
            "INSERT INTO dms.users (id, email, display_name) VALUES (%s, %s, 'Demo steward')",
            (uid, f"act01-{suffix}@example.com"),
        )
        role = conn.execute("SELECT id FROM dms.roles WHERE name = 'steward'").fetchone()
        assert role is not None
        set_tenant_context(conn, tid, role="admin")
        conn.execute(
            "INSERT INTO dms.memberships (tenant_id, user_id, role_id) VALUES (%s, %s, %s)",
            (tid, uid, role[0]),
        )
        conn.commit()
        print(f"tenant        {tid} (steward {uid})")

        def as_steward() -> None:
            set_tenant_context(conn, tid, role="steward")

        def propose(value: str) -> dict[str, Any]:
            as_steward()
            out = propose_action(
                conn,
                tenant_id=tid,
                space_id=None,
                actor_id=uid,
                spec=spec(value),
                source_value="400",
                evidence={"audit_id": "ask_demo_1"},
            )
            conn.commit()
            return out

        def apply(token: str) -> Any:
            as_steward()
            return apply_action_version(
                conn,
                tenant_id=tid,
                idempotency_token=token,
                actor_id=str(uid),
                approval_ref="DEMO-1",
            )

        print("\n1. propose SKU-BETA @ WH-B -> 450.00 (source 400)")
        prop_a = propose("450.00")
        prop_b = propose("500")  # made against the same absent row; goes stale
        print(f"  summary  {prop_a['diff']['summary']}")
        print(f"  token    {prop_a['idempotency_token'][:16]}...")
        check(
            "summary",
            prop_a["diff"]["summary"],
            "Set reorder_level_kg for inventory_item SKU-BETA @ WH-B: 400 (source) -> 450",
        )

        print("\n2. apply with approval_ref DEMO-1, re-read")
        result = apply(prop_a["idempotency_token"])
        as_steward()
        row = conn.execute(
            """
            SELECT object_type, object_key, field, value, row_version
              FROM dms.action_writeback WHERE tenant_id = %s
            """,
            (tid,),
        ).fetchone()
        assert row is not None
        print(f"  re-read  value={row[3]!r} row_version={row[4]} key={row[1]}")
        print(f"  hash     {result['before_hash'][:12]} -> {result['after_hash'][:12]}")
        print(f"  verified {result['verified']}")
        check("re-read (value, row_version)", (row[3], row[4]), ("450", 1))
        check(
            "after_hash == state_hash(re-read)",
            result["after_hash"],
            state_hash(
                {
                    "object_type": row[0],
                    "object_key": row[1],
                    "field": row[2],
                    "value": row[3],
                    "row_version": row[4],
                }
            ),
        )
        check("verified", result["verified"], True)

        print("\n3. record decision with fake ledger entry led_demo_1")
        rec = record_decision(
            conn, result, decision="applied", cortex_entry_id="led_demo_1", space_id=None
        )
        conn.commit()
        as_steward()
        joined = conn.execute(
            """
            SELECT d.decision, d.after->>'value', d.approval_ref, d.verified,
                   l.cortex_entry_id, l.seq
              FROM dms.action_decisions d
              JOIN dms.ledger_ref l ON l.seq = d.ledger_seq
             WHERE d.id = %s
            """,
            (rec["decision_id"],),
        ).fetchone()
        conn.rollback()
        print(f"  decision {joined}")
        check(
            "decision joined to ledger_ref",
            joined[:5] if joined else None,
            ("applied", "450", "DEMO-1", True, "led_demo_1"),
        )

        print("\n4. refusals")
        try:
            apply(prop_a["idempotency_token"])
            check("replay refused", "applied twice", "REFUSED conflict")
        except ConflictError:
            print("  replay         REFUSED conflict")
        conn.rollback()

        try:
            apply(prop_b["idempotency_token"])
            check("stale refused", "applied", "REFUSED object_changed")
        except PreconditionFailed as exc:
            print(
                f"  stale proposal REFUSED {exc.code} "
                f"(current {exc.current_value}, row_version {exc.current_row_version})"
            )
            check("stale current value", (exc.code, exc.current_value), ("object_changed", "450"))
        conn.rollback()

        prop_c = propose("475")
        as_steward()
        conn.execute(
            """
            UPDATE dms.proposal_versions
               SET diff = jsonb_set(diff, '{value}', '"9999"')
             WHERE idempotency_token = %s
            """,
            (prop_c["idempotency_token"],),
        )
        conn.commit()
        try:
            apply(prop_c["idempotency_token"])
            check("tamper refused", "applied", "REFUSED token_diff_mismatch")
        except TokenDiffMismatch as exc:
            print(f"  tampered diff  REFUSED {exc.code}")
        conn.rollback()

        prop_d = propose("480")
        as_steward()
        declined = decline_action_version(
            conn,
            tenant_id=tid,
            idempotency_token=prop_d["idempotency_token"],
            approval_ref="DEMO-2",
        )
        record_decision(
            conn, declined, decision="declined", cortex_entry_id="led_demo_2", space_id=None
        )
        conn.commit()
        as_steward()
        status = conn.execute(
            "SELECT status FROM dms.proposal_versions WHERE idempotency_token = %s",
            (prop_d["idempotency_token"],),
        ).fetchone()
        print(f"  decline        {status[0] if status else None}")
        check("decline status", status[0] if status else None, "rejected")

        final = conn.execute(
            "SELECT value, row_version FROM dms.action_writeback WHERE tenant_id = %s", (tid,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT decision FROM dms.action_decisions WHERE tenant_id = %s ORDER BY decided_at",
            (tid,),
        ).fetchall()
        conn.rollback()
        print(f"\n  final writeback {final}; decisions {[d[0] for d in decisions]}")
        check("final writeback", final, [("450", 1)])
        check("decisions", [d[0] for d in decisions], ["applied", "declined"])

    if _failures:
        print(f"\nACT-01 PROOF FAILED @{sha}: {', '.join(_failures)}")
        return 1
    print(f"\nACT-01 PROOF OK @{sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
