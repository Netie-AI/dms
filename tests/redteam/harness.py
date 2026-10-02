"""Red-team harness: drive cases through the REAL DMS ask route, Cortex and model stubbed.

Real: routes/chat.py handler, Executor.live_ask ladder, generative_ask gate,
build_answer_envelope, assert_envelope_valid, HTTP error mapping.
Stubbed: Cortex compute_insights (returns the case's model_sql, INFERRED), submit
(runs the SQL for real on a DuckDB copy), ledger_append, ask (always abstains), the
manifest minter, and the F5 gate (soft-skipped: the stub has no base_url).

No network, no OpenVault, no model, no key. ``dms_api`` is never imported through its
package ``__init__`` (that imports ``dms_api.app``, which probes OpenVault at import):
a stub parent package is installed first. A network guard raises on any real httpx
transport call or DNS lookup made while a case runs, and counts the attempts.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import json
import os
import platform
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STUB = "redteam-stub"
_PATH_DIRS = (
    REPO / "apps" / "api",
    REPO / "packages" / "core",
    REPO / "packages" / "cortex_client",
    REPO / "packages" / "executor",
    REPO / "packages" / "ledger",
)


def ensure_paths() -> None:
    """Repo package dirs + this folder on sys.path, then prove the worktree is what imports."""
    for d in (HERE, *_PATH_DIRS):
        s = str(d)
        if s not in sys.path:
            sys.path.insert(0, s)
    import dms_executor

    got = Path(dms_executor.__file__).resolve()
    if REPO.resolve() not in got.parents:
        raise RuntimeError(
            f"dms_executor resolves to {got}, outside the worktree {REPO} "
            "(stale editable install?). Set PYTHONPATH to the worktree packages."
        )


ensure_paths()

import httpx  # noqa: E402

from rt_cases import SPACE_IDS, Case, CaseError, ext_sql_path_for, load_cases  # noqa: E402
from rt_data import FamilyDb, build_family_db, run_gold, sha256_file, table_counts  # noqa: E402
from rt_grader import grade  # noqa: E402
from rt_packets import build_packets  # noqa: E402

NETWORK_ATTEMPTS: list[str] = []


# ------------------------------------------------------------------ network guard
@contextlib.contextmanager
def network_guard():
    """Raise (and count) on any real httpx transport call or DNS lookup in the block.

    In-process ASGI calls (TestClient) and MockTransport are not affected: only the
    real network transports are patched.
    """
    orig_sync = httpx.HTTPTransport.handle_request
    orig_async = httpx.AsyncHTTPTransport.handle_async_request
    orig_gai = socket.getaddrinfo

    def blocked_sync(self: Any, request: httpx.Request) -> Any:
        NETWORK_ATTEMPTS.append(f"httpx {request.method} {request.url}")
        raise httpx.ConnectError("redteam network guard: no network", request=request)

    async def blocked_async(self: Any, request: httpx.Request) -> Any:
        NETWORK_ATTEMPTS.append(f"httpx-async {request.method} {request.url}")
        raise httpx.ConnectError("redteam network guard: no network", request=request)

    def blocked_gai(host: Any, *a: Any, **k: Any) -> Any:
        NETWORK_ATTEMPTS.append(f"getaddrinfo {host}")
        raise socket.gaierror(11001, "redteam network guard: no DNS")

    httpx.HTTPTransport.handle_request = blocked_sync  # type: ignore[method-assign]
    httpx.AsyncHTTPTransport.handle_async_request = blocked_async  # type: ignore[method-assign]
    socket.getaddrinfo = blocked_gai  # type: ignore[assignment]
    try:
        yield
    finally:
        httpx.HTTPTransport.handle_request = orig_sync  # type: ignore[method-assign]
        httpx.AsyncHTTPTransport.handle_async_request = orig_async  # type: ignore[method-assign]
        socket.getaddrinfo = orig_gai  # type: ignore[assignment]


# ------------------------------------------------------------------ dms_api without app import
def ensure_dms_api_stub() -> None:
    """Install a parent ``dms_api`` package that does NOT run its ``__init__``.

    The real ``dms_api/__init__.py`` imports ``dms_api.app``, whose last line is
    ``app = create_app()``: that probes OpenVault (127.0.0.1:5000) and seeds a lake at
    import. Submodules (routes, deps, settings, store, wiring) import fine without it.
    ``dms_api.create_app`` still works lazily for anyone who really wants it.
    """
    if "dms_api" in sys.modules:
        return
    pkg = ModuleType("dms_api")
    path = str(REPO / "apps" / "api" / "dms_api")
    pkg.__path__ = [path]  # type: ignore[attr-defined]
    spec = importlib.machinery.ModuleSpec("dms_api", loader=None, is_package=True)
    spec.submodule_search_locations = [path]
    pkg.__spec__ = spec

    def _lazy(name: str) -> Any:
        if name == "create_app":
            from dms_api.app import create_app

            return create_app
        raise AttributeError(name)

    pkg.__getattr__ = _lazy  # type: ignore[attr-defined]
    sys.modules["dms_api"] = pkg


# ------------------------------------------------------------------ the Cortex stub
@dataclass
class Trace:
    """Harness-side facts about what the stubs saw. Never shown to judges."""

    compute_calls: int = 0
    bind_calls: int = 0
    submit_sql: list[str] = field(default_factory=list)
    manifest_tables: list[list[str]] = field(default_factory=list)
    submit_error: str | None = None
    ledger_calls: int = 0
    cortex_ask_calls: int = 0
    cortex_clock: dict[str, str] | None = None
    live_ask_exception: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "compute_calls": self.compute_calls,
            "bind_calls": self.bind_calls,
            "submit_sql": self.submit_sql,
            "manifest_tables": self.manifest_tables,
            "submit_error": self.submit_error,
            "ledger_calls": self.ledger_calls,
            "cortex_ask_calls": self.cortex_ask_calls,
            "cortex_clock": self.cortex_clock,
            "live_ask_exception": self.live_ask_exception,
            "fell_through_to_cortex_ask": self.cortex_ask_calls > 0,
        }


class RedteamCortex:
    """Stub of the CortexClient surface DMS touches. Deliberately has NO ``base_url``,
    so ``compliance_gate`` returns the soft ``gate_unavailable`` (F5 is NOT exercised)."""

    def __init__(self, cortex_db: Path, *, wire: str = "fastapi") -> None:
        self.cortex_db = Path(cortex_db)
        self.wire = wire
        self.case: Case | None = None
        self.trace = Trace()
        self._n = 0

    def begin_case(self, case: Case) -> Trace:
        self.case = case
        self.trace = Trace()
        return self.trace

    def close(self) -> None:  # CortexClient parity
        return None

    # ---- Insights generate: hands back the case's INFERRED model SQL
    def compute_insights(
        self,
        question: str,
        *,
        session_id: str | None = None,
        space_id: str | None = None,
        ontology: Any = None,
        **_: Any,
    ) -> dict[str, Any] | None:
        self.trace.compute_calls += 1
        case = self.case
        if case is None or case.lane != "gen" or not case.model_sql:
            return None  # a sheet-lane ask: no model, a visible miss
        from cortex_client.compute import INSIGHTS_REACHED

        sql = case.model_sql
        return {
            "query_sql": sql,
            "generative": {"ok": True, "valid": True, "sql": sql},
            INSIGHTS_REACHED: True,
            "phase": "generate",
            "plan_origin": "generate_sql",
            "plan_source": "ontology_plan",
            "served_provider": STUB,
            "served_model": STUB,
            "served_local": False,
            "learn_enabled": False,
            "learn_source": STUB,
            "route_store_id": STUB,
            "generate_legs": {
                "count": 1,
                "legs": [{"returned": "sql", "served_provider": STUB, "served_model": STUB}],
            },
        }

    # ---- session bind + SQL execution
    def submit(self, req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        self._n += 1
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "session_bind":
            self.trace.bind_calls += 1
            return QueryResult(ok=True, status="bound", run_id=f"run_rt_bind_{self._n}")
        body = getattr(req, "body", None) or {}
        sql = str(body.get("sql") or "")
        manifest = getattr(req, "manifest", None)
        self.trace.submit_sql.append(sql)
        self.trace.manifest_tables.append(sorted(getattr(manifest, "row_predicates", None) or {}))
        import duckdb

        # External access off: a stub that read local files would be more permissive (and
        # more dangerous) than the engine, which refuses unknown table functions.
        con = duckdb.connect(
            str(self.cortex_db), read_only=True, config={"enable_external_access": False}
        )
        try:
            clk = con.execute(
                "SELECT CAST(CURRENT_DATE AS VARCHAR), current_setting('TimeZone')"
            ).fetchone()
            self.trace.cortex_clock = {"current_date": clk[0], "timezone": clk[1]}
            cur = con.execute(sql)
            cols = [str(d[0]) for d in cur.description or []]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            self.trace.submit_error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:240]}"
            return QueryResult(
                ok=False, status="error", run_id=f"run_rt_{self._n}", error=self.trace.submit_error
            )
        finally:
            con.close()
        if self.wire == "fastapi":
            from fastapi.encoders import jsonable_encoder

            rows = jsonable_encoder(rows)
        return QueryResult(ok=True, status="ok", run_id=f"run_rt_{self._n}", output={"rows": rows})

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        self.trace.ledger_calls += 1
        return LedgerAppendResponse(
            entry_id=f"led_rt_{self._n}_{self.trace.ledger_calls}",
            hash=f"hash_rt_{self._n}_{self.trace.ledger_calls}_digest",
        )

    # ---- contract ask: never answers, so a ladder fall-through is visible
    def ask(self, req: Any) -> Any:
        from cortex_client.models import AskResponse

        self.trace.cortex_ask_calls += 1
        return AskResponse(
            answer=(
                "redteam-stub: the Cortex contract ask is not available in this harness; "
                "this ask fell through the ladder."
            ),
            abstained=True,
            badge="abstain",
            route="abstain",
            assumptions="redteam-stub",
        )


def _stub_minter() -> Any:
    from cortex_contract.execution import Manifest
    from dms_executor.manifest import ManifestMinter, SessionAcl

    class StubMinter(ManifestMinter):
        def __init__(self) -> None:
            # A MockTransport client: even a stray call stays in-process.
            http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(599)))
            super().__init__(openvault_url="http://redteam.invalid", http=http)

        def fetch_intermediate(self, *, ttl_s: int = 900) -> Any:
            raise RuntimeError("redteam: no OpenVault")

        def mint_manifest(self, acl: SessionAcl) -> Manifest:
            now = datetime.now(UTC)
            return Manifest(
                session_id=acl.session_id,
                org_id=acl.org_id,
                space_id=acl.space_id,
                pool_id=acl.pool_id,
                issuer_key_id="redteam-stub-kid",
                allowed_paths=list(acl.allowed_paths),
                row_predicates=dict(acl.row_predicates),
                issued_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=900)).isoformat(),
                signature="cmVkdGVhbS1zdHVi",
            )

        def close(self) -> None:
            return None

        def invalidate(self, *_a: Any, **_k: Any) -> None:
            return None

    return StubMinter()


# ------------------------------------------------------------------ the driver
def _clock_read() -> dict[str, str]:
    import duckdb

    con = duckdb.connect()
    try:
        d, tz = con.execute("SELECT CAST(CURRENT_DATE AS VARCHAR), current_setting('TimeZone')").fetchone()
    finally:
        con.close()
    return {"current_date": d, "timezone": tz, "utc_date": datetime.now(UTC).strftime("%Y-%m-%d")}


def _jsonable(obj: Any) -> Any:
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj)


class Harness:
    """One family's lake + a bare FastAPI app that mounts ONLY ``routes.chat``."""

    def __init__(
        self,
        db: FamilyDb,
        *,
        run_id: str,
        harness_ask_paths: bool = True,
        wire: str = "fastapi",
    ) -> None:
        self.db = db
        self.run_id = run_id
        ensure_dms_api_stub()
        self._env_saved = {
            k: os.environ.get(k)
            for k in ("DMS_WAREHOUSE_DB", "DMS_DEMO_FALLBACK", "DMS_ASK_MODE", "DMS_SYNC_BRONZE")
        }
        # Any default-path code lands in the scratch lake, never <repo>/data.
        os.environ["DMS_WAREHOUSE_DB"] = str(db.dms)
        os.environ["DMS_DEMO_FALLBACK"] = "0"
        os.environ["DMS_ASK_MODE"] = "live"
        os.environ["DMS_SYNC_BRONZE"] = "0"

        from dms_executor import Executor
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from dms_api.middleware_actor import RejectIdentityHeadersMiddleware
        from dms_api.routes import chat
        from dms_api.settings import Settings, get_settings
        from dms_api.store.memory import DemoSpaceStore

        class _RecordingExecutor(Executor):
            last_exception: str | None = None

            def live_ask(self, *a: Any, **k: Any) -> dict[str, Any]:
                self.last_exception = None
                try:
                    return super().live_ask(*a, **k)
                except Exception as exc:  # noqa: BLE001
                    self.last_exception = f"{type(exc).__name__}: {str(exc)[:300]}"
                    raise

        self.cortex = RedteamCortex(db.cortex, wire=wire)
        self.executor = _RecordingExecutor(
            cortex=self.cortex, minter=_stub_minter(), warehouse_path=db.dms  # type: ignore[arg-type]
        )
        settings = Settings(
            _env_file=None,
            dms_ask_mode="live",
            dms_demo_fallback=False,
            dms_harness_ask_paths=harness_ask_paths,
        )
        app = FastAPI()
        app.state.space_store = DemoSpaceStore.seeded()
        app.state.cortex = self.cortex
        app.state.ask_service = self.executor
        app.add_middleware(RejectIdentityHeadersMiddleware)
        app.include_router(chat.router)
        app.dependency_overrides[get_settings] = lambda: settings
        self.settings = settings
        self.client = TestClient(app, raise_server_exceptions=False)

    def close(self) -> None:
        for k, v in self._env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __enter__(self) -> Harness:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- one case
    def ask(self, case: Case) -> dict[str, Any]:
        """Run one case end to end and return its JSONL record (JSON-safe)."""
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        clock_start = _clock_read()
        trace = self.cortex.begin_case(case)
        self.executor.last_exception = None
        body: dict[str, Any] = {
            "question": case.question,
            "space_id": SPACE_IDS[case.space],
            "session_id": f"rt-{self.run_id}-{case.id}",
            "ask_path": case.ask_path,
        }
        if case.grounded_tables:
            body["grounded_tables"] = list(case.grounded_tables)

        status: int | None = None
        raw: Any = None
        exception: str | None = None
        attempts_before = len(NETWORK_ATTEMPTS)
        try:
            with network_guard():
                resp = self.client.post("/v1/chat/ask", json=body)
            status = resp.status_code
            try:
                raw = resp.json()
            except ValueError:
                raw = resp.text
        except Exception as exc:  # noqa: BLE001
            exception = f"{type(exc).__name__}: {str(exc)[:300]}"
        latency = round(time.perf_counter() - t0, 4)
        trace.live_ask_exception = self.executor.last_exception

        gold_rows: list[dict[str, Any]] | None = None
        gold_error: str | None = None
        if case.gold_sql:
            gold_rows, gold_error, _cols = run_gold(self.db.gold, case.gold_sql)
        clock_end = _clock_read()
        crossed = (
            clock_start["utc_date"] != clock_end["utc_date"]
            or clock_start["current_date"] != clock_end["current_date"]
        )
        counts_now = table_counts(self.db.dms)
        lake_intact = counts_now == {
            t: self.db.table_counts[t] for t in counts_now if t in self.db.table_counts
        }

        mech = grade(
            expect=case.expect,
            question=case.question,
            http_status=status,
            http_body=raw,
            gold_sql=case.gold_sql,
            gold_rows=gold_rows,
            gold_error=gold_error,
            exception=exception,
            discarded="crossed_midnight:discarded" if crossed else None,
        )
        if not lake_intact:
            mech["reasons"].append("lake_changed_during_run:extension_rows_or_seed_altered")
            if mech["verdict"] != "HARNESS_ERROR":
                mech["verdict"] = "HARNESS_ERROR"
        env_d = raw if isinstance(raw, dict) else {}
        return {
            "type": "case",
            "run_id": self.run_id,
            "case_id": case.id,
            "family": case.family,
            "lane": case.lane,
            "ask_path": case.ask_path,
            "space": case.space,
            "question": case.question,
            "model_sql": case.model_sql,
            "model_sql_origin": (
                "INFERRED (stub injected, not observed from a model)" if case.model_sql else None
            ),
            "expect": case.expect,
            "control": case.control,
            "http_status": status,
            "latency_s": latency,
            "envelope": raw,
            "exception": exception,
            "started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "engine_date_seen": {
                "harness_clock_start": clock_start,
                "harness_clock_end": clock_end,
                "cortex_stub_submit": trace.cortex_clock,
                "envelope_engine_as_of": env_d.get("engine_as_of"),
            },
            "discarded": bool(crossed),
            "lake_intact": lake_intact,
            "network_attempts_this_case": len(NETWORK_ATTEMPTS) - attempts_before,
            "gold_sql": case.gold_sql,
            "gold_rows": _jsonable(gold_rows) if gold_rows is not None else None,
            "gold_error": gold_error,
            "stub_trace": trace.as_dict(),
            "mechanical": {
                "verdict": mech["verdict"],
                "reasons": mech["reasons"],
                "label_issues": mech["label_issues"],
                "answered": mech["answered"],
                "abstain_reason": mech["abstain_reason"],
                "badge_label": mech["badge_label"],
            },
        }


# ------------------------------------------------------------------ run control
def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=20, check=False
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def run_header(run_id: str, families: dict[str, dict[str, Any]]) -> dict[str, Any]:
    import duckdb
    import fastapi
    import sqlglot

    return {
        "type": "header",
        "run_id": run_id,
        "started_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "python": sys.version.split()[0],
        "duckdb": duckdb.__version__,
        "sqlglot": sqlglot.__version__,
        "fastapi": fastapi.__version__,
        "platform": platform.platform(),
        "dms_executor": str(Path(sys.modules["dms_executor"].__file__).resolve()),
        "stub": STUB,
        "model_sql": "INFERRED, injected through a Cortex stub; never observed from a model",
        "not_exercised": [
            "F5 compliance gate (stub Cortex has no base_url: soft gate_unavailable)",
            "Cortex manifest signature / path allow-list / sqlglot guardrail / EXPLAIN gate / row cap",
            "OpenVault, any model, the real ledger chain",
        ],
        "families": families,
    }


@dataclass
class RunResult:
    run_id: str
    results_path: Path
    header: dict[str, Any]
    records: list[dict[str, Any]]
    footer: dict[str, Any]
    packets_path: Path | None = None
    key_path: Path | None = None

    def lines(self) -> list[dict[str, Any]]:
        return [self.header, *self.records, self.footer]


def run_cases(
    cases_path: str | Path,
    *,
    out_dir: str | Path,
    run_id: str | None = None,
    family: str | None = None,
    ext_sql: str | Path | None = None,
    harness_ask_paths: bool = True,
    judge_packets: bool = False,
) -> RunResult:
    """Strictly sequential. One fresh lake per family, one session id per case."""
    rid = run_id or ("rt-" + uuid.uuid4().hex[:8])
    cases = load_cases(cases_path, family=family)
    out_root = Path(out_dir) / rid
    out_root.mkdir(parents=True, exist_ok=True)
    by_family: dict[str, list[Case]] = {}
    for c in cases:
        by_family.setdefault(c.family, []).append(c)

    records: list[dict[str, Any]] = []
    fam_headers: dict[str, dict[str, Any]] = {}
    gold_hashes: dict[str, tuple[Path, str]] = {}
    for fam in sorted(by_family):
        ext = Path(ext_sql) if ext_sql else ext_sql_path_for(cases_path, fam)
        db = build_family_db(
            fam,
            ext,
            out_root / "db",
            with_sheets=any(c.lane == "sheet" for c in by_family[fam]),
        )
        fam_headers[fam] = db.header()
        gold_hashes[fam] = (db.gold, db.gold_sha256)
        with Harness(db, run_id=rid, harness_ask_paths=harness_ask_paths) as h:
            for case in by_family[fam]:
                records.append(h.ask(case))

    header = run_header(rid, fam_headers)
    verdicts = Counter(r["mechanical"]["verdict"] for r in records)
    footer = {
        "type": "footer",
        "run_id": rid,
        "finished_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cases": len(records),
        "verdicts": dict(verdicts),
        "badge_label_violations": sum(1 for r in records if not r["mechanical"]["badge_label"]["ok"]),
        "gold_pristine": {f: sha256_file(p) == h for f, (p, h) in gold_hashes.items()},
        "network_attempts": list(NETWORK_ATTEMPTS),
    }
    results_path = out_root / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for line in (header, *records, footer):
            fh.write(json.dumps(_jsonable(line), ensure_ascii=False) + "\n")
    res = RunResult(rid, results_path, header, records, footer)
    if judge_packets:
        res.packets_path, res.key_path = write_packets(records, out_root)
    return res


def write_packets(records: list[dict[str, Any]], out_root: Path) -> tuple[Path, Path]:
    packets, key = build_packets(records)
    pp = out_root / "judge_packets.jsonl"
    kp = out_root / "packet_key.json"
    pp.write_text(
        "".join(json.dumps(_jsonable(p), ensure_ascii=False) + "\n" for p in packets), encoding="utf-8"
    )
    kp.write_text(json.dumps(key, indent=2), encoding="utf-8")
    return pp, kp


__all__ = [
    "CaseError",
    "Harness",
    "NETWORK_ATTEMPTS",
    "RedteamCortex",
    "RunResult",
    "ensure_dms_api_stub",
    "network_guard",
    "run_cases",
    "run_header",
    "write_packets",
]
