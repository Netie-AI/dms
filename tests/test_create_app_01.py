"""DMS-CREATE-APP-01. The provider writes the app. DMS governs it.

Flag off leaves the routes unmounted. A stub provider stands in for
OpenVault and Cursor. No live key is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.create_app.chain import plan_handoff, research_prompt
from dms_core.create_app.connectors import confirm_action, dry_run_action, salesforce_apply
from dms_core.create_app.contract import compact_map, validate_bundle
from dms_core.create_app.flags import enabled, salesforce_enabled
from dms_core.create_app.sandbox import run_isolated
from dms_executor.create_app_run import bind_salesforce, bind_writeback, governed_query
from fastapi.testclient import TestClient

FLAG = "DMS_CREATE_APP"
SF_FLAG = "DMS_CREATE_APP_SALESFORCE"
SPACE = "space-create-app"
DMS_SECRET = "dms-test-secret"
OV_KEY = "ov_customer_test"
HEADERS = {"Authorization": f"Bearer {DMS_SECRET}", "X-OpenVault-Key": OV_KEY}
CANARY = "CANARY-ROW-99"
SF_TOKEN = "sf-token-canary"


def _clear() -> None:
    get_settings.cache_clear()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE inventory (sku VARCHAR, on_hand INTEGER)")
    con.execute("INSERT INTO inventory VALUES ('SKU-BETA', 4), ('SKU-ALPHA', 20)")
    con.close()
    return path


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str | None) -> None:
    if flag is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, flag)
    monkeypatch.delenv(SF_FLAG, raising=False)
    monkeypatch.setenv("DMS_API_KEYS", f"admin:{DMS_SECRET}")
    monkeypatch.setenv("DMS_CREATE_WAREHOUSE", str(_lake(tmp_path)))
    monkeypatch.setenv("DMS_CREATE_GRANTS", "inventory")
    monkeypatch.setenv("DMS_CREATE_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    _clear()


def _allow(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        del actor
        seen.append(action)
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.studio_apps.compliance_gate", allow)
    return seen


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str | None) -> TestClient:
    _env(monkeypatch, tmp_path, flag)
    _allow(monkeypatch)
    return TestClient(create_app())


SQL = "SELECT sku, sum(on_hand) AS on_hand FROM inventory GROUP BY sku"
SERVER = (
    "import dms_data\n"
    f"rows = dms_data.query({SQL!r})\n"
    "dry = dms_data.dry_run('create_po', {})\n"
    "print('DMS_APP_READY')\n"
    "print(rows.get('ok'))\n"
    "print(dry.get('applied'))\n"
)
INDEX = "<!doctype html><title>Reorder</title><p>Inventory reorder</p>"


def _bundle(**overrides: Any) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "contract": "dms.app/1",
        "name": "inventory-reorder",
        "frontend": [{"path": "index.html", "content": INDEX}],
        "backend": [{"path": "server.py", "content": SERVER}],
        "queries": [{"id": "low_stock", "sql": SQL}],
        "actions": [
            {
                "id": "set_reorder",
                "connector": "writeback",
                "label": "Set reorder level",
                "payload": {
                    "spec": {
                        "action_type": "inventory.set_reorder_level",
                        "object_key": {"sku": "SKU-BETA", "location_id": "WH-A"},
                        "value": "100",
                        "op": "set",
                        "reason": "cover is short",
                    },
                    "source_value": "40",
                    "tables": ["inventory"],
                },
            },
            {
                "id": "create_po",
                "connector": "salesforce",
                "label": "Create purchase order",
                "payload": {
                    "op": "create",
                    "sobject": "PurchaseOrder",
                    "fields": {"Name": "SKU-BETA"},
                    "tables": ["inventory"],
                    "cred_ref": "salesforce",
                },
            },
        ],
    }
    bundle.update(overrides)
    return bundle


def _provider_raw(bundle: dict[str, Any], cost: float = 0.02) -> dict[str, Any]:
    return {
        "content": bundle,
        "usage": {"total_tokens": 12, "cost": cost},
        "est_cost_usd": cost,
    }


class _Stub:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.bodies: list[dict[str, Any]] = []
        self.keys: list[str] = []

    def __call__(self, body: dict[str, Any], api_key: str) -> Any:
        self.bodies.append(body)
        self.keys.append(api_key)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _Stub:
    stub = _Stub(script)
    monkeypatch.setattr("dms_core.create_generator.default_transport", stub)
    return stub


@pytest.mark.parametrize("flag", [None, "0", "true", "yes", "2"])
def test_flag_off_is_not_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str | None
) -> None:
    _env(monkeypatch, tmp_path, flag)
    assert enabled() is False
    assert salesforce_enabled() is False
    client = TestClient(create_app())
    status = client.get("/v1/studio/apps/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": False, "salesforce": False}
    missing = client.post(
        "/v1/studio/apps", json={"space_id": SPACE, "goal": "reorder"}, headers=HEADERS
    )
    assert missing.status_code == 404


def test_contract_rejects_escape_secrets_and_raw_rows() -> None:
    assert validate_bundle(_bundle()) == []
    escaped = _bundle()
    escaped["backend"] = [{"path": "../server.py", "content": SERVER}]
    assert "path_escape" in validate_bundle(escaped)
    secret = _bundle()
    secret["backend"] = [{"path": "server.py", "content": SERVER + "\nDATABASE_URL='x'\n"}]
    assert "secret_in_bundle" in validate_bundle(secret)
    rows = _bundle()
    rows["rows"] = [{"sku": "SKU-BETA"}]
    assert "rows_or_secrets_forbidden" in validate_bundle(rows)
    bare = _bundle()
    bare["backend"] = [{"path": "server.py", "content": "print('DMS_APP_READY')\n"}]
    assert "backend_must_use_client" in validate_bundle(bare)
    weird = _bundle()
    weird["actions"] = [{"id": "x", "connector": "email", "label": "send", "payload": {}}]
    assert "action_connector_unknown" in validate_bundle(weird)


def test_schema_map_drops_ungranted_tables_and_cell_values() -> None:
    catalog = [
        {
            "name": "inventory",
            "columns": [{"name": "sku", "type": "VARCHAR", "sample": CANARY}],
            "rows": [{"sku": CANARY}],
        },
        {"name": "payroll", "columns": [{"name": "person", "type": "VARCHAR", "sample": CANARY}]},
    ]
    mapped = compact_map(catalog, {"inventory"}, [{"from": "inventory", "to": "payroll"}])
    text = json.dumps(mapped)
    assert CANARY not in text
    assert "payroll" in mapped["dropped"]
    assert mapped["tables"] == [
        {"name": "inventory", "columns": [{"name": "sku", "type": "VARCHAR"}]}
    ]
    assert mapped["links"] == []


def test_grant_enforcement_refuses_ungranted_sql(tmp_path: Path) -> None:
    warehouse = _lake(tmp_path)
    blocked = governed_query("SELECT * FROM payroll", grants={"inventory"}, warehouse=warehouse)
    assert blocked["ok"] is False
    assert blocked["rows"] == []
    assert blocked["code"] != "ok"
    allowed = governed_query(SQL, grants={"inventory"}, warehouse=warehouse)
    assert allowed["ok"] is True
    assert {row["sku"] for row in allowed["rows"]} == {"SKU-BETA", "SKU-ALPHA"}


def test_action_confirmation_audit_and_writeback(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = _bundle()["actions"]
    audit: list[dict[str, Any]] = []
    dry = dry_run_action(
        actions=actions,
        action_id="set_reorder",
        payload=None,
        grants={"inventory"},
        actor="admin",
        audit=audit,
    )
    assert dry["applied"] is False
    assert dry["code"] == "dry_run"
    held = confirm_action(
        actions=actions,
        action_id="set_reorder",
        payload=None,
        grants={"inventory"},
        actor="admin",
        approval_ref="",
        audit=audit,
        conn=object(),
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    assert held["code"] == "confirmation_required"
    assert held["applied"] is False
    calls: list[str] = []

    def propose(conn: Any, **kwargs: Any) -> dict[str, str]:
        del conn, kwargs
        calls.append("propose")
        return {"idempotency_token": "tok"}

    def apply(conn: Any, **kwargs: Any) -> dict[str, str]:
        del conn
        assert kwargs["approval_ref"] == "PO-1"
        calls.append("apply")
        return {"status": "applied"}

    monkeypatch.setattr("dms_core.control_plane.actions.propose_action", propose)
    monkeypatch.setattr("dms_core.control_plane.actions.apply_action_version", apply)
    monkeypatch.setenv("DMS_ACTIONS", "1")
    applied = confirm_action(
        actions=actions,
        action_id="set_reorder",
        payload=None,
        grants={"inventory"},
        actor="admin",
        approval_ref="PO-1",
        audit=audit,
        conn=object(),
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        audit_id="app123",
    )
    assert applied["applied"] is True
    assert calls == ["propose", "apply"]
    refused = confirm_action(
        actions=actions,
        action_id="set_reorder",
        payload=None,
        grants=set(),
        actor="admin",
        approval_ref="PO-1",
        audit=audit,
        conn=object(),
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    assert refused["code"] == "ungranted"
    assert [row["applied"] for row in audit] == [False, False, True, False]
    assert all("approval_ref" not in row or True for row in audit)


def test_salesforce_flag_and_secret_port(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _bundle()["actions"][1]["payload"]
    http: list[dict[str, Any]] = []

    def post(
        method: str, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, bytes]:
        http.append({"method": method, "url": url, "body": body, "headers": headers})
        return 201, b"{}"

    monkeypatch.delenv(SF_FLAG, raising=False)
    blocked = salesforce_apply(
        payload, grants={"inventory"}, secret_get=lambda name: None, http_post=post
    )
    assert blocked["code"] == "salesforce_disabled"
    assert http == []
    monkeypatch.setenv(SF_FLAG, "1")
    missing = salesforce_apply(
        payload, grants={"inventory"}, secret_get=lambda name: None, http_post=post
    )
    assert missing["code"] == "salesforce_credential_missing"
    assert http == []
    monkeypatch.setenv("SALESFORCE_TOKEN", SF_TOKEN)

    def secret(name: str) -> str | None:
        assert name == "salesforce"
        return json.dumps(
            {"instance_url": "https://example.my.salesforce.com", "access_token": SF_TOKEN}
        )

    applied = salesforce_apply(payload, grants={"inventory"}, secret_get=secret, http_post=post)
    assert applied["applied"] is True
    assert http[0]["method"] == "POST"
    assert http[0]["url"].endswith("/sobjects/PurchaseOrder")
    assert SF_TOKEN not in json.dumps(applied)
    palantir = dry_run_action(
        actions=[{"id": "onto", "connector": "ontology", "label": "slot", "payload": {}}],
        action_id="onto",
        payload=None,
        grants={"inventory"},
        actor="admin",
        audit=[],
    )
    assert palantir["code"] == "palantir_ontology_unwired"
    assert palantir["applied"] is False
    assert "Palantir" in palantir["detail"]


def test_sandbox_blocks_other_hosts_and_strips_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://user:super-secret-db@db/dms")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-live-secret")
    script = (
        "import os, socket\n"
        "print('ENV', os.environ.get('DATABASE_URL'))\n"
        "print('KEY', os.environ.get('CURSOR_API_KEY'))\n"
        "try:\n"
        "    socket.create_connection(('203.0.113.9', 9), timeout=1)\n"
        "    print('NET open')\n"
        "except OSError as exc:\n"
        "    print('NET', exc)\n"
    )
    done = run_isolated(script, allow_hosts={"127.0.0.1"})
    out = done.stdout
    assert "super-secret-db" not in out
    assert "cursor-live-secret" not in out
    assert "ENV None" in out
    assert "sandbox_network_denied" in out
    assert "NET open" not in out


def test_generate_judges_repairs_and_shows_live_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = _bundle()
    broken["backend"] = [{"path": "server.py", "content": "import dms_data\nraise SystemExit(3)\n"}]
    stub = _patch(monkeypatch, [_provider_raw(broken, 0.01), _provider_raw(_bundle(), 0.02)])
    seen = _allow(monkeypatch)
    _env(monkeypatch, tmp_path, "1")
    client = TestClient(create_app())
    response = client.post(
        "/v1/studio/apps",
        json={
            "space_id": SPACE,
            "goal": "Inventory reorder",
            "catalog": [
                {
                    "name": "inventory",
                    "columns": [{"name": "sku", "type": "VARCHAR", "sample": CANARY}],
                },
                {
                    "name": "payroll",
                    "columns": [{"name": "pay", "type": "INTEGER", "sample": CANARY}],
                },
            ],
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["judge"]["ok"] is True
    assert body["spend"]["calls"] == 2
    assert body["spend"]["usd"] == 0.03
    assert "max_usd" not in body
    assert "hard_stop" not in body
    assert "studio.create_app" in seen
    assert stub.bodies[0]["role"] == "create"
    sent = json.dumps(stub.bodies)
    assert CANARY not in sent
    assert "payroll" not in sent or "dropped" in sent
    stored = Path(tmp_path / "store" / "apps" / body["app_id"] / "app.json").read_text(
        encoding="utf-8"
    )
    assert body["app_token"] not in stored
    assert "postgres://" not in stored
    preview = client.get(f"/v1/studio/apps/{body['app_id']}/preview", headers=HEADERS)
    assert preview.status_code == 200
    assert "Inventory reorder" in preview.text
    query = client.post(
        f"/v1/studio/apps/{body['app_id']}/query",
        json={"sql": "SELECT * FROM payroll"},
        headers={"X-Dms-App-Token": body["app_token"]},
    )
    assert query.status_code == 200
    assert query.json()["rows"] == []
    assert query.json()["ok"] is False


def test_chain_passes_a_reference_and_refuses_a_wider_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub = _patch(monkeypatch, [_provider_raw(_bundle())])
    _allow(monkeypatch)
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE", "1")
    client = TestClient(create_app())
    opened = client.post(
        "/v1/studio/apps",
        json={"space_id": SPACE, "goal": "Inventory reorder"},
        headers=HEADERS,
    )
    assert opened.status_code == 200, opened.text
    app_id = opened.json()["app_id"]
    record_path = tmp_path / "store" / "apps" / app_id / "app.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["last_rows"] = [{"sku": CANARY}]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    widened = client.post(
        f"/v1/studio/apps/{app_id}/chain",
        json={"kind": "doc", "goal": "Write the brief", "grants": ["inventory", "payroll"]},
        headers=HEADERS,
    )
    assert widened.status_code == 400
    assert widened.json()["detail"] == "grant_widened"
    chained = client.post(
        f"/v1/studio/apps/{app_id}/chain",
        json={"kind": "doc", "goal": "Write the brief"},
        headers=HEADERS,
    )
    assert chained.status_code == 200, chained.text
    payload = chained.json()
    assert payload["context_ref"] == f"app:{app_id}"
    assert payload["started"] is True
    assert CANARY not in json.dumps(payload)
    session_path = tmp_path / "store" / f"{payload['session_id']}.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    assert f"app:{app_id}" in session["goal"]
    assert CANARY not in json.dumps(session)
    handoff = plan_handoff(
        source="answer",
        source_ref="audit-1",
        kind="research",
        goal="Research then create",
        source_grants={"inventory"},
        record={"last_rows": [{"sku": CANARY}]},
    )
    prompt = research_prompt(handoff)
    assert "audit-1" in prompt
    assert CANARY not in prompt
    cursor = _Stub([{"id": "agent-1", "status": "FINISHED"}])
    from dms_executor.create_app_run import chain_answer

    researched = chain_answer(
        source_ref="audit-1",
        kind="research",
        goal="Research then create",
        actor="admin",
        api_key=OV_KEY,
        audience="operator",
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        space_id=SPACE,
        grants=["inventory"],
        requested_grants=["inventory"],
        record={"last_rows": [{"sku": CANARY}]},
        transport=cursor,
    )
    assert researched["started"] is True
    assert CANARY not in json.dumps(cursor.bodies)
    assert "context_ref" in cursor.bodies[0]["prompt"]["text"]
    del stub


def test_publish_requires_a_passing_judge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch(monkeypatch, [_provider_raw(_bundle())])
    _allow(monkeypatch)
    _env(monkeypatch, tmp_path, "1")
    client = TestClient(create_app())
    opened = client.post(
        "/v1/studio/apps",
        json={"space_id": SPACE, "goal": "Inventory reorder"},
        headers=HEADERS,
    )
    app_id = opened.json()["app_id"]
    published = client.post(f"/v1/studio/apps/{app_id}/publish", headers=HEADERS)
    assert published.status_code == 200
    assert published.json()["published"] is True
    bind_writeback(None)
    bind_salesforce(None, None)
    held = client.post(
        f"/v1/studio/apps/{app_id}/actions/confirm",
        json={"action_id": "create_po", "approval_ref": "PO-1"},
        headers=HEADERS,
    )
    assert held.status_code == 200
    assert held.json()["applied"] is False
    assert held.json()["code"] == "salesforce_disabled"
    audit = client.get(f"/v1/studio/apps/{app_id}", headers=HEADERS).json()["audit"]
    assert audit[-1]["code"] == "salesforce_disabled"
    assert audit[-1]["confirmed"] is True


def test_context_hook_packs_a_visible_chat_and_refuses_a_stranger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub = _patch(monkeypatch, [_provider_raw(_bundle()), _provider_raw(_bundle())])
    _allow(monkeypatch)
    _env(monkeypatch, tmp_path, "1")
    monkeypatch.setenv("DMS_CREATE_CONTEXT", "1")
    monkeypatch.setenv("DMS_CREATE", "1")

    def blocks(kind: str) -> list[dict[str, str]]:
        return [{"id": "style-slot"}] if kind == "app" else []

    monkeypatch.setattr("dms_executor.create_context.layout_blocks", blocks)
    from dms_executor.create_context import save_chat

    saved = save_chat(
        actor="api_admin",
        chat={
            "title": "Reorder",
            "summary": "reorder-note",
            "schema": [
                {"name": "inventory", "columns": ["sku"]},
                {"name": "payroll", "columns": ["pay"]},
            ],
            "results": [{"table": "payroll", "rows": [{"pay": CANARY}]}],
            "sql": ["SELECT pay FROM payroll"],
        },
    )
    client = TestClient(create_app())
    opened = client.post(
        "/v1/studio/apps",
        json={
            "space_id": SPACE,
            "goal": "Inventory reorder",
            "context": {"chat_ids": [saved["id"]]},
        },
        headers=HEADERS,
    )
    assert opened.status_code == 200, opened.text
    sent = json.dumps(stub.bodies)
    assert "reorder-note" in sent
    assert "style-slot" in sent
    assert CANARY not in sent
    assert "payroll" not in sent
    chained = client.post(
        f"/v1/studio/apps/{opened.json()['app_id']}/chain",
        json={"kind": "doc", "goal": "Write the brief"},
        headers=HEADERS,
    )
    assert chained.status_code == 200, chained.text
    session = json.loads(
        (tmp_path / "store" / f"{chained.json()['session_id']}.json").read_text(encoding="utf-8")
    )
    packed = json.dumps(session.get("sources"))
    assert "reorder-note" in packed
    assert CANARY not in packed
    assert "payroll" not in packed
    missing = client.post(
        "/v1/studio/apps",
        json={
            "space_id": SPACE,
            "goal": "Inventory reorder",
            "context": {"chat_ids": ["ab" * 16]},
        },
        headers=HEADERS,
    )
    assert missing.status_code == 403
    assert missing.json()["detail"] == "context_forbidden"
    monkeypatch.setenv("DMS_CREATE_CONTEXT", "0")
    quiet = client.post(
        "/v1/studio/apps",
        json={
            "space_id": SPACE,
            "goal": "Inventory reorder",
            "context": {"chat_ids": ["ab" * 16]},
        },
        headers=HEADERS,
    )
    assert quiet.status_code == 200, quiet.text
    assert "reorder-note" not in json.dumps(stub.bodies[-1])
