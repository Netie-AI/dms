"""Capture the 52-question flag-off /ask envelopes for one checkout.

The stub never calls a live Cortex. It replays
``cortex_responses_52.json`` for every pack question. ``DMS_ASK_CLARIFY``
is removed before the replay.

Regenerate main's golden from a checkout of origin/main (72df50d8 or newer)::

    DMS_CAPTURE_ROOT=/path/to/main-checkout \\
      python tests/fixtures/ask_guide/capture_flag_off_52.py \\
      --out tests/fixtures/ask_guide/flag_off_52_main.json

``DMS_CAPTURE_ROOT`` is prepended to ``sys.path`` before DMS imports, so the
script file can live on this branch while the envelopes come from main.
Leave it unset to capture the checkout that is already on ``sys.path``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESPONSES = HERE / "cortex_responses_52.json"
PACK = HERE.parent / "curated_ceo" / "questions.yaml"
SESSION_ID = "ses_flag_off_52"


def _prefer_capture_root() -> None:
    root = os.environ.get("DMS_CAPTURE_ROOT")
    if not root:
        return
    base = Path(root)
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    ):
        sys.path.insert(0, str(base / rel))


def dump_rows(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode() + b"\n"


def load_responses() -> dict[str, Any]:
    data = json.loads(RESPONSES.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("by_id"), dict):
        raise SystemExit(f"bad responses fixture: {RESPONSES}")
    return data


def replay_pack(*, execute_sql: bool = False) -> list[dict[str, Any]]:
    """POST every pack question. Flag stays unset. Same stub on every call.

    ``execute_sql`` runs each submit body on the warehouse instead of the
    canned row. Row order is DuckDB's. The canned replay stays the default.
    """
    os.environ.pop("DMS_ASK_CLARIFY", None)
    import yaml
    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from cortex_contract.execution import Manifest, QueryResult
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_executor import Executor
    from dms_executor.manifest import ManifestMinter
    from fastapi.testclient import TestClient

    recorded = load_responses()
    by_id: dict[str, Any] = recorded["by_id"]
    bind = recorded["session_bind"]
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    spaces: dict[str, str] = dict(pack["spaces"])
    questions: list[dict[str, Any]] = list(pack["questions"])
    if len(questions) != 52:
        raise AssertionError(f"pack size {len(questions)} != 52")
    missing = [q["id"] for q in questions if q["id"] not in by_id]
    if missing:
        raise AssertionError(f"responses fixture missing {missing}")

    class _Stub:
        def __init__(self) -> None:
            self.current = ""

        def _row(self) -> dict[str, Any]:
            row = by_id[self.current]
            if not isinstance(row, dict):
                raise AssertionError(f"response for {self.current} is not an object")
            return row

        def submit(self, req: Any) -> QueryResult:
            plan = getattr(req, "plan", None)
            kind = plan.get("kind") if isinstance(plan, dict) else None
            if kind == "session_bind":
                return QueryResult(
                    ok=bool(bind["ok"]),
                    status=str(bind["status"]),
                    run_id=str(bind["run_id"]),
                )
            if execute_sql:
                body_in = getattr(req, "body", None)
                sql = str(body_in.get("sql") or "") if isinstance(body_in, dict) else ""
                if sql.strip():
                    from dms_executor.demo_warehouse import connect_file, warehouse_path

                    con = connect_file(warehouse_path())
                    try:
                        cur = con.execute(sql)
                        cols = [str(c[0]) for c in (cur.description or [])]
                        executed = [
                            dict(zip(cols, row, strict=True)) for row in cur.fetchall()
                        ]
                    finally:
                        con.close()
                    return QueryResult(
                        ok=True,
                        status="ok",
                        run_id=f"run_exec_{self.current}",
                        output={"rows": executed},
                    )
            body = self._row()["submit"]
            return QueryResult(
                ok=bool(body["ok"]),
                status=str(body["status"]),
                run_id=str(body["run_id"]),
                output={"rows": list(body["rows"])},
            )

        def ask(self, req: AskRequest) -> AskResponse:
            body = self._row()["ask"]
            _ = req
            return AskResponse(
                answer=str(body["answer"]),
                badge=str(body["badge"]),
                sql_used=str(body["sql_used"]),
                rows=list(body["rows"]),
                assumptions=body["assumptions"],
                audit_id=str(body["audit_id"]),
                route=str(body["route"]),
            )

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            body = self._row()["ledger"]
            return LedgerAppendResponse(
                entry_id=str(body["entry_id"]),
                hash=str(body["hash"]),
            )

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            _ = question
            return dict(self._row()["insights"])

    minter = ManifestMinter(openvault_url="http://127.0.0.1:9")

    def _mint(acl: Any) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]

    cortex = _Stub()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    rows: list[dict[str, Any]] = []
    for question in questions:
        cortex.current = str(question["id"])
        res = client.post(
            "/v1/chat/ask",
            json={
                "question": question["question"],
                "space_id": spaces[str(question["space"])],
                "session_id": SESSION_ID,
            },
        )
        if res.status_code != 200:
            raise AssertionError(f"{question['id']} HTTP {res.status_code}: {res.text[:400]}")
        body = res.json()
        if not isinstance(body, dict):
            raise AssertionError(f"{question['id']} envelope is not an object")
        rows.append({"id": question["id"], "env": body})
    return rows


def main() -> None:
    _prefer_capture_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run submit SQL on the warehouse instead of the canned rows",
    )
    args = parser.parse_args()
    import dms_executor

    rows = replay_pack(execute_sql=args.execute)
    args.out.write_bytes(dump_rows(rows))
    print(f"wrote {len(rows)} envelopes from {dms_executor.__file__} -> {args.out}")


if __name__ == "__main__":
    main()
