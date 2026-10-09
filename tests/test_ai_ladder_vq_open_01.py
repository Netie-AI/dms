"""Verified-query reads share the serving lock. Asks stay correct under it."""

from __future__ import annotations

import json
import math
import threading
import time
import traceback
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
import sqlglot
import yaml
from cortex_client.compute import begin_answer_model_calls
from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.demo_warehouse import (
    SERVING_DIALECT,
    connect_file,
    connect_locked_readonly,
    ensure_demo_warehouse,
)
from dms_executor.generative_ask import demo_ontology, load_verified_ontology, maybe_generative_ask
from dms_executor.verified_queries import lookup_verified_query
from sqlglot import exp

_ROOT = Path(__file__).resolve().parents[1]
_QUESTIONS = _ROOT / "tests/fixtures/curated_ceo/questions.yaml"
_ORACLES = _ROOT / "tests/fixtures/curated_ceo/oracles.yaml"
_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_IDS = (
    "cq_capacity_above_90",
    "cq_capacity_utilisation",
    "cq_cctv_wh_a",
    "cq_chemicals_list",
    "cq_cold_storage",
    "cq_expired_items",
    "cq_low_stock_wh_a",
    "cq_sales_top3_volume",
    "cq_sales_top5_syn_sales",
    "cq_sku_count",
    "cq_sku_count_by_category",
    "cq_spend_by_country",
    "cq_stock_value_by_category",
    "cq_supplier_ranking",
    "cq_top3_category_syn_plain",
    "ops_freight_spend_destination",
    "cq_sales_top5_syn_skus",
    "cq_sales_top5_value",
    "cq_sku_count_by_category_per",
    "cq_sku_count_syn_label",
)


def _from_verified_queries() -> bool:
    for frame in traceback.extract_stack():
        if frame.filename.endswith("verified_queries.py"):
            return True
    return False


def test_verified_lookup_read_write_open_collides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified-query read must not hold the serving file read-write.

    The lookup runs for real. If that read opens read-write, this test keeps
    the handle open and a locked read-only connect hits it. That overlap is
    the collision: DuckDB raises, and the test fails.
    """
    db = ensure_demo_warehouse(tmp_path / "vq.duckdb")
    lookup_verified_query("warmup", space_id=_SPACE, warehouse=db)
    entered = threading.Event()
    release = threading.Event()
    done = threading.Event()
    collisions: list[str] = []
    real_connect = duckdb.connect

    def _spy(path: str, *args: Any, **kwargs: Any) -> Any:
        con = real_connect(path, *args, **kwargs)
        if not kwargs.get("read_only", False) and _from_verified_queries():
            entered.set()
            release.wait(5)
        return con

    monkeypatch.setattr(duckdb, "connect", _spy)

    def _lookup() -> None:
        try:
            lookup_verified_query("nothing registered here", space_id=_SPACE, warehouse=db)
        except Exception as exc:  # noqa: BLE001
            collisions.append(f"lookup:{type(exc).__name__}")
        finally:
            done.set()
            release.set()

    def _hit_writer() -> None:
        try:
            con = connect_locked_readonly(db, timeout=1)
            try:
                con.execute("SELECT 1")
            finally:
                con.close()
        except TimeoutError:
            collisions.append("TimeoutError")
            return
        except Exception as exc:  # noqa: BLE001
            collisions.append(type(exc).__name__)
            return
        collisions.append("opened_readonly_beside_writer")

    def _reader() -> None:
        try:
            while not done.is_set():
                if entered.is_set():
                    _hit_writer()
                    return
                try:
                    con = connect_locked_readonly(db, timeout=0.2)
                    try:
                        con.execute("SELECT 1")
                    finally:
                        con.close()
                except TimeoutError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    collisions.append(type(exc).__name__)
                    return
            if entered.is_set():
                _hit_writer()
        finally:
            release.set()

    ask = threading.Thread(target=_lookup, daemon=True)
    reader = threading.Thread(target=_reader, daemon=True)
    ask.start()
    reader.start()
    ask.join(8)
    reader.join(8)
    release.set()
    assert not ask.is_alive()
    assert not reader.is_alive()
    assert collisions == []
    assert not entered.is_set()


def _order_specs(sql: str) -> list[tuple[str | int, bool]] | None:
    try:
        tree = sqlglot.parse_one(sql, read=SERVING_DIALECT)
    except sqlglot.errors.SqlglotError:
        return None
    order = tree.args.get("order")
    if order is None:
        return []
    specs: list[tuple[str | int, bool]] = []
    for ordered in order.expressions:
        this = ordered.this
        desc = bool(ordered.args.get("desc"))
        if isinstance(this, exp.Literal) and this.is_int:
            specs.append((int(this.this), desc))
        elif isinstance(this, exp.Column):
            specs.append((this.name, desc))
        else:
            return None
    return specs


def _order_key(row: dict[str, Any], specs: list[tuple[str | int, bool]]) -> tuple[Any, ...]:
    values = list(row.values())
    keys: list[Any] = []
    for name, _desc in specs:
        if isinstance(name, int):
            keys.append(values[name - 1] if 1 <= name <= len(values) else None)
            continue
        found = None
        for key, val in row.items():
            if str(key).lower() == name.lower():
                found = val
                break
        keys.append(found)
    return tuple(keys)


def _rows_match(sql: str, gold: list[Any], got: list[Any]) -> bool:
    specs = _order_specs(sql)
    if specs is None:
        return gold == got
    def _bag(rows: list[Any]) -> Counter[str]:
        return Counter(json.dumps(row, sort_keys=True, default=str) for row in rows)

    if _bag(gold) != _bag(got):
        return False
    if not specs:
        return True
    return [_order_key(row, specs) for row in gold if isinstance(row, dict)] == [
        _order_key(row, specs) for row in got if isinstance(row, dict)
    ]


def _cases(db: Path) -> list[dict[str, Any]]:
    import duckdb

    pack = yaml.safe_load(_QUESTIONS.read_text(encoding="utf-8"))
    spaces = {str(name): str(sid) for name, sid in pack["spaces"].items()}
    questions = {str(row["id"]): row for row in pack["questions"]}
    oracles = yaml.safe_load(_ORACLES.read_text(encoding="utf-8"))["oracles"]
    grants = {
        sid: set(tables) for sid, (_name, tables) in DEMO_SPACE_GRANTS.items()
    }
    con = duckdb.connect(str(db), read_only=True)
    out: list[dict[str, Any]] = []
    try:
        for cid in _IDS:
            row = questions[cid]
            sql = " ".join(str(oracles[cid]["sql"]).split())
            space = spaces[str(row["space"])]
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            gold = [dict(zip(cols, rec, strict=True)) for rec in cur.fetchall()]
            out.append(
                {
                    "id": cid,
                    "sql": sql,
                    "question": str(row["question"]),
                    "space": space,
                    "grants": grants[space],
                    "gold": gold,
                }
            )
    finally:
        con.close()
    return out


def _grade(case: dict[str, Any], env: dict[str, Any] | None) -> str:
    if not isinstance(env, dict) or env.get("abstained"):
        return "abstain"
    rows = list(env.get("rows") or [])
    return "correct" if _rows_match(case["sql"], case["gold"], rows) else "wrong"


def _ask(case: dict[str, Any], db: Path, onto: Any) -> dict[str, Any] | None:
    lookup_verified_query(case["question"], space_id=case["space"], warehouse=db)
    begin_answer_model_calls()

    def _submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, rec, strict=True)) for rec in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_vq", output={"rows": rows})

    return maybe_generative_ask(
        case["question"],
        space_id=case["space"],
        warehouse=db,
        grantable=set(case["grants"]),
        compute=lambda _ctx, sql=case["sql"]: {
            "phase": "generate",
            "query_sql": sql,
            "served_model": "fake-model",
            "served_provider": "fake-provider",
            "ov_key_id": "ovk-fake",
        },
        submit=_submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led_vq", hash="hash_vq"),
        ontology=onto,
    )


def _run(
    cases: list[dict[str, Any]], db: Path, onto: Any, *, concurrent: bool
) -> tuple[int, int, int]:
    grades: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def _one(case: dict[str, Any]) -> None:
        try:
            grade = _grade(case, _ask(case, db, onto))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            return
        with guard:
            grades.append(grade)

    if concurrent:
        threads = [threading.Thread(target=_one, args=(case,)) for case in cases]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    else:
        for case in cases:
            _one(case)
    assert errors == []
    assert len(grades) == len(cases)
    return (
        sum(grade == "correct" for grade in grades),
        sum(grade == "wrong" for grade in grades),
        sum(grade == "abstain" for grade in grades),
    )


def test_twenty_concurrent_asks_match_serial_over_ten_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    db = ensure_demo_warehouse(tmp_path / "load.duckdb")
    onto = load_verified_ontology(db, demo_ontology(db))
    cases = _cases(db)
    assert len(cases) == 20
    # One read creates the verified-query table so later asks stay on the read-only open.
    lookup_verified_query("warmup", space_id=_SPACE, warehouse=db)
    serial_ok, serial_wrong, _serial_abs = _run(cases, db, onto, concurrent=False)
    assert serial_wrong == 0
    samples: list[float] = []
    for index in range(10):
        started = time.perf_counter()
        correct, wrong, _abstain = _run(cases, db, onto, concurrent=True)
        samples.append(time.perf_counter() - started)
        assert wrong == 0, index
        assert correct >= serial_ok, index
    ordered = sorted(samples)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    print(
        f"serial correct={serial_ok} runs=10 "
        f"p95={ordered[rank - 1]:.3f}s max={ordered[-1]:.3f}s"
    )
