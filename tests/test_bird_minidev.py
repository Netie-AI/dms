"""A1-02 Mini-Dev harness plants. Not a live BIRD score."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bird_minidev import (  # noqa: E402
    EXIT_CONFIG,
    EXIT_PASS,
    MINIDEV_DBS,
    MINIDEV_N,
    PLAN_ORIGIN_GENERATE_SQL,
    PLAN_ORIGIN_ONTOLOGY_RANKING,
    SETUP_FIELD_KEYS,
    SYNTHETIC,
    UNKNOWN,
    cells_equal,
    compare_runs,
    gold_error_dominating,
    grade_envelope,
    load_minidev_source,
    minidev_self_check,
    norm_cell,
    pg_gold_error,
    require_freeroute_frozen,
    run_minidev,
    run_pg_gold,
    served_from_response,
    setup_fingerprint,
    setup_payload,
    snapshot_route_store,
    validate_full_minidev,
)
from score_bird import EXIT_PASS as BIRD_PASS  # noqa: E402
from score_bird import self_check  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "score_bird.py"


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "SYSTEMROOT") if k in os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "apps" / "api"),
            str(ROOT / "packages" / "core"),
            str(ROOT / "packages" / "cortex_client"),
            str(ROOT / "packages" / "executor"),
            str(ROOT / "packages" / "ledger"),
            str(ROOT / "scripts"),
        ]
    )
    env["DMS_SCORE_DIR"] = str(tmp)
    env.update(extra)
    return env


def test_norm_cell_long_numeric_string_does_not_crash() -> None:
    huge = "9" * 40
    kind, _val = norm_cell(huge)
    assert kind == "n"
    assert cells_equal(huge, huge)
    assert not cells_equal(huge, "9" * 39)
    assert cells_equal(huge, int(huge))


def test_numeric_tolerance_large_and_small_magnitudes() -> None:
    # 4 dp absolute would round both to 0.0000 and falsely agree.
    assert not cells_equal("1e-8", "2e-8")
    # Relative tolerance on large magnitudes; 4 dp absolute is the wrong scale.
    assert cells_equal("1000000.00014", "1000000.00015")
    assert cells_equal(1.0000001, 1.0)
    assert not cells_equal(1.1, 1.0)
    assert cells_equal(2, 2.0)


def test_gold_error_dominating_both_branches() -> None:
    assert gold_error_dominating("sql_error", "OK") == "GOLD_ERROR"
    assert gold_error_dominating("dead_connection", "WRONG") == "GOLD_ERROR"
    assert gold_error_dominating(None, "OK") == "OK"
    assert gold_error_dominating(None, "WRONG") == "WRONG"
    assert gold_error_dominating(None, "ABSTAIN") == "ABSTAIN"
    assert gold_error_dominating("", "OK") == "OK"


def test_pg_gold_error_dead_connection_branch() -> None:
    class OperationalError(Exception):
        pass

    class ProgrammingError(Exception):
        pass

    assert pg_gold_error(OperationalError("connection refused")) == "dead_connection"
    assert pg_gold_error(ProgrammingError("syntax")) == "sql_error"

    def _dead() -> object:
        raise OperationalError("connection refused")

    rows, kind = run_pg_gold("SELECT 1", _dead)
    assert rows is None
    assert kind == "dead_connection"


def test_grader_plants_ok_abstain_wrong_column_order() -> None:
    gold = [{"name": "alpha", "n": 1}]
    ok = {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"n": 1, "name": "alpha"}]}
    assert grade_envelope(ok, gold) == "OK"
    abs_env = {"badge": "ABSTAIN", "abstained": True, "rows": []}
    assert grade_envelope(abs_env, gold) == "ABSTAIN"
    wrong = {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"n": 99}]}
    assert grade_envelope(wrong, gold) == "WRONG"
    empty = {"badge": "L2_VALIDATED", "abstained": False, "rows": []}
    assert grade_envelope(empty, gold) == "WRONG"


def _load_served_fixture(name: str) -> dict[str, object]:
    path = ROOT / "tests" / "fixtures" / "bird_minidev" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _unknown_setup() -> dict[str, object]:
    out: dict[str, object] = {key: UNKNOWN for key in SETUP_FIELD_KEYS}
    out["provider"] = UNKNOWN
    out["model"] = UNKNOWN
    out["plan_origin"] = UNKNOWN
    return out


def test_served_from_response_copies_reported_and_unknown_never_guesses() -> None:
    absent = served_from_response(_load_served_fixture("served_absent.json"))
    assert absent == _unknown_setup()
    assert served_from_response(None) == absent
    # Aliases are not envelope setup fields: do not guess.
    assert served_from_response({"provider": "groq", "model": "llama-3.3"}) == absent
    assert served_from_response({"plan_source": "ontology_plan"}) == absent
    present = served_from_response(_load_served_fixture("served_present.json"))
    assert present["served_provider"] == "ov_free_llama_unique"
    assert present["served_model"] == "not-a-default-model"
    assert present["served_local"] is False
    assert present["learn_enabled"] is False
    assert present["learn_source"] == "freeroute-store"
    assert present["route_store_id"] == "rs_present"
    assert present["plan_origin"] == PLAN_ORIGIN_GENERATE_SQL
    assert present["provider"] == present["served_provider"]
    assert present["model"] == present["served_model"]
    # Nested Insights payload is not the DMS envelope: do not infer.
    nested = served_from_response(_load_served_fixture("served_nested.json"))
    assert nested == absent
    nulls = served_from_response({key: None for key in SETUP_FIELD_KEYS})
    assert nulls == {**absent, "plan_origin": UNKNOWN}
    ranking = served_from_response({"plan_origin": PLAN_ORIGIN_ONTOLOGY_RANKING})
    assert ranking["plan_origin"] == PLAN_ORIGIN_ONTOLOGY_RANKING
    for key in SETUP_FIELD_KEYS:
        assert ranking[key] == UNKNOWN
    # Do not treat route/badge as a model.
    assert served_from_response({"route": "generated", "badge": "L2_VALIDATED"}) == absent
    # Non-bool local/learn is unknown, never coerced false.
    coerced = served_from_response({"served_local": 0, "learn_enabled": "false"})
    assert coerced["served_local"] == UNKNOWN
    assert coerced["learn_enabled"] == UNKNOWN


def test_compare_refuses_different_fingerprints_unless_forced() -> None:
    left = {
        "setup_fingerprint": "aaa",
        "summary": {"n": 10, "wrong": 0, "answered": 4},
    }
    right = {
        "setup_fingerprint": "bbb",
        "summary": {"n": 10, "wrong": 1, "answered": 5},
    }
    code, body = compare_runs(left, right, force=False)
    assert code == EXIT_CONFIG
    assert body["refused"] is True
    assert "fingerprint" in body["note"].lower()
    assert "learn_enabled" in body["note"]
    forced, forced_body = compare_runs(left, right, force=True)
    assert forced == EXIT_PASS
    assert forced_body["comparison"] == "cross-setup"
    assert forced_body["refused"] is False
    same, same_body = compare_runs(left, dict(left), force=False)
    assert same == EXIT_PASS
    assert same_body["comparison"] == "same-setup"


def test_fingerprint_includes_envelope_setup_fields_and_compare_refuses() -> None:
    shared = dict(
        learn="0",
        store_id="/tmp/store",
        fresh=True,
        hash_before="abc",
        mix={"ov/model/local=false": 1},
        served_local={"true": 0, "false": 1, "unknown": 0},
    )
    a = setup_payload(
        **shared,
        setup_mix_counts={
            "served_provider=ov|served_model=m|served_local=false|"
            "learn_enabled=false|learn_source=freeroute-store|route_store_id=rs_a": 1
        },
    )
    b = setup_payload(
        **shared,
        setup_mix_counts={
            "served_provider=ov|served_model=m|served_local=false|"
            "learn_enabled=true|learn_source=freeroute-store|route_store_id=rs_a": 1
        },
    )
    assert setup_fingerprint(a) != setup_fingerprint(b)
    left = {
        "setup_fingerprint": setup_fingerprint(a),
        "summary": {"n": 1, "wrong": 0, "answered": 1},
    }
    right = {
        "setup_fingerprint": setup_fingerprint(b),
        "summary": {"n": 1, "wrong": 0, "answered": 1},
    }
    code, body = compare_runs(left, right, force=False)
    assert code == EXIT_CONFIG
    assert body["refused"] is True
    assert "learn_enabled" in body["note"]


def test_plan_origin_counts_generate_sql_and_ranking_separately(tmp_path: Path) -> None:
    questions, meta = load_minidev_source(str(SYNTHETIC), dest_dir=tmp_path)
    gold_rows = [{"n": 2}]

    def gold_fn(_sql: str) -> tuple[list[dict[str, object]] | None, str | None]:
        if "missing_table" in _sql:
            return None, "sql_error"
        return gold_rows, None

    def ask_fn(question: str) -> dict[str, object]:
        origin = (
            PLAN_ORIGIN_ONTOLOGY_RANKING
            if "names" in question.lower() or "list widget" in question.lower()
            else PLAN_ORIGIN_GENERATE_SQL
        )
        if "broken" in question:
            return {
                "badge": "ABSTAIN",
                "abstained": True,
                "rows": [],
                "plan_origin": origin,
            }
        return {
            "badge": "L0_CERTIFIED",
            "abstained": False,
            "rows": gold_rows,
            "plan_origin": origin,
            "served_provider": "ov_free_llama_unique",
            "served_model": "not-a-default-model",
            "served_local": False,
            "learn_enabled": False,
            "learn_source": "freeroute-store",
            "route_store_id": "rs_minidev",
        }

    _code, report, err = run_minidev(
        questions,
        ask_fn=ask_fn,
        gold_fn=gold_fn,
        data_meta=meta,
        cortex=False,
        env=_env(tmp_path),
        write=False,
    )
    assert err is None and report is not None
    origins = report["plan_origin"]
    assert origins[PLAN_ORIGIN_GENERATE_SQL] >= 1
    assert origins[PLAN_ORIGIN_ONTOLOGY_RANKING] >= 1
    by = report["summary"]["by_plan_origin"]
    assert PLAN_ORIGIN_GENERATE_SQL in by
    assert PLAN_ORIGIN_ONTOLOGY_RANKING in by


def test_freeroute_live_refuses_unless_learn_off_and_store_fresh(tmp_path: Path) -> None:
    dirty = tmp_path / "routes.jsonl"
    dirty.write_text("already learned\n", encoding="utf-8")
    assert "learning is not off" in str(require_freeroute_frozen({}))
    assert "learning is not off" in str(
        require_freeroute_frozen({"CORTEX_FREEROUTE_LEARN": "1"})
    )
    missing_store = require_freeroute_frozen({"CORTEX_FREEROUTE_LEARN": "0"})
    assert isinstance(missing_store, str) and "CORTEX_ROUTE_STORE" in missing_store
    not_fresh = require_freeroute_frozen(
        {"CORTEX_FREEROUTE_LEARN": "0", "CORTEX_ROUTE_STORE": str(dirty)}
    )
    assert isinstance(not_fresh, str) and "not fresh" in not_fresh
    fresh = tmp_path / "empty"
    fresh.write_bytes(b"")
    ok = require_freeroute_frozen(
        {"CORTEX_FREEROUTE_LEARN": "0", "CORTEX_ROUTE_STORE": str(fresh)}
    )
    assert isinstance(ok, dict)
    assert ok["learn"] == "0"
    assert ok["fresh"] is True
    assert ok["hash_before"] == snapshot_route_store(fresh)["hash"]


def test_run_minidev_records_models_mix_fingerprint_and_refuses_learn_on(
    tmp_path: Path,
) -> None:
    questions, meta = load_minidev_source(str(SYNTHETIC), dest_dir=tmp_path)
    gold_rows = [{"n": 2}]

    def gold_fn(_sql: str) -> tuple[list[dict[str, object]] | None, str | None]:
        if "missing_table" in _sql:
            return None, "sql_error"
        return gold_rows, None

    def ask_fn(question: str) -> dict[str, object]:
        if "broken" in question:
            return {"badge": "ABSTAIN", "abstained": True, "rows": []}
        return {
            "badge": "L0_CERTIFIED",
            "abstained": False,
            "rows": gold_rows,
            "served_provider": "ov_free_llama_unique",
            "served_model": "not-a-default-model",
            "served_local": False,
            "learn_enabled": False,
            "learn_source": "freeroute-store",
            "route_store_id": "rs_minidev",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
        }

    env = _env(tmp_path, CORTEX_FREEROUTE_LEARN="1")
    code, report, err = run_minidev(
        questions,
        ask_fn=ask_fn,
        gold_fn=gold_fn,
        data_meta=meta,
        cortex=True,
        env=env,
        write=True,
    )
    assert code == EXIT_CONFIG
    assert report is None
    assert err and "learning is not off" in err
    assert not (tmp_path / "score_bird_minidev.json").exists()

    store = tmp_path / "store"
    store.write_bytes(b"")
    env2 = _env(
        tmp_path,
        CORTEX_FREEROUTE_LEARN="0",
        CORTEX_ROUTE_STORE=str(store),
    )
    code2, report2, err2 = run_minidev(
        questions,
        ask_fn=ask_fn,
        gold_fn=gold_fn,
        data_meta=meta,
        cortex=True,
        env=env2,
        write=True,
    )
    assert err2 is None
    assert code2 == EXIT_PASS
    assert report2 is not None
    art = json.loads((tmp_path / "score_bird_minidev.json").read_text(encoding="utf-8"))
    assert art["freeroute"]["learn"] == "0"
    assert art["freeroute"]["fresh"] is True
    assert "hash_before" in art["freeroute"]
    assert "hash_after" in art["freeroute"]
    assert "row_count_before" in art["freeroute"]
    assert "row_count_after" in art["freeroute"]
    assert art["sha"]
    assert art["data_bytes"] == meta["bytes"]
    mix = art["served_mix"]
    assert mix["ov_free_llama_unique/not-a-default-model/local=false"] >= 1
    assert art["served_local"]["false"] >= 1
    assert art["setup"]["served_local"]["false"] >= 1
    assert art["plan_origin"][PLAN_ORIGIN_GENERATE_SQL] >= 1
    assert art["plan_origin"][PLAN_ORIGIN_ONTOLOGY_RANKING] == 0
    assert art["summary"]["by_plan_origin"][PLAN_ORIGIN_GENERATE_SQL]["right"] >= 1
    for case in art["cases"]:
        for key in SETUP_FIELD_KEYS:
            assert key in case
        assert "plan_origin" in case
        if case["verdict"] != "GOLD_ERROR" and case["verdict"] != "ABSTAIN":
            assert case["served_provider"] == "ov_free_llama_unique"
            assert case["served_local"] is False
            assert case["learn_enabled"] is False
            assert case["learn_source"] == "freeroute-store"
            assert case["route_store_id"] == "rs_minidev"
            assert case["plan_origin"] == PLAN_ORIGIN_GENERATE_SQL
    assert art["setup_fingerprint"] == setup_fingerprint(art["setup"])
    assert art["setup"]["setup_mix"]

    unknown_ask = {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
    }
    code3, report3, err3 = run_minidev(
        questions[:1],
        ask_fn=lambda _q: unknown_ask,
        gold_fn=gold_fn,
        data_meta=meta,
        cortex=False,
        env=_env(tmp_path / "off"),
        write=True,
    )
    assert err3 is None and code3 == EXIT_PASS and report3 is not None
    assert report3["cases"][0]["provider"] == UNKNOWN
    assert report3["cases"][0]["model"] == UNKNOWN
    assert report3["cases"][0]["served_local"] == UNKNOWN
    assert report3["cases"][0]["learn_enabled"] == UNKNOWN
    assert report3["cases"][0]["learn_source"] == UNKNOWN
    assert report3["cases"][0]["route_store_id"] == UNKNOWN
    assert report3["cases"][0]["plan_origin"] == UNKNOWN
    assert report3["served_mix"] == {f"{UNKNOWN}/{UNKNOWN}/local={UNKNOWN}": 1}
    assert report3["served_local"] == {"true": 0, "false": 0, "unknown": 1}
    assert report3["plan_origin"] == {
        PLAN_ORIGIN_GENERATE_SQL: 0,
        PLAN_ORIGIN_ONTOLOGY_RANKING: 0,
        UNKNOWN: 1,
    }


def test_validate_refuses_shrunk_full_set_without_limit() -> None:
    small = [
        {
            "question_id": i,
            "db_id": f"db{i % 11}",
            "question": "q",
            "SQL": "SELECT 1",
            "difficulty": "simple",
        }
        for i in range(20)
    ]
    err = validate_full_minidev(small, limit=None)
    assert err and "shrunk" in err
    assert validate_full_minidev(small, limit=5) is None
    full = [
        {
            "question_id": i,
            "db_id": f"db{i % MINIDEV_DBS}",
            "question": "q",
            "SQL": "SELECT 1",
            "difficulty": "simple",
        }
        for i in range(MINIDEV_N)
    ]
    assert validate_full_minidev(full, limit=None) is None


def test_no_bird_corpus_committed() -> None:
    folder = ROOT / "tests" / "fixtures" / "bird_minidev"
    jsons = sorted(p.name for p in folder.glob("*.json"))
    assert "synthetic.json" in jsons
    assert "served_present.json" in jsons
    assert "served_absent.json" in jsons
    data = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
    assert len(data) < 20
    assert len(data) != MINIDEV_N


def test_self_check_includes_minidev_plants() -> None:
    assert minidev_self_check() == []
    assert self_check() == BIRD_PASS


def test_cli_minidev_without_live_is_config(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--minidev", str(SYNTHETIC)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
    )
    assert proc.returncode == EXIT_CONFIG, proc.stdout + proc.stderr
    assert "VERDICT: CONFIG" in proc.stdout


def test_cli_compare_refuses_cross_setup(tmp_path: Path) -> None:
    a = {
        "setup_fingerprint": "one",
        "summary": {"n": 3, "wrong": 0, "answered": 1},
    }
    b = {
        "setup_fingerprint": "two",
        "summary": {"n": 3, "wrong": 1, "answered": 2},
    }
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text(json.dumps(a), encoding="utf-8")
    right.write_text(json.dumps(b), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--compare", str(left), str(right)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
    )
    assert proc.returncode == EXIT_CONFIG, proc.stdout + proc.stderr
    assert "refused" in proc.stdout.lower() or "CONFIG" in proc.stdout
    forced = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--compare",
            str(left),
            str(right),
            "--force-cross-setup",
        ],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
    )
    assert forced.returncode == EXIT_PASS, forced.stdout + forced.stderr
    assert "cross-setup" in forced.stdout


def test_cli_self_check_still_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-check"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == BIRD_PASS, proc.stdout + proc.stderr
    assert "target=75" in proc.stdout
    assert "not a live Mini-Dev score" in proc.stdout.lower() or "minidev" in proc.stdout.lower()
