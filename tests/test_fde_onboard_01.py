"""FDE-ONBOARD-01 (dms#541): one command onboards files into a Space and drafts its config.

Every case runs ``scripts/fde_onboard.py``'s ``main`` on the committed synthetic fixture
(``tests/fixtures/fde_onboard``), against a real lake, the real bronze ingest, the #514
profiler, the PII masker, the item store and ``POST /v1/chat/ask`` in process.

Two recorded fakes stand in for the network: the OpenVault transport (the model) and the
Cortex Insights leg (which returns, per question, the SQL the model proposed). Both are
recorded, not stubs of DMS code: everything between them is the product path.

Asserted on what leaves the machine (every pack file, every model request body) and on what
the steward and the customer receive (stored item status, the ask envelope after
``assert_envelope_valid``, its rows against truth read from the CSV files, rule 10a).
No skip, no xfail.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.control_plane.onto_data import (
    ONBOARD_SOURCE,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_executor import Executor, fde_ai, fde_pack
from dms_executor import demo_warehouse as dw
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from fastapi.testclient import TestClient
from test_onto_derive_01 import _gate_allows, _RecordingCortex, make_minter

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fde_onboard"
CANARIES: list[str] = json.loads((FIXTURE / "CANARIES.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location(
    "fde_onboard_cli", ROOT / "scripts" / "fde_onboard.py"
)
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
sys.modules["fde_onboard_cli"] = cli
_spec.loader.exec_module(cli)

ORDERS, CUSTOMERS, PRODUCTS = "bronze.orders", "bronze.customers", "bronze.products"


def _csv_rows(name: str) -> list[dict[str, str]]:
    with (FIXTURE / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# Truth from the CSV files, never from the lake or the SQL.
ORDER_COUNT = len(_csv_rows("orders.csv"))
CUSTOMER_COUNT = len(_csv_rows("customers.csv"))

Q_ORDERS = "How many orders are there?"
Q_CUSTOMERS = "How many customers are there?"
QUESTIONS = [
    (Q_ORDERS, f"SELECT COUNT(*) AS order_count FROM {ORDERS}"),
    (Q_CUSTOMERS, f"SELECT COUNT(*) AS customer_count FROM {CUSTOMERS}"),
    (
        "How many orders per region?",
        f"SELECT c.region, COUNT(*) AS n FROM {ORDERS} o JOIN {CUSTOMERS} c "
        "ON o.customer_id = c.customer_id GROUP BY c.region",
    ),
    ("How many OMEGA orders?", f"SELECT COUNT(*) AS n FROM {ORDERS} WHERE sku = 'OMEGA'"),
    ("What is the total order amount?", f"SELECT SUM(amount) AS total FROM {ORDERS}"),
    # A model that echoes a cell (prompt injection or a guess) is dropped, never packed.
    (f"{CANARIES[5]}?", f"SELECT COUNT(*) AS n FROM {ORDERS}"),
]
EXPECTED_DROPS = {
    2: "unverified_join:no_ontology",
    3: "literal_value",
    4: "inexact_result",
    5: "echoes_source_value",
}


def _draft() -> dict[str, Any]:
    return {
        "objects": [
            {"table": CUSTOMERS, "key": ["customer_id"], "description": "a customer"},
            {"table": ORDERS, "key": ["order_id"], "description": "an order"},
            {"table": "bronze.nope", "key": ["x"], "description": "unknown"},
        ],
        "links": [
            {
                "from_table": ORDERS,
                "from_columns": ["customer_id"],
                "to_table": CUSTOMERS,
                "to_columns": ["customer_id"],
            }
        ],
    }


@dataclass
class RecordedModel:
    """The OpenVault route, recorded. Answers by which fixed prompt the body carries."""

    bodies: list[dict[str, Any]] = field(default_factory=list)
    questions: list[tuple[str, str]] = field(default_factory=lambda: list(QUESTIONS))

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        self.bodies.append(json.loads(json.dumps(body)))
        system = body["messages"][0]["content"]
        prompts = fde_ai.load_prompts()
        if system == prompts["01_ontology_draft.md"]:
            return _draft()
        if system == prompts["02_verified_questions.md"]:
            return {"questions": [{"question": q, "sql": s} for q, s in self.questions]}
        if system == prompts["03_value_mappings.md"]:
            user = json.loads(body["messages"][1]["content"])
            groups = user["value_groups"]["items"]
            return {
                "decisions": [
                    {
                        "group_id": g["group_id"],
                        "same": True,
                        "target": g["target_mask"],
                        "reason": "same entity",
                    }
                    for g in groups
                ]
            }
        raise AssertionError("unknown prompt")


@dataclass
class _AskCortex(_RecordingCortex):
    """Cortex Insights, recorded: returns the SQL proposed for the question asked."""

    by_question: dict[str, str] = field(default_factory=dict)

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any]:
        self.insights.append({"question": question, **kw})
        return {
            "query_sql": self.by_question.get(question, "SELECT 1 WHERE false"),
            "plan_source": "ontology_plan",
        }


@dataclass
class Rig:
    client: TestClient
    cortex: _AskCortex
    lake: Path
    out: Path
    model: RecordedModel = field(default_factory=RecordedModel)

    def run(self, *extra: str, space: str = "Acme onboarding") -> tuple[int, dict[str, Any]]:
        buf = io.StringIO()
        code = cli.main(
            ["--source", str(FIXTURE), "--space", space, "--out", str(self.out), *extra],
            http=self.client,
            transport=self.model,
            stdout=buf,
        )
        summary = json.loads(buf.getvalue())
        assert summary["exit"] == code
        return code, summary

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))

    def stage(self, n: int) -> dict[str, Any]:
        return self.manifest()["stages"][n - 1]

    def pack_text(self) -> str:
        return "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(self.out.rglob("*")) if p.is_file()
        )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("DMS_ONTO_PG", "DMS_AI_FIRST", "DMS_ONTO_FROM_DATA", "DMS_SCHEMA_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENVAULT_URL", "http://openvault.test")
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_test_key")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


def _cortex_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The /health probes of a reachable Cortex, recorded."""
    import dms_api.routes.health as health

    ok = {"ok": True, "status": 200}
    monkeypatch.setattr(health, "_probe", lambda *a, **k: dict(ok))
    monkeypatch.setattr(health, "_probe_cortex_contract", lambda *a, **k: dict(ok))
    monkeypatch.setattr(health, "_probe_cortex_jwks_refresh", lambda *a, **k: dict(ok))
    monkeypatch.setattr(health, "_probe_openvault_trust", lambda *a, **k: dict(ok))


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Rig:
    lake = tmp_path / "fde_onboard.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    cortex = _AskCortex(warehouse=lake, payload={}, by_question=dict(QUESTIONS))
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return Rig(TestClient(app), cortex, lake, tmp_path / "pack")


def _lake_text(lake: Path) -> str:
    """Every landed cell of the three tables, as text, to prove the canaries landed."""
    import duckdb

    con = duckdb.connect(str(lake), read_only=True)
    try:
        cells = []
        for t in (ORDERS, CUSTOMERS, PRODUCTS):
            for row in con.execute(f"SELECT * EXCLUDE (_src, _ingest_id) FROM {t}").fetchall():
                cells.extend(str(v) for v in row)
    finally:
        con.close()
    return "\n".join(cells)


def _onboard_items(space_id: str) -> list[dict[str, Any]]:
    return ontology_data_store().space_onboard_items(space_id)


def _assert_no_canary(text: str) -> None:
    low = text.casefold()
    for canary in CANARIES:
        assert canary.casefold() not in low, f"canary in pack: {canary[:6]}..."
        assert json.dumps(canary)[1:-1].casefold() not in low


# --- offline: stages 1-4 ------------------------------------------------------------


def test_offline_run_builds_a_masked_pack_with_no_canary(rig: Rig) -> None:
    code, summary = rig.run("--stop-after", "4")
    assert code == 0, summary
    # The canaries really landed: the check below could fail.
    landed = _lake_text(rig.lake)
    for canary in CANARIES:
        assert canary in landed or canary.replace('"', "") in landed.replace('"', ""), canary
    m = rig.manifest()
    assert [s["status"] for s in m["stages"]] == ["done"] * 4 + ["not_requested"] * 2
    files = {str(p.relative_to(rig.out)) for p in rig.out.rglob("*.json")}
    assert {
        "manifest.json",
        "schema.json",
        "profile.json",
        "samples.masked.json",
        "proposals/column_types.json",
        "proposals/value_groups.json",
    } <= files
    _assert_no_canary(rig.pack_text())
    assert m["leak_check"]["status"] == "passed"


def test_manifest_holds_the_sha256_of_every_file_and_of_the_prompts(rig: Rig) -> None:
    import hashlib

    code, _ = rig.run("--stop-after", "4")
    assert code == 0
    m = rig.manifest()
    on_disk = {
        str(p.relative_to(rig.out)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in rig.out.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert {k: v["sha256"] for k, v in m["files"].items()} == on_disk
    assert m["prompts"]["version"] == fde_ai.PROMPT_VERSION
    for name, pinned in fde_ai.PROMPT_SHA256.items():
        actual = hashlib.sha256((fde_ai.PROMPT_DIR / name).read_bytes()).hexdigest()
        assert actual == pinned
        assert m["prompts"]["files"][name]["sha256"] == pinned


def test_every_config_item_lands_proposed_and_changes_no_approved_read(rig: Rig) -> None:
    code, summary = rig.run("--stop-after", "4")
    assert code == 0
    items = _onboard_items(summary["space_id"])
    kinds = {i["kind"] for i in items}
    assert {"column_type", "value_map"} <= kinds, kinds
    assert all(i["status"] == "proposed" and i["approved_by"] is None for i in items)
    assert all(i["source"] == ONBOARD_SOURCE for i in items)
    assert summary["store"]["approved"] == 0
    assert not [i for i in ontology_data_store().approved_items() if i["source"] == ONBOARD_SOURCE]
    # The stage 3 proposals the pack lists are the stored ones, still proposed.
    packed = json.loads((rig.out / "proposals" / "column_types.json").read_text())["items"]
    stored = {i["id"]: i for i in items}
    assert packed and all(stored[p["id"]]["status"] == "proposed" for p in packed)
    unit_price = [
        i for i in items if i["payload"].get("name") == "column_type:bronze.products.unit_price"
    ]
    assert [i["payload"]["proposed_type"] for i in unit_price] == ["DECIMAL(38,2)"]


def test_value_groups_leave_as_masks_and_stay_raw_only_in_the_store(rig: Rig) -> None:
    code, summary = rig.run("--stop-after", "4")
    assert code == 0
    maps = [i for i in _onboard_items(summary["space_id"]) if i["kind"] == "value_map"]
    region = [m for m in maps if m["payload"]["column"] == "region"]
    assert region, maps
    assert set(region[0]["payload"]["map"].values()) <= {"Kuala Lumpur", "KL", "kuala lumpur "}
    pack = (rig.out / "proposals" / "value_groups.json").read_text(encoding="utf-8")
    assert "Kuala Lumpur" not in pack and "SKU-BETA" not in pack


# --- stage 5 refusal: OpenVault unreachable ------------------------------------------


def test_openvault_missing_refuses_with_a_named_reason_and_exit_3(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENVAULT_URL")
    code, summary = rig.run()
    assert code == 3
    assert summary["reason"] == "openvault_url_missing"
    assert rig.stage(5)["status"] == "refused"
    assert rig.stage(5)["reason"] == "openvault_url_missing"
    assert rig.stage(6)["status"] == "not_reached"
    assert [rig.stage(n)["status"] for n in (1, 2, 3, 4)] == ["done"] * 4
    assert not (rig.out / "proposals" / "verified_questions.json").exists()
    assert rig.manifest()["leak_check"]["status"] == "passed"
    assert rig.model.bodies == []


def test_openvault_unreachable_refuses_ao_ov_unreachable(rig: Rig) -> None:
    import httpx

    def _down(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        raise httpx.ConnectError("refused")

    rig.model = _down  # type: ignore[assignment]
    code, summary = rig.run()
    assert (code, summary["reason"]) == (3, "ao_ov_unreachable")
    assert rig.stage(5)["status"] == "refused"
    assert _onboard_items(summary["space_id"]), "stages 1-4 proposals still stored"
    assert all(
        i["kind"] in ("column_type", "value_map") for i in _onboard_items(summary["space_id"])
    )


def test_invalid_model_output_refuses_and_stores_nothing_from_it(rig: Rig) -> None:
    rig.model = lambda url, headers, body: {"objects": "not a list"}  # type: ignore[assignment]
    code, summary = rig.run()
    assert (code, summary["reason"]) == (3, "ao_model_output_invalid")
    assert not [i for i in _onboard_items(summary["space_id"]) if i["kind"] in ("object", "link")]


# --- stages 5 and 6, happy path, recorded model and Cortex -------------------------


def test_happy_path_every_output_pending_measured_on_the_envelope(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_up(monkeypatch)
    code, summary = rig.run()
    assert code == 0, summary
    assert [s["status"] for s in rig.manifest()["stages"]] == ["done"] * 6

    # Nothing leaves unmasked: not the pack, not one model request body.
    _assert_no_canary(rig.pack_text())
    for body in rig.model.bodies:
        _assert_no_canary(json.dumps(body, ensure_ascii=False))
        assert body["role"] == "ontology"
        assert not {"model", "provider", "strict"} & set(body)

    vq = json.loads((rig.out / "proposals" / "verified_questions.json").read_text())
    assert {d["index"]: d["reason"] for d in vq["dropped"]} == EXPECTED_DROPS
    assert [i["question"] for i in vq["items"]] == [Q_ORDERS, Q_CUSTOMERS]
    assert all(i["status"] == "proposed" for i in vq["items"])

    items = _onboard_items(summary["space_id"])
    assert all(i["status"] == "proposed" and i["approved_by"] is None for i in items)
    kinds = {i["kind"] for i in items}
    assert {"object", "link", "column_type", "value_map", "verified_query"} <= kinds
    draft = json.loads((rig.out / "proposals" / "ontology_draft.json").read_text())
    assert [d["reason"] for d in draft["dropped"]] == ["unknown_table"]

    # Measure: the numbers in measure.json are the envelopes' numbers, counted as agreement
    # with the unreviewed proposal. Nothing is called correct before a steward approves.
    measure = json.loads((rig.out / "measure.json").read_text())
    assert (measure["asked"], measure["agrees"], measure["abstained"], measure["disagrees"]) == (
        2,
        2,
        0,
        0,
    )
    assert measure["correct"] is None and measure["wrong"] is None
    assert measure["reference"] == "proposed_sql_unreviewed"
    truth = {Q_ORDERS: ORDER_COUNT, Q_CUSTOMERS: CUSTOMER_COUNT}
    for q, n in truth.items():
        r = rig.client.post(
            "/v1/chat/ask", json={"question": q, "space_id": summary["space_id"], "session_id": "s"}
        )
        env = r.json()
        assert_envelope_valid(env)
        assert env["abstained"] is False
        assert [list(row.values()) for row in env["rows"]] == [[n]]
        assert str(n) in str(env.get("text") or ""), env.get("text")


def test_measure_counts_a_different_answer_as_disagreeing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_up(monkeypatch)
    # The ask leg answers the orders question with the customers figure.
    rig.cortex.by_question[Q_ORDERS] = f"SELECT COUNT(*) AS order_count FROM {CUSTOMERS}"
    code, _ = rig.run()
    assert code == 0
    measure = json.loads((rig.out / "measure.json").read_text())
    assert (measure["agrees"], measure["disagrees"]) == (1, 1)
    outcomes = {i["outcome"] for i in measure["items"]}
    assert outcomes == {"agrees", "disagrees"}


def test_cortex_unreachable_refuses_the_measure(rig: Rig) -> None:
    code, summary = rig.run()
    assert (code, summary["reason"]) == (3, "cortex_unreachable")
    assert rig.stage(5)["status"] == "done"
    assert rig.stage(6)["status"] == "refused"
    assert not (rig.out / "measure.json").exists()


# --- gates ------------------------------------------------------------------------------


def test_a_leaky_pack_is_refused_and_removed(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    real = fde_pack.masked_samples

    def _leaky(space_id: str, names: Any, **kw: Any) -> dict[str, Any]:
        out = real(space_id, names, **kw)
        out["leak"] = CANARIES[0]
        return out

    monkeypatch.setattr(fde_pack, "masked_samples", _leaky)
    code, summary = rig.run("--stop-after", "4")
    assert (code, summary["reason"]) == (4, "pack_leak")
    left = sorted(str(p.relative_to(rig.out)) for p in rig.out.rglob("*") if p.is_file())
    assert left == [cli.LEAK_FILE]
    _assert_no_canary(rig.pack_text())
    assert json.loads((rig.out / cli.LEAK_FILE).read_text())["hits"]


def test_a_drifted_prompt_refuses_before_any_ingest(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(fde_ai.PROMPT_SHA256, "02_verified_questions.md", "0" * 64)
    code, summary = rig.run()
    assert (code, summary["reason"]) == (5, "prompt_drift:02_verified_questions.md")
    assert summary["space_id"] == ""
    assert not rig.out.exists()


def test_a_non_empty_out_dir_is_refused(rig: Rig) -> None:
    rig.out.mkdir()
    (rig.out / "keep.txt").write_text("mine", encoding="utf-8")
    code, summary = rig.run("--stop-after", "4")
    assert (code, summary["reason"]) == (2, "out_not_empty")
    assert (rig.out / "keep.txt").read_text(encoding="utf-8") == "mine"


# --- adversary round 1 (dms#541) --------------------------------------------------------


def test_rows_compare_in_column_order_with_type_classes() -> None:
    """An adversary had swapped figures under swapped aliases counted as agreeing."""
    assert cli._multiset([{"orders": 40, "customers": 30}]) != cli._multiset(
        [{"orders": 30, "customers": 40}]
    )
    assert cli._multiset([{"n": "3"}]) != cli._multiset([{"n": 3}])
    assert cli._multiset([{"n": True}]) != cli._multiset([{"n": "True"}])
    assert cli._multiset([{"a": 1}, {"a": 1}]) != cli._multiset([{"a": 1}])
    # Aliases may differ between two readings of one question; values may not.
    assert cli._multiset([{"n": 40}]) == cli._multiset([{"order_count": Decimal("40.0")}])


def test_an_actor_without_a_steward_grant_is_refused_before_ingest(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    get_settings.cache_clear()
    code, summary = rig.run("--stop-after", "4")
    assert (code, summary["reason"]) == (2, "steward_grant_required")
    assert summary["space_id"] == ""
    assert not (rig.out / "schema.json").exists()


def test_a_rerun_keeps_a_revoked_proposal_revoked(rig: Rig) -> None:
    code, summary = rig.run("--stop-after", "4")
    assert code == 0
    first = _onboard_items(summary["space_id"])
    revoked = first[0]["id"]
    assert ontology_data_store().revoke(revoked, actor="steward") is not None
    import shutil

    shutil.rmtree(rig.out)
    code, again = rig.run("--stop-after", "4")
    assert code == 0, again
    assert again["space_id"] == summary["space_id"]
    rows = _onboard_items(summary["space_id"])
    assert len(rows) == len(first)
    assert {r["id"]: r["status"] for r in rows}[revoked] == "revoked"
    assert again["store"]["proposed"] == 0
    assert again["store"]["held_from_earlier_runs"] == len(first)


def test_a_symlink_in_source_is_not_read(rig: Rig, tmp_path: Path) -> None:
    import shutil

    src = tmp_path / "src"
    src.mkdir()
    for name in ("orders.csv", "customers.csv", "products.csv"):
        shutil.copy(FIXTURE / name, src / name)
    outside = tmp_path / "outside.csv"
    outside.write_text("zz_outside_secret_hdr_7781\n1\n", encoding="utf-8")
    (src / "linked.csv").symlink_to(outside)
    buf = io.StringIO()
    code = cli.main(
        ["--source", str(src), "--space", "Symlink", "--out", str(rig.out), "--stop-after", "4"],
        http=rig.client,
        transport=rig.model,
        stdout=buf,
    )
    assert code == 0, buf.getvalue()
    assert "zz_outside_secret_hdr_7781" not in rig.pack_text()
    assert json.loads(buf.getvalue())["stages"][0]["files_seen"] == 3


def test_a_crash_in_stage_5_is_named_by_class_and_the_pack_is_still_checked(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OverflowError("days=913377")

    monkeypatch.setattr(fde_ai, "check_question_sql", _boom)
    buf = io.StringIO()
    code = cli.main(
        ["--source", str(FIXTURE), "--space", "Crash", "--out", str(rig.out)],
        http=rig.client,
        transport=rig.model,
        stdout=buf,
    )
    assert code == 6
    out = json.loads(buf.getvalue())
    assert out["reason"] == "internal_error:OverflowError"
    assert "913377" not in buf.getvalue()
    m = rig.manifest()
    assert m["leak_check"]["status"] == "passed"
    assert m["stages"][4]["status"] == "failed"
    _assert_no_canary(rig.pack_text())


def test_drop_reasons_reach_the_pack_as_codes_only(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A join refusal names tables; the pack keeps the code, never the names."""
    real = fde_ai.check_question_sql

    def _named(sql: str, **kw: Any) -> Any:
        reason, info = real(sql, **kw)
        if reason and reason.startswith("unverified_join"):
            return "unverified_join:bronze.orders x bronze.staff_880101145678 no link", info
        return reason, info

    monkeypatch.setattr(fde_ai, "check_question_sql", _named)
    _cortex_up(monkeypatch)
    code, _ = rig.run()
    assert code == 0
    vq = (rig.out / "proposals" / "verified_questions.json").read_text(encoding="utf-8")
    assert "880101145678" not in vq and "staff_" not in vq
    assert '"reason": "unverified_join"' in vq
