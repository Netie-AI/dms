"""BRONZE-GRANT-01 / dms#303: bronze sheet reads go through the Space grant.

The lane is Executor.live_ask. A bronze sum with no grant abstains
``ungranted_table:<table>``. No Space abstains ``no_space``. These tests
fail on c1461134 on that assert. HTTP in the live() case is an in-process
stand-in. The ask itself is the real lane. No keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskResponse
from dms_core.pii import mask_payload
from dms_executor import Executor
from dms_executor.bronze import _ensure_registry, bronze_table_for_sheet
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_schema import ensure_lake_schemas
from dms_executor.manifest import ManifestMinter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import score_curated  # noqa: E402
import test_space_boundary_envelope as boundary  # noqa: E402
from score_curated import ab_offline, live  # noqa: E402

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
BRONZE_ID = "bronze_ungranted_sum"
OTHER_ID = "kept_abstain"


class _Stub:
    """Cortex stand-in. The bronze lane returns before ask()."""

    def submit(self, _req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        return QueryResult(ok=True, status="bound", run_id="run-bronze-grant")

    def ask(self, _req: Any) -> AskResponse:
        return AskResponse(
            answer="I cannot answer that here.",
            abstained=True,
            badge="abstain",
            rows=[],
            route="abstain",
            audit_id="aud-stub",
        )


class _Ok:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _question(filename: str) -> str:
    return (
        f"In {filename} sheet Sales, what are the top 3 "
        "categories by sales_value_myr?"
    )


def _seed(
    path: Path,
    filename: str,
    *,
    space_id: str | None,
    amount: float,
) -> str:
    table = bronze_table_for_sheet(filename, "Sales")
    ident = table.split(".", 1)[-1]
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(
            f'CREATE TABLE bronze."{ident}" (category VARCHAR, sales_value_myr DOUBLE)'
        )
        con.execute(
            f'INSERT INTO bronze."{ident}" VALUES '
            "('Electronics', ?), ('Home', 20.0), ('Sports', 5.0)",
            [amount],
        )
        if space_id:
            _ensure_registry(con)
            con.execute(
                """
                INSERT INTO bronze._ingest_registry
                  (table_name, filename, sha256, ingest_id, created_at, space_id)
                VALUES (?, ?, 'abc', 'ing-bronze-grant', now(), ?)
                """,
                [ident, filename, space_id],
            )
    finally:
        con.close()
    return table


def _point_warehouse(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Parent bronze_sheet_ask reads the process warehouse, not Executor._warehouse."""
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("CORTEX_WAREHOUSE_DB", raising=False)
    monkeypatch.delenv("DMS_ORACLE_WAREHOUSE", raising=False)
    monkeypatch.delenv("CORTEX_HOME", raising=False)


def _exe(path: Path) -> Executor:
    return Executor(cortex=_Stub(), warehouse_path=path)  # type: ignore[arg-type]


def _ask(
    path: Path,
    filename: str,
    *,
    space_id: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _point_warehouse(monkeypatch, path)
    env = _exe(path).live_ask(
        _question(filename),
        space_id=space_id,
        session_id="ses_bronze_grant",
    )
    assert_envelope_valid(env)
    return env


def _shape_columns(path: Path, table: str) -> set[str]:
    ident = table.split(".", 1)[-1]
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {
            str(row[0]).lower()
            for row in con.execute(f'DESCRIBE bronze."{ident}"').fetchall()
        }
    finally:
        con.close()


def test_ungranted_space_bronze_sum_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Space is real. The table is not in its grants. No grouped sum."""
    db = tmp_path / "ungranted.duckdb"
    filename = "grantgap.xlsx"
    table = _seed(db, filename, space_id=None, amount=1545366.40)
    env = _ask(db, filename, space_id=OPS, monkeypatch=monkeypatch)
    assert env["badge"] == "ABSTAIN" and f"ungranted_table:{table}" in env["text"]
    assert env["abstained"] is True
    assert not env["rows"]
    assert "1545366.4" not in env["text"]


def test_cross_space_bronze_sum_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered to Finance. Asked from Ops. Another Space is not a grant."""
    db = tmp_path / "cross.duckdb"
    filename = "crossspace.xlsx"
    table = _seed(db, filename, space_id=FINANCE, amount=1545366.40)
    env = _ask(db, filename, space_id=OPS, monkeypatch=monkeypatch)
    assert env["badge"] == "ABSTAIN" and f"ungranted_table:{table}" in env["text"]
    assert env["abstained"] is True
    assert not env.get("values")
    assert not env["rows"]
    assert "1545366.4" not in env["text"]


def test_no_space_bronze_sum_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "nospace.duckdb"
    filename = "nospace.xlsx"
    _seed(db, filename, space_id=FINANCE, amount=1545366.40)
    env = _ask(db, filename, space_id=None, monkeypatch=monkeypatch)
    assert env["badge"] == "ABSTAIN" and "no_space" in env["text"]
    assert env["abstained"] is True
    assert not env["rows"]
    assert "1545366.4" not in env["text"]


def test_shape_pass_without_grant_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """category + sales_value_myr is the old gate. It is not a grant."""
    db = tmp_path / "shape.duckdb"
    filename = "public_client.xlsx"
    table = _seed(db, filename, space_id=None, amount=88001.25)
    cols = _shape_columns(db, table)
    assert "category" in cols and "sales_value_myr" in cols
    env = _ask(db, filename, space_id=FINANCE, monkeypatch=monkeypatch)
    assert env["badge"] == "ABSTAIN" and f"ungranted_table:{table}" in env["text"]
    assert not env["rows"]
    assert "88001.25" not in env["text"]


def test_granted_bronze_table_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive. Fails if the grant check denies every table."""
    db = tmp_path / "granted.duckdb"
    filename = "granted.xlsx"
    table = _seed(db, filename, space_id=FINANCE, amount=42.5)
    _point_warehouse(monkeypatch, db)
    exe = _exe(db)
    assert table in exe.grantable_tables(space_id=FINANCE)
    env = exe.live_ask(
        _question(filename),
        space_id=FINANCE,
        session_id="ses_bronze_grant",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"][0]["category"] == "Electronics"
    assert env["rows"][0]["sales_value_myr"] == 42.5
    assert "42.5" in env["text"]


def test_live_ungranted_bronze_sum_is_not_correct_and_stays_in_n(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """live() records ABSTAIN ungranted_table. The case stays in n."""
    db = tmp_path / "live.duckdb"
    filename = "livesum.xlsx"
    _seed(db, filename, space_id=None, amount=10.0)
    oracle = tmp_path / "oracle.duckdb"
    duckdb.connect(str(oracle)).close()
    question = _question(filename)
    oracle_sql = (
        "SELECT category, sales_value_myr FROM (VALUES "
        "('Electronics', 10.0), ('Home', 20.0), ('Sports', 5.0)"
        ") v(category, sales_value_myr) "
        "ORDER BY sales_value_myr DESC LIMIT 3"
    )
    pack = {
        "questions": [
            {
                "id": BRONZE_ID,
                "space": "ops",
                "expect": "l0",
                "question": question,
            },
            {
                "id": OTHER_ID,
                "space": "finance",
                "expect": "abstain",
                "question": "Tell me a joke about warehouses",
            },
        ],
        "spaces": {"ops": OPS, "finance": FINANCE},
    }
    _point_warehouse(monkeypatch, db)
    exe = _exe(db)
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENVAULT_URL", raising=False)
    import dms_executor.demo_warehouse as warehouse

    warehouse.clear_engine_clock()
    monkeypatch.setattr(score_curated, "load_pack", lambda _path: pack)
    monkeypatch.setattr(score_curated, "merge_pack_questions", lambda rows: list(rows))
    monkeypatch.setattr(
        score_curated,
        "load_oracles",
        lambda: {BRONZE_ID: {"sql": oracle_sql}},
    )

    def fake(method: str, url: str, **kwargs: Any) -> _Ok:
        if method == "GET" and str(url).rstrip("/").endswith("/health"):
            return _Ok(
                {
                    "status": "ok",
                    "engine_as_of": "2024-06-15",
                    "engine_as_of_after": "2024-06-15",
                    "engine_timezone": "UTC",
                    "engine_timezone_after": "UTC",
                }
            )
        body = kwargs.get("json_body")
        if not isinstance(body, dict):
            body = {}
        env = exe.live_ask(
            str(body.get("question") or ""),
            space_id=body.get("space_id"),  # type: ignore[arg-type]
            session_id="ses_bronze_live",
        )
        return _Ok(env)

    monkeypatch.setattr(score_curated, "score_http", fake)
    live("http://127.0.0.1:9", 1.0, oracle)
    report = json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))
    cases = report["cases"]
    assert isinstance(cases, list)
    ids = [row["id"] for row in cases]
    assert report["n"] == 2
    assert BRONZE_ID in ids
    rec_files = sorted(tmp_path.glob("score_cases_*.jsonl"))
    assert len(rec_files) == 1
    records = [
        json.loads(line)
        for line in rec_files[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rec = next(row for row in records if row["id"] == BRONZE_ID)
    recorded = str((rec.get("envelope") or {}).get("text") or "")
    assert rec["outcome"] == "ABSTAIN" and "ungranted_table:" in recorded
    assert int(report["correct"]) == 0


def test_pack_counts_stay_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """52-pack through ab_offline. Not a mocked scorer."""
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    ab_offline()
    report = json.loads((tmp_path / "ab_gen01.json").read_text(encoding="utf-8"))
    exact = report["exact_match"]
    gen = report["generative"]
    keys = ("ok", "layer", "abstain", "wrong", "oracle_error")
    assert [exact[key] for key in keys] == [0, 16, 36, 0, 0]
    assert [gen[key] for key in keys] == [0, 26, 11, 15, 0]
    assert exact["invalid"] == 0
    assert gen["invalid"] == 0
    assert exact["n"] == 52
    assert gen["n"] == 52


def test_masker_still_masks_email() -> None:
    """Real mask_payload. A synthetic email does not stay in the text."""
    out = mask_payload(text="reach the buyer at ada@example.com today")
    assert "ada@example.com" not in out["text"]
    assert "DMSMASK_" in out["text"]


def test_grounded_ask_grant_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grounded revenue ask through live_ask. Finance answers. Ops abstains."""
    minter = ManifestMinter()

    def _mint(acl: Any) -> Any:
        from cortex_contract.execution import Manifest

        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-08-02T00:00:00+00:00",
            expires_at="2026-08-02T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    finance = boundary._ask(minter, boundary.REVENUE, space_id=boundary.FINANCE)
    ops = boundary._ask(minter, boundary.REVENUE, space_id=boundary.WAREHOUSE_OPS)
    assert finance["abstained"] is False
    assert finance["badge"] == "L0_CERTIFIED"
    assert finance["rows"]
    assert ops["abstained"] is True
    assert ops["badge"] == "ABSTAIN"
    assert not ops.get("values")
