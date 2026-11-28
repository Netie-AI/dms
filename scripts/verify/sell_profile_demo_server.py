"""Demo warehouse plus a scripted model. DMS_SELL_PROFILE=1.

Three asks, in order, get three read-only replies. The model text is fixed.
The SQL runs on the demo warehouse. No phrase table.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for rel in (
    "apps/api",
    "packages/core",
    "packages/cortex_client",
    "packages/executor",
    "packages/ledger",
    "packages/ao_vendor",
):
    sys.path.insert(0, str(ROOT / rel))

os.environ["DMS_SELL_PROFILE"] = "1"
os.environ["DMS_DEMO_FALLBACK"] = "0"
os.environ["DMS_ASK_MODE"] = "live"

FINANCE_SQL = (
    {
        "query_sql": (
            "SELECT location_id, SUM(quantity_kg) AS quantity_kg "
            "FROM inventory GROUP BY location_id"
        ),
        "answer": "Here is stock by warehouse.",
        "served_model": "ov-routed",
        "served_provider": "openvault",
        "chart_spec": {
            "kind": "bar",
            "x": "location_id",
            "y": ["quantity_kg"],
            "series": None,
        },
    },
    {
        "query_sql": "SELECT supplier_id, supplier_name FROM suppliers",
        "answer": "Here are the suppliers.",
        "served_model": "ov-routed",
        "served_provider": "openvault",
    },
    {
        "query_sql": "SELECT location_id, name FROM locations",
        "answer": "Here are the warehouses.",
        "served_model": "ov-routed",
        "served_provider": "openvault",
    },
)


def main() -> None:
    import uvicorn
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

    db = Path(os.environ.get("DMS_SELL_DEMO_DB", "/tmp/sell-profile-demo.duckdb"))
    ensure_demo_warehouse(db)
    script = [dict(item) for item in FINANCE_SQL]
    calls = {"n": 0}

    class _Model:
        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            _ = question
            index = min(calls["n"], len(script) - 1)
            calls["n"] += 1
            return dict(script[index])

        def submit(self, req: Any) -> QueryResult:
            plan = getattr(req, "plan", None)
            kind = plan.get("kind") if isinstance(plan, dict) else None
            body = getattr(req, "body", None)
            sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
            if kind == "session_bind" or not sql.strip():
                return QueryResult(ok=True, status="bound", run_id="bind_demo", output={})
            con = connect_file(db)
            try:
                cur = con.execute(sql)
                cols = [str(item[0]) for item in (cur.description or [])]
                rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            finally:
                con.close()
            return QueryResult(ok=True, status="ok", run_id="run_demo", output={"rows": rows})

        def ask(self, req: AskRequest) -> AskResponse:
            _ = req
            return AskResponse(
                answer="rule path",
                badge="ABSTAIN",
                sql_used="",
                rows=[],
                assumptions=[],
                audit_id="aud_demo",
                route="abstain",
            )

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            return LedgerAppendResponse(entry_id="led_demo", hash="hash_demo_not_entry")

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
    app = create_app()
    executor = Executor(
        cortex=_Model(),
        minter=minter,
        warehouse_path=db,
    )
    original = app.router.lifespan_context

    @asynccontextmanager
    async def _keep_model(app_in: Any):
        async with original(app_in):
            # Lifespan builds the live client. Put the scripted model back
            # after that, or the ask talks to a Cortex that is not running.
            app_in.state.ask_service = executor
            yield

    app.router.lifespan_context = _keep_model
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    port = int(os.environ.get("DMS_SELL_DEMO_PORT", "8091"))
    print(f"sell-demo-ready {port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
