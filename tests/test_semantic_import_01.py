"""FEAT-SEMANTIC-IMPORT-01: one-shot semantic model import.

Flag off, the routes are absent and the ask prompt is unchanged. Flag on,
a steward upload is a proposal. Ungranted SQL is needs_grant and is not
executed. A DELETE is the read-only gate. OSI export then import matches.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_core.control_plane.onto_data import (
    approved_items,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_executor.ask_ladder import richer_context_block
from dms_executor.semantic_import import (
    SemanticImportDisabled,
    approve_import_examples,
    audit_records,
    import_file,
    parse_bytes,
    reset_audit,
)
from dms_executor.semantic_import.normalize import canonical
from dms_executor.semantic_import.parse_dbt import parse_dbt
from dms_executor.semantic_import.parse_osi import to_osi
from dms_executor.semantic_import.store import (
    benchmark_rows,
    example_rows,
    reset_state,
    write_benchmarks,
    write_examples,
)
from dms_executor.skills_quarantine import WRITE_BLOCKED, item_content_hash
from dms_executor.verified_queries import (
    _rows_for_space,
    examples_text,
    lookup_verified_query,
    retrieve_verified_examples,
)
from fastapi.testclient import TestClient

FLAG = "DMS_SEMANTIC_IMPORT"
SPACE = "space-semantic-import-01"
ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROOT = Path(__file__).resolve().parent / "fixtures" / "semantic_import"
FORMATS = (
    ("snowflake", "snowflake.yaml"),
    ("osi", "osi.json"),
    ("genie", "genie.json"),
    ("dbt", "dbt.json"),
    ("wren", "wren.json"),
)
DELETE_TOKEN = "zz-delete-token"
MISSING_TABLE = "payroll_zz"
SECRET = "sk-test-secret-value"
CELL = "ada-secret-cell"
BENCH = "bench-token-zz"


def _clear_settings() -> None:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    _clear_settings()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "import.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders (order_id INTEGER, amount INTEGER, status VARCHAR)")
    con.execute("INSERT INTO orders VALUES (1, 10, 'paid'), (2, 5, 'paid')")
    con.execute("CREATE TABLE customers (customer_id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO customers VALUES (7, ?)", [CELL])
    con.close()
    return path


def _document() -> bytes:
    body = {
        "version": "0.1.1",
        "semantic_model": {
            "name": "sales",
            "description": SECRET,
            "ai_context": {"instructions": ["Prefer paid orders."]},
            "datasets": [
                {
                    "name": "orders",
                    "source": "orders",
                    "description": "Order rows",
                    "primary_key": ["order_id"],
                    "fields": [
                        {
                            "name": "order_id",
                            "expression": {"sql": "order_id"},
                            "dimension": {"is_time": False},
                        },
                        {
                            "name": "amount",
                            "expression": {"sql": "amount"},
                            "label": "fact",
                        },
                        {
                            "name": "status",
                            "expression": {"sql": "status"},
                            "dimension": {"is_time": False},
                        },
                        {
                            "name": "missing_col",
                            "expression": {"sql": "missing_col"},
                            "dimension": {"is_time": False},
                        },
                    ],
                    "metrics": [
                        {
                            "name": "total_amount",
                            "expression": {"sql": "SUM(amount)"},
                            "description": "Total amount",
                        }
                    ],
                },
                {
                    "name": "customers",
                    "source": "customers",
                    "primary_key": ["customer_id"],
                    "fields": [
                        {
                            "name": "customer_id",
                            "expression": {"sql": "customer_id"},
                            "dimension": {"is_time": False},
                        }
                    ],
                },
            ],
            "relationships": [
                {
                    "name": "orders_customers",
                    "from": "orders",
                    "to": "customers",
                    "from_columns": ["customer_id"],
                    "to_columns": ["customer_id"],
                }
            ],
            "custom_extensions": [
                {
                    "vendor_name": "DMS",
                    "data": {
                        "filters": [
                            {
                                "name": "is_paid",
                                "expr": "status = 'paid'",
                                "description": "Paid rows",
                                "synonyms": [],
                                "table": "orders",
                            }
                        ],
                        "verified_queries": [
                            {
                                "name": "paid_count",
                                "question": "how many orders",
                                "sql": "SELECT COUNT(*) AS n FROM orders",
                                "onboarding": False,
                            },
                            {
                                "name": "missing_query",
                                "question": "how many missing",
                                "sql": f"SELECT COUNT(*) AS n FROM {MISSING_TABLE}",
                                "onboarding": False,
                            },
                            {
                                "name": "remove_rows",
                                "question": "remove rows",
                                "sql": f"DELETE FROM orders WHERE status = '{DELETE_TOKEN}'",
                                "onboarding": False,
                            },
                        ],
                        "benchmarks": [
                            {
                                "name": "bench_count",
                                "question": BENCH,
                                "sql": "SELECT COUNT(*) AS n FROM orders",
                            }
                        ],
                        "onboarding": ["how many orders"],
                    },
                }
            ],
        },
    }
    return json.dumps(body).encode()


def _ten() -> bytes:
    words = (
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
        "foxtrot",
        "golf",
        "hotel",
        "india",
        "juliet",
    )
    queries = [
        {
            "name": f"q{word}",
            "question": f"how many orders {word}",
            "sql": "SELECT COUNT(*) AS n FROM orders",
            "onboarding": False,
        }
        for word in words
    ]
    body = {
        "version": "0.1.1",
        "semantic_model": {
            "name": "sales",
            "description": "Ten examples",
            "datasets": [
                {
                    "name": "orders",
                    "source": "orders",
                    "primary_key": ["order_id"],
                    "fields": [
                        {
                            "name": "order_id",
                            "expression": {"sql": "order_id"},
                            "dimension": {"is_time": False},
                        }
                    ],
                }
            ],
            "custom_extensions": [{"vendor_name": "DMS", "data": {"verified_queries": queries}}],
        },
    }
    return json.dumps(body).encode()


def _allow(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        seen.setdefault("actions", []).append(action)
        seen["actor"] = actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.semantic_import.compliance_gate", allow)
    return seen


def _grants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dms_executor.semantic_import.service._grants",
        lambda _space, _warehouse, grantable: (
            {"orders", "customers"} if grantable is None else set(grantable)
        ),
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_ontology_data_store()
    reset_audit()
    reset_state()
    yield
    reset_ontology_data_store()
    reset_audit()
    reset_state()


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    from dms_api.routes.semantic_import import enabled

    assert enabled() is False


def test_flag_off_routes_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    client = TestClient(create_app())
    for method, path in (
        ("post", f"/v1/spaces/{SPACE}/semantic-import"),
        ("post", f"/v1/spaces/{SPACE}/semantic-import/approve"),
        ("get", f"/v1/spaces/{SPACE}/semantic-export"),
    ):
        res = client.post(path) if method == "post" else client.get(path)
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(("fmt", "name"), FORMATS)
def test_format_parses_to_golden(fmt: str, name: str) -> None:
    model = parse_bytes((ROOT / name).read_bytes())
    golden = json.loads((ROOT / f"{fmt}.golden.json").read_text(encoding="utf-8"))
    assert model["format"] == fmt
    assert canonical(model) == golden


def test_dbt_osi_document_uses_the_osi_shape() -> None:
    doc = json.loads((ROOT / "osi.json").read_text(encoding="utf-8"))
    parsed = parse_dbt(doc)
    assert parsed["format"] == "dbt"
    direct = parse_bytes((ROOT / "osi.json").read_bytes())
    assert canonical(parsed)["entities"] == canonical(direct)["entities"]
    assert canonical(parsed)["measures"] == canonical(direct)["measures"]


def test_osi_round_trip() -> None:
    model = parse_bytes((ROOT / "osi.json").read_bytes())
    again = parse_bytes(json.dumps(to_osi(model)).encode())
    assert canonical(again) == canonical(model)


def test_flag_off_does_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    path = _lake(tmp_path)
    ran: list[str] = []

    def _boom(sql: str, warehouse: Path) -> tuple[None, str]:
        ran.append(sql)
        raise AssertionError(warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", _boom)
    with pytest.raises(SemanticImportDisabled):
        import_file(
            _document(),
            space_id=SPACE,
            dry_run=False,
            actor=ACTOR,
            warehouse=path,
            grantable={"orders", "customers"},
        )
    assert ran == []
    assert example_rows(path, SPACE) == []
    assert approved_items() == []


def test_ungranted_and_delete_do_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    ran: list[str] = []
    real = __import__("dms_executor.sql_loop", fromlist=["run_readonly"]).run_readonly

    def _spy(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return real(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", _spy)
    result = import_file(
        _document(),
        space_id=SPACE,
        dry_run=True,
        actor=ACTOR,
        warehouse=path,
        grantable={"orders", "customers"},
    )
    by_name = {item["name"]: item for item in result["items"] if item.get("name")}
    assert by_name["missing_query"]["status"] == "needs_grant"
    assert by_name["remove_rows"]["status"].startswith("gate_refused:")
    assert "statement_not_allowed" in by_name["remove_rows"]["status"]
    assert by_name["missing_col"]["status"] == "needs_grant"
    blob = json.dumps(result)
    assert DELETE_TOKEN not in blob
    assert MISSING_TABLE not in blob
    assert CELL not in blob
    assert SECRET not in blob
    assert all(DELETE_TOKEN not in sql and MISSING_TABLE not in sql for sql in ran)
    assert ran


def test_dry_run_writes_nothing_and_live_writes_proposals_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    kwargs = {
        "space_id": SPACE,
        "actor": ACTOR,
        "warehouse": path,
        "grantable": {"orders", "customers"},
    }
    preview = import_file(_document(), dry_run=True, **kwargs)
    assert preview["dry_run"] is True
    assert preview["served"] is False
    assert preview["version_id"] is None
    assert ontology_data_store().diff()["proposed_only"] == []
    assert example_rows(path, SPACE) == []
    written = import_file(_document(), dry_run=False, **kwargs)
    assert written["version_id"]
    proposed = ontology_data_store().diff(written["version_id"])["proposed_only"]
    assert proposed
    assert {row["status"] for row in proposed} == {"proposed"}
    assert {row["source"] for row in proposed} == {"import:osi"}
    assert approved_items() == []
    stored = example_rows(path, SPACE)
    assert stored
    assert {row["status"] for row in stored} == {"candidate"}
    assert {row["provenance"] for row in stored} == {"import:osi"}
    assert _rows_for_space(path, SPACE) == []
    assert (
        lookup_verified_query(
            "how many orders",
            space_id=SPACE,
            warehouse=path,
            grantable={"orders", "customers"},
            dialect="duckdb",
        )
        is None
    )


def test_approved_examples_are_retrieval_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    grants = {"orders", "customers"}
    written = import_file(
        _document(),
        space_id=SPACE,
        dry_run=False,
        actor=ACTOR,
        warehouse=path,
        grantable=grants,
    )
    ontology_data_store().approve_version(written["version_id"], ACTOR)
    assert approve_import_examples(space_id=SPACE, actor=ACTOR, warehouse=path) >= 1
    assert approved_items()
    assert all(row["source"] == "import:osi" for row in approved_items())
    assert (
        lookup_verified_query(
            "how many orders",
            space_id=SPACE,
            warehouse=path,
            grantable=grants,
            dialect="duckdb",
        )
        is None
    )
    monkeypatch.delenv(FLAG, raising=False)
    off = retrieve_verified_examples(
        "how many orders",
        space_id=SPACE,
        warehouse=path,
        grantable=grants,
        dialect="duckdb",
        force=True,
    )
    monkeypatch.setenv(FLAG, "1")
    on = retrieve_verified_examples(
        "how many orders",
        space_id=SPACE,
        warehouse=path,
        grantable=grants,
        dialect="duckdb",
        force=True,
    )
    assert examples_text(off) == ""
    assert "SELECT" in examples_text(on).upper()
    assert BENCH not in examples_text(on)
    monkeypatch.delenv("DMS_VQ_RAG", raising=False)
    gated = retrieve_verified_examples(
        "how many orders",
        space_id=SPACE,
        warehouse=path,
        grantable=grants,
        dialect="duckdb",
    )
    assert examples_text(gated) == ""
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    opened = retrieve_verified_examples(
        "how many orders",
        space_id=SPACE,
        warehouse=path,
        grantable=grants,
        dialect="duckdb",
    )
    assert "SELECT" in examples_text(opened).upper()


def test_ten_verified_queries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    result = import_file(
        _ten(),
        space_id=SPACE,
        dry_run=False,
        actor=ACTOR,
        warehouse=path,
        grantable={"orders"},
    )
    assert result["counts"].get("candidate") == 10
    assert result["elapsed_ms"] >= 0
    assert result["served"] is False
    assert approved_items() == []
    ontology_data_store().approve_version(result["version_id"], ACTOR)
    approved = approve_import_examples(space_id=SPACE, actor=ACTOR, warehouse=path)
    assert approved == 10
    measure = {
        "elapsed_ms": result["elapsed_ms"],
        "counts": result["counts"],
        "approved_examples": approved,
    }
    (tmp_path / "measure.json").write_text(json.dumps(measure), encoding="utf-8")
    # Kept beside the assertion so a reviewer can see the run, not a guessed table.
    print("SEMANTIC_IMPORT_MEASURE", json.dumps(measure))


def test_flag_off_prompt_and_route_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _lake(tmp_path)
    grants = {"orders", "customers"}
    ask = "how many orders"
    monkeypatch.setattr(
        "dms_executor.ask_ladder._column_samples",
        lambda *_a, **_k: ["orders.order_id: 1"],
    )

    def snap() -> tuple[bytes, Any]:
        prompt = richer_context_block(
            ask,
            None,
            warehouse=path,
            grantable=grants,
            dialect="duckdb",
            ontology=None,
            space_id=SPACE,
        )
        looked = lookup_verified_query(
            ask, space_id=SPACE, warehouse=path, grantable=grants, dialect="duckdb"
        )
        return prompt.encode(), looked

    _flag(monkeypatch, False)
    off_prompt, off_route = snap()
    _flag(monkeypatch, True)
    on_prompt, on_route = snap()
    assert off_prompt == on_prompt
    assert off_route is None and on_route is None
    import_file(
        _document(),
        space_id=SPACE,
        dry_run=False,
        actor=ACTOR,
        warehouse=path,
        grantable=grants,
    )
    _flag(monkeypatch, False)
    assert snap()[0] == off_prompt


def test_secret_stays_out_of_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    with caplog.at_level(logging.INFO, logger="dms_executor.semantic_import.service"):
        import_file(
            _document(),
            space_id=SPACE,
            dry_run=True,
            actor=ACTOR,
            warehouse=path,
            grantable={"orders", "customers"},
        )
    assert SECRET not in caplog.text
    assert CELL not in caplog.text
    assert DELETE_TOKEN not in caplog.text
    assert audit_records()
    assert SECRET not in json.dumps(audit_records())


def test_flag_off_openapi_drops_only_the_new_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    _flag(monkeypatch, True)
    on = create_app().openapi()
    _flag(monkeypatch, False)
    off = create_app().openapi()
    new_paths = {
        path for path in on["paths"] if "semantic-import" in path or "semantic-export" in path
    }
    assert new_paths
    stripped = copy.deepcopy(on)
    for path in new_paths:
        del stripped["paths"][path]
    schemas = stripped.get("components", {}).get("schemas", {})
    off_schemas = off.get("components", {}).get("schemas", {})
    for name in list(schemas):
        if name not in off_schemas:
            del schemas[name]
    assert json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def test_steward_upload_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    path = _lake(tmp_path)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    _clear_settings()
    _grants(monkeypatch)
    seen = _allow(monkeypatch)
    client = TestClient(create_app())
    raw = _document()
    dry = client.post(
        f"/v1/spaces/{SPACE}/semantic-import",
        files={"file": ("model.json", raw, "application/json")},
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True
    assert dry.json()["version_id"] is None
    assert ontology_data_store().diff()["proposed_only"] == []
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    _clear_settings()
    denied = client.post(
        f"/v1/spaces/{SPACE}/semantic-import",
        files={"file": ("model.json", raw, "application/json")},
        data={"dry_run": "false"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "steward_grant_required"
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    _clear_settings()
    saved = client.post(
        f"/v1/spaces/{SPACE}/semantic-import",
        files={"file": ("model.json", raw, "application/json")},
        data={"dry_run": "false"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["served"] is False
    assert body["version_id"]
    assert SECRET not in saved.text
    assert CELL not in saved.text
    assert DELETE_TOKEN not in saved.text
    proposed = ontology_data_store().diff(body["version_id"])["proposed_only"]
    assert proposed
    assert approved_items() == []
    ontology_data_store().approve_version(body["version_id"], ACTOR)
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    _clear_settings()
    denied_approve = client.post(f"/v1/spaces/{SPACE}/semantic-import/approve")
    assert denied_approve.status_code == 403
    assert denied_approve.json()["detail"] == "steward_grant_required"
    assert {row["status"] for row in example_rows(path, SPACE)} == {"candidate"}
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    _clear_settings()
    approved = client.post(f"/v1/spaces/{SPACE}/semantic-import/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved"] >= 1
    assert approved.json()["served"] is False
    assert {row["status"] for row in example_rows(path, SPACE)} == {"approved"}
    exported = client.get(f"/v1/spaces/{SPACE}/semantic-export", params={"format": "osi"})
    assert exported.status_code == 200, exported.text
    document = exported.json()
    assert document["version"] == "0.1.1"
    semantic = document["semantic_model"]
    assert "description" in semantic
    assert semantic["datasets"]
    assert semantic["datasets"][0]["primary_key"]
    assert semantic["datasets"][0]["fields"]
    assert semantic["relationships"]
    assert semantic["ai_context"]
    assert "metrics" in semantic["datasets"][0] or semantic.get("metrics") is not None
    bad = client.get(f"/v1/spaces/{SPACE}/semantic-export", params={"format": "tableau"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "export_format_unsupported"
    assert "semantic.import" in seen["actions"]
    assert "semantic.import.approve" in seen["actions"]
    assert "semantic.export" in seen["actions"]


def test_scored_sql_is_rejected_at_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    scored = "SELECT COUNT(*) AS n FROM orders"
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(scored))
    ran: list[str] = []
    real = __import__("dms_executor.sql_loop", fromlist=["run_readonly"]).run_readonly

    def _spy(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return real(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", _spy)
    result = import_file(
        _document(),
        space_id=SPACE,
        dry_run=False,
        actor=ACTOR,
        warehouse=path,
        grantable={"orders", "customers"},
    )
    by_name = {item["name"]: item for item in result["items"] if item.get("name")}
    refused = f"gate_refused:{WRITE_BLOCKED}"
    assert by_name["paid_count"]["status"] == refused
    assert by_name["bench_count"]["status"] == refused
    blob = json.dumps(result)
    assert scored not in blob
    assert example_rows(path, SPACE) == []
    assert benchmark_rows(path, SPACE) == []
    assert all("count(*)" not in sql.lower() for sql in ran)
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        write_examples(
            [{"name": "q", "question": "how many orders", "sql": scored}],
            space_id=SPACE,
            warehouse=path,
            actor=ACTOR,
            provenance="import:osi",
        )
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        write_benchmarks(
            [{"name": "b", "question": "bench", "sql": scored}],
            space_id=SPACE,
            warehouse=path,
        )
    assert example_rows(path, SPACE) == []
    assert benchmark_rows(path, SPACE) == []


def test_scored_measure_blocks_the_proposal_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash("SUM(amount)"))
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        import_file(
            _document(),
            space_id=SPACE,
            dry_run=False,
            actor=ACTOR,
            warehouse=path,
            grantable={"orders", "customers"},
        )
    assert example_rows(path, SPACE) == []
    assert benchmark_rows(path, SPACE) == []
    assert ontology_data_store().diff()["proposed_only"] == []


def test_scored_row_is_filtered_at_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    path = _lake(tmp_path)
    grants = {"orders", "customers"}
    scored = "SELECT COUNT(*) AS n FROM orders"
    keeper = "SELECT SUM(amount) AS n FROM orders"
    import_file(
        _document(),
        space_id=SPACE,
        dry_run=False,
        actor=ACTOR,
        warehouse=path,
        grantable=grants,
    )
    write_examples(
        [{"name": "keeper", "question": "how many orders", "sql": keeper}],
        space_id=SPACE,
        warehouse=path,
        actor=ACTOR,
        provenance="import:osi",
    )
    assert approve_import_examples(space_id=SPACE, actor=ACTOR, warehouse=path) >= 2
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(scored))
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    shown = examples_text(
        retrieve_verified_examples(
            "how many orders",
            space_id=SPACE,
            warehouse=path,
            grantable=grants,
            dialect="duckdb",
        )
    ).lower()
    assert "sum(" in shown
    assert "count(" not in shown
