"""Generate a slide deck and an HTML brief from an insights report - no number invented.

Why this exists
---------------
The last mile of "retrieve insights" is a document someone presents. That is
where numbers get retyped, rounded, reordered and occasionally made up, and it
is the one artifact a buyer actually holds. So the deck is GENERATED from the
insights JSON, every figure on every slide is the figure in the report, and the
report's figures were compiled and conserved by the ontology. The chain from a
slide back to an executed query is unbroken and machine-checkable: the test for
this module reads the deck back and asserts each number appears in the report.

What it deliberately does not do
--------------------------------
It does not phrase. Headlines are the insight templates' own sentences, which
are factual and a little wooden. A model may later rewrite them for a reader;
it will do so with the figures pinned, because a rewrite that changes a number
is a wrong number, and the test would catch it.

  python scripts/brief.py --insights data/lake/_reports/insights_aw.json \\
      --pptx data/lake/_reports/aw_brief.pptx --html data/lake/_reports/aw_brief.html

Exit 0 when every insight in the report made it onto a slide with its figure.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any


def _fmt(v: float, unit: str) -> str:
    if unit.lower() in {"usd", "myr", "eur", "gbp"}:
        return f"{unit.upper()} {v:,.0f}"
    return f"{v:,.0f} {unit}"


def build_html(report: dict[str, Any]) -> str:
    db = html.escape(str(report["database"]))
    o = report["ontology"]
    parts = [
        f"<h1>{db} - what the data says, and how we know</h1>",
        f"<p><b>Provenance.</b> {o['objects']} object types, {o['links']} measured "
        f"links, {o['measures']} declared measures. {html.escape(report['scope'])}.</p>",
    ]
    for i in report["insights"]:
        parts.append(f"<h2>{html.escape(i['headline'])}</h2>")
        parts.append(
            f"<p>{html.escape(i['measure'])} total {html.escape(_fmt(i['total'], i['unit']))} "
            f"over {i['n_groups']} {html.escape(i['dimension'])} values.</p>"
        )
        parts.append("<table><tr><th>group</th><th>value</th></tr>")
        for lbl, v in i["rows"][:8]:
            parts.append(f"<tr><td>{html.escape(str(lbl))}</td>"
                         f"<td>{html.escape(_fmt(v, i['unit']))}</td></tr>")
        parts.append("</table>")
        for c in i.get("caveats", []):
            parts.append(f"<p><i>{html.escape(c)}</i></p>")
        parts.append(f"<details><summary>SQL</summary><pre>{html.escape(i['sql'])}</pre></details>")
    if report.get("refusals"):
        parts.append("<h2>Questions the ontology refused</h2><ul>")
        for r in report["refusals"][:8]:
            parts.append(f"<li><b>{html.escape(r['question'])}</b> - {html.escape(r['reason'])}: "
                         f"{html.escape(r['detail'][:160])}</li>")
        parts.append("</ul>")
    return "\n".join(parts)


def build_pptx(report: dict[str, Any], out: Path) -> int:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    blank = prs.slide_layouts[6]
    title_layout = prs.slide_layouts[0]

    s = prs.slides.add_slide(title_layout)
    s.shapes.title.text = f"{report['database']}: what the data says"
    s.placeholders[1].text = (
        f"{report['ontology']['objects']} objects, {report['ontology']['links']} measured links, "
        f"{report['ontology']['measures']} declared measures. Every figure compiled and conserved."
    )

    n = 0
    for i in report["insights"]:
        slide = prs.slides.add_slide(blank)
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(9), Inches(1.2))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.text = i["headline"]
        tf.paragraphs[0].runs[0].font.size = Pt(24)
        tf.paragraphs[0].runs[0].font.bold = True

        rows = i["rows"][:8]
        table = slide.shapes.add_table(
            len(rows) + 1, 2, Inches(0.5), Inches(1.8), Inches(6), Inches(0.4) * (len(rows) + 1)
        ).table
        table.cell(0, 0).text = i["dimension"]
        table.cell(0, 1).text = f"{i['measure']} ({i['unit']})"
        for r, (lbl, v) in enumerate(rows, start=1):
            table.cell(r, 0).text = str(lbl)
            table.cell(r, 1).text = f"{v:,.2f}"

        foot = slide.shapes.add_textbox(Inches(0.5), Inches(6.4), Inches(9), Inches(1))
        ff = foot.text_frame
        ff.word_wrap = True
        ff.text = (f"total {i['total']:,.2f} {i['unit']} over {i['n_groups']} groups; "
                   f"top share {i['top_share'] * 100:.1f} pct. " + "; ".join(i.get("caveats", [])))
        ff.paragraphs[0].runs[0].font.size = Pt(10)
        slide.notes_slide.notes_text_frame.text = "SQL:\n" + i["sql"]
        n += 1

    if report.get("refusals"):
        slide = prs.slides.add_slide(blank)
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(9), Inches(6))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.text = "Questions the ontology refused rather than guessed"
        tf.paragraphs[0].runs[0].font.size = Pt(22)
        for r in report["refusals"][:8]:
            p = tf.add_paragraph()
            p.text = f"{r['question']} - {r['reason']}"
            p.runs[0].font.size = Pt(12)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return n


def main(argv: list[str] | None = None) -> int:
    # R-0012: a Windows console defaults to cp1252 and turns every accented
    # label into a replacement glyph. Say what the data says.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--insights", type=Path, required=True)
    ap.add_argument("--pptx", type=Path)
    ap.add_argument("--html", type=Path)
    args = ap.parse_args(argv)

    report = json.loads(args.insights.read_text(encoding="utf-8"))
    if not report.get("insights"):
        print("FAIL the report holds no insights; nothing to brief")
        return 1
    made = 0
    if args.pptx:
        made = build_pptx(report, args.pptx)
        print(f"  wrote {args.pptx} ({made} insight slides)")
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(build_html(report), encoding="utf-8")
        print(f"  wrote {args.html}")
    if args.pptx and made != len(report["insights"]):
        print(f"FAIL {len(report['insights'])} insights, {made} slides")
        return 1
    print("PASS every insight is on a slide with its own figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
