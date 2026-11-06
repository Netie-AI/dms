"""FDE-LAND-01: land one customer SQL warehouse on the governed path, with receipts.

A forward-deployed engineer runs this against a running DMS and the customer's
SQL Server, MySQL or PostgreSQL source. It walks the path on main and prints a
receipt per step. It stops at the first refusal and names it.

1. connect   ``POST /v1/studio/sources/sql`` into a Space (created unless given)
2. grants    no grant API on main: printed as ``N/A on main``, never invented
3. ontology  ``GET /v1/spaces/{id}/ontology``: objects and links, verified or not
4. diff      ``POST /v1/ontology/data/compile`` + ``GET /v1/ontology/data/diff``
5. approve   ``POST /v1/ontology/data/items/{id}/approve``, only with ``--approve``
6. ask       ``POST /v1/chat/ask`` per generated question. Every envelope must
             pass ``assert_envelope_valid`` (E1-E9), carry no demo fallback, not
             abstain, and its rows must equal an oracle query run directly on the
             source. The receipt carries the rendered text, the rows and the badge.

Questions come from the Space ontology the asks are served from: one row count
per verified object, and one grouped count over each verified many-to-one link
(at most ``--max-groups``). An unverified link gets no question; its violation
is on the ontology receipt.

The source password is read from ``FDE_SOURCE_PASSWORD``, never from argv, and
never printed. Exit 0 only when every step passed.

    FDE_SOURCE_PASSWORD=... python scripts/verify/fde_land.py \\
        --dms-url https://dms.customer.internal --kind postgresql \\
        --host pg.customer.internal --database sales --user reader
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]

Oracle = Callable[[str], list[dict[str, Any]]]


def _bootstrap() -> None:
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
        "scripts/verify",
    ):
        entry = str(ROOT / rel)
        if entry not in sys.path:
            sys.path.insert(0, entry)


@dataclass
class Receipt:
    """One step. ``failure`` is the named refusal; ``hint`` is what to do about it."""

    step: str
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None
    hint: str | None = None
    #: The step does not exist on main. Printed as N/A, never as a pass.
    na: bool = False


class _Stop(Exception):
    def __init__(self, receipt: Receipt) -> None:
        super().__init__(receipt.failure)
        self.receipt = receipt


def _refuse(step: str, failure: str, summary: str, hint: str, **detail: Any) -> _Stop:
    return _Stop(Receipt(step, False, summary, dict(detail), failure, hint))


def _detail(resp: Any) -> Any:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    return body.get("detail", body) if isinstance(body, dict) else body


def _code(detail: Any, default: str) -> str:
    if isinstance(detail, dict) and detail.get("code"):
        return str(detail["code"])
    if isinstance(detail, str) and detail and " " not in detail:
        return detail
    return default


#: What each HTTP refusal means for the FDE. Mirrored in the landing playbook.
HINTS: dict[str, str] = {
    "source_unreachable": "DMS could not reach or log in to the source: check host, port, "
    "firewall from the DMS host, the login, and that the driver is installed",
    "source_table_unknown": "a --table is not visible to this login: check the name and grants",
    "bad_request": "the connection fields were rejected: check kind, port and table names",
    "gate_unavailable": "the Cortex F5 gate did not answer: a mutation fails closed; "
    "check CORTEX_URL and the Cortex key on the DMS host",
    "steward_grant_required": "the DMS actor is not a steward: set DMS_ACTOR_ROLE=steward "
    "for the person approving",
    "ontology_store_unavailable": "the Space ontology store is down: check DATABASE_URL",
}


def _http_refusal(step: str, resp: Any, default: str) -> _Stop:
    detail = _detail(resp)
    code = _code(detail, default)
    hint = HINTS.get(code, f"HTTP {resp.status_code}: read the detail and the DMS log")
    return _refuse(
        step, code, f"HTTP {resp.status_code} {code}", hint, http=resp.status_code, response=detail
    )


# --- oracle -----------------------------------------------------------------------


def _quote(kind: str, part: str) -> str:
    _bootstrap()
    from dms_executor.db_connector import SourceConfig, _quote_ident

    cfg = SourceConfig(kind=kind, host="-", database="-", user="-")  # type: ignore[arg-type]
    return _quote_ident(cfg, part)


def _relation(kind: str, source_table: str) -> str:
    schema, _, table = source_table.partition(".")
    if not table:
        return _quote(kind, schema)
    return f"{_quote(kind, schema)}.{_quote(kind, table)}"


def source_oracle(cfg: Any) -> Oracle:
    """Run oracle SQL directly on the source, through the connector's own ``connect``."""
    _bootstrap()
    from dms_executor import db_connector as dbc

    def _run(sql: str) -> list[dict[str, Any]]:
        opened: AbstractContextManager[Any] = dbc.connect(cfg)
        with opened as con:
            cur = con.cursor()
            try:
                cur.execute(sql)
                cols = [str(d[0]) for d in (cur.description or [])]
                return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            finally:
                cur.close()

    return _run


def _norm(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        return str(int(number)) if number == number.to_integral_value() else str(number)
    return str(value).strip()


def _multiset(rows: list[dict[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(sorted(_norm(v) for v in row.values())) for row in rows)


# --- question plan ----------------------------------------------------------------


@dataclass(frozen=True)
class PlannedAsk:
    question: str
    oracle_sql: str
    basis: str


def plan_questions(view: dict[str, Any], *, kind: str, max_groups: int = 1) -> list[PlannedAsk]:
    """One count per verified object, one grouped count per verified many-to-one link."""
    out: list[PlannedAsk] = []
    by_name: dict[str, dict[str, Any]] = {}
    for onto in view.get("ontologies") or []:
        for obj in onto.get("objects") or []:
            if obj.get("status") != "verified":
                continue
            by_name[str(obj["name"])] = obj
            out.append(
                PlannedAsk(
                    question=f"How many rows does {obj['name']} have?",
                    oracle_sql="SELECT COUNT(*) AS n FROM "
                    + _relation(kind, str(obj["source_table"])),
                    basis=f"object {obj['name']}",
                )
            )
    groups = 0
    for onto in view.get("ontologies") or []:
        for link in onto.get("links") or []:
            if groups >= max_groups:
                break
            if link.get("status") != "verified" or link.get("cardinality") != "many_to_one":
                continue
            child, parent = by_name.get(str(link["from"])), by_name.get(str(link["to"]))
            if child is None or parent is None:
                continue
            pairs = list(zip(link["from_columns"], link["to_columns"], strict=True))
            on = " AND ".join(f"c.{_quote(kind, f)} = p.{_quote(kind, t)}" for f, t in pairs)
            keys = ", ".join(f"p.{_quote(kind, t)}" for _, t in pairs)
            out.append(
                PlannedAsk(
                    question=f"How many {child['name']} rows are there per "
                    f"{parent['name']} {', '.join(link['to_columns'])}?",
                    oracle_sql=f"SELECT {keys}, COUNT(*) AS n FROM "
                    f"{_relation(kind, str(child['source_table']))} c JOIN "
                    f"{_relation(kind, str(parent['source_table']))} p ON {on} "
                    f"GROUP BY {keys}",
                    basis=f"link {link['name']}",
                )
            )
            groups += 1
    return out


# --- the walk ---------------------------------------------------------------------


def _connect(
    client: Any,
    cfg: Any,
    *,
    space_id: str | None,
    space_name: str | None,
    tables: list[str] | None,
    max_rows: int | None,
) -> tuple[Receipt, str]:
    created = None
    if not space_id:
        name = space_name or f"FDE landing {cfg.database}"
        r = client.post("/v1/spaces", json={"name": name})
        if r.status_code != 201:
            raise _http_refusal("connect", r, "space_create_failed")
        created = r.json()
        space_id = str(created["space"]["id"])
    body = {
        "kind": cfg.kind,
        "host": cfg.host,
        "database": cfg.database,
        "user": cfg.user,
        "password": cfg.password,
        "port": cfg.port,
        "encrypt": cfg.encrypt,
        "trust_server_certificate": cfg.trust_server_certificate,
        "space_id": space_id,
        "tables": tables,
        "max_rows": max_rows,
    }
    r = client.post("/v1/studio/sources/sql", json=body)
    if r.status_code != 200:
        raise _http_refusal("connect", r, "connect_failed")
    out = r.json()
    landed = [
        {k: t.get(k) for k in ("bronze_table", "source", "row_count", "truncated")}
        for t in out.get("tables") or []
    ]
    detail = {
        "space_id": space_id,
        "space_persisted": None if created is None else created.get("persisted"),
        "source": out.get("source"),
        "tables": landed,
        "skipped": out.get("skipped") or [],
        "declared_primary_keys": out.get("declared_primary_keys"),
        "declared_foreign_keys": out.get("declared_foreign_keys"),
        "serving_sync": out.get("serving_sync"),
    }
    if not landed:
        raise _refuse(
            "connect",
            "nothing_landed",
            "the source returned no tables",
            "the login sees no base tables, or every --table was skipped",
            **detail,
        )
    capped = [t["bronze_table"] for t in landed if t["truncated"]]
    if capped:
        raise _refuse(
            "connect",
            "truncated_pull",
            f"pull capped on {', '.join(capped)}",
            "the lake holds a partial copy, so no answer can equal the source: raise "
            "--max-rows or name fewer --table",
            **detail,
        )
    rows = sum(int(t["row_count"] or 0) for t in landed)
    return Receipt(
        "connect", True, f"{len(landed)} tables, {rows} rows into Space {space_id}", detail
    ), space_id


def _grants() -> Receipt:
    return Receipt(
        "grants",
        True,
        "N/A on main",
        {
            "note": "no grant API on main (admin is read-only; CONNECT-GRANT-01 #408 is "
            "open). The Space reads the tables landed into it on connect."
        },
        na=True,
    )


def _ontology(client: Any, space_id: str) -> tuple[Receipt, dict[str, Any]]:
    r = client.get(f"/v1/spaces/{space_id}/ontology")
    if r.status_code != 200:
        raise _http_refusal("ontology", r, "ontology_read_failed")
    view = r.json()
    if not view.get("derived"):
        raise _refuse(
            "ontology",
            "ontology_not_derived",
            str(view.get("reason") or "not derived"),
            "the connect receipt's ontology.reason says why; fix it and POST "
            f"/v1/spaces/{space_id}/ontology/derive",
            view=view,
        )
    objects: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    proposed: list[str] = []
    for onto in view.get("ontologies") or []:
        if onto.get("version_status") != "active" or not onto.get("answers_asks"):
            proposed.append(str(onto.get("version_id")))
        objects += [
            {
                "name": o["name"],
                "key": o.get("key"),
                "status": o.get("status"),
                "violations": o.get("violations") or [],
            }
            for o in onto.get("objects") or []
        ]
        links += [
            {
                "name": k["name"],
                "from": k.get("from"),
                "to": k.get("to"),
                "cardinality": k.get("cardinality"),
                "status": k.get("status"),
                "violations": [v.get("detail") for v in k.get("violations") or []],
            }
            for k in onto.get("links") or []
        ]
    detail = {"objects": objects, "links": links}
    if proposed:
        raise _refuse(
            "ontology",
            "ontology_version_proposed",
            f"version(s) {', '.join(proposed)} not active",
            "the source schema changed since the first connect, so the new version is "
            "proposed and does not answer; main has no promote route (Studio review, #283)",
            **detail,
        )
    verified = [o for o in objects if o["status"] == "verified"]
    if not verified:
        raise _refuse(
            "ontology",
            "no_verified_object",
            "no object passed verification",
            "every object failed a key check (see violations): fix the key in the source "
            "or declare a primary key, then re-derive",
            **detail,
        )
    unverified = [k["name"] for k in links if k["status"] != "verified"]
    summary = (
        f"{len(verified)}/{len(objects)} objects verified, "
        f"{len(links) - len(unverified)}/{len(links)} links verified"
    )
    if unverified:
        summary += f"; unverified (no question uses them): {', '.join(unverified)}"
    return Receipt("ontology", True, summary, detail), view


def _diff(client: Any, space_id: str) -> tuple[Receipt, dict[str, Any] | None]:
    _bootstrap()
    from onto_steward import summarize_diff

    r = client.post("/v1/ontology/data/compile", params={"space_id": space_id})
    if r.status_code != 200:
        raise _http_refusal("diff", r, "compile_failed")
    compiled = r.json()
    if compiled.get("status") != "proposed" or not compiled.get("version_id"):
        return (
            Receipt(
                "diff",
                True,
                f"AO compile {compiled.get('status') or 'off'}: no proposal to review",
                {
                    "compile_status": compiled.get("status"),
                    "note": "DMS_AO_COMPILE is not 1 on the DMS host; Space asks are served "
                    "from the derived Space ontology above",
                },
            ),
            None,
        )
    version_id = str(compiled["version_id"])
    r = client.get("/v1/ontology/data/diff", params={"version_id": version_id})
    if r.status_code != 200:
        raise _http_refusal("diff", r, "diff_failed")
    diff = r.json()
    summary = summarize_diff(diff)
    counts = " ".join(f"{k}={v}" for k, v in sorted(summary["counts"].items())) or "none"
    return Receipt("diff", True, f"version {version_id}: {counts}", summary), diff


def _approve(client: Any, diff: dict[str, Any] | None, *, approve: bool) -> Receipt:
    if diff is None:
        return Receipt("approve", True, "nothing proposed: no steward approval to make")
    pending = [row for key in ("proposed_only", "changed") for row in diff.get(key) or []]
    names = [str((row.get("payload") or {}).get("name") or row.get("id")) for row in pending]
    if not approve:
        raise _refuse(
            "approve",
            "steward_review_pending",
            f"{len(pending)} proposed item(s) await a steward",
            "read the diff receipt, then re-run with --approve as the steward",
            items=names,
        )
    approved: list[str] = []
    for row in pending:
        r = client.post(f"/v1/ontology/data/items/{row['id']}/approve")
        if r.status_code != 200:
            raise _http_refusal("approve", r, "approve_failed")
        approved.append(str((r.json().get("payload") or {}).get("name") or row["id"]))
    return Receipt("approve", True, f"{len(approved)} item(s) approved", {"approved": approved})


def _ask(client: Any, space_id: str, plan: PlannedAsk, oracle: Oracle) -> Receipt:
    _bootstrap()
    from dms_executor.envelope import assert_envelope_valid

    step = f"ask: {plan.question}"
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": plan.question,
            "session_id": f"ses_fde_{uuid4().hex[:12]}",
            "space_id": space_id,
        },
    )
    if r.status_code != 200:
        raise _http_refusal(step, r, "ask_failed")
    env = r.json()
    shown = {
        "badge": env.get("badge"),
        "text": env.get("text"),
        "rows": env.get("rows"),
        "sql_used": env.get("sql_used"),
        "audit_id": env.get("audit_id"),
        "basis": plan.basis,
    }
    try:
        assert_envelope_valid(env)
    except AssertionError as exc:
        raise _refuse(
            step,
            "envelope_invalid",
            str(exc),
            "a DMS defect: file it with this receipt; the envelope broke an E-rule",
            **shown,
        ) from None
    if env.get("demo_fallback_used"):
        raise _refuse(
            step,
            "demo_fallback",
            "the answer is demo data",
            "set DMS_DEMO_FALLBACK=0 on the DMS host (CLAUDE.md rule 11)",
            **shown,
        )
    if env.get("abstained"):
        raise _refuse(
            step,
            "abstained",
            str(env.get("text") or "")[:300],
            "the gap line names the refusal; see the playbook's ask table",
            **shown,
        )
    try:
        truth = oracle(plan.oracle_sql)
    except Exception as exc:  # noqa: BLE001 - named on the receipt
        raise _refuse(
            step,
            "oracle_failed",
            f"{type(exc).__name__}: {str(exc)[:200]}",
            "the oracle could not run on the source; the answer is unchecked",
            oracle_sql=plan.oracle_sql,
            **shown,
        ) from None
    shown["oracle_rows"] = truth
    rows = list(env.get("rows") or [])
    if _multiset(rows) != _multiset(truth):
        raise _refuse(
            step,
            "rows_mismatch",
            f"{env.get('badge')} answer differs from the source",
            "a green wrong number: stop the landing and file it with this receipt "
            "(CLAUDE.md rule 12)",
            oracle_sql=plan.oracle_sql,
            **shown,
        )
    text = str(env.get("text") or "")
    if len(truth) <= 10:
        missing = sorted({_norm(v) for row in truth for v in row.values()} - {"", "NULL"})
        missing = [v for v in missing if v not in text]
        if missing:
            raise _refuse(
                step,
                "figure_not_in_text",
                f"text omits {', '.join(missing)}",
                "rows match but the customer-facing text does not state them: a "
                "DMS defect, file it",
                oracle_sql=plan.oracle_sql,
                **shown,
            )
    return Receipt(step, True, f"{env.get('badge')} rows={len(rows)} = oracle", shown)


def run_landing(
    client: Any,
    cfg: Any,
    *,
    space_id: str | None = None,
    space_name: str | None = None,
    tables: list[str] | None = None,
    max_rows: int | None = None,
    approve: bool = False,
    max_groups: int = 1,
    oracle: Oracle | None = None,
) -> list[Receipt]:
    """Walk every step; stop at the first refusal. ``client`` is any httpx-style client."""
    receipts: list[Receipt] = []
    ask_oracle = oracle or source_oracle(cfg)
    try:
        got, sid = _connect(
            client, cfg, space_id=space_id, space_name=space_name, tables=tables, max_rows=max_rows
        )
        receipts.append(got)
        receipts.append(_grants())
        got, view = _ontology(client, sid)
        receipts.append(got)
        got, diff = _diff(client, sid)
        receipts.append(got)
        receipts.append(_approve(client, diff, approve=approve))
        for plan in plan_questions(view, kind=cfg.kind, max_groups=max_groups):
            receipts.append(_ask(client, sid, plan, ask_oracle))
    except _Stop as stop:
        receipts.append(stop.receipt)
    return receipts


def render(receipts: list[Receipt]) -> Iterator[str]:
    for rec in receipts:
        mark = "N/A" if rec.na else "PASS" if rec.ok else f"FAIL {rec.failure}"
        yield f"[{mark}] {rec.step}: {rec.summary}"
        if rec.step.startswith("ask: "):
            d = rec.detail
            yield f"    badge={d.get('badge')} rows={json.dumps(d.get('rows'), default=str)}"
            if "oracle_rows" in d:
                yield f"    oracle={json.dumps(d['oracle_rows'], default=str)}"
            for line in str(d.get("text") or "").splitlines():
                yield f"    | {line}"
        if not rec.ok and rec.hint:
            yield f"    what to do: {rec.hint}"
    last = receipts[-1] if receipts else None
    yield "LANDED" if last is not None and last.ok else f"STOPPED at {last.step if last else '-'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dms-url", required=True)
    parser.add_argument("--kind", required=True, choices=["sqlserver", "mysql", "postgresql"])
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-encrypt", action="store_true")
    parser.add_argument("--trust-server-certificate", action="store_true")
    parser.add_argument("--table", action="append", dest="tables")
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--space-id")
    parser.add_argument("--space-name")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="approve every proposed AO item as the DMS steward actor",
    )
    parser.add_argument("--max-groups", type=int, default=1)
    parser.add_argument("--json", type=Path, help="also write the receipts here")
    args = parser.parse_args(argv)
    _bootstrap()
    import httpx
    from dms_executor.db_connector import SourceConfig

    cfg = SourceConfig(
        kind=args.kind,
        host=args.host,
        database=args.database,
        user=args.user,
        password=os.environ.get("FDE_SOURCE_PASSWORD", ""),
        port=args.port,
        encrypt=not args.no_encrypt,
        trust_server_certificate=args.trust_server_certificate,
    )
    with httpx.Client(base_url=args.dms_url, timeout=600) as client:
        receipts = run_landing(
            client,
            cfg,
            space_id=args.space_id,
            space_name=args.space_name,
            tables=args.tables,
            max_rows=args.max_rows,
            approve=args.approve,
            max_groups=args.max_groups,
        )
    for line in render(receipts):
        print(line)
    if args.json:
        args.json.write_text(
            json.dumps([r.__dict__ for r in receipts], indent=2, default=str), encoding="utf-8"
        )
    return 0 if receipts and receipts[-1].ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
