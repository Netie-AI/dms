"""FDE-LAND-01: the landing script walks the governed path and prints receipts.

``scripts/verify/fde_land.py`` runs in-process against the ONTO-DERIVE-01 rig
(``test_onto_derive_01._rig``): the real DMS app over a ``TestClient``, the
connector's fake DB-API source, the Cortex fake that enforces the manifest and
runs the submitted SQL on the lake. The model is a stand-in that maps each
generated question to SQL; the receipts prove the DMS side, not model quality.

The oracle is the script's own: it opens the source through the connector's
``connect`` and runs the count there. Here that source is the fake connection,
whose aggregate queries run on an in-memory copy of the source rows - never on
the lake the answers came from.

Every ask receipt carries the rendered text, the rows and the badge, and is
asserted on all three (CLAUDE.md rules 10, 10a, 12).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import db_connector as dbc
from dms_executor.ao_compile import OntologyProposal
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import set_ontology_store
from test_db_connector import _FakeConnection, _FakeCursor
from test_onto_derive_01 import (
    BROKEN_LINK,
    DISTRICTS,
    ENROLL,
    GOOD_LINK,
    SCHOOLS,
    _RecordingCortex,
    _rig,
    _Space,
    make_minter,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import fde_land  # noqa: E402

PASSWORD = "pw-fde-never-echoed"
CFG = dbc.SourceConfig(
    kind="postgresql", host="db.example.net", database="schools", user="reader", password=PASSWORD
)

Q_DISTRICTS = f"How many rows does {DISTRICTS} have?"
Q_ENROLL = f"How many rows does {ENROLL} have?"
Q_SCHOOLS = f"How many rows does {SCHOOLS} have?"
Q_GROUPED = f"How many {SCHOOLS} rows are there per {DISTRICTS} district_id?"
GROUPED_SQL = (
    f"SELECT d.district_id, COUNT(*) AS n FROM {SCHOOLS} s JOIN {DISTRICTS} d "
    "ON s.district_id = d.district_id GROUP BY d.district_id"
)
#: What a model would write for each generated question.
MODEL_SQL = {
    Q_DISTRICTS: f"SELECT COUNT(*) AS n FROM {DISTRICTS}",
    Q_ENROLL: f"SELECT COUNT(*) AS n FROM {ENROLL}",
    Q_SCHOOLS: f"SELECT COUNT(*) AS n FROM {SCHOOLS}",
    Q_GROUPED: GROUPED_SQL,
}


class _SourceCursor(_FakeCursor):
    """Catalog reads replay; an aggregate (the oracle) runs on the source rows."""

    def execute(self, sql: str, params: Any = None) -> None:
        if "COUNT(" not in sql.upper():
            super().execute(sql, params)
            return
        owner = self._owner
        assert isinstance(owner, _SourceDb)
        owner.oracle_sql.append(sql)
        cur = owner.db.execute(sql)
        self.description = [(c[0],) for c in cur.description or []]
        self._result = [tuple(r) for r in cur.fetchall()]


class _SourceDb(_FakeConnection):
    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.oracle_sql: list[str] = []
        self.db = duckdb.connect()
        assert isinstance(self.table_data, dict)
        for ident, (cols, rows) in self.table_data.items():
            schema, table = (p.strip('"') for p in ident.split("."))
            self.db.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self.db.execute(
                f'CREATE TABLE "{schema}"."{table}" ('
                + ", ".join(f'"{c}" VARCHAR' for c in cols)
                + ")"
            )
            if rows:
                marks = ", ".join("?" for _ in cols)
                self.db.executemany(f'INSERT INTO "{schema}"."{table}" VALUES ({marks})', rows)

    def cursor(self) -> _SourceCursor:
        return _SourceCursor(self)


@dataclass
class _ModelCortex(_RecordingCortex):
    """Insights answers each generated question with the SQL in ``answers``."""

    answers: dict[str, str] = field(default_factory=lambda: dict(MODEL_SQL))

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any]:
        sql = self.answers.get(question)
        self.payload = {"query_sql": sql, "plan_source": "ontology_plan"} if sql else {}
        return super().compute_insights(question, **kw)


@pytest.fixture(autouse=True)
def _stores() -> Iterator[None]:
    prev = set_ontology_store(OntologyStore())
    reset_ontology_data_store()
    try:
        yield
    finally:
        set_ontology_store(prev)
        reset_ontology_data_store()


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> _Space:
    from cortex_client.gate import ComplianceDecision

    def _allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.ontology.compliance_gate", _allow)
    return _rig(tmp_path, monkeypatch, minter, connection_cls=_SourceDb, cortex_cls=_ModelCortex)


def _land(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    **kw: Any,
) -> tuple[_Space, list[fde_land.Receipt]]:
    rig = _setup(tmp_path, monkeypatch, minter)
    answers = kw.pop("answers", None)
    if kw.pop("source_down", False):

        @contextmanager
        def _down(cfg: dbc.SourceConfig) -> Iterator[Any]:
            raise dbc.SourceConnectionError(f"could not connect to {cfg.describe()}")
            yield  # pragma: no cover

        monkeypatch.setattr(dbc, "connect", _down)
    if answers:
        assert isinstance(rig.cortex, _ModelCortex)
        rig.cortex.answers.update(answers)
    receipts = fde_land.run_landing(rig.client, CFG, space_id=rig.space_id, **kw)
    print("\n".join(fde_land.render(receipts)))
    blob = json.dumps([r.__dict__ for r in receipts], default=str)
    assert PASSWORD not in blob, "the source password reached a receipt"
    return rig, receipts


def _steps(receipts: list[fde_land.Receipt]) -> list[tuple[str, bool, str | None]]:
    return [(r.step, r.ok, r.failure) for r in receipts]


def _values(rows: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return sorted(tuple(sorted(str(v) for v in row.values())) for row in rows)


def _source_of(rig: _Space) -> _SourceDb:
    with dbc.connect(CFG) as con:
        assert isinstance(con, _SourceDb)
        return con


# --- the walk ----------------------------------------------------------------------


def test_landing_walks_every_step_and_each_answer_equals_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig, receipts = _land(tmp_path, monkeypatch, minter)
    asks = [f"ask: {q}" for q in (Q_DISTRICTS, Q_ENROLL, Q_SCHOOLS, Q_GROUPED)]
    assert _steps(receipts) == [
        ("connect", True, None),
        ("grants", True, None),
        ("ontology", True, None),
        ("diff", True, None),
        ("approve", True, None),
        *[(a, True, None) for a in asks],
    ]
    by_step = {r.step: r for r in receipts}

    connect = by_step["connect"].detail
    assert connect["space_id"] == rig.space_id
    assert {t["bronze_table"]: t["row_count"] for t in connect["tables"]} == {
        DISTRICTS: 2,
        SCHOOLS: 4,
        ENROLL: 4,
    }
    assert by_step["grants"].summary == "N/A on main" and by_step["grants"].na
    assert "[N/A] grants: N/A on main" in list(fde_land.render(receipts))
    onto = by_step["ontology"]
    assert onto.summary.startswith("3/3 objects verified, 1/2 links verified")
    assert f"unverified (no question uses them): {BROKEN_LINK}" in onto.summary
    links = {k["name"]: k for k in onto.detail["links"]}
    assert links[GOOD_LINK]["status"] == "verified"
    assert "2 rows" in links[BROKEN_LINK]["violations"][0]
    assert by_step["diff"].summary.startswith("AO compile off")
    assert by_step["approve"].summary == "nothing proposed: no steward approval to make"

    # User-visible output on every ask: badge, rows (equal to the source), text.
    truth: dict[str, list[tuple[str, ...]]] = {
        Q_DISTRICTS: [("2",)],
        Q_ENROLL: [("4",)],
        Q_SCHOOLS: [("4",)],
        Q_GROUPED: [("1", "3"), ("1", "2")],
    }
    for q, want in truth.items():
        d = by_step[f"ask: {q}"].detail
        assert d["badge"] == "L2_VALIDATED", d
        assert _values(d["rows"]) == sorted(want), (q, d["rows"])
        assert _values(d["oracle_rows"]) == sorted(want), (q, d["oracle_rows"])
        for figure in {v for row in want for v in row}:
            assert figure in str(d["text"]), (q, d["text"])
        assert d["sql_used"] and d["audit_id"]
    assert "n=3" in str(by_step[f"ask: {Q_GROUPED}"].detail["text"])

    # The answers came from the lake; the oracle ran on the source, in its dialect.
    assert rig.cortex.executed == [MODEL_SQL[q] for q in truth]
    assert _source_of(rig).oracle_sql == [
        'SELECT COUNT(*) AS n FROM "public"."districts"',
        'SELECT COUNT(*) AS n FROM "public"."enrollments"',
        'SELECT COUNT(*) AS n FROM "public"."schools"',
        'SELECT p."district_id", COUNT(*) AS n FROM "public"."schools" c JOIN '
        '"public"."districts" p ON c."district_id" = p."district_id" GROUP BY p."district_id"',
    ]
    assert list(fde_land.render(receipts))[-1] == "LANDED"


def _proposal() -> OntologyProposal:
    terms = tuple(
        {"name": name, "table": name, "description": "", "attributes": [], "key": [key]}
        for name, key in ((DISTRICTS, "district_id"), (SCHOOLS, "school_id"))
    )
    return OntologyProposal(
        status="proposed",
        vendor_sha="eb8dd0621d07db3033fe3fddc68f62ff72614610",
        terms=terms,
        attributes=(),
        inferred_fks=(),
        declared_fks=(),
        joins=(),
    )


def test_ao_proposal_stops_for_the_steward_until_approve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    monkeypatch.setattr("dms_executor.compile_source", lambda conn, **_: _proposal())
    _, receipts = _land(tmp_path / "held", monkeypatch, minter)
    assert _steps(receipts)[-3:] == [
        ("ontology", True, None),
        ("diff", True, None),
        ("approve", False, "steward_review_pending"),
    ]
    diff, held = receipts[-2], receipts[-1]
    assert diff.detail["counts"] == {"object": 2}
    assert sorted(diff.detail["object_names"]) == [DISTRICTS, SCHOOLS]
    assert sorted(held.detail["items"]) == [DISTRICTS, SCHOOLS]
    assert held.hint and "--approve" in held.hint
    assert ontology_data_store().approved_items() == []

    reset_ontology_data_store()
    _, receipts = _land(tmp_path / "approved", monkeypatch, minter, approve=True)
    approve = next(r for r in receipts if r.step == "approve")
    assert approve.ok and sorted(approve.detail["approved"]) == [DISTRICTS, SCHOOLS]
    assert {i["payload"]["name"] for i in ontology_data_store().approved_items()} == {
        DISTRICTS,
        SCHOOLS,
    }
    assert receipts[-1].ok and receipts[-1].step == f"ask: {Q_GROUPED}"


# --- refusals: named, and the walk stops -------------------------------------------


def test_unreachable_source_is_named_and_nothing_else_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig, receipts = _land(tmp_path, monkeypatch, minter, source_down=True)
    (only,) = receipts
    assert (only.step, only.ok, only.failure) == ("connect", False, "source_unreachable")
    assert only.detail["http"] == 502
    assert only.hint == fde_land.HINTS["source_unreachable"]
    assert rig.cortex.executed == [] and rig.cortex.insights == []
    assert list(fde_land.render(receipts))[-1] == "STOPPED at connect"


def test_green_wrong_number_is_caught_by_the_source_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """A filter the question never asked for: validates, executes, badge green, 3 not 4."""
    wrong = f"SELECT COUNT(*) AS n FROM {SCHOOLS} WHERE school_id <> '4'"
    rig, receipts = _land(tmp_path, monkeypatch, minter, answers={Q_SCHOOLS: wrong})
    assert _steps(receipts)[-3:] == [
        (f"ask: {Q_DISTRICTS}", True, None),
        (f"ask: {Q_ENROLL}", True, None),
        (f"ask: {Q_SCHOOLS}", False, "rows_mismatch"),
    ]
    d = receipts[-1].detail
    assert d["badge"] == "L2_VALIDATED"
    assert _values(d["rows"]) == [("3",)] and _values(d["oracle_rows"]) == [("4",)]
    assert "3" in str(d["text"])
    assert rig.cortex.executed[-1] == wrong
    assert Q_GROUPED not in rig.cortex.insights[-1]["question"]  # stopped: no later ask


def test_abstain_is_a_named_stop_with_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    wrong_join = GROUPED_SQL.replace(
        "ON s.district_id = d.district_id", "ON s.school_id = d.district_id"
    )
    rig, receipts = _land(tmp_path, monkeypatch, minter, answers={Q_GROUPED: wrong_join})
    last = receipts[-1]
    assert (last.step, last.ok, last.failure) == (f"ask: {Q_GROUPED}", False, "abstained")
    assert last.detail["badge"] == "ABSTAIN" and last.detail["rows"] == []
    assert "gap: unverified_join" in str(last.detail["text"])
    assert wrong_join not in rig.cortex.executed


def test_cli_creates_the_space_reads_the_password_from_env_and_exits_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import httpx

    rig = _setup(tmp_path, monkeypatch, minter)
    seen: dict[str, Any] = {}

    class _NoLifespan:
        """The rig's client, without ``TestClient.__enter__`` (app startup dials out)."""

        def __enter__(self) -> Any:
            return rig.client

        def __exit__(self, *_: Any) -> None:
            return None

    def _client(*, base_url: str, timeout: float) -> _NoLifespan:
        seen["base_url"] = base_url
        return _NoLifespan()

    monkeypatch.setattr(httpx, "Client", _client)
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", PASSWORD)
    out_json = tmp_path / "receipts.json"
    argv = ["--dms-url", "http://dms.test", "--kind", "postgresql", "--host", "db.example.net"]
    argv += ["--database", "schools", "--user", "reader", "--space-name", "Landing"]
    code = fde_land.main([*argv, "--json", str(out_json)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert seen["base_url"] == "http://dms.test"
    assert printed.rstrip().endswith("LANDED")
    assert PASSWORD not in printed and PASSWORD not in out_json.read_text(encoding="utf-8")
    receipts = json.loads(out_json.read_text(encoding="utf-8"))
    created = receipts[0]["detail"]["space_id"]
    assert created != rig.space_id  # the script made its own Space
    assert rig.client.get(f"/v1/spaces/{created}").json()["name"] == "Landing"
    assert receipts[-1]["step"] == f"ask: {Q_GROUPED}" and receipts[-1]["ok"] is True


# --- the question plan -------------------------------------------------------------


def test_plan_quotes_per_dialect_and_skips_unverified_links() -> None:
    view = {
        "ontologies": [
            {
                "objects": [
                    {
                        "name": "bronze.dbo_orders",
                        "source_table": "dbo.orders",
                        "status": "verified",
                    },
                    {
                        "name": "bronze.dbo_customers",
                        "source_table": "dbo.customers",
                        "status": "verified",
                    },
                    {
                        "name": "bronze.dbo_notes",
                        "source_table": "dbo.notes",
                        "status": "unverified",
                    },
                ],
                "links": [
                    {
                        "name": "fk_bad",
                        "from": "bronze.dbo_orders",
                        "to": "bronze.dbo_customers",
                        "from_columns": ["cust"],
                        "to_columns": ["id"],
                        "status": "unverified",
                        "cardinality": "unverified",
                    },
                    {
                        "name": "fk_ok",
                        "from": "bronze.dbo_orders",
                        "to": "bronze.dbo_customers",
                        "from_columns": ["customer_id"],
                        "to_columns": ["id"],
                        "status": "verified",
                        "cardinality": "many_to_one",
                    },
                ],
            }
        ]
    }
    plans = fde_land.plan_questions(view, kind="sqlserver")
    assert [p.basis for p in plans] == [
        "object bronze.dbo_orders",
        "object bronze.dbo_customers",
        "link fk_ok",
    ]
    assert plans[0].oracle_sql == "SELECT COUNT(*) AS n FROM [dbo].[orders]"
    assert plans[2].oracle_sql == (
        "SELECT p.[id], COUNT(*) AS n FROM [dbo].[orders] c JOIN [dbo].[customers] p "
        "ON c.[customer_id] = p.[id] GROUP BY p.[id]"
    )
    mysql = fde_land.plan_questions(view, kind="mysql", max_groups=0)
    assert [p.oracle_sql for p in mysql] == [
        "SELECT COUNT(*) AS n FROM `dbo`.`orders`",
        "SELECT COUNT(*) AS n FROM `dbo`.`customers`",
    ]
