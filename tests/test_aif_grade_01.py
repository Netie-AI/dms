"""AIF-GRADE-01. grade52's armed AI-first profile: Yes pass, copy detection, gate.

``--armed --ai-first`` turns on the AI-first flag set for one run, answers
Yes to each reconfirm offer through the product's confirm route, grades the
Yes envelope against the original question's gold, and keeps an AI answer
that served an example it was shown out of ``ai_share`` (decision D7).

These tests run the real armed loop: this checkout's ``POST /v1/chat/ask``
and ``POST /v1/chat/ask/confirm`` with the product ``CortexClient`` and
OpenVault minter, against a Cortex and an OpenVault that are loopback HTTP
fakes on ephemeral ports. No live-stack port is touched. Every assertion on
a grade is checked against the envelope rows and rendered text the run
captured, not against SQL alone.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import re
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

import grade52  # noqa: E402

_KEY = "ov_aif_grade_test_key_0123456789"
_SVC_TOKEN = "aif-grade-test-service-token"
_CORTEX_SHA = "a1f0" + "0" * 36
_COPY_ONLY = ROOT / "tests" / "fixtures" / "ask_guide" / "copy_only_57d85c52.jsonl"
_FEATURES = {
    "engine_version": "2.5.0",
    "profile": "core",
    "extras": {"agentic": False, "rag": False, "full": False},
}

# The cases this file pins. Each id is a pack question; the fake model below
# behaves for it as named.
_AI_CORRECT = "cq_chemicals_list"  # model writes gold, no example shown
_COPY = "cq_sku_count"  # model writes the example SQL it was shown
_YES_GOLD = "cq_capacity_utilisation"  # no SQL first; the Yes question gets gold
_YES_WRONG = "cq_stock_value_by_category"  # no SQL first; the Yes question gets one row
_EMPTY = "cq_cold_storage"  # no SQL first; the closest question comes back empty
_TRAP = "trap_last_month"  # a declared trap the product refuses, with an offer
_S_GOLD = "Show the capacity utilisation of each warehouse location"
_S_WRONG = "Show the stock value of each category"

_PROFILE_FLAGS = (
    *grade52.AI_FIRST_FLAGS,
    *grade52.AI_FIRST_HELD_FLAGS,
    "DMS_ASK_CLARIFY",
    "DMS_VQ_RAG",
    "DMS_LANE_ONTOLOGY_RANKED",
    "DMS_INSIGHTS_CALL_CAP",
    "DMS_ONTO_FROM_DATA",
)

Route = Callable[[dict[str, Any], dict[str, str]], tuple[int, Any]]


class _Server:
    """One loopback HTTP server on an ephemeral port. Routes are (METHOD, path)."""

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
                status, out = (
                    (404, {"detail": "not found"})
                    if handler is None
                    else handler(body, dict(self.headers))
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

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _as_model_writes(sql: str) -> str:
    """A model writes the current date, never the reserved ``$as_of`` bind."""
    return sql.replace("$as_of", "CAST(CURRENT_DATE AS VARCHAR)")


class _Pack:
    """Pack questions by id, and (space id, question text) -> id for the fake."""

    def __init__(self) -> None:
        pack = grade52._load_yaml(grade52.PACK_PATH)
        self.oracles = grade52._load_yaml(grade52.ORACLE_PATH)["oracles"]
        self.spaces = dict(pack["spaces"])
        self.by_id = {str(q["id"]): q for q in pack["questions"]}
        self.key_of = {
            qid: (self.spaces[str(q["space"])], str(q["question"]).strip())
            for qid, q in self.by_id.items()
        }
        self.gold_sql: dict[tuple[str, str], str] = {}
        for qid, question in self.by_id.items():
            oracle = self.oracles.get(qid)
            oracle = oracle if isinstance(oracle, dict) else None
            if grade52._is_trap(question, oracle):
                continue
            sql = grade52._certified_sql(oracle)
            if sql:
                self.gold_sql[self.key_of[qid]] = _as_model_writes(sql)

    def sql(self, qid: str) -> str:
        return self.gold_sql[self.key_of[qid]]


_EXAMPLE = re.compile(
    r"example_sql:\n(.*?)(?=\n\nexample_question: |\n\nprevious_sql:|\n*\Z)", re.S
)


def _asked(prompt: str) -> str:
    """The user question inside a generate prompt (reading 1 or 2, examples cut)."""
    text = prompt
    if "\nQuestion:\n" in text:
        text = text.rsplit("\nQuestion:\n", 1)[1]
    for cut in ("\n\nverified_examples:", "\nExamples:"):
        text = text.split(cut, 1)[0]
    return text.strip()


class _Model:
    """What the fake Cortex's model writes, per pack question and per Yes question."""

    def __init__(self, pack: _Pack) -> None:
        self.pack = pack
        self.last = ""
        self.asked: list[str] = []
        self.no_sql = {pack.key_of[qid] for qid in (_YES_GOLD, _YES_WRONG, _EMPTY)}
        # ORDER BY: both CLOOP_B readings must return the same one row to serve it.
        self.yes_sql = {
            _S_GOLD: pack.sql(_YES_GOLD),
            _S_WRONG: f"SELECT * FROM ({pack.sql(_YES_WRONG)}) AS answer ORDER BY 1 LIMIT 1",
        }
        self.suggest = {
            pack.key_of[_YES_GOLD][1]: _S_GOLD,
            pack.key_of[_YES_WRONG][1]: _S_WRONG,
            pack.key_of[_EMPTY][1]: "",
        }

    def sql(self, prompt: str, space_id: str) -> str | None:
        question = _asked(prompt)
        self.last = question
        self.asked.append(question)
        if question in self.yes_sql:
            return self.yes_sql[question]
        key = (space_id, question)
        if key in self.no_sql:
            return None
        if key == self.pack.key_of[_COPY]:
            shown = _EXAMPLE.findall(prompt.split("\n\nverified_examples:\n", 1)[-1])
            if "\n\nverified_examples:\n" in prompt and shown:
                return shown[0].strip()
        return self.pack.gold_sql.get(key)

    def closest(self) -> str:
        """The suggestion for the question asked last. Empty only where pinned empty."""
        return self.suggest.get(self.last, f"Show the closest answer to: {self.last}")


def _cortex(warehouse: Path, model: _Model) -> _Server:
    """Cortex on the contract and Insights wire: health, flags, keys, generate,
    a closest-question completion, submit on its own demo warehouse, ledger."""
    from dms_executor.demo_warehouse import connect_file

    seq = itertools.count(1)

    def generate(body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        prompt = str(body.get("question") or "")
        if prompt.startswith("Write the closest question"):
            return 200, {"ok": True, "suggested_question": model.closest()}
        sql = model.sql(prompt, str(body.get("space_id") or ""))
        base = {"ok": True, "phase": "generate", "status": "ABSTAIN", "values": []}
        if not sql:
            return 200, {**base, "generative": {"ok": False, "climb": {"final": "ABSTAIN"}}}
        return 200, {
            **base,
            "served_provider": "fake-provider",
            "served_model": "fake-model",
            "served_local": False,
            "model_called": True,
            "plan_source": "ontology_plan",
            "sql_used": sql,
            "generative": {"ok": True, "valid": True, "sql": sql, "climb": {"final": "COMPLETE"}},
        }

    def submit(body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        plan = body.get("plan") or {}
        if plan.get("kind") == "session_bind":
            return 200, {"ok": True, "status": "bound", "run_id": f"run-aif-bind-{next(seq)}"}
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
        return 200, {
            "ok": True,
            "status": "ok",
            "run_id": f"run-aif-{next(seq)}",
            "output": {"rows": rows},
        }

    def ledger(body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        n = next(seq)
        return 200, {
            "id": f"led-aif-{n}",
            "seq": n,
            "entry_hash": f"hash-aif-{n}",
            "prev_hash": f"hash-aif-{n - 1}",
            "actor": "dms",
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": str(body.get("event_type") or "ask"),
            "payload": body.get("payload") or {},
        }

    def contract_ask(_body: dict[str, Any], _headers: dict[str, str]) -> tuple[int, Any]:
        return 200, {
            "answer": "Not found in the database.",
            "audit_id": f"aud-aif-{next(seq)}",
            "route": "abstain",
            "provenance": {"badge": "abstain", "layer": "contract"},
            "rows": [],
        }

    return _Server(
        {
            ("GET", "/health"): lambda _b, _h: (200, {"status": "ok"}),
            ("GET", "/health/features"): lambda _b, _h: (200, _FEATURES),
            ("GET", "/v1/insights/keys"): lambda _b, _h: (
                200,
                {"ok": True, "armed": True, "reason": "", "spendable_hops": 2},
            ),
            ("POST", "/v1/insights"): generate,
            ("POST", "/v1/contract/submit"): submit,
            ("POST", "/v1/contract/ledger/append"): ledger,
            ("POST", "/v1/contract/ask"): contract_ask,
            ("POST", "/v1/contract/jwks/refresh"): lambda _b, _h: (200, {"kids": ["kid-aif"]}),
            ("GET", "/v1/insights/ontology"): lambda _b, _h: (
                200,
                {"ok": True, "phase": "ontology", "ontology": {"ok": True, "metrics": []}},
            ),
        }
    )


def _openvault(status: dict[str, Any] | None = None) -> _Server:
    """Mints the service intermediate key. FreeRoute status names no stronger route."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )

    def intermediate(_body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        if headers.get("Authorization") != f"Bearer {_SVC_TOKEN}":
            return 401, {"detail": "unauthorized"}
        return 200, {
            "kid": "kid-aif",
            "private_key": base64.urlsafe_b64encode(seed).decode().rstrip("="),
            "not_after": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }

    return _Server(
        {
            ("GET", "/health"): lambda _b, _h: (200, {"ok": True}),
            ("POST", "/keys/intermediate"): intermediate,
            ("GET", "/api/freeroute/status"): lambda _b, _h: (
                200,
                status if status is not None else {"ok": True, "sealed": False},
            ),
        }
    )


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No armed settings, no profile flags; both restored after the test."""
    grade52._bootstrap()
    from dms_api.settings import get_settings
    from dms_executor.reconfirm import _reset_reconfirm

    get_settings.cache_clear()
    _reset_reconfirm()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner"))
    monkeypatch.setenv("DMS_PIPELINE_FAILURE_JSONL", str(tmp_path / "failures.jsonl"))
    for name in (
        *_PROFILE_FLAGS,
        "CORTEX_BASE_URL",
        "CORTEX_API_KEY",
        "CORTEX_SHA",
        "CORTEX_URL",
        "OPENVAULT_URL",
        "DMS_OV_SERVICE_TOKEN",
        "DMS_OV_SERVICE_TOKEN_FILE",
    ):
        monkeypatch.setenv(name, "x")  # records the original, so teardown restores it
        monkeypatch.delenv(name)
    yield
    _reset_reconfirm()
    get_settings.cache_clear()


def _arm(monkeypatch: pytest.MonkeyPatch, cortex: _Server, vault: _Server) -> None:
    monkeypatch.setenv("CORTEX_BASE_URL", cortex.url)
    monkeypatch.setenv("CORTEX_API_KEY", _KEY)
    monkeypatch.setenv("CORTEX_SHA", _CORTEX_SHA)
    monkeypatch.setenv("OPENVAULT_URL", vault.url)
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", _SVC_TOKEN)


def _gold_rows(tmp_path: Path, pack: _Pack, qid: str) -> list[dict[str, Any]]:
    folder = tmp_path / f"gold-{qid}"
    folder.mkdir()
    con, as_of, _path = grade52._open_warehouse(folder)
    try:
        sql = grade52._certified_sql(pack.oracles[qid])
        assert sql, f"{qid} has no certified gold"
        rows = grade52._execute(con, sql, as_of)
    finally:
        con.close()
    return [{key: _cell(val) for key, val in row.items()} for row in rows]


def _values(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Row values in order, names ignored: what a customer reads off the answer."""
    return sorted([[str(v) for v in row.values()] for row in rows])


def _text_names(rows: list[dict[str, Any]], text: str) -> None:
    """The rendered answer names every figure in the rows it serves."""
    assert text.startswith(f"Found {len(rows)} row(s).")
    for row in rows:
        for value in row.values():
            assert str(_cell(value)) in text, (value, text)


def _line(out: str, head: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(head))


def test_ai_first_armed_run_yes_pass_copies_traps_and_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two armed AI-first runs, graded, then gated on their worst case.

    Pinned cases, all through real POST /v1/chat/ask (and /ask/confirm) envelopes:
    a first-pass AI answer is CORRECT; a Yes that serves gold is
    CORRECT_AFTER_YES; a Yes that serves one row of a many-row answer is
    WRONG and fails the gate; an empty suggestion is not an offer (ABSTAIN,
    no confirm); a trap with an offer is REFUSAL_OK and nobody says Yes; an
    AI answer that served the example it was shown is ai_example_copy and is
    not in ai_share.
    """
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid

    pack = _Pack()
    model = _Model(pack)
    cortex = _cortex(ensure_demo_warehouse(tmp_path / "cortex.duckdb"), model)
    vault = _openvault()
    try:
        _arm(monkeypatch, cortex, vault)
        code = grade52.main(["--armed", "--ai-first", "--layout", "full-tree", "--gate"])
    finally:
        cortex.close()
        vault.close()
    out = capsys.readouterr().out
    assert code == 1, out

    # The profile's flags were for the run only.
    for name in (*grade52.AI_FIRST_FLAGS, *grade52.AI_FIRST_HELD_FLAGS):
        assert name not in os.environ, name

    runner = tmp_path / "runner" / "grade52"
    capture = (runner / "grade52.armed.run1.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(capture[0])
    records = {rec["id"]: rec for rec in map(json.loads, capture[1:])}
    assert len(records) == 52
    assert (runner / "grade52.armed.run2.jsonl").is_file()

    # Run header: every DMS flag, the Cortex flags it can see, the OpenVault route.
    assert header["mode"] == "armed" and header["profile"] == "ai-first"
    flags = header["dms_flags"]
    assert {key: flags[key] for key in grade52.AI_FIRST_FLAGS} == grade52.AI_FIRST_FLAGS
    assert flags["DMS_GRAIN_AI"] == "unset" and flags["DMS_AO_COMPILE"] == "unset"
    assert flags["DMS_OV_SERVICE_TOKEN"] == "set"
    assert header["cortex_flags"]["engine_version"] == "2.5.0"
    assert header["cortex_flags"]["extras.agentic"] == "false"
    assert header["openvault_stronger_route"] == "unknown"
    assert header["openvault_stronger_route_reason"] == "not_reported"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    lines = out.splitlines()
    worst_head = (
        f"worst of 2 unstable=0 header mode=armed product_sha={head} cortex_sha={_CORTEX_SHA}"
    )
    at = lines.index(worst_head)
    profile = lines[at + 1]
    assert profile.startswith("worst of 2 unstable=0 profile ai-first dms_flags=")
    for name, value in grade52.AI_FIRST_FLAGS.items():
        assert f"{name}={value}" in profile
    assert "DMS_GRAIN_AI=unset" in profile and "DMS_AO_COMPILE=unset" in profile
    assert "cortex_flags=engine_version=2.5.0" in profile.replace("extras.agentic=false,", "")
    assert profile.endswith("openvault_stronger_route=unknown")
    assert _KEY not in out and _SVC_TOKEN not in out
    assert _KEY not in "\n".join(capture) and _SVC_TOKEN not in "\n".join(capture)

    artifact = json.loads((runner / "grade52.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in artifact["cases"]}
    assert artifact["profile"] == "ai-first" and artifact["runs"] == 2

    # 1. First-pass AI answer, no example shown: CORRECT on the ai route.
    rec = records[_AI_CORRECT]
    env = rec["env"]
    assert_envelope_valid(env)
    gold = _gold_rows(tmp_path, pack, _AI_CORRECT)
    assert gold and _values(env["rows"]) == _values(gold)
    _text_names(env["rows"], str(env["text"]))
    assert rec["served"]["route"] == "ai" and "yes" not in rec
    assert cases[_AI_CORRECT]["bucket"] == "CORRECT"
    assert cases[_AI_CORRECT]["serve_path"] == "ai"
    assert cases[_AI_CORRECT]["copy_check"] == "none"
    assert "ai_example_copy" not in cases[_AI_CORRECT]

    # 2. Reconfirm, Yes, gold: CORRECT_AFTER_YES, graded on the original question's gold.
    rec = records[_YES_GOLD]
    assert rec["env"]["abstained"] is True and rec["env"]["rows"] == []
    assert rec["env"]["reconfirm"]["suggested_question"] == _S_GOLD
    yes_env = rec["yes"]["env"]
    assert_envelope_valid(yes_env)
    assert yes_env["abstained"] is False and yes_env["reconfirm_accepted"] is True
    gold = _gold_rows(tmp_path, pack, _YES_GOLD)
    assert len(gold) > 1 and _values(yes_env["rows"]) == _values(gold)
    _text_names(yes_env["rows"], str(yes_env["text"]))
    case = cases[_YES_GOLD]
    assert case["bucket"] == "CORRECT_AFTER_YES" and case["after_yes"] is True
    assert case["yes_outcome"] == "correct"
    assert case["first_pass"]["bucket"] == "RECONFIRM"

    # 3. Reconfirm, Yes, a wrong figure: WRONG, and it fails the gate.
    rec = records[_YES_WRONG]
    assert rec["env"]["reconfirm"]["suggested_question"] == _S_WRONG
    yes_env = rec["yes"]["env"]
    assert_envelope_valid(yes_env)
    gold = _gold_rows(tmp_path, pack, _YES_WRONG)
    assert len(gold) > 1 and len(yes_env["rows"]) == 1
    assert _values(yes_env["rows"])[0] in _values(gold)
    _text_names(yes_env["rows"], str(yes_env["text"]))
    case = cases[_YES_WRONG]
    assert case["bucket"] == "WRONG" and case["yes_outcome"] == "wrong"
    assert case["reason"] == f"after yes: rowcount 1 vs {len(gold)}"

    # 4. An empty suggestion is not an offer: graded as the abstain it is, no Yes.
    rec = records[_EMPTY]
    assert rec["env"]["abstained"] is True
    assert rec["env"]["reconfirm"]["suggested_question"] == ""
    assert "yes" not in rec
    assert cases[_EMPTY]["bucket"] == "ABSTAIN"
    assert cases[_EMPTY]["offer"] == "empty_suggestion"

    # 5. A trap is judged before the reconfirm branch: refused, never a reconfirm.
    rec = records[_TRAP]
    offered = rec["env"]["reconfirm"]["suggested_question"]
    assert rec["env"]["abstained"] is True and offered.strip()
    assert "yes" not in rec
    assert offered not in model.asked  # nobody asked the trap's suggestion
    assert cases[_TRAP]["bucket"] == "REFUSAL_OK" and cases[_TRAP]["offer"] == "trap"
    # A trap is a declared field (trap / refusal_expected), as in the legacy grade.
    traps = grade52.trap_ids(list(pack.by_id.values()))
    assert _TRAP in traps
    for qid in traps:
        assert "yes" not in records[qid], qid
        assert cases[qid]["bucket"] in ("REFUSAL_OK", "REFUSAL_WRONG"), qid

    # 6. An AI answer that served the example it was shown: ai_example_copy, not AI.
    rec = records[_COPY]
    env = rec["env"]
    assert_envelope_valid(env)
    assert env["ai_first"]["examples"] >= 1
    assert grade52.sql_hash(env["sql_used"]) in rec["served"]["example_sql_sha256"]
    gold = _gold_rows(tmp_path, pack, _COPY)
    assert _values(env["rows"]) == _values(gold)
    _text_names(env["rows"], str(env["text"]))
    assert rec["served"]["route"] == "ai"  # the stamps alone would call it AI
    case = cases[_COPY]
    assert case["bucket"] == "CORRECT" and case["ai_example_copy"] is True
    assert case["serve_path"] == "ai_example_copy" and case["served_route"] == "unattributed"

    # Every offer with a suggestion on a non-trap case got its Yes, and only those.
    for qid, record in records.items():
        wants_yes = grade52.yes_offer(record["env"]) is not None and qid not in traps
        assert ("yes" in record) == wants_yes, qid

    # Printed lines agree with the artifact, and the artifact with the envelopes.
    first_correct = sorted(k for k, c in cases.items() if c["bucket"] == "CORRECT")
    after_yes = sorted(k for k, c in cases.items() if c["bucket"] == "CORRECT_AFTER_YES")
    ai = sorted(k for k in first_correct if cases[k]["serve_path"] == "ai")
    assert _AI_CORRECT in ai and _COPY not in ai and _YES_GOLD in after_yes
    assert artifact["correct"] == len(first_correct)
    assert artifact["correct_after_yes"] == len(after_yes)
    assert artifact["ai_share"] == {"ai": len(ai), "correct": len(first_correct)}
    summary = _line(out, "worst of 2 unstable=0 sha=")
    assert (
        f"52: correct={len(first_correct)} correct_after_yes={len(after_yes)} "
        f"wrong={artifact['wrong']} "
    ) in summary
    assert f"metric ai_share={len(ai)}/{len(first_correct)} " in out
    yes = artifact["yes"]
    assert yes["correct_after_yes"] == len(after_yes) and yes["wrong_after_yes"] >= 1
    assert yes["empty_suggestion"] >= 1 and yes["trap_offer"] >= 1
    assert yes["pressed"] == sum("yes" in record for record in records.values())
    assert _line(out, "worst of 2 unstable=0 yes ").endswith(
        " ".join(f"{key}={yes[key]}" for key in grade52._YES_KEYS)
    )
    copies = artifact["copies"]
    assert copies["ai_example_copy"] >= 1
    assert (
        f"copies ai_example_copy={copies['ai_example_copy']} correct={copies['correct']} "
        f"checked={copies['checked']} blind=0 ai_share={len(ai)}/{len(first_correct)} "
    ) in out

    # The gate: worst case of two runs, with its wrong-rate bound.
    bound = artifact["risk"]["all"]["wrong_ub95"]
    assert bound is not None and artifact["risk"]["all"]["wrong"] == artifact["wrong"] >= 1
    assert (
        f"gate FAIL wrong={artifact['wrong']} refusal_wrong={artifact['refusal_wrong']} "
        f"wrong_ub95={bound * 100:.1f}% runs=2 profile=ai-first"
    ) in out

    # Existing modes grade exactly as before: the legacy grade of this capture
    # has no Yes bucket, and an offer on a trap or with an empty suggestion is
    # still a reconfirm there.
    legacy = grade52.grade_envelopes(
        list(records.values()), mode=grade52.MODE_SERVED, warehouse_dir=tmp_path / "legacy"
    )
    by_id = {case["id"]: case for case in legacy["cases"]}
    assert "correct_after_yes" not in legacy and "profile" not in legacy
    for qid in (_YES_GOLD, _YES_WRONG, _EMPTY, _TRAP):
        assert by_id[qid]["bucket"] == "RECONFIRM", qid
    assert by_id[_COPY]["serve_path"] == "ai"

    # The written capture regrades to the same buckets through --envelopes.
    capsys.readouterr()
    regraded = grade52.grade_envelopes_path(runner / "grade52.armed.run1.jsonl")
    run1 = json.loads((runner / "grade52.run1.json").read_text(encoding="utf-8"))
    assert regraded["profile"] == "ai-first"
    assert {c["id"]: c["bucket"] for c in regraded["cases"]} == {
        c["id"]: c["bucket"] for c in run1["cases"]
    }
    assert capsys.readouterr().out.splitlines()[1].startswith("profile ai-first dms_flags=")


def test_example_sql_hashes_read_what_the_product_writes() -> None:
    """Both example formats the ask path appends hash to the same normal form."""
    grade52._bootstrap()
    from cortex_client.compute import question_with_examples
    from dms_executor.verified_queries import examples_text

    rows = [
        {"question": "How many SKUs?", "sql": "SELECT  COUNT(DISTINCT sku) FROM inventory"},
        {"question": "Old", "sql": "SELECT 1 FROM inventory", "revoked": True},
        {"question": "By category", "sql": "select category,\n count(*) from inventory group by 1"},
    ]
    ai_text = question_with_examples("Write one SELECT.\nQuestion:\nHow many SKUs?", rows)
    vq_text = "Write one SELECT.\nQuestion:\nHow many SKUs?\nExamples:\n" + examples_text(
        [row for row in rows if not row.get("revoked")]
    )
    want = sorted(
        {
            grade52.sql_hash("SELECT COUNT(DISTINCT sku) FROM inventory"),
            grade52.sql_hash("SELECT category, COUNT(*) FROM inventory GROUP BY 1"),
        }
    )
    assert grade52.example_sql_hashes(ai_text) == want
    assert grade52.example_sql_hashes(vq_text) == want
    assert grade52.example_sql_hashes("How many SKUs?") == []
    assert grade52.sql_hash("SELECT 1 FROM inventory") not in grade52.example_sql_hashes(ai_text)


def test_the_run_header_never_writes_a_secret_and_never_fails() -> None:
    """Credential names and non-flag values are recorded as set. Probes say unknown."""
    snap = grade52.dms_flags_snapshot(
        {
            "DMS_AI_FIRST": "1",
            "DMS_OV_SERVICE_TOKEN": "tok-value-123",
            "DMS_OV_SERVICE_TOKEN_FILE": "C:/secret/ov_service_token",
            "DMS_TENANT_ID": "tenant_a",
            "DMS_WAREHOUSE_DB": "C:/data/warehouse.duckdb",
            "OTHER": "1",
        }
    )
    assert snap["DMS_AI_FIRST"] == "1" and snap["DMS_TENANT_ID"] == "tenant_a"
    assert snap["DMS_OV_SERVICE_TOKEN"] == "set"
    assert snap["DMS_OV_SERVICE_TOKEN_FILE"] == "set"
    assert snap["DMS_WAREHOUSE_DB"] == "set"
    assert snap["DMS_CLOOP_B"] == "unset" and snap["DMS_GRAIN_AI"] == "unset"
    assert "OTHER" not in snap
    assert "tok-value-123" not in json.dumps(snap)

    import socket

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    down = f"http://127.0.0.1:{port}"
    route, why = grade52.openvault_stronger_route(down)
    assert route == "unknown" and why.startswith("unreachable:")
    cfg = grade52.ArmedConfig(
        cortex_url=down, api_key=_KEY, cortex_sha=_CORTEX_SHA, openvault_url=down
    )
    assert grade52.cortex_flags(cfg)[0] == "unknown"
    stronger = _openvault({"ok": True, "stronger_route": True})
    silent = _openvault()
    try:
        assert grade52.openvault_stronger_route(stronger.url) == (
            "yes",
            "freeroute_status.stronger_route",
        )
        assert grade52.openvault_stronger_route(silent.url) == ("unknown", "not_reported")
    finally:
        stronger.close()
        silent.close()


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["--armed", "--ai-first", "--all-flags"], "does not take --all-flags"),
        (["--armed", "--ai-first", "--gate", "--runs", "1"], "worst case of >= 2 runs"),
        (["--armed", "--runs", "2"], "are for --armed --ai-first"),
        (["--armed", "--ai-first-also", "DMS_GRAIN_AI"], "are for --armed --ai-first"),
        (["--envelopes", str(_COPY_ONLY), "--ai-first"], "a capture names its own profile"),
    ],
)
def test_ai_first_options_are_refused_by_name(argv: list[str], says: str) -> None:
    """Refused before any setting is read or any server is called."""
    with pytest.raises(SystemExit, match=re.escape(says)):
        grade52.main(argv)


def test_held_flags_stay_off_unless_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """DMS_GRAIN_AI and DMS_AO_COMPILE 422 until OpenVault #172: off unless asked."""
    monkeypatch.setenv("DMS_GRAIN_AI", "1")
    with grade52._restored_env(grade52.AI_FIRST_HELD_FLAGS + tuple(grade52.AI_FIRST_FLAGS)):
        grade52.apply_ai_first_flags()
        assert "DMS_GRAIN_AI" not in os.environ and "DMS_AO_COMPILE" not in os.environ
        assert {name: os.environ[name] for name in grade52.AI_FIRST_FLAGS} == (
            grade52.AI_FIRST_FLAGS
        )
        grade52.apply_ai_first_flags(("DMS_AO_COMPILE",))
        assert os.environ["DMS_AO_COMPILE"] == "1" and "DMS_GRAIN_AI" not in os.environ
    assert os.environ["DMS_GRAIN_AI"] == "1" and "DMS_AO_COMPILE" not in os.environ
    with pytest.raises(SystemExit, match="--ai-first-also takes"):
        grade52.apply_ai_first_flags(("DMS_VQ_RAG",))


def test_a_legacy_capture_grades_with_no_profile_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The capture of record still grades to its own numbers, with no AI-first output."""
    report = grade52.grade_envelopes_path(_COPY_ONLY)
    assert (report["correct"], report["wrong"], report["abstain"]) == (16, 0, 27)
    assert "profile" not in report and "correct_after_yes" not in report
    out = capsys.readouterr().out
    assert not any(line.startswith(("profile ", "yes ", "copies ")) for line in out.splitlines())
    assert "correct_after_yes" not in out
    artifact = json.loads(Path(report["artifact"]).read_text(encoding="utf-8"))
    assert not {"profile", "correct_after_yes", "yes", "copies", "ai_share"} & set(artifact)
    assert not any(set(case) & set(grade52._AI_FIRST_CASE_KEYS) for case in artifact["cases"])
