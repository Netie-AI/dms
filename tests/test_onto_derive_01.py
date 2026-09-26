# ruff: noqa: E501 - the adversary SQL shapes read best one per line
"""ONTO-DERIVE-01 (dms#277 CONNECT-ASK-01 change 1): a SQL-source Space's own ontology.

Before: nothing derived or stored an ontology for a SQL-source Space;
``OntologyStore`` had no non-test caller and the ask path ran with none.

Here a non-demo Space ingests a PostgreSQL source through the real route
(``POST /v1/studio/sources/sql``): three tables, two declared foreign keys, one
of them planted broken (enrollment rows whose school does not exist, the shape
a ``NOT VALID`` constraint over legacy rows leaves behind). The source catalog
is replayed through the connector's fake DB-API connection (CI has no
Postgres); ``tests/control_plane/test_onto_derive_01_pg.py`` runs the same
scenario against a real Postgres source and the Postgres ontology store.

Every answer-path case posts ``POST /v1/chat/ask`` and asserts the customer
envelope (``assert_envelope_valid``, badge, rendered text, rows); the Cortex
fake executes submits on the lake under the Cortex grant rule, so rows are real.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import IntermediateKey, ManifestMinter
from dms_executor.space_ontology import set_ontology_store, unverified_join_reason
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install
from test_space_gen_01 import _multiset, _reasons, _RecordingCortex

SCHOOLS = "bronze.public_schools"
DISTRICTS = "bronze.public_districts"
ENROLL = "bronze.public_enrollments"
SPACE_TABLES = {SCHOOLS, DISTRICTS, ENROLL}
GOOD_LINK = "schools_district_id_fkey"
BROKEN_LINK = "enrollments_school_id_fkey"

DISTRICT_ROWS = [["1", "North"], ["2", "South"]]
SCHOOL_ROWS = [
    ["1", "1", "Alder"],
    ["2", "1", "Birch"],
    ["3", "2", "Cedar"],
    ["4", "1", "Dogwood"],
]
# Rows 3 and 4 name schools 7 and 9, which do not exist: the planted orphans.
ENROLL_ROWS = [["100", "1", "30"], ["101", "2", "25"], ["102", "7", "40"], ["103", "9", "12"]]

PG_TABLES = [("public", "districts"), ("public", "schools"), ("public", "enrollments")]
PG_PKS = [
    ("public", "districts", "district_id", 1),
    ("public", "schools", "school_id", 1),
    ("public", "enrollments", "enrollment_id", 1),
]
PG_FKS = [
    (GOOD_LINK, "public", "schools", "district_id", "public", "districts", "district_id", 1),
    (BROKEN_LINK, "public", "enrollments", "school_id", "public", "schools", "school_id", 1),
]
TABLE_DATA = {
    '"public"."districts"': (["district_id", "name"], DISTRICT_ROWS),
    '"public"."schools"': (["school_id", "district_id", "name"], SCHOOL_ROWS),
    '"public"."enrollments"': (["enrollment_id", "school_id", "students"], ENROLL_ROWS),
}

VERIFIED_Q = "How many schools are in the North district?"
VERIFIED_SQL = (
    "SELECT COUNT(*) AS school_count FROM bronze.public_schools s "
    "JOIN bronze.public_districts d ON s.district_id = d.district_id "
    "WHERE d.name = 'North'"
)
BROKEN_Q = "How many students are enrolled across all schools?"
BROKEN_SQL = (
    "SELECT SUM(CAST(e.students AS INTEGER)) AS students "
    "FROM bronze.public_enrollments e JOIN bronze.public_schools s "
    "ON e.school_id = s.school_id"
)
# Joins schools to districts on the school id: parses, grants, executes, and
# counts schools against the wrong district. Confident and wrong.
WRONG_JOIN_SQL = (
    "SELECT COUNT(*) AS school_count FROM bronze.public_schools s "
    "JOIN bronze.public_districts d ON s.school_id = d.district_id "
    "WHERE d.name = 'North'"
)


def make_minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    """The real ``mint_manifest`` on a local key (as ``test_space_gen_01``)."""
    m = ManifestMinter()
    key = IntermediateKey(
        kid="test-kid",
        private_key=Ed25519PrivateKey.generate(),
        not_after=datetime.now(UTC) + timedelta(hours=1),
    )
    monkeypatch.setattr(m, "_ensure_key", lambda: key)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: key)
    monkeypatch.setattr(m, "close", lambda: None)
    return m


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


class _Space:
    def __init__(self, client: TestClient, cortex: _RecordingCortex, space_id: str, lake: Path):
        self.client = client
        self.cortex = cortex
        self.space_id = space_id
        self.lake = lake
        self.receipt: dict[str, Any] = {}

    def ask(self, question: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.cortex.payload = payload
        r = self.client.post(
            "/v1/chat/ask",
            json={"question": question, "session_id": "ses_onto_derive", "space_id": self.space_id},
        )
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.spaces as space_routes
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    def _allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    monkeypatch.setattr(space_routes, "compliance_gate", _allow)


def _space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    name: str = "District schools",
    enroll_rows: list[list[str]] | None = None,
) -> _Space:
    lake = tmp_path / "onto_derive.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    get_settings.cache_clear()
    data = dict(TABLE_DATA)
    if enroll_rows is not None:
        data['"public"."enrollments"'] = (["enrollment_id", "school_id", "students"], enroll_rows)
    _install(monkeypatch, _FakeConnection(PG_TABLES, data, pks=PG_PKS, fks=PG_FKS))
    _gate_allows(monkeypatch)
    cortex = _RecordingCortex(warehouse=lake, payload={})
    app = create_app()
    space = app.state.space_store.create(name)
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    rig = _Space(client, cortex, space.id, lake)
    r = client.post(
        "/v1/studio/sources/sql",
        json={
            "kind": "postgresql",
            "host": "db.example.net",
            "database": "schools",
            "user": "reader",
            "password": "pw-never-echoed",
            "space_id": space.id,
        },
    )
    assert r.status_code == 200, r.text
    assert "pw-never-echoed" not in r.text
    rig.receipt = r.json()
    return rig


def _oracle(lake: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [str(c[0]) for c in (cur.description or [])]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def _assert_abstain(env: dict[str, Any], gap: str) -> str:
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    assert f"gap: {gap}" in text, text
    return text


# --- derive on ingest, and the customer view -------------------------------------


def test_ingest_derives_verifies_and_stores_the_space_ontology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    onto = rig.receipt["ontology"]
    assert onto["derived"] is True, onto
    assert onto["version_status"] == "active" and onto["answers_asks"] is True
    assert onto["verified"] is False  # one link failed; the Space is not blessed whole
    assert {o["name"] for o in onto["objects"]} == SPACE_TABLES

    body = rig.client.get(f"/v1/spaces/{rig.space_id}/ontology")
    assert body.status_code == 200, body.text
    view = body.json()
    assert view["derived"] is True
    (only,) = view["ontologies"]
    objects = {o["name"]: o for o in only["objects"]}
    assert objects[SCHOOLS]["key"] == ["school_id"]
    assert objects[SCHOOLS]["source_table"] == "public.schools"
    assert {a["name"] for a in objects[SCHOOLS]["attributes"]} == {
        "school_id",
        "district_id",
        "name",
    }
    assert all(a["type"] for a in objects[SCHOOLS]["attributes"])
    assert all(o["status"] == "verified" for o in objects.values()), objects
    links = {link["name"]: link for link in only["links"]}
    assert set(links) == {GOOD_LINK, BROKEN_LINK}
    good, broken = links[GOOD_LINK], links[BROKEN_LINK]
    assert good["status"] == "verified" and good["cardinality"] == "many_to_one"
    assert good["from"] == SCHOOLS and good["to"] == DISTRICTS
    assert good["violations"] == []
    assert broken["status"] == "unverified"
    (why,) = broken["violations"]
    assert why["check"] == "fk_intact" and why["subject"] == BROKEN_LINK
    assert "2 rows" in why["detail"] and "do" in why["detail"]
    assert only["measures"] == []

    # Stored versioned through OntologyStore, with its violations.
    (version,) = store.active_for_space(__import__("uuid").UUID(rig.space_id))
    snap = store.latest_snapshot(version.id)
    assert snap is not None and not snap.verified
    assert [v["subject"] for v in snap.violations] == [BROKEN_LINK]


# --- the ask path uses it ----------------------------------------------------------


def test_join_over_the_verified_link_answers_with_oracle_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    env = rig.ask(VERIFIED_Q, {"query_sql": VERIFIED_SQL, "plan_source": "ontology_plan"})

    oracle = _oracle(rig.lake, VERIFIED_SQL)
    assert oracle == [{"school_count": 3}]
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset(oracle)
    assert "school_count=3" in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == [VERIFIED_SQL]

    # DMS sent this Space's stored ontology to Cortex: objects with keys, only
    # the verified link, no measures, source=space. Never the demo ontology.
    sent = rig.cortex.insights[-1]["ontology"]
    assert sent["source"] == "space"
    assert sent["objects"] == {
        DISTRICTS: {"key": ["district_id"]},
        SCHOOLS: {"key": ["school_id"]},
        ENROLL: {"key": ["enrollment_id"]},
    }
    assert sent["links"] == {
        GOOD_LINK: {"from": SCHOOLS, "to": DISTRICTS, "cardinality": "many_to_one"}
    }
    assert sent["measures"] == {}
    assert sent["verified"] is False
    assert set(rig.cortex.insights[-1]["parsed"]["tables"]) == SPACE_TABLES


def test_question_needing_the_broken_link_abstains_naming_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    env = rig.ask(BROKEN_Q, {"query_sql": BROKEN_SQL, "plan_source": "ontology_plan"})

    text = _assert_abstain(env, f"ontology_unverified: fk_intact on {BROKEN_LINK}")
    assert "2 rows" in text, text
    assert rig.cortex.executed == [], "a join over a broken link reached Cortex submit"
    # The plausible wrong answer really exists: the inner join silently drops
    # the 52 students whose school is missing (107 enrolled, 55 reported).
    assert _oracle(rig.lake, BROKEN_SQL) == [{"students": 55}]


def test_confident_wrong_join_abstains_as_unverified_join(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    # It runs and returns a number: that is the danger.
    assert _oracle(rig.lake, WRONG_JOIN_SQL) == [{"school_count": 1}]
    env = rig.ask(VERIFIED_Q, {"query_sql": WRONG_JOIN_SQL, "plan_source": "ontology_plan"})

    _assert_abstain(env, "unverified_join")
    assert "unverified_join:bronze.public_districts x bronze.public_schools joined on columns" in (
        _reasons(env)
    ), env.get("assumptions")
    assert rig.cortex.executed == []


def test_typed_plan_needing_an_undeclared_measure_abstains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    env = rig.ask(
        "What is the total enrollment by district?",
        {
            "query_plan": {"measure": "total_students", "group_by": [["districts", "name"]]},
            "plan_source": "ontology_plan",
        },
    )
    text = _assert_abstain(env, "no_declared_measure")
    assert "total_students" not in text, "a model-chosen measure name reached the customer"
    assert "no_declared_measure: total_students" in _reasons(env)
    assert rig.cortex.executed == []


def test_rederive_after_data_repair_verifies_the_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    con = duckdb.connect(str(rig.lake))
    try:
        con.execute(f"DELETE FROM {ENROLL} WHERE school_id IN ('7', '9')")
    finally:
        con.close()
    r = rig.client.post(f"/v1/spaces/{rig.space_id}/ontology/derive")
    assert r.status_code == 200, r.text
    (out,) = r.json()["ontologies"]
    assert out["verified"] is True
    assert all(link["status"] == "verified" for link in out["links"])
    view = rig.client.get(f"/v1/spaces/{rig.space_id}/ontology").json()
    (only,) = view["ontologies"]
    assert {link["name"]: link["status"] for link in only["links"]} == {
        GOOD_LINK: "verified",
        BROKEN_LINK: "verified",
    }

    env = rig.ask(BROKEN_Q, {"query_sql": BROKEN_SQL, "plan_source": "ontology_plan"})
    oracle = _oracle(rig.lake, BROKEN_SQL)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert _multiset(env["rows"]) == _multiset(oracle)


def test_link_broken_after_derive_is_refused_at_ask_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """The ask re-measures the stored ontology; yesterday's verdict is not trusted."""
    rig = _space(tmp_path, monkeypatch, minter)
    con = duckdb.connect(str(rig.lake))
    try:
        con.execute(
            f"INSERT INTO {SCHOOLS} (school_id, district_id, name) VALUES ('5', '99', 'Elm')"
        )
    finally:
        con.close()
    env = rig.ask(VERIFIED_Q, {"query_sql": VERIFIED_SQL, "plan_source": "ontology_plan"})
    _assert_abstain(env, f"ontology_unverified: fk_intact on {GOOD_LINK}")
    assert rig.cortex.executed == []


def test_space_without_a_derived_ontology_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    other = rig.client.post("/v1/spaces", json={"name": "Empty"})
    assert other.status_code == 201, other.text
    other_id = other.json()["space"]["id"]
    view = rig.client.get(f"/v1/spaces/{other_id}/ontology").json()
    assert view["derived"] is False and view["ontologies"] == []
    assert "no ontology has been derived" in view["reason"]
    r = rig.client.post(f"/v1/spaces/{other_id}/ontology/derive")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "no_catalog"


# --- unit: the join rule ---------------------------------------------------------


def test_join_rule_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    from dms_executor.space_ontology import load_space_ontology, relation_columns

    rig = _space(tmp_path, monkeypatch, minter)
    onto = load_space_ontology(rig.space_id)
    assert onto is not None
    con = duckdb.connect(str(rig.lake))
    try:
        violations = onto.verify(con)
    finally:
        con.close()
    cols = relation_columns(onto, rig.lake)

    def why(sql: str) -> str | None:
        return unverified_join_reason(sql, onto, violations, columns_of=cols)

    assert why(VERIFIED_SQL) is None
    assert why("SELECT COUNT(*) FROM bronze.public_schools") is None
    # Either orientation of the equality is the same link.
    assert (
        why(VERIFIED_SQL.replace("s.district_id = d.district_id", "d.district_id = s.district_id"))
        is None
    )
    # Derived table over the key, correlated EXISTS, IN-subquery on the link: all the link.
    assert (
        why(
            "SELECT d.name, x.n FROM bronze.public_districts d JOIN (SELECT district_id, "
            "COUNT(*) AS n FROM bronze.public_schools GROUP BY district_id) x "
            "ON x.district_id = d.district_id"
        )
        is None
    )
    assert (
        why(
            "SELECT COUNT(*) FROM bronze.public_districts d WHERE EXISTS (SELECT 1 FROM "
            "bronze.public_schools s WHERE s.district_id = d.district_id)"
        )
        is None
    )
    assert (
        why(
            "SELECT COUNT(*) FROM bronze.public_districts WHERE district_id IN "
            "(SELECT district_id FROM bronze.public_schools)"
        )
        is None
    )
    assert (why(WRONG_JOIN_SQL) or "").startswith("unverified_join:")
    assert (why(BROKEN_SQL) or "") == f"unverified_join:link {BROKEN_LINK} is not verified"
    assert (
        why("SELECT COUNT(*) FROM bronze.public_schools, bronze.public_districts") or ""
    ).startswith("unverified_join:cross_product")
    assert (
        why(
            "SELECT COUNT(*) FROM bronze.public_schools s JOIN bronze.public_districts d "
            "ON s.district_id > d.district_id"
        )
        or ""
    ) == "unverified_join:non_link_predicate"
    # Enrollments to districts: no declared link at all.
    assert "has no declared link" in (
        why(
            "SELECT COUNT(*) FROM bronze.public_enrollments e JOIN bronze.public_districts d "
            "ON e.school_id = d.district_id"
        )
        or ""
    )


# --- store: migration 0005 and the snapshot rules (CI has no Postgres) -----------


def test_migration_0005_is_append_only_and_tenant_scoped() -> None:
    import importlib.util

    import alembic.op  # noqa: F401 - the revision imports it

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0005_onto_snapshot.py"
    spec = importlib.util.spec_from_file_location("rev0005", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "0004_ontology_store"

    class _Rec:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str) -> None:
            self.sql.append(str(sql))

    rec = _Rec()
    mod.op = rec
    mod.upgrade()
    blob = "\n".join(rec.sql)
    assert "CREATE TABLE dms.onto_snapshot" in blob
    assert "FORCE ROW LEVEL SECURITY" in blob
    assert "GRANT UPDATE" not in blob and "INSERT, UPDATE" not in blob
    assert "NOT verified OR jsonb_array_length(violations) = 0" in blob
    rec.sql.clear()
    mod.downgrade()
    assert rec.sql == ["DROP TABLE IF EXISTS dms.onto_snapshot CASCADE"]


def test_snapshot_cannot_claim_verified_with_violations() -> None:
    import uuid

    from dms_core.control_plane.onto_store import SourceIdentity

    s = OntologyStore()
    ident = SourceIdentity(kind="postgresql", host="h", database="d", schema="public")
    v = s.reconnect(space_id=uuid.uuid4(), identity=ident, fingerprint="f1")
    snap = s.record_snapshot(
        v.id,
        body={"catalog": {}},
        violations=[{"check": "fk_intact", "subject": "l", "detail": "x"}],
        verified=True,
    )
    assert snap.verified is False
    assert s.latest_snapshot(v.id) == snap
    assert s.active_for_space(uuid.uuid4()) == []
    with pytest.raises(KeyError):
        s.record_snapshot(uuid.uuid4(), body={}, violations=[], verified=True)


# --- adversary round 1: no shape of wrong join answers ---------------------------

S = SCHOOLS
D = DISTRICTS
BASE = f"SELECT COUNT(*) AS school_count FROM {S} s JOIN {D} d ON s.district_id = d.district_id"
#: Adversary round 1 (independent agent): every shape joins schools to districts
#: on something other than the verified link. None may answer a wrong number.
JOIN_ATTACKS = {
    "self_join_fanout": f"SELECT COUNT(*) AS school_count FROM {S} s JOIN {S} s2 ON s.district_id = s2.district_id JOIN {D} d ON s.district_id = d.district_id WHERE d.name = 'North'",
    "second_alias_cross": f"{BASE}, {D} d2 WHERE d.name = 'North'",
    "second_alias_wrong_on": f"{BASE} JOIN {D} d2 ON s.school_id = d2.district_id WHERE d.name = 'North'",
    "derived_star_wrong_col": f"{BASE} JOIN (SELECT * FROM {D}) t ON t.district_id = s.school_id WHERE d.name = 'North'",
    "cte_star_wrong_col": f"WITH t AS (SELECT * FROM {D}) {BASE} JOIN t ON t.district_id = s.school_id WHERE d.name = 'North'",
    "derived_expr_cross": f"{BASE} JOIN (SELECT district_id + 0 AS k FROM {D}) t ON t.k = s.school_id WHERE d.name = 'North'",
    "or_true": f"SELECT COUNT(*) AS school_count FROM {S} s, {D} d WHERE (s.district_id = d.district_id OR d.name IS NOT NULL) AND d.name = 'North'",
    "not_eq_antijoin": f"SELECT COUNT(*) AS school_count FROM {S} s, {D} d WHERE NOT (s.district_id = d.district_id) AND d.name = 'North'",
    "eq_any_subq": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE s.school_id = ANY (SELECT district_id FROM {D} WHERE name = 'North')",
    "scalar_subq_eq": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE s.school_id = (SELECT MIN(district_id) FROM {D} WHERE name = 'North')",
    "in_union": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE s.school_id IN (SELECT district_id FROM {D} WHERE name = 'North' UNION SELECT district_id FROM {D} WHERE name = 'North')",
    "in_expr_left": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE s.school_id || '' IN (SELECT district_id FROM {D} WHERE name = 'North')",
    "in_wrong_col": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE s.school_id IN (SELECT district_id FROM {D} WHERE name = 'North')",
    "exists_wrong_col": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE EXISTS (SELECT 1 FROM {D} d WHERE d.district_id = s.school_id AND d.name = 'North')",
    "using_wrong": f"SELECT COUNT(*) AS school_count FROM {S} s JOIN {D} d USING (name) WHERE d.name = 'North'",
    "natural": f"SELECT COUNT(*) AS school_count FROM {S} NATURAL JOIN {D} WHERE name = 'North'",
    "case_alias_extra_pred": f"SELECT COUNT(*) AS school_count FROM {S} S JOIN {D} d ON S.district_id = d.district_id AND s.school_id = d.district_id WHERE d.name = 'North'",
    "upper_relation_wrong": "SELECT COUNT(*) AS school_count FROM BRONZE.PUBLIC_SCHOOLS s JOIN BRONZE.PUBLIC_DISTRICTS d ON s.school_id = d.district_id WHERE d.name = 'North'",
    "quoted_alias_wrong": f'SELECT COUNT(*) AS school_count FROM {S} "S" JOIN {D} d ON "S".school_id = d.district_id WHERE d.name = \'North\'',
    "union_branch_wrong": f"SELECT SUM(c) AS school_count FROM ({VERIFIED_SQL.replace('COUNT(*) AS school_count', 'COUNT(*) AS c')} UNION ALL SELECT COUNT(*) FROM {S} s JOIN {D} d ON s.school_id = d.district_id WHERE d.name='North') u",
    "lateral_wrong": f"SELECT COUNT(*) AS school_count FROM {S} s, LATERAL (SELECT * FROM {D} d WHERE d.district_id = s.school_id) x WHERE x.name = 'North'",
    "join_on_function": f"{BASE} JOIN {D} d2 ON list_contains([d2.district_id], s.school_id) WHERE d.name = 'North'",
    "positional_or_asof": f"SELECT COUNT(*) AS school_count FROM {S} s POSITIONAL JOIN {D} d WHERE d.name = 'North'",
    "in_tuple": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE (s.school_id, 'x') IN (SELECT district_id, 'x' FROM {D} WHERE name = 'North')",
    "ne_disguise_between": f"SELECT COUNT(*) AS school_count FROM {S} s JOIN {D} d ON s.school_id BETWEEN d.district_id AND d.district_id WHERE d.name = 'North'",
    "semi_join_duckdb": f"SELECT COUNT(*) AS school_count FROM {S} s SEMI JOIN {D} d ON s.school_id = d.district_id AND d.name='North'",
}


@pytest.mark.parametrize("name", sorted(JOIN_ATTACKS))
def test_no_wrong_join_shape_answers(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    env = rig.ask(VERIFIED_Q, {"query_sql": JOIN_ATTACKS[name], "plan_source": "ontology_plan"})
    if env["abstained"] is False:
        # Answering is allowed only with the true figure.
        assert _multiset(env["rows"]) == _multiset([{"school_count": 3}]), (name, env["rows"])
    else:
        assert env["badge"] == "ABSTAIN" and env["rows"] == []
        assert "gap: " in str(env.get("text") or "")


def test_fan_trap_over_the_verified_link_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _space(tmp_path, monkeypatch, minter)
    sql = f"SELECT COUNT(d.district_id) AS district_count FROM {D} d JOIN {S} s ON s.district_id = d.district_id"
    assert _oracle(rig.lake, sql) == [{"district_count": 4}]  # truth is 2
    env = rig.ask(
        "How many districts have schools?", {"query_sql": sql, "plan_source": "ontology_plan"}
    )
    _assert_abstain(env, "unverified_join")
    assert "fan_trap (bronze.public_districts" in _reasons(env)
    fixed = sql.replace("COUNT(d.district_id)", "COUNT(DISTINCT d.district_id)")
    env = rig.ask(
        "How many districts have schools?", {"query_sql": fixed, "plan_source": "ontology_plan"}
    )
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert _multiset(env["rows"]) == _multiset([{"district_count": 2}])


def test_contract_ask_sql_obeys_the_space_join_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """Generation misses; the contract ask's own SQL must pass the same rule."""
    from cortex_client.models import AskResponse

    rig = _space(tmp_path, monkeypatch, minter)

    def _ask(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer="There is 1 school.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=WRONG_JOIN_SQL,
            rows=[{"school_count": 1}],
        )

    monkeypatch.setattr(rig.cortex, "ask", _ask)
    env = rig.ask(VERIFIED_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    _assert_abstain(env, "unverified_join")


def test_ontology_payload_stays_inside_cortex_limits() -> None:
    from dms_executor.space_ontology import WIRE_MAX_BYTES, budget_space_block

    space = {
        "objects": {f"bronze.t{i}": {"key": [f"id_{i}"]} for i in range(400)},
        "links": {
            f"fk_{i}": {"from": f"bronze.t{i}", "to": "bronze.t0", "cardinality": "many_to_one"}
            for i in range(400)
        },
        "measures": {},
        "verified": True,
    }
    out = budget_space_block(space, used=40 * 1024)
    import json as _json

    assert 40 * 1024 + len(_json.dumps(out)) <= WIRE_MAX_BYTES + 1024
    assert len(out["links"]) <= 256
    assert out["space_truncated"] is True
