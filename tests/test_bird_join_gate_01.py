"""BIRD-JOIN-GATE-01 and BIRD-FK-ORPHANS-01. Synthetic schemas only.

A junction table with two declared foreign keys and no primary key keeps
those links when DMS_JOIN_DECLARED_FK is 1. The parent side is measured.
Flag off, derive matches the unset process.

A few orphan rows are a disclosed exclusion only when a steward set a ratio
above 0 and DMS_FK_ORPHAN_DISCLOSE is 1. The default ratio stays 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import duckdb
import pytest
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.join_flags import (
    bind_orphan_space,
    clear_orphan_thresholds,
    fk_orphan_disclose_enabled,
    join_declared_fk_enabled,
    reset_orphan_space,
    set_orphan_threshold,
)
from dms_executor.join_repair import (
    bind_repair_transport,
    repair_request,
    reset_repair_transport,
)
from dms_executor.ontology import from_manifest
from dms_executor.space_ontology import derive_and_verify

HERO = "main.hero"
POWER = "main.power"
JUNCTION = "main.hero_power"


def _catalog() -> dict[str, Any]:
    def table(name: str) -> dict[str, Any]:
        leaf = name.split(".", 1)[1]
        return {
            "schema": "main",
            "table": leaf,
            "path": name,
            "source_table": name,
        }

    return {
        "source": "synthetic",
        "tables": [table(HERO), table(POWER), table(JUNCTION)],
        "primary_keys": {HERO: ["id"], POWER: ["id"]},
        "foreign_keys": [
            {
                "name": "hero_power_hero",
                "from_table": JUNCTION,
                "from_column": "hero_id",
                "to_table": HERO,
                "to_column": "id",
            },
            {
                "name": "hero_power_power",
                "from_table": JUNCTION,
                "from_column": "power_id",
                "to_table": POWER,
                "to_column": "id",
            },
        ],
        "unlanded_links": [],
    }


def _land(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE hero (id INTEGER, name VARCHAR)")
        con.execute("CREATE TABLE power (id INTEGER, name VARCHAR)")
        con.execute("CREATE TABLE hero_power (hero_id INTEGER, power_id INTEGER)")
        con.execute("INSERT INTO hero VALUES (1, 'a'), (2, 'b')")
        con.execute("INSERT INTO power VALUES (10, 'fly'), (20, 'strong')")
        con.execute("INSERT INTO hero_power VALUES (1, 10), (1, 20), (2, 10)")
    finally:
        con.close()


def _body(path: Path) -> dict[str, Any]:
    body, _violations = derive_and_verify(_catalog(), warehouse=path)
    return body


def _stable(body: dict[str, Any]) -> str:
    kept = {
        "objects": body.get("objects"),
        "links": body.get("links"),
        "unkeyed_tables": body.get("unkeyed_tables"),
        "measures": body.get("measures"),
    }
    return json.dumps(kept, sort_keys=True, default=str)


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_JOIN_DECLARED_FK", raising=False)
    monkeypatch.delenv("DMS_FK_ORPHAN_DISCLOSE", raising=False)
    clear_orphan_thresholds()


def test_only_digit_one_enables_the_join_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    assert join_declared_fk_enabled() is False
    for raw in ("0", "true", "yes", "on", "2", ""):
        monkeypatch.setenv("DMS_JOIN_DECLARED_FK", raw)
        assert join_declared_fk_enabled() is False
    monkeypatch.setenv("DMS_JOIN_DECLARED_FK", "1")
    assert join_declared_fk_enabled() is True


def test_flag_off_drops_the_keyless_child_and_matches_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "off.duckdb"
    _land(lake)
    unset = _stable(_body(lake))
    monkeypatch.setenv("DMS_JOIN_DECLARED_FK", "0")
    assert _stable(_body(lake)) == unset
    onto = from_manifest(_catalog(), relation_for=lambda schema, table: f"{schema}.{table}")
    assert JUNCTION not in onto.objects
    assert onto.links == {}


def test_flag_on_keeps_declared_fks_on_a_keyless_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_JOIN_DECLARED_FK", "1")
    lake = tmp_path / "on.duckdb"
    _land(lake)
    body, violations = derive_and_verify(_catalog(), warehouse=lake)
    assert JUNCTION in body["objects"]
    assert body["objects"][JUNCTION]["key"] == []
    assert set(body["links"]) == {"hero_power_hero", "hero_power_power"}
    for spec in body["links"].values():
        assert spec["cardinality"] == "many_to_one"
        assert spec["from"] == JUNCTION
    assert not any(v.check == "fk_intact" for v in violations)
    assert not any(v.subject in body["links"] for v in violations)


FAN_SQL = (
    "SELECT COUNT(h.id) AS hero_count FROM main.hero h "
    "JOIN main.hero_power hp ON hp.hero_id = h.id"
)
FIXED_SQL = (
    "SELECT COUNT(DISTINCT h.id) AS hero_count FROM main.hero h "
    "JOIN main.hero_power hp ON hp.hero_id = h.id"
)
HERO_Q = "How many heroes have a linked power?"
SPACE = "11111111-1111-4111-8111-111111111111"


def _submitter(path: Path, seen: list[str]) -> Any:
    def submit(sql: str) -> Any:
        seen.append(sql)
        con = duckdb.connect(str(path), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_bird", output={"rows": rows})

    return submit


def _ask(
    lake: Path,
    sql: str,
    *,
    question: str,
    transport: Any,
    prompts: list[str],
    submitted: list[str],
    ontology: Any,
    space_id: str = SPACE,
) -> dict[str, Any]:
    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        prompts.append(json.dumps(ctx, sort_keys=True, default=str))
        return {"query_sql": sql, "plan_source": "ontology_plan"}

    token = bind_repair_transport(transport)
    try:
        env = maybe_generative_ask(
            question,
            warehouse=lake,
            grantable={"hero", "power", "hero_power", "parent", "child"},
            compute=compute,
            submit=_submitter(lake, submitted),
            ledger_append=lambda _p: SimpleNamespace(entry_id="led_bird", hash="hash_bird_x"),
            ontology=ontology,
            dialect="duckdb",
            space_id=space_id,
            session_id="sess-bird",
            demo_ontology_allowed=False,
            demo_gate=False,
        )
    finally:
        reset_repair_transport(token)
    assert env is not None
    assert_envelope_valid(env)
    return env


def _stable_env(env: dict[str, Any]) -> str:
    kept = {
        "badge": env.get("badge"),
        "abstained": env.get("abstained"),
        "route": env.get("route"),
        "served_route": env.get("served_route"),
        "rows": env.get("rows"),
        "sql_used": env.get("sql_used"),
        "text": env.get("text"),
        "assumptions": env.get("assumptions"),
        "reconfirm": env.get("reconfirm"),
    }
    return json.dumps(kept, sort_keys=True, default=str)


def test_repair_request_sends_a_role_and_no_model_id() -> None:
    body = repair_request(FAN_SQL, "unverified_join:fan_trap (main.hero)")
    assert body["role"] == "sql"
    assert "model" not in body and "provider" not in body and "strict" not in body
    assert "question" not in body["messages"][1]["content"]


def test_flag_off_fan_trap_envelope_matches_unset_and_does_not_call_the_generator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "fan.duckdb"
    _land(lake)
    calls: list[dict[str, Any]] = []

    def transport(body: dict[str, Any]) -> dict[str, str]:
        calls.append(body)
        return {"sql": FIXED_SQL}

    def once(flag: str | None) -> tuple[str, str]:
        calls.clear()
        if flag is None:
            monkeypatch.delenv("DMS_JOIN_DECLARED_FK", raising=False)
        else:
            monkeypatch.setenv("DMS_JOIN_DECLARED_FK", flag)
        prompts: list[str] = []
        submitted: list[str] = []
        onto = from_manifest(_catalog(), relation_for=lambda schema, table: f"{schema}.{table}")
        env = _ask(
            lake,
            FAN_SQL,
            question=HERO_Q,
            transport=transport,
            prompts=prompts,
            submitted=submitted,
            ontology=onto,
        )
        assert calls == []
        assert submitted == []
        assert env["abstained"] is True and env["rows"] == []
        assert "unverified_join" in str(env.get("text") or "")
        return _stable_env(env), prompts[0]

    unset_env, unset_prompt = once(None)
    off_env, off_prompt = once("0")
    assert off_env == unset_env
    assert off_prompt == unset_prompt


def test_fan_trap_is_repaired_and_served_only_after_the_gates_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_JOIN_DECLARED_FK", "1")
    lake = tmp_path / "fix.duckdb"
    _land(lake)
    seen: list[dict[str, Any]] = []

    def transport(body: dict[str, Any]) -> dict[str, str]:
        seen.append(body)
        return {"sql": FIXED_SQL}

    submitted: list[str] = []
    onto = from_manifest(_catalog(), relation_for=lambda schema, table: f"{schema}.{table}")
    env = _ask(
        lake,
        FAN_SQL,
        question=HERO_Q,
        transport=transport,
        prompts=[],
        submitted=submitted,
        ontology=onto,
    )
    assert len(seen) == 1
    assert seen[0]["role"] == "sql"
    assert "model" not in seen[0]
    assert submitted == [FIXED_SQL]
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"] == [{"hero_count": 2}]
    assert "2" in env["text"]


def test_a_still_unsafe_repair_reconfirms_and_names_both_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_JOIN_DECLARED_FK", "1")
    lake = tmp_path / "unsafe.duckdb"
    _land(lake)

    def transport(_body: dict[str, Any]) -> dict[str, str]:
        return {"sql": FAN_SQL}

    submitted: list[str] = []
    onto = from_manifest(_catalog(), relation_for=lambda schema, table: f"{schema}.{table}")
    env = _ask(
        lake,
        FAN_SQL,
        question=HERO_Q,
        transport=transport,
        prompts=[],
        submitted=submitted,
        ontology=onto,
    )
    assert submitted == []
    assert env["abstained"] is True and env["rows"] == []
    block = env["reconfirm"]
    assert block["closest_question"]
    assert "main.hero" in env["text"] and "main.hero_power" in env["text"]
    assert block["suggested_question"] == block["closest_question"]


def _orphan_catalog() -> dict[str, Any]:
    def table(name: str) -> dict[str, Any]:
        leaf = name.split(".", 1)[1]
        return {"schema": "main", "table": leaf, "path": name, "source_table": name}

    parent, child = "main.parent", "main.child"
    return {
        "source": "synthetic",
        "tables": [table(parent), table(child)],
        "primary_keys": {parent: ["id"], child: ["id"]},
        "foreign_keys": [
            {
                "name": "child_parent",
                "from_table": child,
                "from_column": "parent_id",
                "to_table": parent,
                "to_column": "id",
            }
        ],
        "unlanded_links": [],
    }


def _land_orphans(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE parent (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO parent SELECT range, 'p' FROM range(1, 11)")
        con.execute("CREATE TABLE child (id INTEGER, parent_id INTEGER)")
        con.execute(
            "INSERT INTO child SELECT range, ((range - 1) % 10) + 1 FROM range(1, 399)"
        )
        con.execute("INSERT INTO child VALUES (399, 999), (400, 998)")
    finally:
        con.close()


ORPHAN_SQL = (
    "SELECT COUNT(*) AS n FROM main.child c "
    "JOIN main.parent p ON c.parent_id = p.id"
)
ORPHAN_Q = "How many child rows match a parent?"


def _orphan_onto() -> Any:
    return from_manifest(_orphan_catalog(), relation_for=lambda schema, table: f"{schema}.{table}")


def test_only_digit_one_enables_the_orphan_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    assert fk_orphan_disclose_enabled() is False
    for raw in ("0", "true", "yes", "on", ""):
        monkeypatch.setenv("DMS_FK_ORPHAN_DISCLOSE", raw)
        assert fk_orphan_disclose_enabled() is False
    monkeypatch.setenv("DMS_FK_ORPHAN_DISCLOSE", "1")
    assert fk_orphan_disclose_enabled() is True


def test_orphan_threshold_zero_matches_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "orphans.duckdb"
    _land_orphans(lake)
    space = str(uuid4())

    def run(flag: str | None, ratio: float) -> str:
        if flag is None:
            monkeypatch.delenv("DMS_FK_ORPHAN_DISCLOSE", raising=False)
        else:
            monkeypatch.setenv("DMS_FK_ORPHAN_DISCLOSE", flag)
        set_orphan_threshold(space, ratio, actor="steward-1")
        token = bind_orphan_space(space)
        try:
            env = _ask(
                lake,
                ORPHAN_SQL,
                question=ORPHAN_Q,
                transport=lambda _b: {"sql": ORPHAN_SQL},
                prompts=[],
                submitted=[],
                ontology=_orphan_onto(),
                space_id=space,
            )
        finally:
            reset_orphan_space(token)
        assert env["abstained"] is True and env["rows"] == []
        assert "fk_intact" in str(env.get("text") or "")
        return _stable_env(env)

    # A stored ratio does nothing while the flag is off. Threshold 0 refuses too.
    unset = run(None, 0.01)
    assert run("0", 0.01) == unset
    assert run("1", 0.0) == unset


def test_below_threshold_serves_with_the_exclusion_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FK_ORPHAN_DISCLOSE", "1")
    lake = tmp_path / "disclose.duckdb"
    _land_orphans(lake)
    space = str(uuid4())
    set_orphan_threshold(space, 0.01, actor="steward-ada")
    token = bind_orphan_space(space)
    try:
        env = _ask(
            lake,
            ORPHAN_SQL,
            question=ORPHAN_Q,
            transport=lambda _b: {"sql": ORPHAN_SQL},
            prompts=[],
            submitted=[],
            ontology=_orphan_onto(),
            space_id=space,
        )
    finally:
        reset_orphan_space(token)
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"] == [{"n": 398}]
    reasons = env["audit_receipt"]["exclude"]["reasons"]
    assert any(
        item.get("detail") == "2 child rows reference a missing parent and are excluded by the join"
        for item in reasons
    )
    assert "999" not in json.dumps(env["audit_receipt"])


def test_above_threshold_reconfirms_with_the_measured_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FK_ORPHAN_DISCLOSE", "1")
    lake = tmp_path / "hold.duckdb"
    _land_orphans(lake)
    space = str(uuid4())
    set_orphan_threshold(space, 0.001, actor="steward-ada")
    token = bind_orphan_space(space)
    submitted: list[str] = []
    try:
        env = _ask(
            lake,
            ORPHAN_SQL,
            question=ORPHAN_Q,
            transport=lambda _b: {"sql": ORPHAN_SQL},
            prompts=[],
            submitted=submitted,
            ontology=_orphan_onto(),
            space_id=space,
        )
    finally:
        reset_orphan_space(token)
    assert submitted == []
    assert env["abstained"] is True and env["rows"] == []
    assert env["reconfirm"]["closest_question"]
    text = str(env.get("text") or "")
    assert "2" in text and "400" in text
    assert "999" not in text and "998" not in text


def test_steward_route_records_the_server_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_FK_ORPHAN_DISCLOSE", "1")
    from dms_api.routes.spaces import OrphanThresholdIn, put_orphan_threshold

    seen: dict[str, Any] = {}

    def gate(**kwargs: Any) -> SimpleNamespace:
        seen["gate"] = kwargs
        return SimpleNamespace(allowed=True, reason="ok")

    monkeypatch.setattr("dms_api.routes.spaces.compliance_gate", gate)
    space = str(uuid4())
    out = put_orphan_threshold(
        space,
        OrphanThresholdIn(ratio=0.01),
        SimpleNamespace(get=lambda _id: object()),
        cortex=None,
        settings=SimpleNamespace(dms_actor_user_id="steward-ada"),
    )
    assert out["set_by"] == "steward-ada"
    assert out["ratio"] == 0.01
    assert seen["gate"]["actor"] == "steward-ada"
    assert seen["gate"]["action"] == "spaces.orphan_threshold.set"
