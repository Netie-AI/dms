"""FDE-ONTO-STEWARD-01: the armed FDE path compiles like grade52, approval stays human.

All cases use the in-memory ontology store; no Postgres, OpenVault or Cortex.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.onto_data import OntologyDataStore

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "fde"))
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import fde_pipeline as fp  # noqa: E402
import onto_compile  # noqa: E402

HUMAN = "162994ce-d61d-4363-8601-3654bece12af"
DEFAULT_ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
REF = "steward=founder Jian Hong, test"


def _body() -> dict[str, Any]:
    return {
        "status": "proposed",
        "served": False,
        "vendor_sha": "fde-onto-steward-01",
        "terms": [
            {
                "name": "Customer",
                "table": "customers",
                "description": "buyer",
                "attributes": [],
                "key": ["id"],
            }
        ],
    }


def _args(**kw: Any) -> argparse.Namespace:
    base = {
        "dms": "armed",
        "compile": True,
        "onto_db_env": "FDE_ONTO_TEST_URL",
        "steward_approved_by": "",
        "approval_ref": "",
        "approval_file": "",
    }
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def compiled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for the OpenVault compile: store a fixed proposal, never approve."""
    calls: list[dict[str, Any]] = []

    def fake(
        warehouse: Path, tenant: str, *, transport: Any = None, store: Any = None, dest: Any = None
    ) -> dict[str, Any]:
        stored = store.store_proposal(_body(), created_by=HUMAN, tenant_id=tenant)
        calls.append({"warehouse": str(warehouse), "tenant": tenant})
        return {
            "version_id": stored["version_id"],
            "counts": {"object": 1},
            "model_calls": 1,
        }

    monkeypatch.setattr(onto_compile, "compile_warehouse", fake)
    return calls


def _report(tmp_path: Path) -> dict[str, Any]:
    (tmp_path / "t1").mkdir(exist_ok=True)
    return {"tenant": "t1", "warehouse": str(tmp_path / "t1" / "serve.duckdb")}


def test_default_is_pending_and_never_approves(tmp_path: Path, compiled: list[Any]) -> None:
    store = OntologyDataStore()
    res = fp.stage_steward(_report(tmp_path), tmp_path, _args(), {}, store=store)
    assert res["status"] == "PENDING_STEWARD"
    assert res["compiled"] == "new" and res["version_id"]
    assert compiled and compiled[0]["tenant"] == fp.onto_tenant_id("t1")
    assert list(store.approved_items()) == []
    fails = fp.steward_failures(res)
    assert fails and "PENDING_STEWARD" in fails[0] and "not asked" in fails[0]


def test_explicit_flag_approves_with_actor_and_ref(tmp_path: Path, compiled: list[Any]) -> None:
    store = OntologyDataStore()
    args = _args(steward_approved_by=HUMAN, approval_ref=REF)
    res = fp.stage_steward(_report(tmp_path), tmp_path, args, {}, store=store)
    assert res["status"] == "APPROVED", res
    assert res["approval_source"] == "flag"
    assert res["approved_by"] == HUMAN and res["approval_ref"] == REF
    assert res["approved_items"] >= 1
    assert {str(i.get("approved_by")) for i in store.approved_items()} == {HUMAN}
    assert fp.steward_failures(res) == []


def test_default_actor_is_refused(tmp_path: Path, compiled: list[Any]) -> None:
    store = OntologyDataStore()
    args = _args(steward_approved_by=DEFAULT_ACTOR, approval_ref=REF)
    res = fp.stage_steward(_report(tmp_path), tmp_path, args, {}, store=store)
    assert res["status"] == "APPROVAL_REFUSED"
    assert "default_actor" in res["detail"]
    assert list(store.approved_items()) == []


def test_recorded_file_approves_only_its_exact_version(tmp_path: Path, compiled: list[Any]) -> None:
    store = OntologyDataStore()
    first = fp.stage_steward(_report(tmp_path), tmp_path, _args(), {}, store=store)
    assert first["status"] == "PENDING_STEWARD"
    f = tmp_path / "approvals.json"
    entry = {"tenant": "t1", "version_id": first["version_id"], "actor": HUMAN, "approval_ref": REF}
    f.write_text(json.dumps({"approvals": [entry]}))
    recorded = fp.load_approval_file(str(f))
    n_compiles = len(compiled)
    res = fp.stage_steward(
        _report(tmp_path), tmp_path, _args(approval_file=str(f)), recorded, store=store
    )
    assert res["status"] == "APPROVED" and res["approval_source"] == "file"
    assert res["compiled"] == "reused" and res["version_id"] == first["version_id"]
    assert len(compiled) == n_compiles  # the approved version, not a fresh compile


def test_file_entry_for_another_version_does_not_approve() -> None:
    recorded = {"t1": {"version_id": "v-old", "actor": HUMAN, "approval_ref": REF}}
    assert fp.steward_decision("t1", "v-new", _args(), recorded) is None
    assert fp.steward_decision("t2", "v-new", _args(), recorded) is None
    assert (
        fp.steward_decision("t1", None, _args(steward_approved_by=HUMAN, approval_ref=REF), {})
        is None
    )


def test_approval_file_entry_missing_field_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"approvals": [{"tenant": "t1", "version_id": "v", "actor": HUMAN}]}))
    with pytest.raises(SystemExit, match="approval_ref"):
        fp.load_approval_file(str(f))


def test_compile_refusal_is_named_not_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*a: Any, **k: Any) -> dict[str, Any]:
        raise SystemExit("onto_compile: REFUSED openvault_unknown_role")

    monkeypatch.setattr(onto_compile, "compile_warehouse", refuse)
    res = fp.stage_steward(
        _report(tmp_path),
        tmp_path,
        _args(steward_approved_by=HUMAN, approval_ref=REF),
        {},
        store=OntologyDataStore(),
    )
    assert res["status"] == "COMPILE_REFUSED"
    assert fp.steward_failures(res) == [
        "ontology COMPILE_REFUSED: onto_compile: REFUSED openvault_unknown_role"
    ]


def test_config_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FDE_ONTO_TEST_URL", raising=False)
    assert fp.compile_config_error(_args(dms="none")) == "--compile needs --dms armed"
    assert "FDE_ONTO_TEST_URL" in (fp.compile_config_error(_args()) or "")
    monkeypatch.setenv("FDE_ONTO_TEST_URL", "postgresql://x")
    assert fp.compile_config_error(_args()) is None
    assert (
        fp.compile_config_error(_args(steward_approved_by=HUMAN))
        == "--steward-approved-by needs --approval-ref"
    )
    assert (
        fp.compile_config_error(_args(compile=False, approval_file="f"))
        == "steward approval needs --compile"
    )


def test_onto_env_is_scoped_and_tenant_is_not_box_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDE_ONTO_TEST_URL", "postgresql://scratch")
    monkeypatch.delenv("DMS_TENANT_ID", raising=False)
    env = fp.onto_env(_args(), "t1")
    assert env["DMS_AO_COMPILE"] == env["DMS_ONTO_PG"] == env["DMS_ONTO_FROM_DATA"] == "1"
    assert env["DMS_TENANT_ID"] == fp.onto_tenant_id("t1") != fp.onto_tenant_id("t2")
    assert env["DMS_TENANT_ID"] != "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with fp.scoped_env(env):
        assert os.environ["DATABASE_URL"] == "postgresql://scratch"
    assert "DMS_TENANT_ID" not in os.environ


def test_reason_codes_are_counted_per_question() -> None:
    cases = [
        {"verdict": "RECONFIRM", "reconfirm": {"reason_code": "insights_timeout"}},
        {"verdict": "RECONFIRM", "reconfirm": {"reason_code": "insights_timeout"}},
        {"verdict": "ABSTAIN", "abstain_reason": "out_of_scope"},
        {"verdict": "CORRECT"},
    ]
    assert fp.reason_code_counts(cases) == {
        "insights_timeout": 2,
        "out_of_scope": 1,
        "verdict:CORRECT": 1,
    }


def test_store_error_is_named_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise PermissionError("permission denied to set role")

    monkeypatch.setattr(onto_compile, "compile_warehouse", boom)
    res = fp.stage_steward(_report(tmp_path), tmp_path, _args(), {}, store=OntologyDataStore())
    assert res["status"] == "COMPILE_ERROR"
    assert res["detail"].startswith("PermissionError")
    assert fp.steward_failures(res)[0].startswith("ontology COMPILE_ERROR")
