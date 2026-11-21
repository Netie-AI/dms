# ruff: noqa: F811 - the imported fixtures are used by name
"""VQ-03 (#527 class, parent ruling 1): a verified query meets rule 16 and the total guard.

A certified figure that is wrong breaks rule 13 whoever wrote the SQL. Before
this, registration ran only the grant and hostile checks and replay only the
total guard, so the join rule never ran on this door: DEMO_FINANCE served
L0_CERTIFIED 39450 for 7890 (cross product), 630000 for 450000 (fan trap) and
55230 for 7890 (issue shape 6), and an ontology Space served L0 over a link
``verify()`` rejects. Now:

* register refuses with the named reason (HTTP 400), so the steward sees why;
* replay abstains with the named reason, so a query registered before this
  check never serves L0 either.

Every assertion is on the customer envelope from ``POST /v1/chat/ask``
(rule 10a), and on the figure the lake gives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
import test_onto_derive_01 as od
import test_vq02_verified_register as v
from cortex_contract.execution import QueryResult
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import verified_queries
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from test_onto_derive_01 import store  # noqa: F401 - fixture
from test_vq02_verified_register import minter, warehouse  # noqa: F401 - fixtures

#: name -> (sql, the wrong figure the parent served, the table's figure).
DEMO_WRONG: dict[str, tuple[str, float, float]] = {
    "cross_product_with_locations": (
        "SELECT SUM(i.quantity_kg) AS total FROM inventory i, locations l",
        39450.0,
        7890.0,
    ),
    "fan_trap_over_a_declared_link": (
        "SELECT SUM(l.capacity_kg) AS total FROM inventory i "
        "JOIN locations l ON i.location_id = l.location_id",
        630000.0,
        450000.0,
    ),
    "issue_shape_6_cte_read_twice": (
        "WITH c AS (SELECT quantity_kg AS x FROM inventory) "
        "SELECT (SELECT SUM(x) FROM c) * COUNT(*) AS total FROM c",
        55230.0,
        7890.0,
    ),
}
DEMO_GOOD = (
    "SELECT l.name AS location, SUM(i.quantity_kg) AS total FROM inventory i "
    "JOIN locations l ON i.location_id = l.location_id GROUP BY l.name ORDER BY l.name"
)


def _lake_rows(lake: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def _lake_cortex(lake: Path) -> v._MarkerCortex:
    """Cortex that runs submitted SQL on the lake, so a wrong figure is the real one."""
    cortex = v._MarkerCortex()

    def submit(req: Any) -> QueryResult:
        cortex.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            rows = _lake_rows(lake, str((req.body or {}).get("sql")))
            return QueryResult(ok=True, status="ok", run_id="run_vq03", output={"rows": rows})
        return QueryResult(ok=True, status="bound", run_id="run_vq03_b")

    cortex.submit = submit  # type: ignore[method-assign]
    return cortex


def _sql_submits(cortex: v._MarkerCortex) -> list[str]:
    out = []
    for s in cortex.submits:
        plan = getattr(s, "plan", None)
        if isinstance(plan, dict) and plan.get("kind") == "sql":
            out.append(str((s.body or {}).get("sql")))
    return out


def _reasons(env: dict[str, Any]) -> str:
    return " ".join(map(str, env.get("assumptions") or [])) + " " + str(env.get("text") or "")


def _assert_no_figure(env: dict[str, Any], wrong: float) -> None:
    text = str(env.get("text") or "")
    for shown in (f"{wrong:g}", str(wrong), f"{wrong:,.0f}", str(int(wrong))):
        assert shown not in text, (shown, text)
    assert all(wrong not in row.values() for row in env.get("rows") or []), env.get("rows")


def _ask(client: Any, space_id: str, question: str) -> dict[str, Any]:
    r = client.post(
        "/v1/chat/ask", json={"question": question, "space_id": space_id, "session_id": "ses_vq03"}
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env


@pytest.mark.parametrize("name", sorted(DEMO_WRONG))
def test_a_demo_verified_query_that_multiplies_is_refused_at_register(
    name: str,
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    sql, wrong, true = DEMO_WRONG[name]
    assert _lake_rows(warehouse, sql) == [{"total": wrong}] and wrong != true
    v._gate_allows(monkeypatch)
    cortex = _lake_cortex(warehouse)
    client = v._live_client(warehouse, minter, monkeypatch, cortex)
    question = f"VQ-03 {name}: total?"
    reg = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": v.FINANCE, "question": question, "sql": sql},
    )
    assert reg.status_code == 400, reg.text
    detail = str(reg.json()["detail"])
    assert detail.startswith(("unverified_join:", "repeated_input:")), detail
    env = _ask(client, v.FINANCE, question)
    assert env["badge"] != "L0_CERTIFIED", env
    _assert_no_figure(env, wrong)
    assert sql not in _sql_submits(cortex)


@pytest.mark.parametrize("name", sorted(DEMO_WRONG))
def test_a_demo_verified_query_registered_before_the_check_abstains_at_replay(
    name: str,
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    sql, wrong, _true = DEMO_WRONG[name]
    v._gate_allows(monkeypatch)
    cortex = _lake_cortex(warehouse)
    client = v._live_client(warehouse, minter, monkeypatch, cortex)
    question = f"VQ-03 stored {name}: total?"
    with monkeypatch.context() as before:
        # The row as the parent stored it: no join rule at register.
        before.setattr(verified_queries, "space_sql_reason", lambda *_a, **_k: None, raising=False)
        reg = client.post(
            "/v1/studio/verified-queries",
            json={"space_id": v.FINANCE, "question": question, "sql": sql},
        )
        assert reg.status_code == 200, reg.text
    env = _ask(client, v.FINANCE, question)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True, env
    assert env["rows"] == [] and env["values"] == []
    reasons = _reasons(env)
    assert "unverified_join:" in reasons or "repeated_input:" in reasons, reasons
    _assert_no_figure(env, wrong)
    assert sql not in _sql_submits(cortex), "refused SQL reached Cortex submit"


def test_a_demo_verified_query_over_a_declared_link_still_serves_l0(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    v._gate_allows(monkeypatch)
    cortex = _lake_cortex(warehouse)
    client = v._live_client(warehouse, minter, monkeypatch, cortex)
    question = "VQ-03 inventory per location?"
    reg = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": v.FINANCE, "question": question, "sql": DEMO_GOOD},
    )
    assert reg.status_code == 200, reg.text
    env = _ask(client, v.FINANCE, question)
    truth = _lake_rows(warehouse, DEMO_GOOD)
    assert truth
    assert env["badge"] == "L0_CERTIFIED" and env["abstained"] is False, env
    assert od._multiset(env["rows"]) == od._multiset(truth)
    first = truth[0]
    assert str(first["location"]) in str(env["text"]), env["text"]


#: Ontology Space: name -> sql. Each served L0 on the parent.
ONTO_WRONG: dict[str, str] = {
    "join_over_the_link_verify_rejects": od.BROKEN_SQL,
    "join_on_the_wrong_columns": od.WRONG_JOIN_SQL,
    "schools_crossed_with_districts": (
        f"SELECT COUNT(*) AS total FROM {od.SCHOOLS} s, {od.DISTRICTS} d"
    ),
}


@pytest.mark.parametrize("stored_before", [False, True])
@pytest.mark.parametrize("name", sorted(ONTO_WRONG))
def test_an_ontology_space_verified_query_meets_its_ontology(
    name: str,
    stored_before: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql = ONTO_WRONG[name]
    rig = od._space(tmp_path, monkeypatch, minter)
    oracle = od._oracle(rig.lake, sql)
    assert oracle, name
    figure = next(iter(oracle[0].values()))
    question = f"VQ-03 onto {name} ({stored_before})?"
    with monkeypatch.context() as before:
        if stored_before:
            before.setattr(
                verified_queries, "space_sql_reason", lambda *_a, **_k: None, raising=False
            )
        reg = rig.client.post(
            "/v1/studio/verified-queries",
            json={"space_id": rig.space_id, "question": question, "sql": sql},
        )
    if not stored_before:
        assert reg.status_code == 400, reg.text
        assert str(reg.json()["detail"]).startswith("unverified_join:"), reg.text
    else:
        assert reg.status_code == 200, reg.text
    r = rig.client.post(
        "/v1/chat/ask", json={"question": question, "space_id": rig.space_id, "session_id": "s"}
    )
    env = r.json()
    assert_envelope_valid(env)
    assert env["badge"] != "L0_CERTIFIED", env
    assert env["rows"] == [] or figure not in [next(iter(x.values())) for x in env["rows"]]
    if stored_before:
        assert env["badge"] == "ABSTAIN" and env["abstained"] is True, env
        assert "unverified_join:" in od._reasons(env), od._reasons(env)
        assert env["rows"] == [] and env["values"] == []


def test_an_ontology_space_verified_query_over_the_verified_link_still_serves_l0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    rig = od._space(tmp_path, monkeypatch, minter)
    truth = od._oracle(rig.lake, od.VERIFIED_SQL)
    assert truth == [{"school_count": 3}]
    reg = rig.client.post(
        "/v1/studio/verified-queries",
        json={"space_id": rig.space_id, "question": od.VERIFIED_Q, "sql": od.VERIFIED_SQL},
    )
    assert reg.status_code == 200, reg.text
    r = rig.client.post(
        "/v1/chat/ask",
        json={"question": od.VERIFIED_Q, "space_id": rig.space_id, "session_id": "s"},
    )
    env = r.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED", (env["badge"], env.get("text"), od._reasons(env))
    assert env["rows"] == truth
    assert "3" in str(env["text"])


# --- parent ruling on the VQ-03 verifier's routes: same verdict as the ask doors ---------


def _register(
    client: Any, space: str, question: str, sql: str, *, stored_before: bool = False
) -> Any:
    with pytest.MonkeyPatch.context() as before:
        if stored_before:
            before.setattr(
                verified_queries, "space_sql_reason", lambda *_a, **_k: None, raising=False
            )
        return client.post(
            "/v1/studio/verified-queries",
            json={"space_id": space, "question": question, "sql": sql},
        )


def _ask_no_space(client: Any, question: str) -> dict[str, Any]:
    r = client.post("/v1/chat/ask", json={"question": question, "session_id": "ses_vq03_cd"})
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env


def test_a_company_default_verified_join_over_a_declared_link_registers_and_serves_l0(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) The UI registers "Company (default ACL)" as ``company-default``: the ask
    with no Space. That ask meets the demo pack's ontology and answers this join,
    so register must accept it (refused ``unverified_join:no_ontology`` at 7ffbe45)."""
    v._gate_allows(monkeypatch)
    cortex = _lake_cortex(warehouse)
    client = v._live_client(warehouse, minter, monkeypatch, cortex)
    question = "VQ-03 company inventory per location?"
    reg = _register(client, "company-default", question, DEMO_GOOD)
    assert reg.status_code == 200, reg.text
    env = _ask_no_space(client, question)
    truth = _lake_rows(warehouse, DEMO_GOOD)
    assert env["badge"] == "L0_CERTIFIED" and env["abstained"] is False, env
    assert od._multiset(env["rows"]) == od._multiset(truth)
    assert str(truth[0]["location"]) in str(env["text"])


def test_a_company_default_verified_cross_product_is_still_refused(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql, wrong, _true = DEMO_WRONG["cross_product_with_locations"]
    v._gate_allows(monkeypatch)
    cortex = _lake_cortex(warehouse)
    client = v._live_client(warehouse, minter, monkeypatch, cortex)
    question = "VQ-03 company cross?"
    reg = _register(client, "company-default", question, sql)
    assert reg.status_code == 400, reg.text
    assert str(reg.json()["detail"]).startswith("unverified_join:"), reg.text
    stored = "VQ-03 company cross stored?"
    assert _register(client, "company-default", stored, sql, stored_before=True).status_code == 200
    env = _ask_no_space(client, stored)
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == [], env
    _assert_no_figure(env, wrong)
    assert sql not in _sql_submits(cortex)


_CAPS = b"location_id,cap\nWH-A,100\nWH-B,200\nWH-C,300\nWH-D,400\nWH-E,500\n"
#: (c) Joins to an uploaded table nobody ticked: the ask reads the demo spine only.
UNTICKED: dict[str, str] = {
    "on_true_cross_with_an_upload": (
        "SELECT SUM(i.quantity_kg) AS total FROM inventory i JOIN {u} u ON true"
    ),
    "fan_trap_onto_an_upload": (
        "SELECT SUM(u.cap) AS total FROM inventory i JOIN {u} u ON i.location_id = u.location_id"
    ),
}


@pytest.mark.parametrize("name", sorted(UNTICKED))
def test_a_demo_verified_join_to_an_unticked_upload_is_refused_and_abstains(
    name: str, warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) Registered 200 at 7ffbe45 (the demo check skips tables outside the demo
    ontology) and replayed as an HTTP 503 with no envelope. Now: refused at
    register with the grant reason, and a row stored before abstains."""
    from dms_executor.bronze import ingest_csv_bytes

    rec = ingest_csv_bytes(filename="caps.csv", data=_CAPS, path=warehouse, space_id=v.FINANCE)
    sql = UNTICKED[name].format(u=rec.table)
    v._gate_allows(monkeypatch)
    cortex = _lake_cortex(warehouse)
    client = v._live_client(warehouse, minter, monkeypatch, cortex)
    question = f"VQ-03 upload {name}?"
    reg = _register(client, v.FINANCE, question, sql)
    assert reg.status_code == 400, reg.text
    assert str(reg.json()["detail"]).startswith("validate:"), reg.text
    stored = f"VQ-03 upload stored {name}?"
    assert _register(client, v.FINANCE, stored, sql, stored_before=True).status_code == 200
    env = _ask(client, v.FINANCE, stored)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True, env
    assert env["rows"] == [] and env["values"] == []
    assert "validate:" in _reasons(env), _reasons(env)
    assert sql not in _sql_submits(cortex)
