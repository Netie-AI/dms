"""DEMO-PACK-LAZY-01 (dms#386): the API boots without ``tests/``.

``apps/api/Dockerfile`` does not copy ``tests/``, and must not: curated_ceo
holds oracle answers. ``demo_pack`` read that pack at import, so the image
crashed on start. The pack now loads on the first pack lookup. A missing pack
is the named ``demo_pack_unavailable`` refusal, never a crash at boot.

The golden is the ``POST /v1/chat/ask`` bytes from main @ 84a73f01 (pack
read at import), so the default ask is pinned byte-identical before/after.
Only ``as_of`` (wall clock, seconds) is masked.
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
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "demo_pack_lazy_01" / "ask_golden.json"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"

#: Score-pack exact (lazy-loaded), base pack, planted refuse, contract ask.
GOLDEN_QUESTIONS = (
    "How many SKUs do we have in inventory?",
    "What is our total spend by supplier country?",
    "how full is each warehouse",
    "Top 5 selling SKUs by revenue",
)

_AS_OF = re.compile(r'"as_of":\s*"[^"]*"')


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
                run_id="run_lazy_sql",
                output={"rows": [{"n": 7}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_lazy_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_lazy", hash="hash_lazy_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Top 5 SKUs by revenue, highest first.",
            badge="certified",
            sql_used="SELECT sku FROM transactions LIMIT 5",
            rows=[{"sku": "SKU-00397"}],
            assumptions="fixture",
            audit_id="aud_lazy",
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
            issued_at="2026-10-06T00:00:00+00:00",
            expires_at="2026-10-06T01:00:00+00:00",
            signature="dGVzdA",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m


def _client(cortex: _Cortex, warehouse: Path) -> tuple[TestClient, Executor]:
    ensure_demo_warehouse(warehouse)
    app = create_app()
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=warehouse)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), exe


def ask_bytes(warehouse: Path) -> dict[str, str]:
    """Default product /ask response bytes per question, ``as_of`` masked."""
    out: dict[str, str] = {}
    client, exe = _client(_Cortex(), warehouse)
    try:
        for i, question in enumerate(GOLDEN_QUESTIONS):
            r = client.post(
                "/v1/chat/ask",
                json={"question": question, "space_id": FINANCE, "session_id": f"ses_lazy_{i}"},
            )
            out[question] = f"{r.status_code} " + _AS_OF.sub('"as_of":"<as_of>"', r.text)
    finally:
        exe.close()
    return out


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
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT],
        cwd=app_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert Path(got["file"]).resolve().is_relative_to(app_root.resolve()), got
    assert got["health"] == 200, got
    assert got["pack"] == "demo_pack_unavailable", got


def test_pack_lookup_with_pack_missing_raises_named_error(pack_missing: None) -> None:
    from dms_core.ask import AskServiceError
    from dms_executor.demo_pack import DemoPackUnavailable, maybe_pack_ask

    with pytest.raises(DemoPackUnavailable) as caught:
        maybe_pack_ask("What is our total spend?", grantable={"inventory", "suppliers"})
    assert isinstance(caught.value, AskServiceError)
    assert caught.value.code == "demo_pack_unavailable"


def test_ask_with_pack_missing_returns_demo_pack_unavailable(
    pack_missing: None, tmp_path: Path
) -> None:
    cortex = _Cortex()
    client, exe = _client(cortex, tmp_path / "wh.duckdb")
    try:
        r = client.post(
            "/v1/chat/ask",
            json={"question": "What is our total spend?", "space_id": FINANCE},
        )
    finally:
        exe.close()
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "demo_pack_unavailable"
    assert "curated demo pack not found" in detail["message"]
    assert "Traceback" not in r.text
    # Refused before any later lane: no Cortex ask answered in its place.
    assert cortex.asks == []
    assert cortex.submits == []


def test_default_ask_bytes_match_main_golden(tmp_path: Path) -> None:
    from dms_executor import demo_pack

    demo_pack.score_pack_exact_metrics.cache_clear()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = ask_bytes(tmp_path / "wh.duckdb")
    assert list(got) == list(GOLDEN_QUESTIONS)
    for question in GOLDEN_QUESTIONS:
        assert got[question] == golden[question], question
    sku = json.loads(got[GOLDEN_QUESTIONS[0]].split(" ", 1)[1])
    assert sku["badge"] == "L1_GOVERNED_METRIC"
    assert sku["rows"] == [{"n": 7}]
    assert "n=7" in sku["text"]


def test_pack_is_not_read_at_import() -> None:
    src = (ROOT / "packages/executor/dms_executor/demo_pack.py").read_text(encoding="utf-8")
    assert not re.search(r"^\w[\w: .,\[\]]*=\s*load_score_pack_metrics\(", src, re.M)
    from dms_executor import demo_pack

    assert demo_pack.SCORE_PACK_EXACT_METRICS == demo_pack.score_pack_exact_metrics()
