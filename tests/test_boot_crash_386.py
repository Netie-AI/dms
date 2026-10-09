"""#386: the API image boots when ``tests/`` is absent.

``apps/api/Dockerfile`` does not copy ``tests/``. On ``f9ffc3e1``,
``demo_pack.py:245`` assigned ``SCORE_PACK_EXACT_METRICS =
load_score_pack_metrics()`` at import, so the process died with
``FileNotFoundError``.

A missing fixture is an empty pack (no file-backed phrases), the same as
``curated_l0_question_norms``. ``/health`` says the pack is absent. An
ungrounded ``POST /v1/chat/ask`` returns a normal envelope, never 503.

Flag-off live envelopes for every question already in
``tests/fixtures/curated_ceo/questions.yaml`` stay the bytes captured from
``f9ffc3e1`` when that fixture is on disk. Flags left unset:
``DMS_CCA_CASCADE``, ``DMS_DEMO_FALLBACK``, ``DMS_SERVED_ATTR_DIAG``,
``DMS_INSIGHTS_TIMEOUT_S``, ``DMS_INSIGHTS_CALL_CAP``, ``DMS_HARNESS_ASK_PATHS``.
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


# f9ffc3e1 demo_pack.py:245. Import reads the fixture. No tests/ -> FileNotFoundError.
_F9_IMPORT = """
from pathlib import Path

def load_score_pack_metrics():
    root = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "curated_ceo"
    return (root / "questions.yaml").read_text(encoding="utf-8")

SCORE_PACK_EXACT_METRICS = load_score_pack_metrics()
"""


def test_f9ffc3e1_import_crashes_without_tests_dir(tmp_path: Path) -> None:
    """The f9ffc3e1 import-time read. This head does not do that read."""
    pkg = tmp_path / "packages" / "executor" / "dms_executor"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "demo_pack.py").write_text(_F9_IMPORT, encoding="utf-8")
    assert not (tmp_path / "tests").exists()
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "packages" / "executor")}
    proc = subprocess.run(
        [sys.executable, "-c", "import dms_executor.demo_pack"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0, proc.stdout
    assert "FileNotFoundError" in proc.stderr
    assert "curated_ceo" in proc.stderr and "questions.yaml" in proc.stderr


_BOOT = """
import json
from pathlib import Path
from typing import Any

from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

class Cortex:
    def compute_insights(self, question: str, **_k: Any) -> dict[str, Any]:
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "sql":
            return QueryResult(
                ok=True, status="ok", run_id="run_boot_sql", output={"rows": [{"n": 7}]}
            )
        return QueryResult(ok=True, status="bound", run_id="run_boot_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_boot", hash="hash_boot_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        return AskResponse(
            answer="Top 5 SKUs by revenue, highest first.",
            badge="certified",
            sql_used="SELECT 1",
            rows=[{"n": 1}],
            assumptions="fixture",
            audit_id="aud_boot",
            route="sql",
        )

def minter() -> ManifestMinter:
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

app = create_app()
wh = Path("wh.duckdb")
ensure_demo_warehouse(wh)
cortex = Cortex()
exe = Executor(cortex=cortex, minter=minter(), warehouse_path=wh)
app.state.ask_service = exe
app.state.cortex = cortex
client = TestClient(app)
health = client.get("/health")
ask = client.post(
    "/v1/chat/ask",
    json={
        "question": "How many SKUs do we have in inventory?",
        "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "session_id": "ses_boot",
    },
)
body = ask.json()
if ask.status_code == 200 and isinstance(body, dict) and "badge" in body:
    assert_envelope_valid(body)
print(json.dumps({
    "health": health.status_code,
    "pack": (health.json().get("gen_path_climb") or {}).get("pack"),
    "ask": ask.status_code,
    "badge": body.get("badge") if isinstance(body, dict) else None,
    "abstained": body.get("abstained") if isinstance(body, dict) else None,
}))
exe.close()
"""


def _image_env(app_root: Path) -> dict[str, str]:
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
    for name in _FLAGS_OFF:
        env.pop(name, None)
    return env


def test_image_layout_health_is_absent_and_ask_is_not_503(tmp_path: Path) -> None:
    """Dockerfile COPY set, no tests/. Health names the pack absent. Ask is an envelope."""
    app_root = tmp_path / "app"
    _image_layout(app_root)
    assert not (app_root / "tests").exists()
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT],
        cwd=app_root,
        env=_image_env(app_root),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["health"] == 200, got
    assert got["pack"] == "absent", got
    assert got["ask"] == 200, got
    assert got["badge"] in {"ABSTAIN", "L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED"}, got
    assert got["pack"] != "curated_ceo"


def test_health_pack_stays_curated_ceo_when_the_fixture_is_present() -> None:
    from dms_api.routes.health import GEN_PATH_CLIMB
    from dms_api.wiring import health_pack_name

    assert GEN_PATH_CLIMB["pack"] == "curated_ceo"
    assert health_pack_name() == "curated_ceo"


def test_pack_is_not_read_at_import() -> None:
    src = (ROOT / "packages/executor/dms_executor/demo_pack.py").read_text(encoding="utf-8")
    assert not re.search(r"^\w[\w: .,\[\]]*=\s*load_score_pack_metrics\(", src, re.M)
    from dms_executor import demo_pack

    assert demo_pack.SCORE_PACK_EXACT_METRICS == demo_pack.score_pack_exact_metrics()


def test_missing_pack_is_empty_and_ask_is_not_503(
    pack_missing: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.demo_pack import maybe_pack_ask, score_pack_exact_metrics

    assert score_pack_exact_metrics() == ()
    assert (
        maybe_pack_ask(
            "How many SKUs do we have in inventory?",
            grantable={"inventory"},
        )
        is None
    )
    _flags_off(monkeypatch)
    cortex = _Cortex()
    client, exe = _client(cortex, tmp_path / "wh.duckdb")
    try:
        r = client.post(
            "/v1/chat/ask",
            json={
                "question": "How many SKUs do we have in inventory?",
                "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "session_id": "ses_missing",
            },
        )
    finally:
        exe.close()
    assert r.status_code == 200, r.text
    body = r.json()
    assert_envelope_valid(body)
    assert body["badge"] == "ABSTAIN"
    assert body["abstained"] is True
    assert "demo_pack_unavailable" not in r.text


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
    def _drop(body: Any) -> Any:
        if isinstance(body, dict):
            return {
                key: _drop(val)
                for key, val in body.items()
                if key not in {"served_route", "plan_origin", "ladder_rung", "served_model"}
            }
        if isinstance(body, list):
            return [_drop(val) for val in body]
        return body

    for qid in ids:
        assert _drop(got["cases"][qid]) == _drop(golden["cases"][qid]), qid
