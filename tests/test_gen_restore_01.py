"""GEN-RESTORE-01 (dms#276): Insights-only compute seam, fail closed, WRONG=0.

Does not stamp COMPLETE. Does not invent a live #231 score. Platform owns
the Studio re-prove. Timeout bound is INSIGHTS_ASK_TIMEOUT_SECONDS (60s default,
``DMS_INSIGHTS_ASK_TIMEOUT_SECONDS`` override); see test_insights_timeout_fallback.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cortex_client import CortexClient
from cortex_client.compute import (
    COMPUTE_PATH,
    INSIGHTS_ASK_TIMEOUT_SECONDS,
    INSIGHTS_FAIL_EMPTY,
    INSIGHTS_FAIL_REFUSED,
    INSIGHTS_FAIL_TIMEOUT,
    INSIGHTS_FAIL_UNARMED,
    INSIGHTS_FAIL_UNAUTHORIZED,
    INSIGHTS_PATH,
    PLAN_ORIGIN_GENERATE_SQL,
    PLAN_ORIGIN_ONTOLOGY_RANKING,
    compute_insights,
    compute_query,
    insights_fail_payload,
)
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import QueryResult
from dms_executor import Executor, _chart_from_rows
from dms_executor.envelope import assert_envelope_valid, chart_from_rows
from dms_executor.generative_ask import (
    SETUP_FIELD_KEYS,
    load_verified_ontology,
    maybe_generative_ask,
)
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import Ontology

_PREDICT_Q = "Predict how much revenue we will make"
_REVENUE_2099_Q = "What was revenue in 2099?"
_NOT_COLD_Q = "Which locations are not cold storage?"
_GENERATE_SQL = "SELECT sku, SUM(amount) AS revenue FROM sales GROUP BY sku"
# Distinctive Cortex#269 values. Must not match any DMS default or guess.
_SETUP_PRESENT = {
    "served_provider": "ov_free_llama_unique",
    "served_model": "not-a-default-model",
    "served_local": False,
    "learn_enabled": True,
    "learn_source": "freeroute-store",
    "route_store_id": "rs_276_test",
}
_SETUP_NULL = {key: None for key in SETUP_FIELD_KEYS}
_GUESSES = ("unknown", "openai", "local", "", 0, "0")


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("lot", "lots", ["lot_id"])
    o.add_object("region", "regions", ["region"])
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(category) AS category FROM lots GROUP BY sku)",
        ["sku"],
    )
    o.add_link("sale_of_lot", "sale", ["sku"], "lot", ["sku"])
    o.add_link("sale_of_product", "sale", ["sku"], "product", ["sku"])
    o.add_link("sale_in_region", "sale", ["region"], "region", ["region"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    o.add_measure("sku_count", "product", "COUNT(*)")
    return o


def _seed(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE lots (lot_id VARCHAR, sku VARCHAR, category VARCHAR, qty DOUBLE)"
        )
        con.execute(
            "INSERT INTO lots VALUES "
            "('L1','SKU-1','ALPHA',10),('L2','SKU-1','ALPHA',20),"
            "('L3','SKU-1','ALPHA',30),('L4','SKU-2','BETA',40)"
        )
        con.execute(
            "CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE)"
        )
        con.execute(
            "INSERT INTO sales VALUES ('T1','SKU-1','North',100),('T2','SKU-2','South',50)"
        )
        con.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
        con.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    finally:
        con.close()


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "restore.duckdb"
    _seed(path)
    return path


@pytest.fixture()
def onto(warehouse: Path) -> Ontology:
    loaded = load_verified_ontology(warehouse, _ontology())
    assert loaded is not None
    return loaded


def _submit_rows(rows: list[dict[str, Any]]) -> Any:
    def _submit(_sql: str) -> Any:
        return SimpleNamespace(
            ok=True,
            status="ok",
            run_id="run_restore",
            output={"rows": list(rows)},
        )

    return _submit


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_restore", hash="hash_restore_not_entry")


def _grantable() -> set[str]:
    return {"sales", "lots", "regions"}


def _ask(
    question: str,
    *,
    warehouse: Path,
    onto: Ontology,
    compute: Any,
    rows: list[dict[str, Any]] | None = None,
    bind_on_miss: bool = False,
) -> dict[str, Any] | None:
    return maybe_generative_ask(
        question,
        warehouse=warehouse,
        grantable=_grantable(),
        compute=compute,
        submit=_submit_rows(rows or [{"product_category": "ALPHA", "revenue": 100.0}]),
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=bind_on_miss,
    )


def _notes(env: dict[str, Any] | None) -> str:
    if not env:
        return ""
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    return notes + " " + str(env.get("text") or "")


def _assert_named_abstain(env: dict[str, Any] | None, reason: str) -> None:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["values"] == []
    assert env["rows"] == []
    blob = _notes(env)
    assert reason in blob, blob
    assert "compute_fallback:bind_plan" not in blob


class _FakeHttp:
    """Records Insights vs /dms/query posts. One response map per URL suffix."""

    def __init__(
        self,
        *,
        posts: list[dict[str, Any]],
        timeouts: list[Any],
        generate: dict[str, Any] | None = None,
        ontology: dict[str, Any] | None = None,
        status: int = 200,
        timeout: bool = False,
        generate_status: int | None = None,
    ) -> None:
        self.posts = posts
        self.timeouts = timeouts
        self.generate = generate if generate is not None else {}
        self.ontology = ontology
        self.status = status
        self.timeout = timeout
        self.generate_status = generate_status if generate_status is not None else status

    def __call__(self, *a: Any, timeout: Any = None, **k: Any) -> _FakeHttp:
        self.timeouts.append(timeout)
        return self

    def __enter__(self) -> _FakeHttp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def _resp(self, payload: dict[str, Any], status: int) -> Any:
        class _Resp:
            status_code = status

            def json(self) -> dict[str, Any]:
                return payload

        return _Resp()

    def post(
        self, url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        self.posts.append({"url": url, "json": json or {}, "headers": headers})
        if self.timeout:
            raise httpx.TimeoutException("insights timed out")
        if str(url).endswith(COMPUTE_PATH):
            return self._resp({"answer": "dms-query-must-not-run"}, 200)
        return self._resp(self.generate, self.generate_status)

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.posts.append({"url": url, "json": params or {}, "headers": headers})
        if self.timeout:
            raise httpx.TimeoutException("insights timed out")
        body = self.ontology if self.ontology is not None else {}
        return self._resp(body, 200)


def test_insights_timeout_bound_default_clears_a_generate() -> None:
    # 8s was shorter than a measured Cortex generate (13-53s): 13/52 asks
    # died as insights_timeout on the live prove.
    assert INSIGHTS_ASK_TIMEOUT_SECONDS == 60.0


def test_compute_insights_calls_insights_never_dms_query() -> None:
    posts: list[dict[str, Any]] = []
    timeouts: list[Any] = []
    fake = _FakeHttp(
        posts=posts,
        timeouts=timeouts,
        generate={"phase": "generate", "generative": {"ok": False, "sql": None}},
        ontology={"phase": "ontology", "ontology": {"metrics": []}},
    )
    with patch("cortex_client.compute.httpx.Client", fake):
        out = compute_insights("http://127.0.0.1:8010", question="how many skus?")
    urls = [str(p["url"]) for p in posts]
    assert any(u.endswith(INSIGHTS_PATH) for u in urls), urls
    assert all(COMPUTE_PATH not in u for u in urls), urls
    assert timeouts
    assert timeouts[0] == INSIGHTS_ASK_TIMEOUT_SECONDS
    gen = next(p["json"] for p in posts if str(p["url"]).endswith(INSIGHTS_PATH))
    assert gen["generate"] is True
    assert gen["ask"] is False
    assert "LIVE_KEY" not in str(gen)
    assert "api_key" not in gen
    assert ":5000" not in str(gen)
    assert "sk-" not in str(gen)
    headers = posts[0]["headers"] or {}
    assert "Authorization" not in headers
    assert out is not None
    assert out.get("insights_fail") == INSIGHTS_FAIL_EMPTY


def test_compute_query_still_posts_dms_query() -> None:
    """CONTRACT-FAKE-01: leftover /dms/query stays on compute_query default."""
    posts: list[dict[str, Any]] = []
    fake = _FakeHttp(
        posts=posts,
        timeouts=[],
        generate={"phase": "generate"},
        ontology={"ontology": {"metrics": []}},
    )
    with patch("cortex_client.compute.httpx.Client", fake):
        compute_query("http://127.0.0.1:8010", question="hello")
    urls = [str(p["url"]) for p in posts]
    assert any(u.endswith(COMPUTE_PATH) for u in urls), urls


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            insights_fail_payload(
                INSIGHTS_FAIL_UNARMED,
                {
                    "ok": False,
                    "status": "REFUSE",
                    "phase": "generate",
                    "generative": {"ok": False, "climb": {"final": "UNARMED"}},
                },
            ),
            INSIGHTS_FAIL_UNARMED,
        ),
        (
            insights_fail_payload(
                INSIGHTS_FAIL_REFUSED,
                {"ok": False, "status": "REFUSE", "phase": "generate"},
            ),
            INSIGHTS_FAIL_REFUSED,
        ),
        (
            insights_fail_payload(
                INSIGHTS_FAIL_UNAUTHORIZED,
                {"ok": False, "status": "REFUSE", "_insights_http_status": 401},
            ),
            INSIGHTS_FAIL_UNAUTHORIZED,
        ),
        (
            insights_fail_payload(INSIGHTS_FAIL_TIMEOUT),
            INSIGHTS_FAIL_TIMEOUT,
        ),
        (
            insights_fail_payload(INSIGHTS_FAIL_EMPTY, {"phase": "generate"}),
            INSIGHTS_FAIL_EMPTY,
        ),
    ],
    ids=["unarmed", "refused", "unauthorized", "timeout", "empty"],
)
def test_named_insights_fail_abstains_and_never_binds(
    warehouse: Path,
    onto: Ontology,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    reason: str,
) -> None:
    binds: list[str] = []

    def _trap(question: str, *_a: Any, **_k: Any) -> dict[str, Any] | None:
        binds.append(question)
        raise AssertionError(f"bind_plan called for {question!r}")

    monkeypatch.setattr("dms_executor.generative_ask.bind_plan", _trap)
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: payload,
        bind_on_miss=True,
    )
    _assert_named_abstain(env, reason)
    assert binds == []
    assert env is not None
    assert env.get("plan_source") != "bind_plan"


def test_http_401_is_unauthorized_never_dms_query() -> None:
    posts: list[dict[str, Any]] = []
    fake = _FakeHttp(
        posts=posts,
        timeouts=[],
        generate={
            "ok": False,
            "status": "REFUSE",
            "refused": "needs its own OpenVault ov_ key",
        },
        ontology={"ontology": {"metrics": []}},
        generate_status=401,
    )
    with patch("cortex_client.compute.httpx.Client", fake):
        out = compute_insights("http://127.0.0.1:8010", question="how many skus?")
    assert out is not None
    assert out.get("insights_fail") == INSIGHTS_FAIL_UNAUTHORIZED
    assert all(COMPUTE_PATH not in str(p["url"]) for p in posts)


def test_http_timeout_is_insights_timeout_never_dms_query() -> None:
    posts: list[dict[str, Any]] = []
    timeouts: list[Any] = []
    fake = _FakeHttp(posts=posts, timeouts=timeouts, timeout=True)
    with patch("cortex_client.compute.httpx.Client", fake):
        out = compute_insights("http://127.0.0.1:8010", question="how many skus?")
    assert out is not None
    assert out.get("insights_fail") == INSIGHTS_FAIL_TIMEOUT
    assert timeouts == [INSIGHTS_ASK_TIMEOUT_SECONDS]
    assert all(COMPUTE_PATH not in str(p["url"]) for p in posts)


def test_generate_sql_stamps_plan_origin(
    warehouse: Path, onto: Ontology, monkeypatch: pytest.MonkeyPatch
) -> None:
    binds: list[str] = []
    monkeypatch.setattr(
        "dms_executor.generative_ask.bind_plan",
        lambda q, *_a, **_k: binds.append(q),
    )
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "query_sql": _GENERATE_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
        },
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env.get("plan_source") == "ontology_plan"
    assert env.get("plan_origin") == PLAN_ORIGIN_GENERATE_SQL
    assert binds == []


def test_ranking_stamps_plan_origin_ontology_ranking(
    warehouse: Path, onto: Ontology, monkeypatch: pytest.MonkeyPatch
) -> None:
    binds: list[str] = []
    monkeypatch.setattr(
        "dms_executor.generative_ask.bind_plan",
        lambda q, *_a, **_k: binds.append(q),
    )
    env = _ask(
        "How many SKUs do we have in inventory?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "ok": False,
            "status": "REFUSE",
            "phase": "generate",
            "generative": {"ok": False, "sql": None, "climb": {"final": "UNARMED"}},
            "ontology": {
                "ok": True,
                "metrics": [{"id": "sku_count", "importance": {"rank": 1}}],
            },
            "values": [],
        },
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert env.get("plan_origin") == PLAN_ORIGIN_ONTOLOGY_RANKING
    assert binds == []
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))


def test_chart_builder_is_the_contract_path_builder() -> None:
    assert _chart_from_rows is chart_from_rows


def test_answered_envelope_attaches_chart_from_fitting_rows(
    warehouse: Path, onto: Ontology
) -> None:
    rows = [{"product_category": "ALPHA", "revenue": 100.0}]
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "query_sql": _GENERATE_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
        },
        rows=rows,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("chart") == chart_from_rows(rows)
    # DMS-VIZ-01: one row x one measure (+ a label) -> big number over the cell.
    assert env["chart"]["kind"] == "bignum"
    assert env["chart"]["y"] == "revenue"
    assert env["chart"]["value"] == 100.0
    assert_envelope_valid(env)


def test_answered_envelope_has_table_chart_when_rows_do_not_fit(
    warehouse: Path, onto: Ontology
) -> None:
    rows = [{"sku": "SKU-1"}]
    env = _ask(
        "List SKUs",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "query_sql": "SELECT sku FROM lots",
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
        },
        rows=rows,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    # DMS-VIZ-01: no measure -> ``table`` (the rows table is the view), no spec.
    assert env.get("chart") == {"kind": "table", "title": "Result"}
    assert chart_from_rows(rows) == {"kind": "table", "title": "Result"}
    assert_envelope_valid(env)


def test_abstain_envelope_has_no_chart(
    warehouse: Path, onto: Ontology
) -> None:
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: insights_fail_payload(INSIGHTS_FAIL_EMPTY),
        bind_on_miss=True,
    )
    _assert_named_abstain(env, INSIGHTS_FAIL_EMPTY)
    assert env is not None
    assert env.get("chart") is None


def test_setup_fields_copied_verbatim(
    warehouse: Path, onto: Ontology
) -> None:
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "query_sql": _GENERATE_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
            **_SETUP_PRESENT,
        },
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    for key, want in _SETUP_PRESENT.items():
        assert key in env
        assert env[key] == want
        assert env[key] is want or type(env[key]) is type(want)
    assert_envelope_valid(env)


def test_setup_fields_null_stay_null(
    warehouse: Path, onto: Ontology
) -> None:
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "query_sql": _GENERATE_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
            **_SETUP_NULL,
        },
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    for key in SETUP_FIELD_KEYS:
        assert key in env
        assert env[key] is None
    assert_envelope_valid(env)


def test_setup_fields_missing_are_not_inferred(
    warehouse: Path, onto: Ontology
) -> None:
    env = _ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: {
            "query_sql": _GENERATE_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
        },
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    for key in SETUP_FIELD_KEYS:
        assert key not in env
        assert env.get(key) not in _GUESSES
    assert_envelope_valid(env)


def test_wrong_pins_pre_gate_before_seam(
    warehouse: Path, onto: Ontology, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Predict / 2099 still abstain before Insights. Binder never runs."""
    computes: list[str] = []
    binds: list[str] = []
    monkeypatch.setattr(
        "dms_executor.generative_ask.bind_plan",
        lambda q, *_a, **_k: binds.append(q),
    )

    def _compute(ctx: dict[str, Any]) -> dict[str, Any]:
        computes.append("hit")
        return {
            "query_sql": _GENERATE_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": PLAN_ORIGIN_GENERATE_SQL,
        }

    for question in (_PREDICT_Q, _REVENUE_2099_Q, "Predict revenue for 2099"):
        env = _ask(question, warehouse=warehouse, onto=onto, compute=_compute)
        assert env is not None
        assert env["badge"] == "ABSTAIN"
        assert env["abstained"] is True
        assert env["values"] == []
        text = str(env.get("text") or "")
        assert "1234567" not in text.replace(",", "")
    assert computes == []
    assert binds == []


def test_not_cold_pin_is_not_confident_from_binder(
    warehouse: Path, onto: Ontology, monkeypatch: pytest.MonkeyPatch
) -> None:
    binds: list[str] = []

    def _trap(question: str, *_a: Any, **_k: Any) -> dict[str, Any] | None:
        binds.append(question)
        raise AssertionError(f"bind_plan called for {question!r}")

    monkeypatch.setattr("dms_executor.generative_ask.bind_plan", _trap)
    env = _ask(
        _NOT_COLD_Q,
        warehouse=warehouse,
        onto=onto,
        compute=lambda _c: insights_fail_payload(INSIGHTS_FAIL_EMPTY),
        bind_on_miss=True,
    )
    _assert_named_abstain(env, INSIGHTS_FAIL_EMPTY)
    assert binds == []


@dataclass
class _RestoreCortex:
    asks: list[Any] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    computes: list[dict[str, Any]] = field(default_factory=list)
    insights: list[dict[str, Any]] = field(default_factory=list)
    insights_payload: dict[str, Any] | None = None

    def compute_query(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        self.computes.append({"question": question, **kwargs})
        raise AssertionError("ask lane called compute_query /dms/query")

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        self.insights.append({"question": question, **kwargs})
        return self.insights_payload

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        return QueryResult(ok=True, status="ok", run_id="run_restore", output={"rows": []})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_restore", hash="hash_restore_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Cortex fallback.",
            badge="certified",
            sql_used="SELECT 1 AS cortex_marker",
            rows=[{"cortex_marker": 1}],
            audit_id="aud_restore_cortex",
            route="sql",
        )


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()
    from cortex_contract.execution import Manifest as M

    def _mint(acl: SessionAcl) -> M:
        return M(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-25T00:00:00+00:00",
            expires_at="2026-09-25T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def test_live_ask_uses_compute_insights_never_compute_query(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    binds: list[str] = []
    monkeypatch.setattr(
        "dms_executor.generative_ask.bind_plan",
        lambda q, *_a, **_k: binds.append(q),
    )
    cortex = _RestoreCortex(insights_payload=insights_fail_payload(INSIGHTS_FAIL_UNARMED))
    exe = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask(
        "How many florbs did wibble sell last week?",
        session_id="ses_restore_gen",
        ask_path="generative",
    )
    assert cortex.computes == []
    assert cortex.insights
    assert binds == []
    _assert_named_abstain(env, INSIGHTS_FAIL_UNARMED)
    assert cortex.asks == []


def test_cortex_client_compute_insights_uses_insights_bound_timeout() -> None:
    posts: list[dict[str, Any]] = []
    timeouts: list[Any] = []
    fake = _FakeHttp(
        posts=posts,
        timeouts=timeouts,
        generate={"phase": "generate"},
        ontology={"ontology": {"metrics": []}},
    )
    with patch("cortex_client.compute.httpx.Client", fake):
        client = CortexClient("http://127.0.0.1:8010", timeout=120.0)
        client.compute_insights("how many skus?")
    assert timeouts == [INSIGHTS_ASK_TIMEOUT_SECONDS]
    assert all(COMPUTE_PATH not in str(p["url"]) for p in posts)
