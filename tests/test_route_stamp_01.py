"""ROUTE-STAMP-01: each serving path stamps its own route.

COPY-only is the Dockerfile COPY set (no tests/). Insights are unarmed and
submit executes the product SQL. Flags off and on, every envelope has one
route. A planted contradiction is not a route. Answers stay the main bytes
apart from the two new keys (served_route, ladder_rung), as_of, and
ticket_id: plan_origin, served_model, and abstain_reason are compared.
Unordered SQL uses the row-order rule: rows, value cells, and the row lines
in the text compare as a set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.abstain import build_abstain
from dms_executor.route_stamp import (
    ROUTE_COMPILE,
    ROUTE_L1,
    ROUTE_LADDER,
    ROUTE_MODEL,
    ROUTES,
    is_abstain_route,
    route_class,
    route_of,
    stamp_route,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route_stamp"
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
# Wall clock, per-run ticket ids, route stamps, the reconfirm token, and the
# grant-miss code. The copy fixtures predate the offer and the code.
_VOLATILE = frozenset(
    {"as_of", "ticket_id", "served_route", "ladder_rung", "reconfirm", "abstain_reason"}
)
# Base pack phrase. The supplier-rank sibling lives in the score fixture,
# which this copy does not include, so the model abstain there may offer.
_DIRECT_GRANT = frozenset({"ops_spend_boundary"})

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


def _ordered_sql(sql: str) -> bool:
    """True when the statement has a top-level ORDER BY. Nested ORDER BY does not count."""
    from sqlglot import parse_one

    try:
        tree = parse_one(sql, read="duckdb")
    except Exception:
        return True
    return bool(tree.args.get("order"))


def _align_unordered(env: dict[str, Any]) -> dict[str, Any]:
    sql = env.get("sql_used")
    if not isinstance(sql, str) or _ordered_sql(sql):
        return env
    out = dict(env)
    def _row_key(row: dict[str, Any]) -> str:
        return json.dumps(row, sort_keys=True, default=str)

    rows = out.get("rows")
    if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
        out["rows"] = sorted(rows, key=_row_key)
    receipt = out.get("audit_receipt")
    if isinstance(receipt, dict):
        include = receipt.get("include")
        nested = include.get("rows") if isinstance(include, dict) else None
        if isinstance(nested, list) and all(isinstance(row, dict) for row in nested):
            receipt = dict(receipt)
            include = dict(include)
            include["rows"] = sorted(nested, key=_row_key)
            receipt["include"] = include
            out["audit_receipt"] = receipt
    values = out.get("values")
    if isinstance(values, list) and all(isinstance(item, dict) for item in values):
        ranked = sorted(
            values,
            key=lambda item: json.dumps(
                {key: val for key, val in item.items() if key != "id"},
                sort_keys=True,
                default=str,
            ),
        )
        renumbered: list[Any] = []
        number = 0
        for item in ranked:
            item = dict(item)
            ident = item.get("id")
            if isinstance(ident, str) and ident[1:].isdigit() and ident.startswith("v"):
                item["id"] = f"v{number}"
                number += 1
            renumbered.append(item)
        out["values"] = renumbered
    text = out.get("text")
    if isinstance(text, str) and "\n  - " in text:
        head, rest = text.split("\n", 1)
        lines = rest.split("\n")
        bullets = [line for line in lines if line.startswith("  - ")]
        other = [line for line in lines if line and not line.startswith("  - ")]
        if bullets and not other:
            out["text"] = head + "\n" + "\n".join(sorted(bullets))
    return out


def _assert_main_bytes(live: list[dict[str, Any]], fixture_name: str) -> None:
    frozen = json.loads((FIXTURE / fixture_name).read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in frozen]
    for got, old in zip(live, frozen, strict=True):
        assert _align_unordered(_drop(got["env"])) == _align_unordered(_drop(old["env"])), got["id"]


_FORBIDDEN_ROUTE = frozenset(
    {
        "compile",
        "l1",
        "l1_governed_metric",
        "ai",
        "generate_sql",
        "model_sql",
        "ontology_compile",
        "ontology_ranking",
        "ontology_plan",
    }
)


def _no_sql(env: dict[str, Any]) -> bool:
    sql = env.get("sql_used")
    return not isinstance(sql, str) or not sql.strip()


def no_sql_route_violation(env: dict[str, Any]) -> str:
    """Why a no-SQL envelope is compile, L1, or ai. Empty when it is abstain:<stage>.

    Reads the builder's stamp (``served_route``, ``ladder_rung``).
    ``plan_origin`` is the producer's copied stamp, compared to main bytes
    elsewhere, and the customer reason is compared to main bytes too.
    """
    if not _no_sql(env):
        return ""
    if str(env.get("badge") or "") == "L1_GOVERNED_METRIC":
        return "badge L1"
    route = str(env.get("served_route") or "").strip()
    if not is_abstain_route(route):
        return f"served_route {route or 'missing'}"
    rung = str(env.get("ladder_rung") or "").strip().lower()
    if rung in _FORBIDDEN_ROUTE or rung != route:
        return f"ladder_rung {rung or 'missing'}"
    return ""


def _assert_one_route(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        got = route_of(row["env"])
        assert got in ROUTES or is_abstain_route(got), (row["id"], got)


def _assert_no_sql_is_abstain_stage(rows: list[dict[str, Any]]) -> None:
    assert len(rows) == 52
    for row in rows:
        violation = no_sql_route_violation(row["env"])
        assert not violation, (row["id"], violation, row["env"].get("served_route"))


def test_unordered_row_swap_still_matches_main_bytes() -> None:
    frozen = json.loads((FIXTURE / "copy_off.json").read_text(encoding="utf-8"))
    env = next(item["env"] for item in frozen if item["id"] == "ops_shipment_cost")
    swapped = json.loads(json.dumps(env))
    swapped["rows"] = list(reversed(env["rows"]))
    swapped["values"] = list(reversed(env["values"]))
    head, rest = str(env["text"]).split("\n", 1)
    swapped["text"] = head + "\n" + "\n".join(reversed(rest.split("\n")))
    receipt = swapped.get("audit_receipt")
    if isinstance(receipt, dict) and isinstance(receipt.get("include"), dict):
        receipt["include"]["rows"] = list(reversed(receipt["include"]["rows"]))
    assert _align_unordered(_drop(swapped)) == _align_unordered(_drop(env))


def test_ba179651_no_sql_compile_or_l1_is_a_violation() -> None:
    """The ba179651 stamps are the failure this test exists to catch."""
    compile_abstain = {
        "badge": "ABSTAIN",
        "sql_used": None,
        "served_route": "ladder_rung",
        "plan_origin": "compile",
        "ladder_rung": "compile",
    }
    l1_abstain = {
        "badge": "ABSTAIN",
        "sql_used": None,
        "served_route": "l1_governed_metric",
        "plan_origin": "l1",
        "ladder_rung": "l1",
    }
    assert no_sql_route_violation(compile_abstain)
    assert no_sql_route_violation(l1_abstain)
    served = {"sql_used": "SELECT 1", "served_route": "l1_governed_metric"}
    assert no_sql_route_violation(served) == ""


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


def _from_dir(rel: str, module: str) -> Any:
    import importlib

    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(module)


def _rendered_reason(env: dict[str, Any]) -> str:
    """The reason the customer sees: rt_grader order, same as the UI's namedAbstainReason."""
    return str(_from_dir("tests/redteam", "rt_grader").abstain_reason(env))


def _grade52_path(env: dict[str, Any]) -> str:
    return str(_from_dir("scripts/verify", "grade52").serve_path(env))


def test_cortex_abstain_keeps_the_named_reason() -> None:
    """The ticket code ``abstain`` is not the customer's reason."""
    named = "no governed metric for spend by bin"
    resp = AskResponse(
        answer=f"I can't answer that: {named}",
        badge="ABSTAIN",
        abstained=True,
        assumptions=[named],
        route="abstain",
    )
    env = map_ask_response_to_envelope(resp, session_id="ses_rs", question="spend by bin")
    assert env["abstained"] is True
    assert "abstain_reason" not in env
    assert _rendered_reason(env) == named
    assert env["served_route"] == "abstain:cortex_ask"
    assert env["ladder_rung"] == "abstain:cortex_ask"
    assert "plan_origin" not in env
    assert route_of(env) == "abstain:cortex_ask"


def test_build_abstain_writes_abstain_reason_only_when_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    fields: dict[str, Any] = {
        "answer_id": "ans_rs",
        "text": "Abstained: insights are not armed.",
        "assumptions": ["GEN-01: insights_unarmed"],
        "as_of": "2026-01-01T00:00:00Z",
        "ask_mode": "live",
        "route": "generated",
    }
    coded = build_abstain(reason="insights_unarmed", stage="generative", **dict(fields))
    assert "abstain_reason" not in coded
    assert _rendered_reason(coded) == "insights_unarmed"
    assert coded["served_route"] == "abstain:generative"
    named = build_abstain(
        reason="reserved_param:as_of",
        stage="generative",
        abstain_reason="reserved_param:as_of",
        **dict(fields),
    )
    assert named["abstain_reason"] == "reserved_param:as_of"


def test_cortex_l2_answer_is_unattributed_not_a_rule_path() -> None:
    """Cortex does not say which route built an L2 answer. DMS invents none."""
    resp = AskResponse(
        answer="Found 1 row(s).",
        badge="L2_VALIDATED",
        abstained=False,
        sql_used="SELECT 1 AS n",
        rows=[{"n": 1}],
        values=[{"id": "v0", "value": 1.0, "label": "n"}],
        route="generated",
    )
    env = map_ask_response_to_envelope(resp, session_id="ses_rs", question="count")
    assert env["badge"] == "L2_VALIDATED"
    for key in ("served_route", "ladder_rung", "plan_origin"):
        assert key not in env, key
    assert route_of(env) == "unattributed"
    assert _grade52_path(env) == "unattributed"


def test_cortex_l1_badge_is_the_l1_rung() -> None:
    resp = AskResponse(
        answer="Found 1 row(s).",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        sql_used="SELECT 1 AS n",
        rows=[{"n": 1}],
        values=[{"id": "v0", "value": 1.0, "label": "n"}],
        route="metric",
    )
    env = map_ask_response_to_envelope(resp, session_id="ses_rs", question="count")
    assert env["abstained"] is False
    assert env["served_route"] == ROUTE_L1
    assert env["ladder_rung"] == "l1"
    assert "plan_origin" not in env
    assert route_of(env) == ROUTE_L1
    assert _grade52_path(env) == "L1"


def test_stamp_route_does_not_relabel_a_no_sql_abstain() -> None:
    env: dict[str, Any] = {
        "abstained": True,
        "sql_used": None,
        "served_route": "abstain:envelope",
        "ladder_rung": "abstain:envelope",
    }
    stamp_route(env, ROUTE_L1)
    assert env["served_route"] == "abstain:envelope"
    assert env["ladder_rung"] == "abstain:envelope"
    assert no_sql_route_violation(env) == ""
    ran = {"abstained": True, "sql_used": "SELECT 1"}
    stamp_route(ran, ROUTE_L1)
    assert ran["served_route"] == ROUTE_L1


def test_rule_serve_is_not_stamped_generated() -> None:
    """Compile stamps compile, ladder stamps ladder, and neither stays generated."""
    compiled = {"abstained": False, "sql_used": "SELECT 1", "route": "generated"}
    stamp_route(compiled, ROUTE_COMPILE)
    assert compiled["route"] == "compile"
    assert compiled["ladder_rung"] == "compile"
    assert compiled["served_route"] == ROUTE_COMPILE
    assert route_class(compiled["route"]) == route_class(compiled["served_route"]) == "compile"
    ladder = {"abstained": False, "sql_used": "SELECT 1", "route": "generated"}
    stamp_route(ladder, ROUTE_LADDER)
    assert ladder["route"] == "ladder"
    assert ladder["ladder_rung"] == "ladder"
    assert route_class(ladder["route"]) == route_class(ladder["served_route"]) == "ladder"
    model = {
        "abstained": False,
        "sql_used": "SELECT 1",
        "route": "generated",
        "served_model": "openai/gpt-oss-120b",
    }
    stamp_route(model, ROUTE_MODEL)
    assert model["route"] == "generated"
    assert route_class(model["route"]) == route_class(model["served_route"]) == "ai"
    assert route_of(ladder) == ROUTE_LADDER
    assert route_of(compiled) == ROUTE_COMPILE


def test_route_of_reads_the_builder_stamp() -> None:
    compile_with_model = {
        "served_route": "ontology_compile",
        "ladder_rung": "compile",
        "plan_origin": "ontology_ranking",
        "served_model": "model-305-unique",
    }
    assert route_of(compile_with_model) == "ontology_compile"
    assert _grade52_path(compile_with_model) == "compile"
    model = {
        "served_route": "model",
        "ladder_rung": "generate_sql",
        "plan_origin": "generate_sql",
        "served_model": "model-305-unique",
    }
    assert route_of(model) == "model"
    assert _grade52_path(model) == "ai"
    assert route_of({**model, "served_model": ""}) == "contradictory"
    assert route_of({**model, "plan_origin": "ontology_ranking"}) == "contradictory"
    assert route_of({"ladder_rung": "compile"}) == "contradictory"
    assert route_of({"served_route": "ontology_compile", "ladder_rung": "l1"}) == "contradictory"


def _assert_grant_miss_stays_direct(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        if row["id"] not in _DIRECT_GRANT:
            continue
        seen.add(row["id"])
        env = row["env"]
        assert env.get("abstained") is True, row["id"]
        assert "reconfirm" not in env, row["id"]
        assert env.get("abstain_reason") in {
            "ungranted",
            "sql_relation_not_granted",
        }, row["id"]
    assert seen == _DIRECT_GRANT


def test_copy_only_52_flags_off_one_route(tmp_path: Path) -> None:
    rows = _serve(tmp_path, flags_on=False)
    _assert_one_route(rows)
    _assert_no_sql_is_abstain_stage(rows)
    _assert_grant_miss_stays_direct(rows)
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
    _assert_no_sql_is_abstain_stage(rows)
    _assert_grant_miss_stays_direct(rows)
    _assert_main_bytes(rows, "copy_on.json")
    l1 = [
        row["id"]
        for row in rows
        if row["env"].get("badge") == "L1_GOVERNED_METRIC" and row["env"].get("abstained") is False
    ]
    abstain = [row["id"] for row in rows if row["env"].get("abstained") is True]
    assert len(l1) == 16
    assert len(abstain) == 36
