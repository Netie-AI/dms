"""DEMO-PACK-LAZY-01 (dms#386): served /ask never opens the scored pack.

``tests/fixtures/curated_ceo`` (questions.yaml, oracles.yaml) is the score
pack and holds oracle answers. The API image does not ship ``tests/`` and
must not. Served code (dms_api, dms_executor, dms_core, cortex_client) now
matches only the ten code-constant ``PACK_METRICS``. Scoring and tests may
still read the pack. The five former score-pack exact ids take the normal
lanes like any other question.

The golden is ``POST /v1/chat/ask`` bytes from main @ 6f7139a3 for every
curated question except those five, plus the four default asks and the ten
base pack phrases. ``head_intended`` holds the bytes that change on purpose.
Only ``as_of`` (wall clock, seconds) is masked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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
TESTS = Path(__file__).resolve().parent
PACK_DIR = ROOT / "tests" / "fixtures" / "curated_ceo"
GOLDEN = ROOT / "tests" / "fixtures" / "demo_pack_lazy_01" / "ask_golden.json"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SERVED = (
    "apps/api/dms_api",
    "packages/core/dms_core",
    "packages/cortex_client/cortex_client",
    "packages/executor/dms_executor",
    "packages/ledger/dms_ledger",
)
FIVE_IDS = (
    "cq_sku_count",
    "cq_sales_top3_volume",
    "cq_sku_count_by_category",
    "cq_supplier_ranking",
    "trap_categoty",
)
GENERIC_Q = "How many florbs did wibble sell last week?"

#: cq_sku_count phrase, base pack phrase, planted refuse, curated contract phrase.
GOLDEN_QUESTIONS = (
    "How many SKUs do we have in inventory?",
    "What is our total spend by supplier country?",
    "how full is each warehouse",
    "Top 5 selling SKUs by revenue",
)

_AS_OF = re.compile(r'"as_of":\s*"[^"]*"')
#: Path-shaped references only. ``/health`` labels its harness pack by name.
_PACK_PATH_REF = re.compile(
    r"""curated_ceo["']?\s*/|/\s*["']curated_ceo|oracles\.yaml|questions\.yaml"""
    r"""|["']fixtures["']|tests/fixtures"""
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


def _client(
    cortex: _Cortex, warehouse: Path, *, harness: bool = False
) -> tuple[TestClient, Executor]:
    ensure_demo_warehouse(warehouse)
    app = create_app()
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=warehouse)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=harness,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), exe


def ask_bytes(
    warehouse: Path, cases: list[dict[str, str]], *, ask_path: str | None = None
) -> dict[str, str]:
    """/ask response bytes per case key, ``as_of`` masked."""
    out: dict[str, str] = {}
    client, exe = _client(_Cortex(), warehouse, harness=ask_path is not None)
    try:
        for case in cases:
            body: dict[str, Any] = {
                "question": case["question"],
                "space_id": case["space"],
                "session_id": "ses_lazy_" + re.sub(r"\W", "_", case["key"]),
            }
            if ask_path is not None:
                body["ask_path"] = ask_path
            r = client.post("/v1/chat/ask", json=body)
            out[case["key"]] = f"{r.status_code} " + _AS_OF.sub('"as_of":"<as_of>"', r.text)
    finally:
        exe.close()
    return out


def pack_cases() -> list[dict[str, str]]:
    """Every curated_ceo question with its Space. Test-time read, never served."""
    import yaml

    data = yaml.safe_load((PACK_DIR / "questions.yaml").read_text(encoding="utf-8"))
    spaces = data["spaces"]
    return [
        {
            "key": f"curated:{c['id']}",
            "question": str(c["question"]),
            "space": str(spaces.get(c.get("space") or "finance", c.get("space"))),
        }
        for c in data["questions"]
    ]


def base_cases() -> list[dict[str, str]]:
    from dms_executor.demo_pack import PACK_METRICS

    defaults = [
        {"key": f"default:{i}", "question": q, "space": FINANCE}
        for i, q in enumerate(GOLDEN_QUESTIONS)
    ]
    base = [
        {"key": f"base:{m.metric_id}", "question": m.question, "space": FINANCE}
        for m in PACK_METRICS
    ]
    return defaults + base + [{"key": "generic", "question": GENERIC_Q, "space": FINANCE}]


def _golden() -> dict[str, Any]:
    return dict(json.loads(GOLDEN.read_text(encoding="utf-8")))


def _five_cases() -> list[dict[str, str]]:
    return [c for c in _golden()["cases"] if c["key"] in {f"curated:{i}" for i in FIVE_IDS}]


_RUN = """
import json, os, sys

opens = []

def _hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        p = os.fsdecode(args[0]).replace(os.sep, "/")
        if "curated_ceo" in p or p.endswith(("oracles.yaml", "questions.yaml")):
            opens.append(p)

sys.addaudithook(_hook)
job = json.loads(sys.stdin.read())
sys.path.insert(0, job["tests"])
import test_demo_pack_lazy_01 as t
from dms_api.app import create_app
from dms_executor import demo_pack
from fastapi.testclient import TestClient
from pathlib import Path

health = TestClient(create_app()).get("/health").status_code
asks = {}
for i, ask_path in enumerate(job["ask_paths"]):
    got = t.ask_bytes(Path(job["warehouse"] + str(i)), job["cases"], ask_path=ask_path)
    asks.update({f"{ask_path or 'product'}|{k}": v for k, v in got.items()})
print(json.dumps({"file": demo_pack.__file__, "health": health, "asks": asks, "opens": opens}))
"""


def _run(
    app_root: Path, tmp: Path, cases: list[dict[str, str]], ask_paths: list[str | None]
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Fresh interpreter: the audit hook is on before any served import."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(app_root / p.rsplit("/", 1)[0]) for p in SERVED),
        "DMS_SKIP_CONTROL_PLANE_TESTS": "1",
        "DMS_DEMO_FALLBACK": "0",
    }
    job = {
        "tests": str(TESTS),
        "warehouse": str(tmp / "wh"),
        "cases": cases,
        "ask_paths": ask_paths,
    }
    proc = subprocess.run(
        [sys.executable, "-c", _RUN],
        cwd=app_root,
        env=env,
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=600,
    )
    got = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else {}
    return proc, got


def _image_layout(dst: Path) -> None:
    """What apps/api/Dockerfile COPYs. No ``tests/``."""
    skip = shutil.ignore_patterns("__pycache__", "*.egg-info", "node_modules", ".pytest_cache")
    for rel in ("packages", "apps/api", "contract", "alembic"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=skip)
    for rel in ("pyproject.toml", "README.md", "alembic.ini"):
        shutil.copy2(ROOT / rel, dst / rel)


@pytest.fixture(scope="module")
def with_pack(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    assert (PACK_DIR / "questions.yaml").is_file()
    assert (PACK_DIR / "oracles.yaml").is_file()
    tmp = tmp_path_factory.mktemp("with_pack")
    return ask_bytes(tmp / "wh.duckdb", _golden()["cases"])


@pytest.fixture(scope="module")
def no_pack(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], Path]:
    tmp = tmp_path_factory.mktemp("no_pack")
    app_root = tmp / "app"
    _image_layout(app_root)
    assert not (app_root / "tests").exists()
    proc, got = _run(app_root, tmp, _golden()["cases"], [None])
    if got:
        got["asks"] = {k.split("|", 1)[1]: v for k, v in got["asks"].items()}
    return proc, got, app_root


def _ok(no_pack: tuple[subprocess.CompletedProcess[str], dict[str, Any], Path]) -> dict[str, Any]:
    proc, got, app_root = no_pack
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert Path(got["file"]).resolve().is_relative_to(app_root.resolve()), got["file"]
    return got


def test_no_served_module_opens_scored_pack_during_ask(tmp_path: Path) -> None:
    """Runtime: audit hook on 'open' before import, real /v1/chat/ask, pack on disk."""
    assert (PACK_DIR / "oracles.yaml").is_file()
    cases = _five_cases() + [
        c for c in base_cases() if c["key"] in {"generic", "default:1", "default:2"}
    ]
    cases.append(next(c for c in pack_cases() if c["key"] == "curated:cq_sku_count_syn_short"))
    proc, got = _run(ROOT, tmp_path, cases, [None, "exact", "generative"])
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert got["health"] == 200
    assert len(got["asks"]) == 3 * len(cases)
    assert all(v.startswith("200 ") for v in got["asks"].values()), got["asks"]
    assert got["opens"] == []


def test_served_source_has_no_scored_pack_path() -> None:
    """Static: no served module names a scored-pack path. Allowlist is empty."""
    allow: set[str] = set()
    hits = []
    for pkg in SERVED:
        for py in sorted((ROOT / pkg).rglob("*.py")):
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if _PACK_PATH_REF.search(line):
                    hits.append(f"{py.relative_to(ROOT).as_posix()}:{n}")
    assert [h for h in hits if h.split(":")[0] not in allow] == []


#: Normal lanes, no pack phrase, no special case. The fake Insights is unsure,
#: so each is the generative lane's named abstain. Never a number.
_GEN01 = "GEN-01: compute abstained (unsure)"
_GEN01_TEXT = (
    "I cannot certify an ontology-grounded query for that question, so I am not executing one."
)
_FIVE_PINNED = {qid: ("generated", "ABSTAIN", _GEN01, _GEN01_TEXT) for qid in FIVE_IDS}


def test_five_ids_pinned_outcome_same_with_and_without_pack(
    with_pack: dict[str, str],
    no_pack: tuple[subprocess.CompletedProcess[str], dict[str, Any], Path],
) -> None:
    golden = _golden()
    absent = _ok(no_pack)["asks"]
    for qid, (route, badge, reason, text) in _FIVE_PINNED.items():
        key = f"curated:{qid}"
        want = golden["head_intended"][key]
        assert with_pack[key] == want, qid
        assert absent[key] == want, qid
        status, raw = want.split(" ", 1)
        env = json.loads(raw)
        assert status == "200"
        assert (env["route"], env["badge"], env["abstained"]) == (route, badge, True), qid
        assert env["assumptions"] == [reason], qid
        assert env["audit_receipt"]["unsure"]["why"] == reason, qid
        assert env["text"] == text, qid
        assert env["rows"] == [] and env["values"] == [] and env["sql_used"] is None, qid
    _assert_no_oracle_sql_in_served_source()


def _assert_no_oracle_sql_in_served_source() -> None:
    import yaml

    oracles = yaml.safe_load((PACK_DIR / "oracles.yaml").read_text(encoding="utf-8"))["oracles"]
    src = " ".join(
        " ".join(py.read_text(encoding="utf-8").split())
        for pkg in SERVED
        for py in (ROOT / pkg).rglob("*.py")
    )
    for qid in FIVE_IDS:
        cortex_id = "cq_top3_category_sales" if qid == "trap_categoty" else qid
        sql = " ".join(str((oracles.get(qid) or oracles[cortex_id])["sql"]).split())
        assert sql and sql not in src, qid


def test_every_other_question_bytes_match_main_golden(
    with_pack: dict[str, str],
    no_pack: tuple[subprocess.CompletedProcess[str], dict[str, Any], Path],
) -> None:
    golden = _golden()
    five = {f"curated:{i}" for i in FIVE_IDS}
    want_keys = {c["key"] for c in pack_cases()} - five
    want_keys |= {c["key"] for c in base_cases()}
    assert {c["key"] for c in golden["cases"]} - five == want_keys
    assert golden["main_sha"].startswith("6f7139a3")
    absent = _ok(no_pack)["asks"]
    by_key = {c["key"]: c for c in golden["cases"]}
    five_q = {by_key[k]["question"] for k in five}
    for key in sorted(want_keys):
        if key in golden["head_intended"]:
            # Only two reasons to differ from main: the same text as one of
            # the five ids, or main's questions.yaml curated-l0 override.
            same_text = by_key[key]["question"] in five_q
            override = (
                '"answer_id":"ans_curated_step"' in golden["main"][key]
                and "pack-metric miss" in golden["main"][key]
            )
            assert same_text or override, key
        want = golden["head_intended"].get(key, golden["main"][key])
        assert with_pack[key] == want, key
        assert absent[key] == want, key


def test_api_boots_without_tests_dir_and_health_is_200(
    with_pack: dict[str, str],
    no_pack: tuple[subprocess.CompletedProcess[str], dict[str, Any], Path],
) -> None:
    got = _ok(no_pack)
    assert got["health"] == 200
    assert got["opens"] == []
    for key in ("base:spend_by_country", "curated:cq_sku_count", "generic"):
        assert got["asks"][key].startswith("200 "), got["asks"][key]
        assert got["asks"][key] == with_pack[key], key
    spend = json.loads(got["asks"]["base:spend_by_country"].split(" ", 1)[1])
    assert spend["badge"] == "L1_GOVERNED_METRIC" and spend["rows"] == [{"n": 7}]
    for key, raw in got["asks"].items():
        assert not raw.startswith("503 "), key
        assert "demo_pack_unavailable" not in raw, key


def test_score_pack_missing_is_named_demo_pack_unavailable(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from score_curated import load_oracles, load_pack

    for load in (load_pack, load_oracles):
        with pytest.raises(SystemExit, match="^demo_pack_unavailable: "):
            load(tmp_path / "absent.yaml")
