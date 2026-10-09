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


def replay_pack(
    *,
    reconfirm_writer: Any = None,
    suggestion: str | None = None,
    suggestion_sql: str | None = None,
    warehouse: Path | None = None,
    follow_yes: bool = False,
    yes_warehouse: Path | None = None,
) -> list[dict[str, Any]]:
    """POST every pack question. Flag stays unset unless the caller set it.

    ``reconfirm_writer`` is only attached. The flag-off capture leaves it
    unset, so the model is not called. ``follow_yes`` posts the stored
    token after a confirm. ``yes_warehouse`` is attached only for that
    second call, so the first pass keeps the flag-off warehouse.
    """
    if reconfirm_writer is None:
        os.environ.pop("DMS_ASK_RECONFIRM", None)
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
            sql = ""
            body_in = getattr(req, "body", None)
            if isinstance(body_in, dict):
                sql = str(body_in.get("sql") or "")
            exec_db = warehouse if warehouse is not None else yes_warehouse
            if suggestion_sql and sql.strip() == suggestion_sql.strip() and exec_db is not None:
                from dms_executor.demo_warehouse import connect_file

                con = connect_file(exec_db)
                try:
                    cur = con.execute(sql)
                    cols = [d[0] for d in (cur.description or [])]
                    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
                finally:
                    con.close()
                return QueryResult(
                    ok=True,
                    status="ok",
                    run_id="run_exec",
                    output={"rows": rows},
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
            if suggestion and question.strip() == suggestion and suggestion_sql:
                return {"query_sql": suggestion_sql}
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
    app.state.ask_service = Executor(  # type: ignore[arg-type]
        cortex=cortex,
        minter=minter,
        warehouse_path=warehouse,
        clarify_model=reconfirm_writer,
    )
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
        row_out: dict[str, Any] = {"id": question["id"], "env": body}
        if (
            follow_yes
            and body.get("status") == "confirm"
            and isinstance(body.get("confirm_id"), str)
        ):
            service = app.state.ask_service
            held = getattr(service, "_warehouse", None)
            if yes_warehouse is not None:
                service._warehouse = yes_warehouse
            try:
                yes = client.post(
                    "/v1/chat/ask",
                    json={
                        "question": "client supplied text that must be ignored",
                        "space_id": spaces[str(question["space"])],
                        "session_id": SESSION_ID,
                        "confirm_id": body["confirm_id"],
                        "confirm": "yes",
                    },
                )
            finally:
                if yes_warehouse is not None:
                    service._warehouse = held
            if yes.status_code != 200:
                raise AssertionError(
                    f"{question['id']} yes HTTP {yes.status_code}: {yes.text[:400]}"
                )
            yes_body = yes.json()
            if not isinstance(yes_body, dict):
                raise AssertionError(f"{question['id']} yes envelope is not an object")
            row_out["yes"] = yes_body
        rows.append(row_out)
    return rows


def main() -> None:
    _prefer_capture_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import dms_executor

    rows = replay_pack()
    args.out.write_bytes(dump_rows(rows))
    print(f"wrote {len(rows)} envelopes from {dms_executor.__file__} -> {args.out}")


if __name__ == "__main__":
    main()
