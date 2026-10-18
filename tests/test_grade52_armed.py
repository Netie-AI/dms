"""GRADE52-ARMED-01. The 52-question grader can measure the AI lane, or says it did not.

The stub run cannot exercise the AI lane: Insights is unarmed and the
recorded Cortex serves placeholder answers, so its ai=0 is the harness, not
the product. ``--armed`` posts each question through this checkout's
``POST /v1/chat/ask`` with the product ``CortexClient`` and the product
OpenVault minter against a live Cortex. These tests stand in for that Cortex
and OpenVault with real HTTP servers on loopback, so every byte crosses the
same client code a live run uses.

Each armed failure is a named non-zero exit, never a stub score labelled
armed: no keys, unreachable Cortex, unarmed FreeRoute, a failed mint, a
placeholder answer, or a run that never reached a model.
"""

from __future__ import annotations

import base64
import itertools
import json
import socket
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import capture_layout  # noqa: E402
import grade52  # noqa: E402

_KEY = "ov_armed_test_key_0123456789"
_SVC_TOKEN = "armed-test-service-token"
_CORTEX_SHA = "c0ffee" + "0" * 34
_COPY_ONLY = ROOT / "tests" / "fixtures" / "ask_guide" / "copy_only_57d85c52.jsonl"
# The model in these fakes truncates this one question's answer to one row.
_TRUNCATED = "cq_sales_top5_syn_sales"
# A question the generative lane serves on full-tree (not a pack question).
_AI_PROBE = "cq_chemicals_list"

Route = Callable[[dict[str, Any], dict[str, str]], tuple[int, Any]]


class _Server:
    """One loopback HTTP server. Routes are (METHOD, path) -> handler(body, headers)."""

    def __init__(self, routes: dict[tuple[str, str], Route]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _serve(self, method: str) -> None:
                path = self.path.split("?", 1)[0]
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size) if size else b""
                body = json.loads(raw) if raw else {}
                server.calls.append((method, path))
                handler = server.routes.get((method, path))
                status, out = (404, {"detail": "not found"}) if handler is None else handler(
                    body, dict(self.headers)
                )
                data = json.dumps(out, default=str).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                self._serve("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def posts(self, path: str) -> int:
        return sum(1 for method, seen in self.calls if method == "POST" and seen == path)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _gold_by_question() -> dict[str, tuple[str, str]]:
    """Question text -> (id, certified SQL) for every non-trap case with gold."""
    pack = grade52._load_yaml(grade52.PACK_PATH)
    oracles = grade52._load_yaml(grade52.ORACLE_PATH)["oracles"]
    out: dict[str, tuple[str, str]] = {}
    for question in pack["questions"]:
        qid = str(question["id"])
        oracle = oracles.get(qid) if isinstance(oracles.get(qid), dict) else None
        if grade52._is_trap(question, oracle):
            continue
        sql = grade52._certified_sql(oracle)
        if sql:
            out[str(question["question"]).strip()] = (qid, _as_model_writes(sql))
    return out


def _as_model_writes(sql: str) -> str:
    """A model writes the current date, never the reserved ``$as_of`` bind."""
    return sql.replace("$as_of", "CAST(CURRENT_DATE AS VARCHAR)")


def _model_sql(qid: str, gold: str) -> str:
    """What the fake model writes: gold, except one question cut to its first row."""
    if qid == _TRUNCATED:
        return f"SELECT * FROM ({gold}) AS answer LIMIT 1"
    return gold


def _cortex(
    warehouse: Path,
    *,
    armed: bool = True,
    model: Callable[[str], str | None] | None = None,
    insights: bool = True,
) -> _Server:
    """A Cortex on the contract and Insights wire: health, key posture, generate,
    ontology, submit (executes on its own demo warehouse), ledger, contract ask."""
    from dms_executor.demo_warehouse import connect_file

    seq = itertools.count(1)

    def keys(_body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        reason = "" if armed else "OpenVault FreeRoute unarmed: vault sealed"
        return 200, {"ok": True, "armed": armed, "reason": reason, "spendable_hops": 2}

    def generate(body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        sql = model(str(body.get("question") or "").strip()) if model else None
        base = {
            "ok": True,
            "phase": "generate",
            "status": "ABSTAIN",
            "values": [],
            "served_provider": "fake-provider",
            "served_model": "fake-model",
            "served_local": False,
            "model_called": True,
        }
        if not sql:
            return 200, {**base, "generative": {"ok": False, "climb": {"final": "ABSTAIN"}}}
        return 200, {
            **base,
            "plan_source": "ontology_plan",
            "sql_used": sql,
            "generative": {"ok": True, "valid": True, "sql": sql, "climb": {"final": "COMPLETE"}},
        }

    def submit(body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        plan = body.get("plan") or {}
        if plan.get("kind") == "session_bind":
            return 200, {"ok": True, "status": "bound", "run_id": f"run-live-bind-{next(seq)}"}
        sql = str((body.get("body") or {}).get("sql") or "")
        con = connect_file(warehouse)
        try:
            cur = con.execute(sql)
            cols = [str(item[0]) for item in (cur.description or [])]
            rows = [
                {col: _cell(val) for col, val in zip(cols, row, strict=True)}
                for row in cur.fetchall()
            ]
        finally:
            con.close()
        run_id = f"run-live-{next(seq)}"
        return 200, {"ok": True, "status": "ok", "run_id": run_id, "output": {"rows": rows}}

    def ledger(body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        n = next(seq)
        return 200, {
            "id": f"led-live-{n}",
            "seq": n,
            "entry_hash": f"hash-live-{n}",
            "prev_hash": f"hash-live-{n - 1}",
            "actor": "dms",
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": str(body.get("event_type") or "ask"),
            "payload": body.get("payload") or {},
        }

    def ask(_body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        return 200, {
            "answer": "Not found in the database.",
            "audit_id": f"aud-live-{next(seq)}",
            "route": "abstain",
            "provenance": {"badge": "abstain", "layer": "contract"},
            "rows": [],
        }

    routes: dict[tuple[str, str], Route] = {
        ("GET", "/health"): lambda _b, _h: (200, {"status": "ok"}),
        ("GET", "/v1/insights/keys"): keys,
        ("POST", "/v1/contract/submit"): submit,
        ("POST", "/v1/contract/ledger/append"): ledger,
        ("POST", "/v1/contract/ask"): ask,
        ("POST", "/v1/contract/jwks/refresh"): lambda _b, _h: (200, {"kids": ["kid-live"]}),
        ("GET", "/v1/insights/ontology"): lambda _b, _h: (
            200,
            {"ok": True, "phase": "ontology", "ontology": {"ok": True, "metrics": []}},
        ),
    }
    if insights:
        routes[("POST", "/v1/insights")] = generate
    return _Server(routes)


def _openvault(*, mint_ok: bool = True) -> _Server:
    """POST /keys/intermediate hands out an Ed25519 seed for the service Bearer."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )

    def intermediate(_body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        if not mint_ok or headers.get("Authorization") != f"Bearer {_SVC_TOKEN}":
            return 401, {"detail": "unauthorized"}
        return 200, {
            "kid": "kid-live",
            "private_key": base64.urlsafe_b64encode(seed).decode().rstrip("="),
            "not_after": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }

    return _Server(
        {
            ("GET", "/health"): lambda _b, _h: (200, {"ok": True}),
            ("POST", "/keys/intermediate"): intermediate,
        }
    )


_GOLD: dict[str, tuple[str, str]] = {}


def _gold_model(question: str) -> str | None:
    hit = _GOLD.get(question)
    return _model_sql(*hit) if hit else None


def _gold_sql(qid: str) -> str:
    """The certified SQL for one id, as the fake model writes it (by id: mirrors share text)."""
    oracle = grade52._load_yaml(grade52.ORACLE_PATH)["oracles"][qid]
    sql = grade52._certified_sql(oracle)
    assert sql, f"no gold for {qid}"
    return _as_model_writes(sql)


@pytest.fixture(autouse=True)
def _armed_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start from no armed settings. Restore everything an armed run touches.

    ``create_app`` reads the cached ``get_settings``; clearing it on both
    sides keeps a fake server's URL out of later tests.
    """
    grade52._bootstrap()
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    if not _GOLD:
        _GOLD.update(_gold_by_question())
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner"))
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    for name in (
        "CORTEX_BASE_URL",
        "CORTEX_API_KEY",
        "CORTEX_SHA",
        "CORTEX_URL",
        "OPENVAULT_URL",
        "DMS_OV_SERVICE_TOKEN",
        "DMS_OV_SERVICE_TOKEN_FILE",
        "DMS_ASK_CLARIFY",
        "DMS_CLOOP_B",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    get_settings.cache_clear()


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Callable[..., tuple[_Server, _Server]]]:
    """Start a fake Cortex and OpenVault; every server is shut down after the test."""
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    servers: list[_Server] = []

    def start(**cortex_kwargs: Any) -> tuple[_Server, _Server]:
        mint_ok = cortex_kwargs.pop("mint_ok", True)
        warehouse = ensure_demo_warehouse(tmp_path / f"cortex{len(servers)}.duckdb")
        cortex = _cortex(warehouse, **cortex_kwargs)
        vault = _openvault(mint_ok=mint_ok)
        servers.extend([cortex, vault])
        return cortex, vault

    yield start
    for server in servers:
        server.close()


def _arm(monkeypatch: pytest.MonkeyPatch, cortex: _Server, vault: _Server) -> None:
    monkeypatch.setenv("CORTEX_BASE_URL", cortex.url)
    monkeypatch.setenv("CORTEX_API_KEY", _KEY)
    monkeypatch.setenv("CORTEX_SHA", _CORTEX_SHA)
    monkeypatch.setenv("OPENVAULT_URL", vault.url)
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", _SVC_TOKEN)


def _no_score(out: str) -> None:
    assert "correct=" not in out
    assert "header mode=armed" not in out


def test_armed_refuses_when_not_armed(
    stack: Callable[..., tuple[_Server, _Server]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No keys: refused by name before any network call, and nothing is scored or written."""
    cortex, vault = stack(model=_gold_model)
    with pytest.raises(SystemExit, match="REFUSED cortex_base_url_unset"):
        grade52.main(["--armed", "--layout", "full-tree"])
    monkeypatch.setenv("CORTEX_BASE_URL", cortex.url)
    with pytest.raises(SystemExit, match="REFUSED insights_bearer_missing"):
        grade52.main(["--armed", "--layout", "full-tree"])
    monkeypatch.setenv("CORTEX_API_KEY", "dms-demo-viewer-key")
    with pytest.raises(SystemExit, match="REFUSED insights_bearer_missing"):
        grade52.main(["--armed", "--layout", "full-tree"])
    monkeypatch.setenv("CORTEX_API_KEY", _KEY)
    with pytest.raises(SystemExit, match="REFUSED cortex_sha_unset"):
        grade52.main(["--armed", "--layout", "full-tree"])
    monkeypatch.delenv("CORTEX_API_KEY")
    out_path = tmp_path / "armed.jsonl"
    with pytest.raises(SystemExit, match="REFUSED insights_bearer_missing"):
        capture_layout.main(["--layout", "full-tree", "--armed", "--out", str(out_path)])
    assert not out_path.exists()
    assert cortex.calls == [] and vault.calls == []
    _no_score(capsys.readouterr().out)


def test_armed_refuses_unreachable_cortex_unarmed_freeroute_and_failed_mint(
    stack: Callable[..., tuple[_Server, _Server]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    cortex, vault = stack(model=_gold_model)
    _arm(monkeypatch, cortex, vault)
    monkeypatch.setenv("CORTEX_BASE_URL", f"http://127.0.0.1:{port}")
    with pytest.raises(SystemExit, match="REFUSED cortex_unreachable") as down:
        grade52.main(["--armed", "--layout", "full-tree"])

    unarmed, unarmed_vault = stack(model=_gold_model, armed=False)
    _arm(monkeypatch, unarmed, unarmed_vault)
    with pytest.raises(SystemExit, match="REFUSED freeroute_unarmed: .*vault sealed") as off:
        grade52.main(["--armed", "--layout", "full-tree"])
    assert unarmed.calls == [("GET", "/health"), ("GET", "/v1/insights/keys")]
    assert unarmed_vault.calls == []

    armed, locked_vault = stack(model=_gold_model, mint_ok=False)
    _arm(monkeypatch, armed, locked_vault)
    with pytest.raises(SystemExit, match="REFUSED openvault_mint_failed") as mint:
        grade52.main(["--armed", "--layout", "full-tree"])
    assert armed.posts("/v1/insights") == 0
    out = capsys.readouterr().out
    _no_score(out)
    said = out + "".join(str(exc.value) for exc in (down, off, mint))
    assert _KEY not in said and _SVC_TOKEN not in said


def _relabelled(tmp_path: Path, mode: str) -> Path:
    """The stub capture of record, with its header relabelled and served records added."""
    lines = _COPY_ONLY.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header.update({"mode": mode, "cortex_sha": _CORTEX_SHA})
    body = []
    for line in lines[1:]:
        if not line.strip():
            continue
        record = json.loads(line)
        record["served"] = {"model_served": True, "insights_fail": None, "model_sql": None}
        body.append(json.dumps(record))
    path = tmp_path / f"{mode}.jsonl"
    path.write_text(json.dumps(header) + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    return path


def test_placeholder_answer_in_an_armed_capture_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stub envelopes labelled armed are refused, named, and never scored.

    The same envelopes labelled stub still grade to the stub's own numbers.
    """
    with pytest.raises(
        SystemExit,
        match="FAIL cq_spend_by_country: placeholder: audit_id is a recorded stub id",
    ):
        grade52.grade_envelopes_path(_relabelled(tmp_path, "armed"))
    _no_score(capsys.readouterr().out)
    report = grade52.grade_envelopes_path(_relabelled(tmp_path, "stub"))
    assert report["mode"] == "served"
    assert (report["correct"], report["wrong"], report["abstain"]) == (16, 0, 27)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == (
        "header mode=stub product_sha=57d85c529aa363825aaa566f12b8822fd76a4215 cortex_sha=none"
    )
    assert "split ai=0 rule=16 unattributed=0 abstain=36 n=52" in out


def test_placeholder_answer_from_a_live_cortex_fails_the_armed_run(
    stack: Callable[..., tuple[_Server, _Server]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Cortex whose model answers with the recorded stub SQL stops the run by name."""
    stub_sql = json.loads(grade52.STUB_RESPONSES_PATH.read_text(encoding="utf-8"))["by_id"][
        "cq_sku_count"
    ]["ask"]["sql_used"]
    cortex, vault = stack(model=lambda _question: stub_sql)
    _arm(monkeypatch, cortex, vault)
    out_path = tmp_path / "armed.jsonl"
    with pytest.raises(
        SystemExit, match=r"FAIL cq_\w+: placeholder: (sql_used|model SQL) is the recorded stub SQL"
    ):
        capture_layout.main(["--layout", "full-tree", "--armed", "--out", str(out_path)])
    assert cortex.posts("/v1/insights") >= 1
    assert not out_path.exists()
    _no_score(capsys.readouterr().out)


def test_a_run_that_never_reaches_a_model_is_not_armed(
    stack: Callable[..., tuple[_Server, _Server]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cortex is up and says armed, but Insights generate is not there: no AI lane ran."""
    cortex, vault = stack(insights=False)
    _arm(monkeypatch, cortex, vault)
    with pytest.raises(SystemExit, match="FAIL ai_lane_not_reached"):
        grade52.main(["--armed", "--layout", "full-tree"])
    # Generate was asked and answered 404 every time: no model served anything.
    assert cortex.posts("/v1/insights") >= 1
    assert cortex.posts("/v1/contract/submit") >= 1
    _no_score(capsys.readouterr().out)


def test_armed_run_counts_the_ai_route_on_a_fake_http_cortex(
    stack: Callable[..., tuple[_Server, _Server]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real-shaped generated answers over HTTP are served as ai, graded on rows and text.

    The fake model writes gold SQL, except for one question it cuts to one
    row. That one is WRONG on the ai route, so the bound moves and the gate
    fails: the AI lane can fail this grade.
    """
    cortex, vault = stack(model=_gold_model)
    _arm(monkeypatch, cortex, vault)
    assert grade52.main(["--armed", "--layout", "full-tree", "--gate"]) == 1
    out = capsys.readouterr().out
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    lines = out.splitlines()
    header = next(line for line in lines if line.startswith("header "))
    assert header == f"header mode=armed product_sha={head} cortex_sha={_CORTEX_SHA}"
    assert lines.index(header) < next(i for i, line in enumerate(lines) if "52: correct=" in line)
    assert "gate FAIL wrong=1 refusal_wrong=0" in out
    assert cortex.posts("/v1/insights") >= 1
    assert cortex.posts("/v1/contract/submit") >= 1

    runner = tmp_path / "runner" / "grade52"
    artifact = json.loads((runner / "grade52.json").read_text(encoding="utf-8"))
    assert artifact["mode"] == "armed" and artifact["run"] == "armed"
    assert artifact["dms_sha"] == head and artifact["cortex_sha"] == _CORTEX_SHA
    split = artifact["split"]
    assert sum(split.values()) == 52 and split["ai"] >= 2
    assert artifact["risk"]["ai"]["served"] >= 2
    assert artifact["risk"]["ai"]["wrong"] == 1
    assert artifact["risk"]["ai"]["wrong_ub95"] is not None
    assert f"ai served={artifact['risk']['ai']['served']} wrong=1" in out
    assert f"split ai={split['ai']} rule={split['rule']}" in out
    cases = {case["id"]: case for case in artifact["cases"]}
    assert cases[_AI_PROBE]["bucket"] == "CORRECT"
    assert cases[_AI_PROBE]["serve_path"] == "ai"
    assert cases[_TRUNCATED]["bucket"] == "WRONG"
    assert cases[_TRUNCATED]["serve_path"] == "ai"
    assert cases[_TRUNCATED]["reason"].startswith("rowcount 1 vs")

    # The capture: per question route, model SQL, rows and the answer text.
    capture = (runner / "grade52.armed.jsonl").read_text(encoding="utf-8").splitlines()
    meta = json.loads(capture[0])
    assert meta["mode"] == "armed" and meta["cortex_sha"] == _CORTEX_SHA
    assert meta["dms_sha"] == head and meta["layout"] == "full-tree"
    records = {rec["id"]: rec for rec in map(json.loads, capture[1:])}
    assert len(records) == 52
    probe = records[_AI_PROBE]
    env, served = probe["env"], probe["served"]
    assert served["route"] == "ai"
    assert served["model_sql"] == _gold_sql(_AI_PROBE)
    folder = tmp_path / "gold"
    folder.mkdir()
    con, as_of, _path = grade52._open_warehouse(folder)
    try:
        gold = grade52._execute(con, _gold_sql(_AI_PROBE), as_of)
    finally:
        con.close()
    assert gold, "probe gold must have rows"
    assert env["rows"] == [{key: _cell(val) for key, val in row.items()} for row in gold]
    assert served["row_count"] == len(gold)
    text = str(env["text"])
    assert text == served["text"]
    assert text.startswith(f"Found {len(gold)} row(s).")
    for row in gold:
        for value in row.values():
            assert str(_cell(value)) in text
    assert env["abstained"] is False and env["audit_id"].startswith("led-live-")
    truncated = records[_TRUNCATED]
    assert len(truncated["env"]["rows"]) == 1
    assert truncated["served"]["route"] == "ai"

    # The written capture grades the same through --envelopes, still armed.
    capsys.readouterr()
    regraded = grade52.grade_envelopes_path(runner / "grade52.armed.jsonl")
    assert regraded["mode"] == "armed" and regraded["cortex_sha"] == _CORTEX_SHA
    assert regraded["wrong"] == 1
    assert capsys.readouterr().out.splitlines()[0] == header



def test_cortex_model_sql_stamp_is_the_ai_route() -> None:
    """Cortex stamps plan_source=ontology_plan only when a model wrote the SQL.

    Beside DMS's generate_sql origin that is AI, not a compile. The ranking
    compile (origin ontology_ranking) stays compile even with a model name.
    """
    model = {
        "badge": "L2_VALIDATED",
        "plan_origin": "generate_sql",
        "plan_source": "ontology_plan",
        "served_model": "fake-model",
    }
    assert grade52.serve_path(model) == "ai"
    assert grade52.stamp_contradiction(model) is False
    assert grade52.served_route(model) == "ai"
    assert grade52.serve_path({**model, "plan_origin": "ontology_ranking"}) == "compile"
    assert grade52.serve_path({**model, "served_model": ""}) == "unattributed"
    assert grade52.serve_path({k: v for k, v in model.items() if k != "plan_origin"}) == "compile"
    assert grade52.served_route({**model, "badge": "ABSTAIN", "abstained": True}) == "abstain"
