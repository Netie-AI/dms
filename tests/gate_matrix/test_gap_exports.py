"""Plan E gate-matrix, theme "exports": two gaps on the answer-delivery projections.

Both gaps are about what the export routes (POST /v1/chat/export.xlsx and
POST /v1/chat/export.bi) will restate. They serialise the envelope the CALLER
posts: ``refuse_envelope_export`` (dms_core/xlsx_export.py) checks the shape
only, and neither route reaches Cortex for a submit or a ledger lookup.

Each test stands on a CONTROL (``control`` raises ``ControlFailed``, which is not
an ``AssertionError`` and so is never absorbed as the expected failure): a real
answer returned by this app's own ``POST /v1/chat/ask`` is exported through the
same routes and comes back 200. Only then does the single plain ``assert`` state
what a correct gate would do. Run with ``--runxfail`` to read the real
``AssertionError`` text.

Not covered here: the ``export:csv-client`` and ``ui:share-answer`` cells of the
same two gaps. They are projections built in the browser (AnswerMessage.tsx,
rowsToCsv.ts, answerDelivery.ts) with no server route, so a Python test cannot
reach them; they need vitest.
"""

from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from _harness import (
    FINANCE,
    assert_envelope,
    control,
    gap,
    require_envelope,
)
from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# A question the demo router answers (dms_executor/demo_ask.py) but the pack does
# not, so with the Cortex ask failing and DMS_DEMO_FALLBACK=1 it falls back to demo.
DEMO_REVENUE_Q = "What was total revenue?"

#: Keys an export response would use to say "this is demo data".
_DEMO_MARKER_KEYS = ("ask_mode", "demo_fallback_used", "demo_fallback_banner")


# --------------------------------------------------------------------------
# helpers (stdlib only: the workbook is read, never written)
# --------------------------------------------------------------------------


def _sheet_grid(data: bytes, sheet: str) -> list[list[str | None]]:
    """Cell text of one worksheet of an xlsx the app produced (inlineStr or <v>)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [
            s.get("name")
            for s in ET.fromstring(z.read("xl/workbook.xml")).iterfind(".//m:sheet", _NS)
        ]
        control(sheet in names, f"workbook has sheets {names}, wanted {sheet!r}")
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{names.index(sheet) + 1}.xml"))
    grid: list[list[str | None]] = []
    for row in root.iterfind(".//m:row", _NS):
        cells: list[str | None] = []
        for c in row.iterfind("m:c", _NS):
            if c.get("t") == "inlineStr":
                cells.append("".join(t.text or "" for t in c.iterfind(".//m:t", _NS)))
            else:
                v = c.find("m:v", _NS)
                cells.append(None if v is None else v.text)
        grid.append(cells)
    return grid


def _cover(data: bytes) -> dict[str, str | None]:
    """The Cover sheet as {field: value}."""
    return {row[0]: row[1] for row in _sheet_grid(data, "Cover")[1:] if len(row) >= 2}


def _post_xlsx(harness: Any, envelope: dict[str, Any]) -> Any:
    return harness.client.post("/v1/chat/export.xlsx", json={"envelope": envelope})


def _post_bi(harness: Any, envelope: dict[str, Any], target: str | None = None) -> Any:
    body: dict[str, Any] = {"envelope": envelope}
    if target is not None:
        body["target"] = target
    return harness.client.post("/v1/chat/export.bi", json=body)


#: A figure no real answer in these tests contains. The forged envelopes below state it.
_FORGED_FIGURE = 9999999.0
_FORGED_TOKEN = "9999999"


def _export_text(resp: Any) -> str:
    """Everything an export response carries as searchable text (xlsx parts are unzipped)."""
    if resp.content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    return resp.text


def _with_figure(envelope: dict[str, Any], old: float, new: float) -> dict[str, Any]:
    """A deep copy of ``envelope`` where every rendering of the figure ``old`` (rows, values,
    text, audit_receipt) now says ``new``. Still a complete, well-formed envelope."""
    raw = json.dumps(envelope)
    needle = json.dumps(float(old))
    control(needle in raw, f"the figure {needle} is not in the envelope to be restated")
    return json.loads(raw.replace(needle, json.dumps(float(new))))


def _demo_markers(node: Any, path: str = "response") -> list[str]:
    """Every place in a BI export response that says the data is demo.

    Walks keys (not data cells, and not the answer_id that happens to embed the word
    ``demo`` in the file name) for ask_mode == "demo" / demo_fallback_* set, and
    reads only the ``//`` comment header of each Power Query M stub.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if key in _DEMO_MARKER_KEYS and value in ("demo", True):
                found.append(f"{here}={value!r}")
            if key == "power_query_m" and isinstance(value, str):
                for line in value.splitlines():
                    if line.lstrip().startswith("//") and "demo" in line.lower():
                        found.append(f"{here}: {line.strip()}")
            found.extend(_demo_markers(value, here))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found.extend(_demo_markers(item, f"{path}[{i}]"))
    return found


# --------------------------------------------------------------------------
# GAP export-projections-no-provenance-tie (G7)
# --------------------------------------------------------------------------


@gap("export:xlsx,export:bi", "G7", "new", "export-projections-no-provenance-tie")
def test_export_routes_restate_an_answer_this_app_never_issued(harness):  # type: ignore[no-untyped-def]
    """Exports must be tied to an answer this app issued, not to whatever the caller posts.

    Both forgeries below are COMPLETE, E1-E9-valid envelopes (a deep copy of a real answer
    with one figure changed everywhere it is written). A build that only validates the shape
    of the posted envelope still serves them, so this test stays an xfail; only a build that
    binds the export to an issued answer (its id and its content) can satisfy it.
    """
    # CONTROL: a real answer from this app's own /v1/chat/ask exports 200, through
    # both routes, with its own badge and rows. So the exports work and a refusal
    # below can only be the provenance check, not a broken route or fixture.
    s, asked = harness.ask(SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_exp_ctl")
    require_envelope(s, asked)
    control(
        asked["badge"] == "L1_GOVERNED_METRIC" and not asked["abstained"] and asked["rows"],
        f"the control ask did not return a governed answer: {asked.get('text')!r}",
    )
    issued_ids = {asked["answer_id"], asked["audit_id"]}
    real_figure = float(asked["values"][0]["value"])
    real_token = str(int(real_figure))
    real_xlsx = _post_xlsx(harness, asked)
    control(real_xlsx.status_code == 200, f"control xlsx export: HTTP {real_xlsx.status_code}")
    real_cover = _cover(real_xlsx.content)
    control(
        real_cover.get("badge") == asked["badge"]
        and real_cover.get("answer_id") == asked["answer_id"],
        f"control xlsx Cover does not restate the ask: {real_cover}",
    )
    real_bi = _post_bi(harness, asked, "powerbi")
    control(real_bi.status_code == 200, f"control BI export: HTTP {real_bi.status_code}")
    real_bi_body = real_bi.json()
    control(
        real_bi_body.get("ok") is True
        and real_bi_body.get("badge") == asked["badge"]
        and real_bi_body.get("table") == asked["rows"],
        f"control BI export does not restate the ask: {real_bi_body.get('badge')}",
    )

    # CONTROL: the token the forgeries are detected by is in neither legitimate export, and
    # the real figure is in both, so "forged figure absent / real figure present" below can
    # only mean the export restated the issued answer.
    for name, resp in (("xlsx", real_xlsx), ("bi", real_bi)):
        text = _export_text(resp)
        control(
            _FORGED_TOKEN not in text and real_token in text,
            f"the legitimate {name} export should carry {real_token} and not {_FORGED_TOKEN}",
        )

    # The gap requests. Two envelopes this app never returned, both complete and valid:
    #   tampered   - the issued answer's own ids and badge, one figure changed to 9,999,999;
    #   fabricated - a new answer_id and audit_id, claiming the top badge, same invented figure.
    tampered = _with_figure(asked, real_figure, _FORGED_FIGURE)
    fabricated = {
        **copy.deepcopy(tampered),
        "answer_id": "ans_forged_never_asked",
        "audit_id": "led_forged_never_issued",
        "badge": "L0_CERTIFIED",
    }
    for name, env in (("tampered", tampered), ("fabricated", fabricated)):
        assert_envelope(env)  # E1-E9: a refusal below cannot be about the envelope's shape
        control(
            _FORGED_TOKEN in json.dumps(env) and env["values"][0]["value"] == _FORGED_FIGURE,
            f"the {name} envelope does not state the invented figure: {env['values']!r}",
        )
    control(
        {fabricated["answer_id"], fabricated["audit_id"]}.isdisjoint(issued_ids)
        and (tampered["answer_id"], tampered["audit_id"])
        == (asked["answer_id"], asked["audit_id"]),
        "fabricated must carry unissued ids, tampered the issued ones",
    )

    calls_before = len(harness.cortex.calls)
    served: list[tuple[str, str, Any]] = []
    for name, env in (("fabricated", fabricated), ("tampered", tampered)):
        served.append((name, "export.xlsx", _post_xlsx(harness, env)))
        served.append((name, "export.bi", _post_bi(harness, env, "powerbi")))

    def _tied(name: str, resp: Any) -> bool:
        """Refused (4xx), or, for the issued ids only, the export restates the ISSUED figure."""
        if 400 <= resp.status_code < 500:
            return True
        if name == "tampered" and resp.status_code == 200:
            text = _export_text(resp)
            return _FORGED_TOKEN not in text and real_token in text
        return False

    def _what(route: str, resp: Any) -> str:
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"
        if route == "export.xlsx":
            return f"HTTP 200 (Cover badge {_cover(resp.content).get('badge')!r})"
        return f"HTTP 200 (table {resp.json().get('table')!r})"

    untied = [(n, r, resp) for n, r, resp in served if not _tied(n, resp)]
    # What a correct gate does: an answer the server never issued is refused (4xx), and an
    # issued answer's id cannot carry a figure the server did not issue (refused, or the
    # export restates the issued figure). Today every one of these is a 200 that carries
    # the invented figure out of the product, and no Cortex call is made to check it.
    assert not untied, (
        "export restated an envelope this app never issued: "
        + "; ".join(f"{n} -> {r}: {_what(r, resp)}" for n, r, resp in untied)
        + f"; Cortex calls made while serving the forged exports: "
        f"{len(harness.cortex.calls) - calls_before}"
    )


# --------------------------------------------------------------------------
# GAP demo-answers-exported-and-shared-unlabelled (G8)
# --------------------------------------------------------------------------


@gap("export:bi", "G8", "new", "demo-answers-exported-and-shared-unlabelled")
def test_bi_export_of_a_demo_fallback_answer_says_it_is_demo(harness_factory):  # type: ignore[no-untyped-def]
    # DMS_DEMO_FALLBACK=1 with the Cortex ask failing: the route returns a demo
    # envelope that says so (ask_mode "demo", demo_fallback_used, the banner flag).
    h = harness_factory(demo_fallback=True, ask_raises=RuntimeError("engine unavailable"))
    s, env = h.ask(DEMO_REVENUE_Q, space_id=FINANCE, session_id="ses_exp_demo")
    require_envelope(s, env)
    control(
        env["ask_mode"] == "demo"
        and env["demo_fallback_used"] is True
        and env.get("demo_fallback_banner") is True
        and env["rows"],
        f"the ask did not come back as a demo fallback: {env.get('ask_mode')!r}",
    )
    assert_envelope(env)

    # CONTROL: the xlsx export of the same envelope carries the demo marker on its
    # Cover, so the data to label the export with reaches the serializer.
    xlsx = _post_xlsx(h, env)
    control(xlsx.status_code == 200, f"control xlsx export: HTTP {xlsx.status_code}")
    cover = _cover(xlsx.content)
    control(cover.get("ask_mode") == "demo", f"xlsx Cover has no ask_mode=demo: {cover}")

    # The gap request: the BI export of that same demo envelope. It must succeed
    # (so a 4xx cannot stand in for the missing marker) and restate the same rows.
    bi = _post_bi(h, env)
    control(bi.status_code == 200, f"BI export: HTTP {bi.status_code} {bi.text[:200]}")
    body = bi.json()
    control(
        body.get("ok") is True and body.get("table") == env["rows"],
        f"BI export does not restate the demo rows: {body.get('table')!r}",
    )

    # What a correct export does: the demo figures do not leave the product
    # unlabelled. Today the response and both stubs name no ask_mode and no
    # demo_fallback_*, and the Power Query header says nothing about demo.
    markers = _demo_markers(body)
    assert markers, (
        "export.bi restated demo-fallback figures "
        f"{env['rows']!r} (envelope ask_mode={env['ask_mode']!r}, "
        f"demo_fallback_used={env['demo_fallback_used']!r}) with no demo marker; "
        f"response keys: {sorted(body)}, powerbi stub keys: {sorted(body['targets']['powerbi'])}"
    )
