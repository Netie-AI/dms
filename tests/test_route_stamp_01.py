"""ROUTE-STAMP-01: each serving path stamps its own route.

COPY-only is the Dockerfile COPY set (no tests/). Insights are unarmed and
submit executes the product SQL. Flags off and on, every envelope has one
route. A planted contradiction is not a route. Answers stay the main bytes
apart from the stamp fields, as_of, and ticket_id.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from dms_executor.route_stamp import ROUTES, route_of

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route_stamp"
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
_VOLATILE = frozenset(
    {
        "as_of",
        "ticket_id",
        "served_route",
        "plan_origin",
        "ladder_rung",
        "served_model",
    }
)

_ASK = r"""
import json, os
from pathlib import Path
from typing import Any
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter
from fastapi.testclient import TestClient

questions = json.loads(Path(os.environ["QFILE"]).read_text())
db = Path(os.environ["DB"])
ensure_demo_warehouse(db)

class Stub:
    def submit(self, req):
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_bind")
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
        return AskResponse(
            answer="Abstained.",
            badge="abstain",
            sql_used=None,
            rows=[],
            assumptions="fixture",
            audit_id="aud_copy",
            route="abstain",
            abstained=True,
        )

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_copy", hash="hash_copy_not_entry")

    def compute_insights(self, question: str, **_k: Any):
        return {
            "phase": "generate",
            "status": "REFUSE",
            "generative": {"ok": False, "sql": None, "climb": {"final": "UNARMED"}},
        }

minter = ManifestMinter(openvault_url="http://127.0.0.1:9")

def _mint(acl):
    return Manifest(
        session_id=acl.session_id, org_id=acl.org_id, space_id=acl.space_id,
        pool_id=acl.pool_id, issuer_key_id="test-kid",
        allowed_paths=list(acl.allowed_paths), row_predicates=dict(acl.row_predicates),
        issued_at="2026-07-30T00:00:00+00:00", expires_at="2026-07-30T01:00:00+00:00",
        signature="dGVzdHNpZw",
    )

minter.mint_manifest = _mint
minter.fetch_intermediate = lambda: None
minter.close = lambda: None
minter.invalidate = lambda *_a, **_k: None
app = create_app()
cortex = Stub()
app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=db)
app.state.cortex = cortex
settings = Settings(_env_file=None, dms_ask_mode="live", dms_demo_fallback=False)
app.dependency_overrides[get_settings] = lambda: settings
client = TestClient(app)
rows = []
for q in questions:
    res = client.post("/v1/chat/ask", json={
        "question": q["question"], "space_id": q["space_id"], "session_id": "ses_copy",
    })
    if res.status_code != 200:
        raise SystemExit(f"{q['id']} HTTP {res.status_code}")
    rows.append({"id": q["id"], "env": res.json()})
Path(os.environ["OUT"]).write_text(json.dumps(rows))
print(len(rows))
"""


def _questions() -> list[dict[str, str]]:
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    spaces = pack["spaces"]
    return [
        {
            "id": str(row["id"]),
            "question": str(row["question"]),
            "space_id": str(spaces[row["space"]]),
        }
        for row in pack["questions"]
    ]


def _layout(dst: Path) -> None:
    skip = shutil.ignore_patterns("__pycache__", "*.egg-info", "node_modules", ".pytest_cache")
    for rel in ("packages", "apps/api", "contract", "alembic"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=skip)
    for rel in ("pyproject.toml", "README.md", "alembic.ini"):
        shutil.copy2(ROOT / rel, dst / rel)


def _drop(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop(item) for key, item in value.items() if key not in _VOLATILE}
    if isinstance(value, list):
        return [_drop(item) for item in value]
    return value


def _serve(tmp_path: Path, *, flags_on: bool) -> list[dict[str, Any]]:
    app_root = tmp_path / ("on" if flags_on else "off")
    _layout(app_root)
    assert not (app_root / "tests").exists()
    qfile = tmp_path / "questions.json"
    qfile.write_text(json.dumps(_questions()), encoding="utf-8")
    out = tmp_path / ("on.json" if flags_on else "off.json")
    db = tmp_path / ("on.duckdb" if flags_on else "off.duckdb")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(app_root / rel)
            for rel in (
                "apps/api",
                "packages/core",
                "packages/cortex_client",
                "packages/executor",
                "packages/ledger",
            )
        ),
        "DMS_DEMO_FALLBACK": "0",
        "DMS_SKIP_CONTROL_PLANE_TESTS": "1",
        "QFILE": str(qfile),
        "OUT": str(out),
        "DB": str(db),
    }
    for name in (
        "DMS_CLOOP_B",
        "DMS_SCHEMA_CONTEXT",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_LANE_BRONZE_SHEET",
        "DMS_CCA_CASCADE",
        "DMS_HARNESS_ASK_PATHS",
        "DATABASE_URL",
    ):
        env.pop(name, None)
    if flags_on:
        env["DMS_CLOOP_B"] = "1"
        env["DMS_SCHEMA_CONTEXT"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _ASK],
        cwd=app_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 52
    return rows


def _assert_main_bytes(live: list[dict[str, Any]], fixture_name: str) -> None:
    frozen = json.loads((FIXTURE / fixture_name).read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in frozen]
    for got, old in zip(live, frozen, strict=True):
        assert _drop(got["env"]) == _drop(old["env"]), got["id"]


def _assert_one_route(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        got = route_of(row["env"])
        assert got in ROUTES, (row["id"], got)


def test_planted_contradiction_is_not_a_route() -> None:
    planted = {
        "badge": "L1_GOVERNED_METRIC",
        "plan_origin": "generate_sql",
        "ladder_rung": "compile",
        "served_model": "some-model",
    }
    assert route_of(planted) == "contradictory"
    assert route_of({}) == "unattributed"
    src = (ROOT / "packages/executor/dms_executor/route_stamp.py").read_text(encoding="utf-8")
    assert "lane_for_route" not in src
    assert "NO_MODEL_LANES" not in src


def test_copy_only_52_flags_off_one_route(tmp_path: Path) -> None:
    rows = _serve(tmp_path, flags_on=False)
    _assert_one_route(rows)
    _assert_main_bytes(rows, "copy_off.json")
    l1 = [
        row["id"]
        for row in rows
        if row["env"].get("badge") == "L1_GOVERNED_METRIC" and row["env"].get("abstained") is False
    ]
    abstain = [row["id"] for row in rows if row["env"].get("abstained") is True]
    assert len(l1) == 16
    assert len(abstain) == 36
    assert len(l1) + len(abstain) == 52


def test_copy_only_52_flags_on_one_route(tmp_path: Path) -> None:
    rows = _serve(tmp_path, flags_on=True)
    _assert_one_route(rows)
    _assert_main_bytes(rows, "copy_on.json")
    l1 = [
        row["id"]
        for row in rows
        if row["env"].get("badge") == "L1_GOVERNED_METRIC" and row["env"].get("abstained") is False
    ]
    abstain = [row["id"] for row in rows if row["env"].get("abstained") is True]
    assert len(l1) == 16
    assert len(abstain) == 36
