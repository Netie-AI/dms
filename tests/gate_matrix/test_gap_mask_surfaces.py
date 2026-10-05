"""Gate-matrix gaps, theme "mask-surfaces": routes that return rows without the masker.

Every answer path through ``Executor.live_ask`` ends in ``mask_unknown_keys`` (and the
envelope builder masks text/rows/values), so a personal value in an ask result reaches
the customer as a ``DMSMASK_*`` token. These tests show the surfaces that hand Cortex
or warehouse rows back WITHOUT that step, and the drillthrough route that also turns a
Cortex failure into an empty success. Each test fails today on its own final assert and
passes once the surface gets the gate.

Synthetic data only: ``example.com`` addresses and the NRIC shape used by
``tests/test_pii_01.py``. No network (a ``httpx.MockTransport`` stands in for Cortex).
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import httpx
from _harness import (
    FINANCE,
    Harness,
    control,
    csv_bytes,
    gap,
    require_envelope,
)

EMAIL = "siti.synth@example.com"
NRIC = "900101-14-5678"
SUPPLIER_EMAIL = "ops.synth@example.com"
RAW_PII = (EMAIL, NRIC, SUPPLIER_EMAIL)

#: A non-PII value that must survive masking (the "no over-masking" half of each control).
CITY = "Ipoh"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _leaked(payload: Any) -> list[str]:
    """Raw synthetic personal values present anywhere in a JSON-able payload."""
    blob = json.dumps(payload, default=str)
    return [raw for raw in RAW_PII if raw in blob]


def _people_csv() -> bytes:
    return csv_bytes(
        ["name", "email", "ic_no", "city"],
        [["Siti Synth", EMAIL, NRIC, CITY]],
    )


def _table_rows(h: Harness, sql: str) -> list[dict[str, Any]]:
    """Read rows straight from the tmp warehouse (the fixture's own contents)."""
    con = duckdb.connect(str(h.warehouse), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _ask_masks_these_rows(
    h: Harness,
    rows: list[dict[str, Any]],
    *,
    session_id: str,
    grounded_tables: list[str] | None = None,
    token: str = "dt_ms_ctl",
) -> dict[str, Any]:
    """CONTROL on the already-gated path: POST /v1/chat/ask hands Cortex's rows back masked.

    The rows are the fixture's own table contents, returned by the fake Cortex.ask. The
    assertion is on the customer's envelope: no raw personal value anywhere, DMSMASK
    tokens where they were, and the non-PII cell untouched.
    """
    from cortex_client.models import AskResponse

    h.cortex.ask_response = AskResponse(
        answer="Result rows: " + "; ".join(str(v) for r in rows for v in r.values()),
        abstained=False,
        badge="certified",
        sql_used="SELECT * FROM fixture",
        rows=rows,
        audit_id="aud_ms_ctl",
        route="sql",
        drillthrough_token=token,
    )
    status, env = h.ask(
        "Show me the rows",
        space_id=FINANCE,
        session_id=session_id,
        grounded_tables=grounded_tables,
    )
    require_envelope(status, env)
    control(
        env["abstained"] is False and env["rows"], f"ask did not return rows: {env.get('text')}"
    )
    control(not _leaked(env), f"the ASK path leaked raw values: {_leaked(env)}")
    cells = [str(v) for r in env["rows"] for v in r.values()]
    control(any("DMSMASK_" in c for c in cells), f"ask rows carry no mask token: {env['rows']}")
    return env


def _mock_cortex(handler: Any) -> Any:
    """A real ``CortexClient`` whose HTTP goes to ``handler`` (no network).

    An empty ``base_url`` keeps ``compliance_gate`` on its no-HTTP ``gate_unavailable``
    branch; the generated client resolves the contract path against the mock's base URL.
    """
    from cortex_client import CortexClient

    client = CortexClient("")
    client._client.set_httpx_client(
        httpx.Client(base_url="http://cortex.mock", transport=httpx.MockTransport(handler))
    )
    return client


def _contract_drill_body(rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """A contract-shaped DrillthroughResponse (generated model: answer_id/row_count/
    session_id/sql_used required; rows, approximate, total_count optional)."""
    body: dict[str, Any] = {
        "answer_id": "ans_ms_drill",
        "row_count": len(rows),
        "session_id": "ses_ms_drill",
        "sql_used": "SELECT * FROM bronze.people",
        "rows": rows,
    }
    body.update(extra)
    return body


# --------------------------------------------------------------------------
# preview-and-drill-rows-unmasked
# --------------------------------------------------------------------------


@gap(
    "library:bronze-preview,library:warehouse-preview,mcp:preview",
    "G5",
    "new",
    "preview-and-drill-rows-unmasked",
)
def test_library_and_mcp_previews_return_personal_values_raw(harness_factory) -> None:  # type: ignore[no-untyped-def]
    """The Library previews and the MCP preview twin hand back raw cell values.

    Control: the same two tables' rows, asked through ``POST /v1/chat/ask`` in the same
    Space, come back masked. Then each preview surface is called and must be masked
    too; today each returns the stored email and NRIC verbatim.
    """
    h: Harness = harness_factory(env={"DMS_MCP": "1"})
    receipt = h.upload("people.csv", _people_csv(), space_id=FINANCE)
    table = receipt.table
    con = duckdb.connect(str(h.warehouse))
    try:
        con.execute(
            "UPDATE suppliers SET supplier_name = ? WHERE supplier_id = 'SUP-01'",
            [SUPPLIER_EMAIL],
        )
    finally:
        con.close()

    # CONTROL 1: the ask path masks both fixtures' contents.
    _ask_masks_these_rows(
        h,
        _table_rows(h, f"SELECT name, email, ic_no, city FROM {table}"),
        session_id="ses_ms_bronze",
        grounded_tables=[table],
    )
    _ask_masks_these_rows(
        h,
        _table_rows(h, "SELECT supplier_name, country FROM suppliers WHERE supplier_id = 'SUP-01'"),
        session_id="ses_ms_wh",
        token="dt_ms_ctl_wh",
    )

    # CONTROL 2: each preview surface is reached (HTTP 200, rows), so the assertion
    # below cannot pass or fail on a 403/404. The non-PII cell is not masked.
    bronze = h.client.get(f"/v1/library/bronze/{table}/preview", params={"space_id": FINANCE})
    warehouse = h.client.get(
        "/v1/library/warehouse/suppliers/preview", params={"space_id": FINANCE}
    )
    mcp = h.client.post(
        "/v1/mcp/call",
        json={"name": "preview", "arguments": {"table": "suppliers", "space_id": FINANCE}},
    )
    control(
        bronze.status_code == 200, f"bronze preview HTTP {bronze.status_code}: {bronze.text[:200]}"
    )
    control(warehouse.status_code == 200, f"warehouse preview HTTP {warehouse.status_code}")
    control(mcp.status_code == 200, f"mcp preview HTTP {mcp.status_code}: {mcp.text[:200]}")
    bronze_body, warehouse_body, mcp_body = bronze.json(), warehouse.json(), mcp.json()
    control(
        any(r.get("city") == CITY for r in bronze_body["rows"]),
        f"bronze preview did not return the uploaded row: {bronze_body['rows']}",
    )
    control(
        any(r.get("country") == "MY" for r in warehouse_body["rows"]),
        "warehouse preview did not return seed rows",
    )
    control(mcp_body.get("ok") is True and mcp_body["result"]["rows"], f"mcp body: {mcp_body}")

    # THE GAP: a correct gate returns DMSMASK tokens, never the stored value.
    leaked = {
        "GET /v1/library/bronze/{table}/preview": _leaked(bronze_body),
        "GET /v1/library/warehouse/suppliers/preview": _leaked(warehouse_body),
        "POST /v1/mcp/call preview": _leaked(mcp_body),
    }
    leaked = {route: raw for route, raw in leaked.items() if raw}
    assert not leaked, f"preview surfaces returned raw personal values: {leaked}"


@gap(
    "chat:drillthrough,export:csv-client",
    "G5",
    "new",
    "preview-and-drill-rows-unmasked",
)
def test_drillthrough_rows_come_back_unmasked(harness) -> None:  # type: ignore[no-untyped-def]
    """``POST /v1/chat/drillthrough`` returns contributing rows verbatim.

    The UI's "Download CSV" writes exactly these rows (AnswerMessage.tsx:187-203), so
    the server response is the whole leg a pytest can reach. Control: the ask that minted
    the token returned the same table's rows masked.
    """
    receipt = harness.upload("people.csv", _people_csv(), space_id=FINANCE)
    contributing = _table_rows(harness, f"SELECT name, email, ic_no, city FROM {receipt.table}")

    # CONTROL: the ask envelope for this table is masked, and carries the drill token.
    env = _ask_masks_these_rows(
        harness,
        contributing,
        session_id="ses_ms_drill",
        grounded_tables=[receipt.table],
        token="dt_ms_drill",
    )
    token = env["drillthrough_token"]
    control(token == "dt_ms_drill", f"envelope lost the drillthrough token: {token!r}")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_contract_drill_body(contributing))

    harness.app.state.cortex = _mock_cortex(handler)
    resp = harness.client.post("/v1/chat/drillthrough", json={"token": token})

    # CONTROL: the drill reached Cortex with the envelope's token and answered 200 with
    # the contributing rows (the non-PII cell is present).
    control(resp.status_code == 200, f"drillthrough HTTP {resp.status_code}: {resp.text[:200]}")
    control(len(seen) == 1, f"expected one Cortex drillthrough call, saw {len(seen)}")
    control(json.loads(seen[0].content)["token"] == token, "token was not forwarded to Cortex")
    body = resp.json()
    control(
        any(r.get("city") == CITY for r in body["rows"]),
        f"drillthrough returned no contributing rows: {body}",
    )

    # THE GAP: the drill rows must be masked like the ask rows.
    assert not _leaked(body), f"drillthrough returned raw personal values: {_leaked(body)}"


# --------------------------------------------------------------------------
# insights-routes-return-payload-unmasked
# --------------------------------------------------------------------------


@gap(
    "insights:post-ask,insights:post-generate,insights:get-ontology-ranking",
    "G5",
    "new",
    "insights-routes-return-payload-unmasked",
)
def test_insights_routes_return_cortex_payload_unmasked(harness) -> None:  # type: ignore[no-untyped-def]
    """``/v1/insights`` returns whatever Cortex says, personal values included.

    The fake Cortex's ``insights_ask`` / ``insights_ontology`` stand in for the engine
    (the payload is ours, not a claim about what Cortex returns). The assertion is on
    the DMS route's own pass-through. Control: a numeric, non-PII payload comes back
    unchanged (no over-masking) and the ask path masks the same personal values.
    """
    state: dict[str, dict[str, Any]] = {}
    clean = {
        "ok": True,
        "status": "CERTIFIED",
        "values": [{"sku_count": 12}],
        "answer": "There are 12 skus.",
    }
    dirty = {
        "ok": True,
        "status": "CERTIFIED",
        "values": [{"customer": "Siti Synth", "email": EMAIL, "ic_no": NRIC}],
        "answer": f"Siti Synth can be reached at {EMAIL}.",
    }
    state["ask"] = clean
    state["ontology"] = {"phase": "ontology", "intent": "x", "values": [{"email": EMAIL}]}
    harness.cortex.insights_ask = lambda **_kw: dict(state["ask"])  # type: ignore[attr-defined]
    harness.cortex.insights_ontology = lambda _q: dict(state["ontology"])  # type: ignore[attr-defined]

    # CONTROL 1: a clean payload passes through unchanged, so the route is reached and
    # does not over-mask.
    r = harness.client.post("/v1/insights", json={"intent": "how many skus", "space_id": FINANCE})
    control(r.status_code == 200, f"insights HTTP {r.status_code}: {r.text[:200]}")
    control(
        r.json().get("values") == [{"sku_count": 12}]
        and r.json().get("answer") == "There are 12 skus.",
        f"clean payload was altered: {r.json()}",
    )

    # CONTROL 2: the same personal values through the ask path are masked.
    _ask_masks_these_rows(
        harness,
        [{"customer": "Siti Synth", "email": EMAIL, "ic_no": NRIC}],
        session_id="ses_ms_ins",
    )

    # The three surfaces, each reached (HTTP 200, a dict body).
    state["ask"] = dirty
    harness.monkeypatch.setattr(
        "dms_api.routes.insights.openvault_reachable", lambda *_a, **_k: True
    )
    post_ask = harness.client.post(
        "/v1/insights", json={"intent": "list customer emails", "space_id": FINANCE}
    )
    post_generate = harness.client.post(
        "/v1/insights",
        json={"intent": "list customer emails", "generate": True, "space_id": FINANCE},
    )
    get_ontology = harness.client.get("/v1/insights/ontology", params={"q": "list customer emails"})
    for name, resp in (
        ("post-ask", post_ask),
        ("post-generate", post_generate),
        ("ontology", get_ontology),
    ):
        control(
            resp.status_code == 200, f"insights {name} HTTP {resp.status_code}: {resp.text[:200]}"
        )
        control(isinstance(resp.json(), dict), f"insights {name} body: {resp.text[:200]}")

    # THE GAP: a correct gate masks the payload before it leaves DMS.
    leaked = {
        "POST /v1/insights (ask)": _leaked(post_ask.json()),
        "POST /v1/insights (generate)": _leaked(post_generate.json()),
        "GET /v1/insights/ontology": _leaked(get_ontology.json()),
    }
    leaked = {route: raw for route, raw in leaked.items() if raw}
    assert not leaked, f"insights routes returned raw personal values: {leaked}"


# --------------------------------------------------------------------------
# drillthrough-failure-and-truncation-not-named
# --------------------------------------------------------------------------


def _drill_client(harness: Harness, mode: dict[str, str], seen: list[httpx.Request]) -> None:
    """Point the app's Cortex at a mock whose answer is chosen by ``mode['m']``."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        kind = mode["m"]
        if kind == "transport_error":
            raise httpx.ConnectError("cortex unreachable", request=request)
        if kind == "422":
            return httpx.Response(
                422,
                json={
                    "detail": [
                        {"loc": ["body", "token"], "msg": "token expired", "type": "value_error"}
                    ]
                },
            )
        if kind == "capped":
            return httpx.Response(
                200,
                json=_contract_drill_body(
                    [
                        {"sku": "A", "amount": 1},
                        {"sku": "B", "amount": 2},
                        {"sku": "C", "amount": 3},
                    ],
                    approximate=True,
                    total_count=84201,
                ),
            )
        return httpx.Response(200, json=_contract_drill_body([{"sku": "A", "amount": 1}]))

    harness.app.state.cortex = _mock_cortex(handler)


@gap(
    "chat:drillthrough,export:csv-client",
    "G8",
    "new",
    "drillthrough-failure-and-truncation-not-named",
)
def test_drillthrough_cortex_422_is_an_empty_success_not_a_named_failure(harness) -> None:  # type: ignore[no-untyped-def]
    """A Cortex 422 on drillthrough reads as a successful drill with no rows.

    Controls on the same route: a 200 returns its rows, and a transport failure is
    already a named 502 ``drillthrough_failed``. The gap is the 422.
    """
    mode = {"m": "rows"}
    seen: list[httpx.Request] = []
    _drill_client(harness, mode, seen)

    # CONTROL a: a 200 returns the rows.
    ok = harness.client.post("/v1/chat/drillthrough", json={"token": "dt_ms_422"})
    control(
        ok.status_code == 200 and ok.json()["rows"] == [{"sku": "A", "amount": 1}], ok.text[:200]
    )

    # CONTROL b: a transport failure is a named failure today.
    mode["m"] = "transport_error"
    err = harness.client.post("/v1/chat/drillthrough", json={"token": "dt_ms_422"})
    control(err.status_code == 502, f"transport failure: HTTP {err.status_code}")
    control(err.json()["detail"]["code"] == "drillthrough_failed", err.text[:200])

    # THE REQUEST: Cortex refuses the token with a 422.
    mode["m"] = "422"
    before = len(seen)
    resp = harness.client.post("/v1/chat/drillthrough", json={"token": "dt_ms_422"})
    control(len(seen) == before + 1, "the 422 request never reached the Cortex mock")
    body = resp.json()

    # THE GAP: a failed drill must be a named failure (4xx/5xx with a code), not a 200.
    detail = body.get("detail") if isinstance(body, dict) else None
    named = resp.status_code >= 400 and (
        (isinstance(detail, dict) and bool(detail.get("code")))
        or (isinstance(detail, str) and bool(detail))
    )
    assert named, f"Cortex 422 reached the caller as HTTP {resp.status_code} {resp.text[:200]}"


@gap(
    "chat:drillthrough,export:csv-client",
    "G8",
    "new",
    "drillthrough-failure-and-truncation-not-named",
)
def test_drillthrough_drops_the_cap_and_approximate_flags(harness) -> None:  # type: ignore[no-untyped-def]
    """A capped / approximate drill is returned as if it were the complete detail.

    The contract response carries ``approximate`` and ``total_count`` (generated
    DrillthroughResponse; DMS_TECHNICAL_ARCHITECTURE.md:238,242: "showing 5,000 of
    84,201 contributing rows"). DMS's own response model has no field for them, so the
    caller cannot tell the rows are a page. Control: the rows themselves are returned.
    """
    mode = {"m": "capped"}
    seen: list[httpx.Request] = []
    _drill_client(harness, mode, seen)

    resp = harness.client.post("/v1/chat/drillthrough", json={"token": "dt_ms_cap"})

    # CONTROL: the drill reached Cortex, returned 200 and the page of rows.
    control(len(seen) == 1, f"expected one Cortex drillthrough call, saw {len(seen)}")
    control(resp.status_code == 200, f"drillthrough HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    control([r["sku"] for r in body["rows"]] == ["A", "B", "C"], f"rows: {body}")

    # THE GAP: the response must say it is partial/approximate (the contract's own
    # names, or an equivalent `truncated` flag).
    states_partial = (
        body.get("approximate") is True
        or body.get("truncated") is True
        or body.get("total_count") == 84201
    )
    assert states_partial, f"capped, approximate drill returned as complete: {resp.text[:300]}"
