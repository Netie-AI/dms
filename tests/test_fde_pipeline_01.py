"""FDE-PIPELINE-01: the tenant factory and the stage pipeline, offline (no engine)."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "fde"))

import fde_pipeline as fp  # noqa: E402
import tenant_factory as tf  # noqa: E402


def test_factory_is_deterministic_and_names_are_unique() -> None:
    a = tf.make_fleet(6, seed=3)
    b = tf.make_fleet(6, seed=3)
    assert [t.key for t in a] == [t.key for t in b]
    for x, y in zip(a, b, strict=True):
        assert [tb.rows[:5] for tb in x.dataset.tables] == [tb.rows[:5] for tb in y.dataset.tables]
        names = [tb.name for tb in x.dataset.tables]
        assert len(names) == len(set(names))
        for tb in x.dataset.tables:
            cols = [c for c, _ in tb.columns]
            assert len(cols) == len(set(cols)), (x.key, tb.name)


@pytest.mark.parametrize("style", tf.NAMING_STYLES)
def test_naming_styles(style: str) -> None:
    got = tf.physical(style, "prescription_item", kind="table")
    assert got and " " not in got
    if style == "legacy":
        assert got.startswith("T_") and got.isupper()


def test_generated_fleet_runs_every_stage(tmp_path: Path) -> None:
    tenants = tmp_path / "tenants"
    assert fp.main(["factory", "--n", "3", "--seed", "5", "--out", str(tenants)]) == 0
    work = tmp_path / "run"
    assert fp.main(["run", "--tenants", str(tenants), "--work", str(work), "--expect-keys"]) == 0
    fleet = (work / "FLEET.md").read_text()
    assert "SKIPPED" in fleet  # DMS was not asked: never reported as a pass
    import json

    for rep in json.loads((work / "fleet.json").read_text()):
        assert rep["status"] == "OK" and rep["failures"] == []
        ev = rep["eval"]
        assert ev["fidelity"]["mismatch"] == 0 and ev["fidelity"]["gold_ok"] >= 10
        assert ev["ontology"]["roles_ok"] == ev["ontology"]["tables"]
        pack = rep["stages"]["pack"]
        assert pack["traps"] == 2 and pack["answerable"] >= 10


def test_keys_are_inferred_when_none_are_visible(tmp_path: Path) -> None:
    t = tf.make_tenant(5, 0)
    db = tmp_path / "v.duckdb"
    fp.fs.build_duckdb_dataset(t.dataset, db)
    prof = fp.stage_profile(db, {})
    assert prof["keys_source"] == "inferred"
    onto = fp.stage_ontology(prof, prof["pk_used"], [], t.labels)
    for name, r in t.roles.items():
        assert onto["entities"][name]["role"] == r["role"], name
    truth = {(tb.name, c, rt, rc) for tb in t.dataset.tables for c, rt, rc in tb.fks}
    assert {tuple(j[:4]) for j in onto["joins"]} == truth


def test_ambiguous_golds_are_dropped(tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE s (k VARCHAR, v INT)")
    con.execute("INSERT INTO s VALUES ('a', 3), ('b', 2), ('c', 2), ('d', 1)")
    con.close()
    tie = {"gold_sql": "SELECT k, v FROM s ORDER BY v DESC LIMIT 2", "ordered": True}
    empty = {"gold_sql": "SELECT k FROM s WHERE v > 9", "ordered": False}
    nulls = {"gold_sql": "SELECT SUM(v) FROM s WHERE v > 9", "ordered": False}
    ok = {"gold_sql": "SELECT k, v FROM s ORDER BY v DESC LIMIT 1", "ordered": True}
    assert fp._gold_problem(db, tie) == "tie_at_limit"
    assert fp._gold_problem(db, empty) == "empty"
    assert fp._gold_problem(db, nulls) == "all_null"
    assert fp._gold_problem(db, ok) is None


def test_tenant_file_refuses_inline_password(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("tenant: x\nsource: {kind: postgresql, database: d, password: hunter2}\n")
    with pytest.raises(SystemExit, match="password_env"):
        fp.TenantSpec.load(p)
