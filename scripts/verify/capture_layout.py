"""Capture flag-off ask envelopes from this checkout's product code.

COPY-only hides ``tests/fixtures/curated_ceo`` before DMS imports, which is
the API image: the Dockerfile does not COPY ``tests/``. Insights stay
unarmed. Submit executes the SQL the product hands it. TestClient is
constructed without entering lifespan.

    python scripts/verify/capture_layout.py --layout copy-only --out PATH
    python scripts/verify/capture_layout.py --layout full-tree --out PATH
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "curated_ceo"
SESSION_ID = "ses_flag_off_52"
PRODUCT_SHA = "57d85c529aa363825aaa566f12b8822fd76a4215"


def _prepare() -> None:
    os.environ.pop("DMS_ASK_CLARIFY", None)
    os.environ.pop("DMS_CLOOP_B", None)
    os.environ["DMS_DEMO_FALLBACK"] = "0"
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    ):
        path = str(ROOT / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


def _capture(questions: list[dict[str, Any]], spaces: dict[str, str]) -> list[dict[str, Any]]:
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
    from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter
    from fastapi.testclient import TestClient
    from tests.fixtures.ask_guide.capture_flag_off_52 import load_responses

    recorded = load_responses()
    by_id = recorded["by_id"]
    bind = recorded["session_bind"]
    db = Path(tempfile.mkdtemp(prefix="capture-layout-")) / "warehouse.duckdb"
    ensure_demo_warehouse(db)

    class _Stub:
        def __init__(self) -> None:
            self.current = ""

        def _row(self) -> dict[str, Any]:
            return by_id[self.current]

        def submit(self, req: Any) -> QueryResult:
            plan = getattr(req, "plan", None)
            kind = plan.get("kind") if isinstance(plan, dict) else None
            if kind == "session_bind":
                return QueryResult(
                    ok=bool(bind["ok"]),
                    status=str(bind["status"]),
                    run_id=str(bind["run_id"]),
                )
            body = getattr(req, "body", None)
            sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
            con = connect_file(db)
            try:
                cur = con.execute(sql)
                cols = [str(item[0]) for item in (cur.description or [])]
                rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            finally:
                con.close()
            return QueryResult(ok=True, status="ok", run_id="run_exec", output={"rows": rows})

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
            return {"insights_fail": "insights_unarmed", "insights_reached": True}

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
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=db)  # type: ignore[arg-type]
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
            raise SystemExit(f"{question['id']} HTTP {res.status_code}")
        body = res.json()
        if not isinstance(body, dict) or "rows" not in body:
            raise SystemExit(f"{question['id']} envelope has no rows")
        rows.append({"id": question["id"], "env": body})
    if len(rows) != 52:
        raise SystemExit(f"capture size {len(rows)} != 52")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", choices=("copy-only", "full-tree"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    _prepare()
    import yaml

    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    questions = list(pack["questions"])
    spaces = dict(pack["spaces"])
    hidden = FIXTURE.with_name("curated_ceo.hide_capture")
    moved = False
    if args.layout == "copy-only":
        if hidden.exists():
            raise SystemExit("grade52: capture hide path is already present")
        FIXTURE.rename(hidden)
        moved = True
    try:
        rows = _capture(questions, spaces)
    finally:
        if moved and hidden.exists() and not FIXTURE.exists():
            hidden.rename(FIXTURE)
    header = {"dms_sha": PRODUCT_SHA, "layout": args.layout}
    lines = [json.dumps(header, sort_keys=True)]
    lines.extend(json.dumps(row, sort_keys=True, default=str) for row in rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out} layout={args.layout} n={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
