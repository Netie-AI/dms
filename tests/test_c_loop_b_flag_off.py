"""Flag off is byte-equal to main on the 73-ask set, shadow fields included.

The stub binds the session, then runs one canned SELECT. Several asks
serve L2 from that SQL. Planted and pre-gate asks abstain. The stub
does not route on question words.

Wall-clock fields are placeholders. Every other key and value is
compared, including ``served_check_shadow``. A key main does not send
fails the test.

The fixture bytes were captured from ce08153. The same bytes match
beabdc6 (skills quarantine, no stamp on this stub).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter, SessionAcl

_FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "c_loop_b" / "flag_off_main.json"
_BASELINE = "ce08153c7f74418db33a38c0372d675e567d7040"
_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_CLOCK = {
    "as_of",
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
}


def _questions() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    questions_path = root / "tests/fixtures/curated_ceo/questions.yaml"
    pack = yaml.safe_load(questions_path.read_text(encoding="utf-8"))
    out = [str(row["question"]) for row in pack["questions"]]
    out.extend(
        [
            "List chemicals in inventory",
            "What chemicals do we have in stock?",
            "top 5 SKUs by sales",
            "show the five best-selling SKUs",
            "top 3 categories by value",
            "show top 3 categoty sales",
            "which supplier audits are overdue",
            "what's the freight cost by destination",
            "what's the bottom three categories by stock value",
            "which three categories are sitting on the least inventory value",
            "give me the lowest five categories by stock value",
            "the three cheapest categories by stock",
            "who is the third-lowest category by stock value",
            "are there any chemicals expiring in November 2026",
            "can you list the chemicals we carry",
            "show me our top five SKUs by sales",
            "top 3 categoty by value",
            "which supplier audits are overdue",
            "what's freight looking like by destination",
            "how many florbs did wibble sell last quarter",
            "what is the meaning of life for this warehouse",
        ]
    )
    return out


def _norm(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            name = str(key)
            if name in _CLOCK and isinstance(val, str):
                out[name] = "CLOCK"
            else:
                out[name] = _norm(val)
        return out
    if isinstance(obj, list):
        return [_norm(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _freeze(env: dict[str, Any]) -> dict[str, Any]:
    frozen = json.loads(json.dumps(_norm(env), sort_keys=True, default=str))
    assert isinstance(frozen, dict)
    return frozen


def _extra_keys(main: Any, got: Any, path: str = "") -> list[str]:
    """Keys present on got that main never sends. Values are ignored."""
    found: list[str] = []
    if isinstance(main, dict) and isinstance(got, dict):
        for key in got:
            here = f"{path}.{key}" if path else str(key)
            if key not in main:
                found.append(here)
            else:
                found.extend(_extra_keys(main[key], got[key], here))
    elif isinstance(main, list) and isinstance(got, list):
        for index, (left, right) in enumerate(zip(main, got, strict=False)):
            found.extend(_extra_keys(left, right, f"{path}[{index}]"))
    return found


class _Cortex:
    def __init__(self, db: Path) -> None:
        self._db = db

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        del question
        return {
            "phase": "generate",
            "query_sql": _SQL,
            "served_model": "stub-model",
            "served_provider": "stub-provider",
            "ov_key_id": "ovk-stub",
            "generative": {"sql": _SQL, "ok": True, "stamp": {"impl": "stub"}},
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_flag_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_file(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_flag", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_flag", hash="hash_flag")

    def ask(self, req: AskRequest) -> AskResponse:
        del req
        return AskResponse(
            answer="There are 5 locations.",
            badge="certified",
            sql_used="SELECT COUNT(*) AS location_count FROM locations",
            rows=[{"location_count": 5}],
            audit_id="aud_flag",
            route="sql",
        )


def _executor(db: Path) -> Executor:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-26T00:00:00+00:00",
            expires_at="2026-09-26T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return Executor(cortex=_Cortex(db), minter=minter, warehouse_path=db)  # type: ignore[arg-type]


def _flags_off(monkeypatch: pytest.MonkeyPatch | None = None) -> None:
    names = (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_LANE_BRONZE_SHEET",
        "DMS_SERVED_ATTR_DIAG",
        "DMS_CCA_CASCADE",
        "DMS_HARNESS_ASK_PATHS",
    )
    if monkeypatch is None:
        for name in names:
            os.environ.pop(name, None)
        os.environ["DMS_DEMO_FALLBACK"] = "0"
        return
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")


def run_envelopes(db: Path) -> list[dict[str, Any]]:
    exe = _executor(db)
    rows: list[dict[str, Any]] = []
    for question in _questions():
        env = exe.live_ask(question, session_id="ses_flag")
        rows.append({"question": question, "envelope": _freeze(env)})
    return rows


def test_flag_off_envelopes_match_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags_off(monkeypatch)
    assert os.environ.get("DMS_CLOOP_B") is None
    db = tmp_path / "flag.duckdb"
    ensure_demo_warehouse(db)
    golden = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert golden["baseline"] == _BASELINE
    expected = golden["asks"]
    got = run_envelopes(db)
    assert len(got) == len(expected) == 73
    l2 = 0
    abstain = 0
    canned = 0
    extras_all: list[tuple[str, list[str]]] = []
    for index, question in enumerate(_questions()):
        env = got[index]["envelope"]
        main = expected[index]["envelope"]
        assert expected[index]["question"] == question
        extras = _extra_keys(main, env)
        if extras:
            extras_all.append((question, extras))
    assert extras_all == [], extras_all
    for index, question in enumerate(_questions()):
        env = got[index]["envelope"]
        main = expected[index]["envelope"]
        assert env == main, question
        if env.get("badge") == "L2_VALIDATED":
            l2 += 1
        if env.get("abstained") is True:
            abstain += 1
        sql_used = str(env.get("sql_used") or "")
        if env.get("badge") == "L2_VALIDATED" and "is_cold_storage" in sql_used:
            canned += 1
    assert any("served_check_shadow" in row["envelope"] for row in got)
    assert l2 >= 5, l2
    assert abstain >= 1, abstain
    assert canned >= 3, canned


def _capture(dest: Path) -> None:
    import tempfile

    _flags_off()
    with tempfile.TemporaryDirectory() as raw:
        db = Path(raw) / "flag.duckdb"
        ensure_demo_warehouse(db)
        rows = run_envelopes(db)
    badges: dict[str, int] = {}
    for row in rows:
        badge = str(row["envelope"].get("badge"))
        badges[badge] = badges.get(badge, 0) + 1
    payload = {"baseline": _BASELINE, "asks": rows}
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"n": len(rows), "badges": badges, "out": str(dest)}))


if __name__ == "__main__":
    _capture(Path(sys.argv[1]) if len(sys.argv) > 1 else _FIXTURE)
