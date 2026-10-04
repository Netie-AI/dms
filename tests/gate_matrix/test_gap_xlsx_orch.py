"""Gate-matrix gaps on the Studio xlsx-orch routes (EPIC-016: crosscheck, extract, golden).

Three gaps, each a strict-xfail test (``@gap``):

* xlsx-orch-coercion-and-false-ok    (G4 typed ingest, G8 named abstain)
* xlsx-orch-cross-space-path-read    (G1 grant / Space check)
* xlsx-orch-unmasked-and-raw-persist (G5 PII mask of the envelope)

How these requests reach the code. The three routes are mounted unconditionally
and are fail-closed on ``compliance_gate`` (the harness Cortex has no base_url,
so the real gate answers ``gate_unavailable`` and ``enforce`` returns 403). Each
test proves that first, then patches ``compliance_gate`` in
``dms_api.routes.studio`` to allow, exactly as ``tests/test_xlsx_orch.py`` does,
so the assertion that fails is about what the route does AFTER the gate. The
allowlist root is the warehouse parent directory, which the harness pins to the
test's ``tmp_path``; every workbook here is written under it. Workbooks are
built with ``dms_core.xlsx_ooxml.write_xlsx_sheets`` (stdlib zip, no openpyxl
save), and every cell value is synthetic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from _harness import FINANCE, WAREHOUSE_OPS, control, gap

CROSSCHECK = "/v1/studio/xlsx-orch/crosscheck"
EXTRACT = "/v1/studio/xlsx-orch/extract"
GOLDEN = "/v1/studio/xlsx-orch/golden"

ROW_CROSSCHECK = "studio:xlsx-orch-crosscheck"
ROW_EXTRACT = "studio:xlsx-orch-extract"
ROW_GOLDEN = "studio:xlsx-orch-golden"

#: A synthetic Malaysian IC-shaped number (valid yymmdd) that the repo masker knows.
IC = "900101-14-5566"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _write_xlsx(path: Path, sheets: list[tuple[str, list[list[Any]]]]) -> Path:
    from dms_core.xlsx_ooxml import write_xlsx_sheets

    write_xlsx_sheets(path, sheets)
    return path


def _candidate_pack(source_sheet: str = "Carriers") -> dict[str, Any]:
    """A pack that passes every structural check in ``pack_issues``."""
    return {
        "ok": True,
        "ask": "OnTime=true average cost + export xlsx + chart for PPT",
        "workbook": {"title": "synthetic.xlsx", "path": "", "source_sheet": source_sheet},
        "steps": [
            {"n": 1, "sheet": "Cover", "intent": "Cover provenance"},
            {"n": 2, "sheet": "OnTime Export", "intent": "FILTER OnTime=TRUE"},
            {"n": 3, "sheet": "Analysis", "intent": "AVERAGEIF OnTime TRUE"},
            {"n": 4, "sheet": "Presentation Chart", "intent": "chart for PPT"},
        ],
        "expected_result_sheets": ["Cover", "OnTime Export", "Analysis", "Presentation Chart"],
        "paste_owner": "pointer",
        "cross_check_owner": "dms",
        "not_doing": ["pointer_paste", "excel_copilot_drive", "mcp_user_excel_primary"],
    }


def _result_workbook(
    path: Path,
    *,
    analysis: list[list[Any]],
    export_rows: int = 2,
) -> Path:
    """A Pointer/Copilot-shaped result workbook: Cover, OnTime Export, Analysis, Chart."""
    export: list[list[Any]] = [["OnTime", "Cost"]] + [["TRUE", 300.27] for _ in range(export_rows)]
    return _write_xlsx(
        path,
        [
            ("Cover", [["source", "synthetic"], ["filter", "OnTime=TRUE"]]),
            ("OnTime Export", export),
            ("Analysis", analysis),
            ("Presentation Chart", [["note", "chart placeholder"]]),
        ],
    )


def _allow_studio_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compliance gate allows. The harness fake has no base_url, so the real
    gate is ``gate_unavailable`` (a fail-closed 403 on these mutation routes)."""
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(allowed=True, reason="test_allow", action=action),
    )


def _post(client: Any, route: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    r = client.post(route, json=body)
    try:
        payload = r.json()
    except ValueError:
        payload = {"_raw_body": r.text}
    return r.status_code, payload if isinstance(payload, dict) else {"_non_object": payload}


# --------------------------------------------------------------------------
# GAP 1: untyped workbook cells, per-request coercion, ok:true with no named reason
# --------------------------------------------------------------------------


@gap(ROW_CROSSCHECK, "G4,G8", "new", "xlsx-orch-coercion-and-false-ok")
def test_crosscheck_text_flags_and_costs_do_not_answer_ok_without_a_named_reason(
    harness, tmp_path, monkeypatch
):
    """OnTime holds 'On Time'/'Late' and cost holds 'RM 300.27': the oracle silently
    finds zero rows, and the route still answers ok:true / reason 'live' with no oracle.

    ``_truthy('On Time')`` is False and ``_as_float('RM 300.27')`` is None, so every row is
    dropped. ``oracle_from_sheets`` does return a named ``no_ontime_rows``, but
    ``crosscheck_pack`` throws it away (``source_oracle: None``).
    """
    client = harness.client
    inputs = tmp_path / "inputs"
    clean = _write_xlsx(
        inputs / "clean.xlsx",
        [
            (
                "Carriers",
                [
                    ["Carrier", "OnTime", "Cost"],
                    ["Acme", "TRUE", 310.0],
                    ["Beta", "FALSE", 200.0],
                    ["Gamma", "yes", 290.0],
                ],
            )
        ],
    )
    no_ontime_col = _write_xlsx(
        inputs / "no_ontime_col.xlsx",
        [("Carriers", [["Carrier", "Cost"], ["Acme", 310.0], ["Beta", 200.0]])],
    )
    text_cells = _write_xlsx(
        inputs / "text_cells.xlsx",
        [
            (
                "Carriers",
                [
                    ["Carrier", "OnTime", "Cost"],
                    ["Acme", "On Time", "RM 300.27"],
                    ["Beta", "Late", "RM 200.00"],
                    ["Gamma", "On Time", "RM 290.00"],
                ],
            )
        ],
    )

    # CONTROL 0: the route is gated. With the harness's real gate it fails closed.
    status, body = _post(
        client, CROSSCHECK, {"pack": _candidate_pack(), "workbook_path": str(clean)}
    )
    control(status == 403 and body.get("detail") == "gate_unavailable", f"{status} {body}")
    _allow_studio_gate(monkeypatch)

    # CONTROL 1: a workbook whose flags and costs the coercion understands gets a real oracle.
    status, body = _post(
        client, CROSSCHECK, {"pack": _candidate_pack(), "workbook_path": str(clean)}
    )
    control(status == 200 and body.get("ok") is True, f"{status} {body}")
    oracle = body.get("source_oracle")
    control(isinstance(oracle, dict) and oracle.get("ok") is True, f"no oracle: {body}")
    control(oracle["ontime_count"] == 2 and oracle["total_count"] == 3, f"oracle {oracle}")
    control(abs(float(oracle["avg_cost"]) - 300.0) < 0.01, f"oracle {oracle}")

    # CONTROL 2: a structural miss (no OnTime header) is already a NAMED refusal.
    status, body = _post(
        client, CROSSCHECK, {"pack": _candidate_pack(), "workbook_path": str(no_ontime_col)}
    )
    control(status == 200 and body.get("ok") is False, f"{status} {body}")
    control(
        body.get("status") == "rejected" and body.get("reason") == "ontime_col_missing", f"{body}"
    )

    # GAP REQUEST: same pack, same route, flags and costs as text.
    status, body = _post(
        client, CROSSCHECK, {"pack": _candidate_pack(), "workbook_path": str(text_cells)}
    )
    control(status == 200 and "ok" in body, f"{status} {body}")

    oracle = body.get("source_oracle")
    refused = body.get("ok") is False
    oracle_names_reason = isinstance(oracle, dict) and bool(oracle.get("reason"))
    top_level_reason_names_it = body.get("reason") not in (None, "", "live")
    shown = {k: v for k, v in body.items() if k != "strengthened_pack"}
    assert refused or oracle_names_reason or top_level_reason_names_it, (
        "crosscheck answered ok:true / reason 'live' with source_oracle null for a workbook "
        f"where every row was dropped by coercion, and named no reason: {json.dumps(shown, default=str)}"
    )


@gap(ROW_GOLDEN, "G4,G8", "new", "xlsx-orch-coercion-and-false-ok")
def test_golden_zero_average_is_not_replaced_by_the_nearest_cell(harness, tmp_path, monkeypatch):
    """A labeled average of 0 (a broken Copilot formula) is falsy, so ``or _nearest(...)``
    goes fishing for any cell within 0.05 of 300.27 and the golden passes.

    The real FRTR counts (184005 / 200000) need a ~184k-row Export sheet. The fishing logic does
    not depend on the constants, so they are patched to 1000 / 2000 to keep the workbook small.
    The real-size run (one-off, while building this test) returned the same ok:true /
    frtr_golden. ``FRTR_AVG`` and the tolerances are the real ones.
    """
    import dms_core.xlsx_orch as core

    monkeypatch.setattr(core, "FRTR_ONTIME", 1000)
    monkeypatch.setattr(core, "FRTR_TOTAL", 2000)
    client = harness.client
    inputs = tmp_path / "inputs"

    def workbook(name: str, average: float) -> Path:
        return _result_workbook(
            inputs / name,
            analysis=[
                ["Average Cost", average],
                ["Chart data point", 300.27],
                ["OnTime Count", 1000],
                ["Total Rows", 2000],
            ],
            export_rows=1000,
        )

    correct = workbook("avg_correct.xlsx", 300.27)
    wrong = workbook("avg_wrong.xlsx", 12.34)
    zero = workbook("avg_zero.xlsx", 0)

    def golden(path: Path) -> tuple[int, dict[str, Any]]:
        return _post(client, GOLDEN, {"path": str(path), "producer": "pointer_copilot"})

    # CONTROL 0: the route is gated.
    status, body = golden(correct)
    control(status == 403 and body.get("detail") == "gate_unavailable", f"{status} {body}")
    _allow_studio_gate(monkeypatch)

    # CONTROL 1: a workbook whose labeled average, count and total are right passes.
    status, body = golden(correct)
    control(
        status == 200 and body.get("ok") is True and body.get("reason") == "frtr_golden", f"{body}"
    )

    # CONTROL 2: a labeled average that is wrong but non-zero is refused, even though the same
    # 300.27 chart cell is sitting in the sheet. The labeled figure wins when it is truthy.
    status, body = golden(wrong)
    control(status == 200 and body.get("ok") is False, f"{status} {body}")
    control(
        body.get("reason") == "golden_miss" and "avg_cost" in str(body.get("error")), f"{body}"
    )
    control(body.get("avg_cost") == 12.34, f"{body}")

    # GAP REQUEST: identical workbook, labeled average 0.
    status, body = golden(zero)
    control(status == 200 and "ok" in body, f"{status} {body}")
    assert body.get("ok") is False, (
        "golden passed a workbook whose labeled average is 0, by reading the unrelated 300.27 "
        f"chart cell instead: {json.dumps(body, default=str)}"
    )


# --------------------------------------------------------------------------
# GAP 2: any Space may name any xlsx under the warehouse parent (every Space's space_docs)
# --------------------------------------------------------------------------

#: A label that exists only in the FINANCE workbook (stored lower-cased by the route).
FINANCE_ONLY_LABEL = "finance only q3 margin"


@gap(f"{ROW_EXTRACT},{ROW_GOLDEN}", "G1", "new", "xlsx-orch-cross-space-path-read")
def test_one_space_cannot_read_or_copy_another_spaces_stored_workbook(
    harness, tmp_path, tmp_path_factory, monkeypatch
):
    """A request addressed to WAREHOUSE_OPS names FINANCE's stored workbook as its input.

    The only check on ``result_path`` / ``path`` is the filesystem allowlist (the warehouse parent
    dir, which holds every Space's ``space_docs``). Nothing ties the file to the ``space_id``.
    """
    client = harness.client
    inputs = tmp_path / "inputs"
    space_docs = tmp_path / "space_docs"

    finance_src = _result_workbook(
        inputs / "finance_result.xlsx",
        analysis=[[FINANCE_ONLY_LABEL.title(), 777.25], ["avg_cost", 300.27]],
    )
    ops_src = _result_workbook(
        inputs / "ops_result.xlsx",
        analysis=[["Ops only pallets", 42], ["avg_cost", 11.5]],
    )
    # A sibling of tmp_path (the allowlist root), so it is outside every allowlisted root.
    outside = _result_workbook(
        tmp_path_factory.mktemp("xlsx_outside") / "outside_root.xlsx", analysis=[["x", 1]]
    )

    def extract(pack_id: str, space_id: str, path: Path | str) -> tuple[int, dict[str, Any]]:
        return _post(
            client,
            EXTRACT,
            {
                "pack_id": pack_id,
                "space_id": space_id,
                "producer": "pointer_copilot",
                "result_path": str(path),
            },
        )

    # CONTROL 0: the route is gated.
    status, body = extract("p0", FINANCE, finance_src)
    control(status == 403 and body.get("detail") == "gate_unavailable", f"{status} {body}")
    _allow_studio_gate(monkeypatch)

    # CONTROL 1: a path outside the allowlist root is already refused.
    status, body = extract("p_out", WAREHOUSE_OPS, outside)
    control(status == 200 and body.get("ok") is False, f"{status} {body}")
    control(body.get("reason") == "path_not_allowlisted", f"{body}")

    # CONTROL 2: FINANCE stores its own workbook, and WAREHOUSE_OPS can use its OWN stored one.
    status, body = extract("p1", FINANCE, finance_src)
    control(status == 200 and body.get("ok") is True and body.get("status") == "stored", f"{body}")
    finance_stored = Path(body["path"])
    control(finance_stored.is_file() and FINANCE in finance_stored.parts, str(finance_stored))
    finance_sha = hashlib.sha256(finance_stored.read_bytes()).hexdigest()

    status, body = extract("po1", WAREHOUSE_OPS, ops_src)
    control(status == 200 and body.get("ok") is True, f"{status} {body}")
    ops_stored = Path(body["path"])
    control(WAREHOUSE_OPS in ops_stored.parts, str(ops_stored))
    status, body = extract("po2", WAREHOUSE_OPS, ops_stored)
    control(status == 200 and body.get("ok") is True, f"own-Space re-extract: {status} {body}")
    control(
        FINANCE_ONLY_LABEL not in json.dumps(body).lower(),
        "WAREHOUSE_OPS's own workbook must not carry FINANCE's label",
    )

    # GAP REQUESTS: WAREHOUSE_OPS asks for FINANCE's stored workbook, on two routes.
    ex_status, ex_body = extract("p9", WAREHOUSE_OPS, finance_stored)
    control(ex_status < 500, f"extract crashed: {ex_status} {ex_body}")
    go_status, go_body = _post(
        client,
        GOLDEN,
        {
            "pack_id": "p9",
            "space_id": WAREHOUSE_OPS,
            "path": str(finance_stored),
            "producer": "pointer_copilot",
        },
    )
    control(go_status < 500, f"golden crashed: {go_status} {go_body}")

    ops_copies = [
        p
        for p in (space_docs / WAREHOUSE_OPS).rglob("*.xlsx")
        if hashlib.sha256(p.read_bytes()).hexdigest() == finance_sha
    ]
    leaks = []
    if ops_copies:
        leaks.append(f"extract copied FINANCE's workbook into {ops_copies[0]}")
    if FINANCE_ONLY_LABEL in json.dumps(ex_body, default=str).lower():
        leaks.append("extract returned FINANCE's Analysis labels to a WAREHOUSE_OPS request")
    if FINANCE_ONLY_LABEL in json.dumps(go_body, default=str).lower():
        leaks.append("golden returned FINANCE's Analysis labels to a WAREHOUSE_OPS request")
    if go_body.get("reason") in ("frtr_golden", "golden_miss"):
        leaks.append(f"golden graded FINANCE's workbook for WAREHOUSE_OPS ({go_body.get('reason')})")
    assert not leaks, (
        f"cross-Space read: extract -> HTTP {ex_status} ok={ex_body.get('ok')!r}; "
        f"golden -> HTTP {go_status} reason={go_body.get('reason')!r}; " + "; ".join(leaks)
    )


# --------------------------------------------------------------------------
# GAP 3: workbook labels, headers and values leave the routes unmasked
# --------------------------------------------------------------------------


@gap(
    f"{ROW_CROSSCHECK},{ROW_EXTRACT},{ROW_GOLDEN}",
    "G5",
    "new",
    "xlsx-orch-unmasked-and-raw-persist",
)
def test_workbook_labels_and_headers_are_masked_in_the_response(harness, tmp_path, monkeypatch):
    """An IC number in an Analysis label (extract, golden miss) or a column header (crosscheck
    ``source_oracle.cost_col``) comes back verbatim.

    Only the response masking is asserted. Extract persisting the posted bytes unmodified is the
    route's stated purpose (a byte-faithful copy), so that half is by design, not tested here.
    The header is 'Spend ...', not 'Cost - ...': ``_COST_COL`` only matches unit cost, a bare
    'cost', spend, amount or baserate, so a 'Cost - ...' header is never picked as the cost column.
    """
    from dms_core.pii import mask_payload

    client = harness.client
    inputs = tmp_path / "inputs"
    result = _result_workbook(
        inputs / "ic_result.xlsx",
        analysis=[[f"IC {IC}", 4521.50], ["avg_cost", 300.27]],
    )
    source = _write_xlsx(
        inputs / "ic_source.xlsx",
        [
            (
                "Carriers",
                [
                    ["Carrier", "OnTime", f"Spend IC {IC}"],
                    ["Acme", "TRUE", 310.0],
                    ["Beta", "TRUE", 290.0],
                ],
            )
        ],
    )
    extract_body = {
        "pack_id": "pm",
        "space_id": FINANCE,
        "producer": "pointer_copilot",
        "result_path": str(result),
    }

    # CONTROL 0: the routes are gated.
    status, body = _post(client, EXTRACT, extract_body)
    control(status == 403 and body.get("detail") == "gate_unavailable", f"{status} {body}")
    _allow_studio_gate(monkeypatch)

    # CONTROL 1: the repo masker recognises this IC, so "not masked" is the route's omission,
    # not an undetectable pattern.
    masked = mask_payload(text=f"IC {IC}")
    control(IC not in masked["text"] and "DMSMASK_" in masked["text"], f"masker: {masked['text']!r}")

    # CONTROL 2: the figures themselves come back correct and numeric on every route.
    ex_status, ex_body = _post(client, EXTRACT, extract_body)
    control(
        ex_status == 200 and ex_body.get("ok") is True and ex_body.get("status") == "stored",
        f"{ex_body}",
    )
    inspected = ex_body.get("inspected") or {}
    control(4521.5 in inspected.get("analysis_numbers", []), f"{inspected}")
    control(300.27 in inspected.get("analysis_numbers", []), f"{inspected}")
    control(inspected.get("export_row_count") == 2, f"{inspected}")

    cc_status, cc_body = _post(
        client, CROSSCHECK, {"pack": _candidate_pack(), "workbook_path": str(source)}
    )
    control(cc_status == 200 and cc_body.get("ok") is True, f"{cc_status} {cc_body}")
    oracle = cc_body.get("source_oracle") or {}
    control(oracle.get("ok") is True and oracle.get("ontime_count") == 2, f"{oracle}")
    control(abs(float(oracle["avg_cost"]) - 300.0) < 0.01, f"{oracle}")

    go_status, go_body = _post(
        client, GOLDEN, {"path": str(ex_body["path"]), "producer": "pointer_copilot"}
    )
    control(go_status == 200 and go_body.get("reason") == "golden_miss", f"{go_status} {go_body}")
    control(
        4521.5 in (go_body.get("inspected") or {}).get("analysis_numbers", []),
        f"{go_body}",
    )

    # GAP: the raw IC is in the response bodies.
    leaking = sorted(
        route
        for route, payload in (("extract", ex_body), ("crosscheck", cc_body), ("golden", go_body))
        if IC in json.dumps(payload, default=str)
    )
    assert not leaking, (
        f"raw IC {IC} returned unmasked by {leaking}: "
        f"extract analysis_labeled={inspected.get('analysis_labeled')!r}, "
        f"crosscheck cost_col={oracle.get('cost_col')!r}, "
        f"golden analysis_labeled={(go_body.get('inspected') or {}).get('analysis_labeled')!r}"
    )
