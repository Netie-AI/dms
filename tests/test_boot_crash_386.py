"""#386: the API image boots when ``tests/`` is absent.

``apps/api/Dockerfile`` does not copy ``tests/``. ``demo_pack`` used to call
``load_score_pack_metrics()`` at import, which reads
``tests/fixtures/curated_ceo``, so the container died on start.

This file's boot test fails on ``f9ffc3e1`` (import raises ``FileNotFoundError``)
and passes once that read is deferred to the first pack lookup.

Flag-off live ``POST /v1/chat/ask`` envelopes for every question already in
``tests/fixtures/curated_ceo/questions.yaml`` stay the bytes captured from
``f9ffc3e1``. Flags left unset: ``DMS_CCA_CASCADE``, ``DMS_DEMO_FALLBACK``,
``DMS_SERVED_ATTR_DIAG``, ``DMS_INSIGHTS_TIMEOUT_S``, ``DMS_INSIGHTS_CALL_CAP``,
``DMS_HARNESS_ASK_PATHS``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
GOLDEN = ROOT / "tests" / "fixtures" / "boot_crash_386" / "ask_envelopes_f9ffc3e1.json"
BASE = "f9ffc3e15ac81574ee5d21d326a3b46aa559f960"

# Off by default. The pin is the product path with none of these set.
_FLAGS_OFF = (
    "DMS_CCA_CASCADE",
    "DMS_DEMO_FALLBACK",
    "DMS_SERVED_ATTR_DIAG",
    "DMS_INSIGHTS_TIMEOUT_S",
    "DMS_INSIGHTS_CALL_CAP",
    "DMS_HARNESS_ASK_PATHS",
)


@dataclass
class _Cortex:
    asks: list[Any] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_boot_sql",
                output={"rows": [{"n": 7}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_boot_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_boot", hash="hash_boot_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Top 5 SKUs by revenue, highest first.",
            badge="certified",
            sql_used="SELECT sku FROM transactions LIMIT 5",
            rows=[{"sku": "SKU-00397"}],
            assumptions="fixture",
            audit_id="aud_boot",
            route="sql",
        )


def _minter() -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdA",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m


def _cases() -> list[dict[str, str]]:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8")) or {}
    spaces = data.get("spaces") or {}
    out: list[dict[str, str]] = []
    for row in data.get("questions") or []:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("id") or "")
        question = str(row.get("question") or "")
        space_name = str(row.get("space") or "")
        space_id = str(spaces.get(space_name) or "")
        if qid and question and space_id:
            out.append({"id": qid, "question": question, "space_id": space_id})
    return out


def _mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<as_of>" if k == "as_of" else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    return obj


def _flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FLAGS_OFF:
        monkeypatch.delenv(name, raising=False)


def _client(cortex: _Cortex, warehouse: Path) -> tuple[TestClient, Executor]:
    ensure_demo_warehouse(warehouse)
    # The injected executor is the one that serves. Skip the OpenVault probe
    # on the executor create_app builds and then discards.
    import dms_executor

    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    try:
        app = create_app()
    finally:
        dms_executor.probe_openvault = previous
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=warehouse)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=False,
        dms_mcp=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    # TestClient without `with` does not run lifespan, so the injection sticks.
    return TestClient(app), exe


def flag_off_envelopes(warehouse: Path) -> dict[str, Any]:
    """Live /ask body per curated_ceo id, ``as_of`` masked. Flags stay off."""
    cases: dict[str, Any] = {}
    client, exe = _client(_Cortex(), warehouse)
    try:
        for row in _cases():
            r = client.post(
                "/v1/chat/ask",
                json={
                    "question": row["question"],
                    "space_id": row["space_id"],
                    "session_id": f"ses_{row['id']}",
                },
            )
            body: Any
            try:
                body = r.json()
            except json.JSONDecodeError:
                body = r.text
            if r.status_code == 200 and isinstance(body, dict):
                assert_envelope_valid(body)
            cases[row["id"]] = {"status": r.status_code, "body": _mask(body)}
    finally:
        exe.close()
    return {
        "captured_from": BASE,
        "fixture": "tests/fixtures/curated_ceo/questions.yaml",
        "flags_off": list(_FLAGS_OFF),
        "cases": cases,
    }


@pytest.fixture()
def pack_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from dms_executor import demo_pack

    demo_pack.score_pack_exact_metrics.cache_clear()
    demo_pack.curated_l0_question_norms.cache_clear()
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: tmp_path / "absent")
    yield
    demo_pack.score_pack_exact_metrics.cache_clear()
    demo_pack.curated_l0_question_norms.cache_clear()


def _image_layout(dst: Path) -> None:
    """What apps/api/Dockerfile COPYs. No ``tests/``."""
    skip = shutil.ignore_patterns("__pycache__", "*.egg-info", "node_modules", ".pytest_cache")
    for rel in ("packages", "apps/api", "contract", "alembic"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=skip)
    for rel in ("pyproject.toml", "README.md", "alembic.ini"):
        shutil.copy2(ROOT / rel, dst / rel)


_BOOT = """
import json
from dms_api.app import create_app
from dms_executor import demo_pack
from fastapi.testclient import TestClient

health = TestClient(create_app()).get("/health")
try:
    demo_pack.match_pack_phrase("What is our total spend?")
    pack = "loaded"
except demo_pack.DemoPackUnavailable as exc:
    pack = exc.code
print(json.dumps({"file": demo_pack.__file__, "health": health.status_code, "pack": pack}))
"""


def test_api_boots_without_tests_dir_and_health_is_200(tmp_path: Path) -> None:
    """Reproduces #386: on f9ffc3e1 this import dies with FileNotFoundError."""
    app_root = tmp_path / "app"
    _image_layout(app_root)
    assert not (app_root / "tests").exists()
    paths = (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(app_root / p) for p in paths),
        "DMS_SKIP_CONTROL_PLANE_TESTS": "1",
        "DMS_DEMO_FALLBACK": "0",
    }
    env.pop("DATABASE_URL", None)
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT],
        cwd=app_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert Path(got["file"]).resolve().is_relative_to(app_root.resolve()), got
    assert got["health"] == 200, got
    assert got["pack"] == "demo_pack_unavailable", got


def test_pack_is_not_read_at_import() -> None:
    src = (ROOT / "packages/executor/dms_executor/demo_pack.py").read_text(encoding="utf-8")
    assert not re.search(r"^\w[\w: .,\[\]]*=\s*load_score_pack_metrics\(", src, re.M)
    from dms_executor import demo_pack

    assert demo_pack.SCORE_PACK_EXACT_METRICS == demo_pack.score_pack_exact_metrics()


def test_pack_lookup_with_pack_missing_raises_named_error(pack_missing: None) -> None:
    from dms_core.ask import AskServiceError
    from dms_executor.demo_pack import DemoPackUnavailable, maybe_pack_ask

    with pytest.raises(DemoPackUnavailable) as caught:
        maybe_pack_ask("What is our total spend?", grantable={"inventory", "suppliers"})
    assert isinstance(caught.value, AskServiceError)
    assert caught.value.code == "demo_pack_unavailable"


def test_ask_with_pack_missing_returns_demo_pack_unavailable(
    pack_missing: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags_off(monkeypatch)
    cortex = _Cortex()
    client, exe = _client(cortex, tmp_path / "wh.duckdb")
    try:
        r = client.post(
            "/v1/chat/ask",
            json={
                "question": "What is our total spend?",
                "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            },
        )
    finally:
        exe.close()
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "demo_pack_unavailable"
    assert "curated demo pack not found" in detail["message"]
    assert "Traceback" not in r.text
    assert cortex.asks == []
    assert cortex.submits == []


def test_flag_off_fixture_envelopes_match_f9ffc3e1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every curated_ceo question, flags off, matches the f9ffc3e1 capture."""
    _flags_off(monkeypatch)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["captured_from"] == BASE
    assert golden["flags_off"] == list(_FLAGS_OFF)
    got = flag_off_envelopes(tmp_path / "wh.duckdb")
    ids = [row["id"] for row in _cases()]
    assert set(golden["cases"]) == set(ids)
    assert list(got["cases"]) == ids
    for qid in ids:
        assert got["cases"][qid] == golden["cases"][qid], qid
