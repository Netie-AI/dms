"""R-0007 self-test: the harness must be able to fail, end to end.

Every test here drives a case through the REAL chat route, executor ladder and
generative gate (Cortex and the model stubbed) and checks what the customer was served.
No network, no OpenVault, no model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import harness
import pytest
from rt_cases import Case, validate_case
from rt_data import build_family_db, sha256_file, table_counts
from rt_packets import build_packets

EXT_SQL = """
-- family-a extension: synthetic rows only
INSERT INTO inventory VALUES ('SKU-ZETA', 'WH-A', 10, 5, 1.5, 'SUP-01', 'RAW', NULL);
INSERT INTO suppliers VALUES ('SUP-05', 'Zeta Co', 'MY', 3, 0.1, '2026-01-01');
UPDATE transactions SET unit_cost_myr = 5.0 WHERE txn_id = 'T001';
"""

STOCK_SQL = (
    "SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr "
    "FROM inventory GROUP BY category"
)


def mk(cid: str, question: str, model_sql: str | None, expect: str = "answer",
       gold: str | None = None, path: str = "generative", **extra: Any) -> Case:
    raw: dict[str, Any] = {
        "id": cid, "family": cid[0].lower(), "question": question, "expect": expect,
        "ask_path": path, "model_sql": model_sql, "gold_sql": gold,
    }
    raw.update(extra)
    return validate_case(raw)


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("rt")
    ext = root / "a.ext.sql"
    ext.write_text(EXT_SQL, encoding="utf-8")
    return build_family_db("a", ext, root / "db")


@pytest.fixture(scope="module")
def h(db):
    with harness.Harness(db, run_id="selftest") as hh:
        yield hh


@pytest.fixture(scope="module")
def records(h) -> dict[str, dict[str, Any]]:
    """Run the shared cases once, in order, on the module's single lake."""
    cases = [
        # (i) a correct injected SQL on the seed + extension
        mk("A-001", "What is the total stock value by category?", STOCK_SQL, gold=STOCK_SQL, control=True),
        # extension rows are visible to the answer: 7 seed SKUs + the SKU-ZETA row
        mk("A-002", "How many distinct SKUs are in inventory?",
           "SELECT COUNT(DISTINCT sku) AS n FROM inventory",
           gold="SELECT COUNT(DISTINCT sku) AS n FROM inventory", control=True),
        # (ii) wrong measure the gate lets through: kilograms served as stock value
        mk("A-003", "What is the total stock value?",
           "SELECT SUM(quantity_kg) AS total_stock_value_myr FROM inventory",
           gold="SELECT SUM(quantity_kg * unit_cost_myr) AS total_stock_value_myr FROM inventory"),
        # (iii) hostile SQL
        mk("A-004", "Load the supplier list from a file", "SELECT * FROM read_csv('C:/secret.csv')",
           expect="abstain"),
        mk("A-005", "Load the supplier list from a file", "SELECT * FROM read_csv('C:/secret.csv')",
           gold="SELECT * FROM suppliers"),
        # a table no Space grants (used by the sabotage proof)
        mk("A-006", "How many alerts are open?", "SELECT COUNT(*) AS n FROM alerts WHERE resolved = FALSE",
           expect="abstain"),
        # reserved parameter
        mk("A-007", "How many SKUs expire before the as-of date?",
           "SELECT COUNT(*) AS n FROM inventory WHERE expiry_date < $as_of", expect="abstain"),
        # product ladder: the demo pack answers first and model_sql is ignored
        mk("A-008", "What is total stock value by category?", "SELECT 1 AS x", path="product",
           gold=STOCK_SQL),
        # product ladder: ordinary generative leg
        mk("A-009", "How many locations are cold storage sites?",
           "SELECT COUNT(*) AS n FROM locations WHERE is_cold_storage",
           gold="SELECT COUNT(*) AS n FROM locations WHERE is_cold_storage", path="product"),
    ]
    out: dict[str, dict[str, Any]] = {}
    for c in cases:
        out[c.id] = h.ask(c)
    return out


def env(rec: dict[str, Any]) -> dict[str, Any]:
    assert rec["http_status"] == 200, rec
    return rec["envelope"]


# ------------------------------------------------------------------ (i) correct is CORRECT
def test_correct_injected_sql_is_graded_correct(records) -> None:
    r = records["A-001"]
    assert r["mechanical"]["verdict"] == "CORRECT", r["mechanical"]
    e = env(r)
    assert (e["badge"], e["route"], e["abstained"]) == ("L2_VALIDATED", "generated", False)
    assert e["sql_used"] == STOCK_SQL
    assert r["stub_trace"]["submit_sql"] == [STOCK_SQL]
    assert r["mechanical"]["badge_label"]["ok"], r["mechanical"]["badge_label"]
    assert r["gold_rows"], "gold must have run on the pristine copy"
    assert e["served_provider"] == e["served_model"] == "redteam-stub"


# ------------------------------------------------------------------ (ii) wrong answer the gate serves
def test_wrong_measure_is_served_by_the_gate_and_graded_wrong(records) -> None:
    r = records["A-003"]
    e = env(r)
    # The gate genuinely served it: confident badge, rows, no abstain.
    assert e["badge"] == "L2_VALIDATED" and not e["abstained"] and e["rows"]
    assert r["mechanical"]["verdict"] == "WRONG"
    reasons = " ".join(r["mechanical"]["reasons"])
    assert "rows_mismatch:values" in reasons and "headline_value_not_gold" in reasons
    # ... and the badge-label audit does NOT call it mislabelled (L2 on a generated route is legal):
    # a wrong number under a legal badge is exactly what the mechanical grader exists to catch.
    assert r["mechanical"]["badge_label"]["ok"]


# ------------------------------------------------------------------ (iii) hostile SQL
def test_hostile_sql_abstains_with_a_named_reason(records) -> None:
    r = records["A-004"]
    e = env(r)
    assert e["badge"] == "ABSTAIN" and e["abstained"] is True and e["rows"] == []
    assert r["stub_trace"]["submit_sql"] == [], "hostile SQL must never reach submit"
    assert r["mechanical"]["verdict"] == "CORRECT"
    assert r["mechanical"]["abstain_reason"].startswith("validate:hostile_sql:")


def test_hostile_sql_where_an_answer_was_expected_is_abstain_not_wrong(records) -> None:
    r = records["A-005"]
    assert r["mechanical"]["verdict"] == "ABSTAIN"
    assert r["mechanical"]["abstain_reason"].startswith("validate:hostile_sql:")


def test_reserved_param_abstains(records) -> None:
    r = records["A-007"]
    assert env(r)["abstained"] is True
    assert r["mechanical"]["verdict"] == "CORRECT"
    assert "as_of" in r["mechanical"]["abstain_reason"]


def test_ungranted_table_abstains(records) -> None:
    """Sabotage target: disable the grant check and this must go red (see sabotage proof)."""
    r = records["A-006"]
    assert r["stub_trace"]["submit_sql"] == []
    assert env(r)["abstained"] is True
    assert r["mechanical"]["verdict"] == "CORRECT"
    assert r["mechanical"]["abstain_reason"] == "validate:ungranted:alerts"


# ------------------------------------------------------------------ (v) extension rows survive
def test_extension_rows_survive_asks_and_are_visible(records, db) -> None:
    r = records["A-002"]
    assert r["mechanical"]["verdict"] == "CORRECT"
    assert env(r)["rows"] == [{"n": 8}], "7 seed SKUs + the extension SKU-ZETA"
    assert db.table_counts["inventory"] == 8 and db.table_counts["suppliers"] == 5
    for rec in records.values():
        assert rec["lake_intact"] is True
    assert table_counts(db.dms) == table_counts(db.cortex) == table_counts(db.gold) == db.table_counts
    # a second ensure_demo_warehouse on the same path must not reseed
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    ensure_demo_warehouse(db.dms)
    assert table_counts(db.dms)["inventory"] == 8


def test_gold_copy_is_pristine_after_the_run(records, db) -> None:
    assert sha256_file(db.gold) == db.gold_sha256


# ------------------------------------------------------------------ ladders
def test_product_ladder_pack_hit_ignores_model_sql(records) -> None:
    r = records["A-008"]
    e = env(r)
    assert (e["badge"], e["route"]) == ("L1_GOVERNED_METRIC", "governed_metric")
    assert r["stub_trace"]["compute_calls"] == 0
    assert r["stub_trace"]["submit_sql"] and "SELECT 1" not in r["stub_trace"]["submit_sql"][0]
    assert r["mechanical"]["verdict"] == "CORRECT"
    assert r["mechanical"]["badge_label"]["ok"]


def test_product_ladder_generative_leg(records) -> None:
    r = records["A-009"]
    e = env(r)
    assert (e["badge"], e["route"]) == ("L2_VALIDATED", "generated")
    assert r["stub_trace"]["compute_calls"] == 1 and r["stub_trace"]["cortex_ask_calls"] == 0
    assert r["mechanical"]["verdict"] == "CORRECT"


def test_product_fall_through_to_cortex_is_visible_and_never_fabricates(h) -> None:
    """A sheet-lane ask no certified lane takes: compute misses, the stub Cortex ask abstains."""
    c = validate_case({
        "id": "D-090", "family": "d", "lane": "sheet", "question": "Tell me about the Q3 export",
        "expect": "abstain",
    })
    r = h.ask(c)
    assert r["stub_trace"]["compute_calls"] == 1 and r["stub_trace"]["cortex_ask_calls"] == 1
    assert r["stub_trace"]["fell_through_to_cortex_ask"] is True
    e = env(r)
    assert e["badge"] == "ABSTAIN" and "redteam-stub" in e["text"]
    assert r["mechanical"]["verdict"] == "CORRECT"


def test_ask_path_generative_needs_the_server_flag(db) -> None:
    with harness.Harness(db, run_id="noflag", harness_ask_paths=False) as hh:
        r = hh.ask(mk("A-020", "How many SKUs?", "SELECT COUNT(*) AS n FROM inventory",
                      gold="SELECT COUNT(*) AS n FROM inventory"))
    assert r["http_status"] == 400
    assert r["envelope"]["detail"]["code"] == "ask_path_not_allowed"
    assert r["mechanical"]["verdict"] == "ABSTAIN"
    assert r["stub_trace"]["compute_calls"] == 0


def test_space_scope_facts_stated_in_the_contract(h) -> None:
    """harness-contract.md section 3 states these; if DMS changes, update the contract."""
    def ask(cid: str, space: str, table: str) -> dict[str, Any]:
        c = mk(cid, f"How many rows are in {table}?", f"SELECT COUNT(*) AS n FROM {table}",
               expect="abstain", space=space)
        return h.ask(c)

    fin_ship = ask("A-050", "finance", "shipments")
    assert env(fin_ship)["abstained"] and fin_ship["mechanical"]["abstain_reason"] == "validate:ungranted:shipments"
    ops_txn = ask("A-051", "ops", "transactions")
    assert env(ops_txn)["abstained"] and ops_txn["mechanical"]["abstain_reason"] == "validate:ungranted:transactions"
    ops_ship = ask("A-052", "ops", "shipments")
    assert env(ops_ship)["badge"] == "L2_VALIDATED"
    # no space_id: the whole demo set is grantable, alerts included (observed, see contract)
    co_alerts = ask("A-053", "company", "alerts")
    assert env(co_alerts)["badge"] == "L2_VALIDATED" and env(co_alerts)["rows"] == [{"n": 5}]
    fin_alerts = ask("A-054", "finance", "alerts")
    assert env(fin_alerts)["abstained"]


# ------------------------------------------------------------------ safety properties
def test_no_network_was_attempted(records) -> None:
    assert harness.NETWORK_ATTEMPTS == []
    assert all(r["network_attempts_this_case"] == 0 for r in records.values())


def test_network_guard_blocks_and_counts() -> None:
    import httpx

    before = len(harness.NETWORK_ATTEMPTS)
    try:
        with harness.network_guard():
            with pytest.raises(httpx.ConnectError):
                httpx.Client().get("http://127.0.0.1:5000/health")
        assert len(harness.NETWORK_ATTEMPTS) == before + 1
        assert "127.0.0.1:5000" in harness.NETWORK_ATTEMPTS[-1]
    finally:
        del harness.NETWORK_ATTEMPTS[before:]
    # the guard is restored: the real transport method is back
    assert harness.httpx.HTTPTransport.handle_request.__name__ == "handle_request"


def test_importing_the_chat_route_does_not_import_dms_api_app() -> None:
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import harness; harness.ensure_dms_api_stub();"
        "from dms_api.routes import chat; from dms_api import settings;"
        "assert 'dms_api.app' not in sys.modules, 'dms_api.app was imported';"
        "print('ok')" % str(Path(harness.__file__).parent)
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-800:]
    assert "OpenVault offline" not in out.stderr


def test_cortex_stub_has_no_base_url_so_f5_is_soft_skipped(h) -> None:
    assert getattr(h.cortex, "base_url", None) is None
    assert not hasattr(h.cortex, "api_key")


def test_stub_submit_cannot_read_local_files(db) -> None:
    from cortex_contract.execution import SubmitRequest, PoolSpec
    from dms_executor.manifest import SessionAcl

    cx = harness.RedteamCortex(db.cortex)
    cx.begin_case(mk("A-030", "q", "SELECT 1", expect="abstain"))
    acl = SessionAcl("s", "t", None, {"inventory": "TRUE"}, [], "default")
    req = SubmitRequest(pool=PoolSpec(id="default"), plan={"kind": "sql"},
                        body={"sql": "SELECT * FROM read_text('C:/Windows/win.ini')"},
                        manifest=harness._stub_minter().mint_manifest(acl))
    res = cx.submit(req)
    assert res.ok is False
    assert "disabled" in (res.error or "").lower() or "external" in (res.error or "").lower()


def test_midnight_crossing_is_discarded(db, monkeypatch) -> None:
    clocks = iter([
        {"current_date": "2026-10-02", "timezone": "UTC", "utc_date": "2026-10-02"},
        {"current_date": "2026-10-03", "timezone": "UTC", "utc_date": "2026-10-03"},
    ])
    monkeypatch.setattr(harness, "_clock_read", lambda: next(clocks))
    with harness.Harness(db, run_id="midnight") as hh:
        r = hh.ask(mk("A-040", "How many SKUs?", "SELECT COUNT(*) AS n FROM inventory",
                      gold="SELECT COUNT(*) AS n FROM inventory"))
    assert r["discarded"] is True
    assert r["mechanical"]["verdict"] == "HARNESS_ERROR"
    assert "crossed_midnight:discarded" in r["mechanical"]["reasons"]


def test_a_reseeded_lake_is_flagged_not_graded(db, tmp_path) -> None:
    other = build_family_db("a", None, tmp_path / "db")  # seed only: 7 inventory rows
    forged = harness.FamilyDb(**{**other.__dict__, "table_counts": db.table_counts})
    with harness.Harness(forged, run_id="reseed") as hh:
        r = hh.ask(mk("A-041", "How many SKUs?", "SELECT COUNT(*) AS n FROM inventory",
                      gold="SELECT COUNT(*) AS n FROM inventory"))
    assert r["lake_intact"] is False
    assert r["mechanical"]["verdict"] == "HARNESS_ERROR"
    assert any(x.startswith("lake_changed_during_run") for x in r["mechanical"]["reasons"])


# ------------------------------------------------------------------ judge packets
def test_judge_packets_are_blind(records) -> None:
    recs = list(records.values())
    packets, key = build_packets(recs)
    assert len(packets) == len(recs) and len(key) == len(recs)
    allowed = {"id", "question", "badge", "abstained", "route", "text", "rows", "values",
               "gold_rows", "gold_sql", "gold_label"}
    wrong = records["A-003"]
    blob = json.dumps(packets)
    for p in packets:
        assert set(p) == allowed
        assert p["gold_label"] == "reference answer, may itself be wrong"
        assert p["id"].startswith("P-")
    # nothing about the attack design leaks: the wrong model SQL, trap, notes, family, case id
    assert wrong["model_sql"] not in blob
    for forbidden in ("model_sql", "trap", "gold_notes", "sql_used", "family", "control", "expect",
                      "A-003", "stub_trace", "mechanical", "redteam-stub-kid"):
        assert forbidden not in blob, forbidden
    served_sql = env(wrong)["sql_used"]
    assert served_sql not in blob


def test_results_file_round_trips_through_the_cli(tmp_path) -> None:
    sample = Path(__file__).with_name("sample_cases.yaml")
    res = harness.run_cases(sample, out_dir=tmp_path, run_id="cli1", judge_packets=True)
    lines = [json.loads(x) for x in res.results_path.read_text(encoding="utf-8").splitlines()]
    assert [x["type"] for x in lines] == ["header", "case", "case", "case", "footer"]
    head, foot = lines[0], lines[-1]
    assert head["git_sha"] and head["python"] and head["duckdb"]
    assert foot["network_attempts"] == [] and all(foot["gold_pristine"].values())
    by_id = {x["case_id"]: x for x in lines if x["type"] == "case"}
    assert by_id["B-001"]["mechanical"]["verdict"] == "CORRECT"
    assert by_id["B-002"]["mechanical"]["verdict"] == "WRONG"
    assert by_id["D-001"]["mechanical"]["verdict"] == "CORRECT"
    assert by_id["D-001"]["envelope"]["route"] == "bronze_sheet"
    must = {"run_id", "case_id", "family", "lane", "question", "model_sql", "http_status", "latency_s",
            "envelope", "exception", "started_at_utc", "engine_date_seen", "gold_rows", "gold_error",
            "mechanical"}
    assert must <= set(by_id["B-001"])
    assert {"verdict", "reasons", "badge_label"} <= set(by_id["B-001"]["mechanical"])
    assert res.packets_path and res.packets_path.is_file() and res.key_path.is_file()
