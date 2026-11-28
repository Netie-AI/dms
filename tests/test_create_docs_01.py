"""Doc and slides through the shared adapter seam.

The stub stands in for ``generate(spec, data_context, style)``. The product
package does not draw slides. python-pptx is a test stand-in for the pptx skill.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest
from create_docs.adapter import AdapterMissing, ProviderStop, bind_generate, bind_sign
from create_docs.flag import enabled
from create_docs.harness import edit, new_run, produce
from create_docs.judge import judge
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

_FIXTURE = Path(__file__).parent / "fixtures" / "create_docs" / "answers.json"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_COST = {"prompt_tokens": 420, "completion_tokens": 180, "usd": None}


def _answers() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _style(**extra: Any) -> dict[str, Any]:
    style: dict[str, Any] = {
        "format": "pptx",
        "skill_id": "pptx",
        "prompt_version": "draft",
        "prompt_text": "Use the skill. Cite only data_context.",
        "super_guide": "Quiet type. Wide margins. One idea on a page.",
        "template_id": "plain",
        "brand": {"name": "Netie", "primary": "", "font": "Calibri", "notes": "No logo lockup."},
        "few_shots": [{"title": "Region note", "text": "Lead with the larger region."}],
        "route": "openvault",
    }
    style.update(extra)
    return style


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(lines: list[str]) -> bytes:
    escaped = [_pdf_escape(line) for line in lines if line]
    commands = ["BT", "/F1 11 Tf", "72 740 Td"]
    for index, line in enumerate(escaped):
        if index:
            commands.append("0 -14 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", "replace")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0] * 6

    def obj(number: int, body: bytes) -> None:
        offsets[number] = len(out)
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    obj(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    )
    obj(
        4,
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    )
    obj(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    xref = len(out)
    out.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(out)


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        '<w:p><w:r><w:t xml:space="preserve">' + escape(p) + "</w:t></w:r></w:p>"
        for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def _chart(slide, answer: dict[str, Any]) -> None:
    rows = answer["rows"]
    keys = list(rows[0].keys())
    label = next(
        k for k in keys if not isinstance(rows[0][k], (int, float)) or isinstance(rows[0][k], bool)
    )
    value = next(
        k for k in keys if isinstance(rows[0][k], (int, float)) and not isinstance(rows[0][k], bool)
    )
    data = CategoryChartData()
    data.categories = [str(row[label]) for row in rows]
    data.add_series(str(value), tuple(float(row[value]) for row in rows))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(3.2), Inches(6.4), Inches(3.2), data
    )


def _body_lines(spec: dict[str, Any], answers: list[dict[str, Any]], bad: bool) -> list[str]:
    by_id = {a["id"]: a for a in answers}
    edit_block = spec.get("edit") if isinstance(spec.get("edit"), dict) else None
    lines: list[str] = []
    for section in spec["sections"]:
        lines.append(str(section["title"]))
        if section["kind"] == "title":
            continue
        if section["kind"] == "appendix":
            lines.extend(a["sql"] for a in answers)
            continue
        answer = by_id[str(section["id"])]
        extra = ""
        if bad:
            extra = " 99999"
        elif edit_block and edit_block.get("target") == section["id"]:
            extra = " " + str(edit_block.get("instruction") or "")
        lines.append(str(answer["text"]) + extra)
        lines.append("Source: " + ", ".join(answer["sources"]) + " | " + str(answer["as_of"]))
    return lines


def _pptx(spec: dict[str, Any], answers: list[dict[str, Any]], bad: bool) -> bytes:
    by_id = {a["id"]: a for a in answers}
    edit_block = spec.get("edit") if isinstance(spec.get("edit"), dict) else None
    prs = Presentation()
    for section in spec["sections"]:
        if section["kind"] == "title":
            slide = prs.slides.add_slide(prs.slide_layouts[0])
            slide.shapes.title.text = str(section["title"])
            continue
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = str(section["title"])
        frame = slide.placeholders[1].text_frame
        if section["kind"] == "appendix":
            frame.text = "\n".join(a["sql"] for a in answers)
        else:
            answer = by_id[str(section["id"])]
            extra = ""
            if bad:
                extra = " 99999"
            elif edit_block and edit_block.get("target") == section["id"]:
                extra = " " + str(edit_block.get("instruction") or "")
            frame.text = (
                str(answer["text"])
                + extra
                + "\nSource: "
                + ", ".join(answer["sources"])
                + " | "
                + str(answer["as_of"])
            )
            _chart(slide, answer)
        slide.notes_slide.notes_text_frame.text = (
            "One insight. Figures come from the granted answer."
        )
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _generate(fail_first: bool, usd: float | None = None):
    def generate(
        spec: dict[str, Any], data_context: dict[str, Any], style: dict[str, Any]
    ) -> dict[str, Any]:
        assert style["skill_id"] in {"pptx", "docx", "xlsx", "pdf"}
        assert "SELECT *" not in data_context["xml"]
        answers = data_context["answers"]
        bad = fail_first and not spec.get("repair") and not spec.get("edit")
        lines = _body_lines(spec, answers, bad)
        fmt = spec["format"]
        if fmt == "docx":
            content, media, filename = _docx(lines), _DOCX, "netie-answers.docx"
        else:
            content, media, filename = _pptx(spec, answers, bad), _PPTX, "netie-answers.pptx"
        cost = dict(_COST)
        cost["usd"] = usd
        return {
            "content": content,
            "pdf": _pdf(lines),
            "media_type": media,
            "filename": filename,
            "cost": cost,
        }

    return generate


def _bind(generate, sign) -> None:
    bind_generate(generate)
    bind_sign(sign)


def _clear() -> None:
    bind_generate(None)
    bind_sign(None)


def _save_artifacts(deck: bytes, pdf: bytes, doc: bytes | None, meta: dict[str, Any]) -> None:
    root = "/opt/cursor/artifacts"
    if not os.path.isdir("/opt/cursor"):
        return
    os.makedirs(root, exist_ok=True)
    Path(root, "create-docs-stub-deck.pptx").write_bytes(deck)
    Path(root, "create-docs-stub-sign.pdf").write_bytes(pdf)
    if doc is not None:
        Path(root, "create-docs-stub-doc.docx").write_bytes(doc)
    Path(root, "create-docs-stub-meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def test_deck_through_adapter() -> None:
    signed: list[dict[str, Any]] = []
    _bind(
        _generate(False),
        lambda payload: signed.append(payload) or {"status": "queued", "sha256": payload["sha256"]},
    )
    try:
        run = new_run(_answers(), _style())
        view = produce(run)
    finally:
        _clear()
    assert view["stopped"] is None
    assert view["steps"] == 1
    assert view["spend"]["prompt_tokens"] == 420
    assert view["spend"]["completion_tokens"] == 180
    assert view["spend"]["usd"] is None
    version = view["versions"][0]
    assert version["judge"]["ok"] is True
    content = run["versions"][0]["content"]
    pdf = run["versions"][0]["pdf"]
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        slides = [
            n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        ]
        charts = [n for n in zf.namelist() if n.startswith("ppt/charts/chart")]
        assert len(slides) == 4
        assert charts
        blob = "\n".join(zf.read(n).decode("utf-8") for n in slides)
    assert "Revenue by region" in blob
    assert "Orders by channel" in blob
    assert "Appendix: SQL" in blob
    assert "1200" in blob and "800" in blob and "40" in blob and "10" in blob
    assert "99999" not in blob
    assert "sales" in blob and "2026-11-28T09:00:00Z" in blob
    assert "SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region" in blob
    assert pdf.startswith(b"%PDF") and b"%%EOF" in pdf
    assert version["pdf_sha256"] == __import__("hashlib").sha256(pdf).hexdigest()
    assert __import__("hashlib").sha256(pdf).hexdigest() == version["pdf_sha256"]
    assert signed[0]["tool"] == "sign_request"
    assert signed[0]["sha256"] == version["pdf_sha256"]
    assert signed[0]["version"] == "v1"
    _save_artifacts(
        content,
        pdf,
        None,
        {
            "pdf_sha256": version["pdf_sha256"],
            "artifact_sha256": version["artifact_sha256"],
            "prompt_tokens": 420,
            "completion_tokens": 180,
            "usd": None,
            "via": "generate(spec, data_context, style)",
        },
    )


def test_doc_through_adapter() -> None:
    _bind(_generate(False), lambda payload: {"status": "queued", "sha256": payload["sha256"]})
    try:
        run = new_run(_answers(), _style(format="docx", skill_id="docx"))
        view = produce(run)
    finally:
        _clear()
    content = run["versions"][0]["content"]
    verdict = judge(content, media_type=_DOCX, spec=run["spec"], allowed=run["allowed"])
    assert view["versions"][0]["judge"]["ok"] is True
    assert verdict["ok"] is True
    text = zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml").decode("utf-8")
    assert "North was 1200. South was 800." in text
    assert "Appendix: SQL" in text
    root = "/opt/cursor/artifacts"
    if os.path.isdir("/opt/cursor"):
        os.makedirs(root, exist_ok=True)
        Path(root, "create-docs-stub-doc.docx").write_bytes(content)


def test_judge_repair_keeps_both_attempts() -> None:
    signed: list[str] = []
    _bind(
        _generate(True),
        lambda payload: (
            signed.append(payload["sha256"]) or {"status": "queued", "sha256": payload["sha256"]}
        ),
    )
    try:
        run = new_run(_answers(), _style())
        view = produce(run)
    finally:
        _clear()
    assert view["steps"] == 2
    assert len(view["versions"]) == 2
    assert view["versions"][0]["judge"]["ok"] is False
    assert "number_mismatch" in view["versions"][0]["judge"]["problems"]
    assert view["versions"][0]["sign"]["status"] == "withheld"
    assert view["versions"][1]["judge"]["ok"] is True
    assert view["versions"][1]["repair"]
    assert signed == [view["versions"][1]["pdf_sha256"]]
    assert view["spend"]["prompt_tokens"] == 840


def test_section_edit_keeps_versions_and_rechecks() -> None:
    signed: list[str] = []
    _bind(
        _generate(False),
        lambda payload: (
            signed.append(payload["sha256"]) or {"status": "queued", "sha256": payload["sha256"]}
        ),
    )
    try:
        run = new_run(_answers(), _style())
        produce(run)
        view = edit(run, "s0", "Make the insight shorter.")
    finally:
        _clear()
    assert len(view["versions"]) == 2
    assert all(v["judge"]["ok"] for v in view["versions"])
    assert view["versions"][0]["pdf_sha256"] != view["versions"][1]["pdf_sha256"]
    assert view["versions"][1]["edit"]["target"] == "s0"
    assert signed == [v["pdf_sha256"] for v in view["versions"]]


def test_budget_exceeded_keeps_the_prior_version() -> None:
    def generate(spec, data_context, style):
        if spec.get("edit"):
            raise ProviderStop("budget_exceeded")
        return _generate(False)(spec, data_context, style)

    _bind(generate, lambda payload: {"status": "queued", "sha256": payload["sha256"]})
    try:
        run = new_run(_answers(), _style())
        produce(run)
        view = edit(run, "s1", "Shorten this paragraph.")
    finally:
        _clear()
    assert view["stopped"] == "budget_exceeded"
    assert len(view["versions"]) == 1
    assert view["versions"][0]["judge"]["ok"] is True


def test_max_steps_blocks_the_edit() -> None:
    _bind(_generate(False), lambda payload: {"status": "queued", "sha256": payload["sha256"]})
    try:
        run = new_run(_answers(), _style(), max_steps=1)
        produce(run)
        view = edit(run, "appendix", "Leave the SQL as it is.")
    finally:
        _clear()
    assert view["stopped"] == "max_steps"
    assert len(view["versions"]) == 1


def test_max_usd_stops_when_the_provider_reports_it() -> None:
    _bind(
        _generate(True, usd=2.0), lambda payload: {"status": "queued", "sha256": payload["sha256"]}
    )
    try:
        run = new_run(_answers(), _style(), max_usd=1.0)
        view = produce(run)
    finally:
        _clear()
    assert view["stopped"] == "budget_usd"
    assert view["steps"] == 1
    assert view["versions"][0]["judge"]["ok"] is False


def test_xml_carries_style_and_not_a_raw_table() -> None:
    run = new_run(_answers(), _style())
    xml = run["data_context"]["xml"]
    assert 'skill_id="pptx"' in xml
    assert "Quiet type. Wide margins. One idea on a page." in xml
    assert "Region note" in xml
    assert 'granted_only="true"' in xml
    assert "<schema_summary>" in xml
    assert "<aggregates>" in xml
    assert "SELECT *" not in xml
    assert all(len(a["rows"]) <= 20 for a in run["data_context"]["answers"])


def test_missing_adapter_does_not_invent_a_file() -> None:
    _clear()
    run = new_run(_answers(), _style())
    with pytest.raises(AdapterMissing):
        produce(run)
    assert run["versions"] == []


@pytest.mark.parametrize(
    ("raw", "on"),
    [
        (None, False),
        ("0", False),
        ("true", False),
        ("yes", False),
        ("2", False),
        ("01", False),
        ("1", True),
        (" 1 ", True),
    ],
)
def test_flag_digit_one_only(monkeypatch: pytest.MonkeyPatch, raw: str | None, on: bool) -> None:
    if raw is None:
        monkeypatch.delenv("DMS_CREATE_DOCS", raising=False)
    else:
        monkeypatch.setenv("DMS_CREATE_DOCS", raw)
    assert enabled() is on


def test_routes_flag_off_404_and_flag_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cortex_contract")
    pytest.importorskip("fastapi")
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    body = {
        "answers": [
            {
                "question": "Revenue by region",
                "text": "North was 1200.",
                "rows": [{"region": "North", "revenue": 1200}],
            }
        ]
    }
    monkeypatch.delenv("DMS_CREATE_DOCS", raising=False)
    off = TestClient(create_app())
    assert off.post("/v1/create-docs/runs", json=body).status_code == 404
    status = off.get("/v1/create-docs/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": False}
    monkeypatch.setenv("DMS_CREATE_DOCS", "1")
    on = TestClient(create_app())
    assert on.get("/v1/create-docs/status").json() == {"enabled": True}
    denied = on.post("/v1/create-docs/runs", json=body)
    assert denied.status_code == 403
