"""HTML app. One page per laid-out page, positioned on the same grid."""

from __future__ import annotations

import base64
import html

from dms_core.create_style.layout import Page
from dms_core.create_style.theme import Theme


def html_bytes(pages: list[Page], theme: Theme, title: str) -> bytes:
    colours = theme.colours
    css = (
        f":root{{--ink:{colours.ink};--paper:{colours.paper};--accent:{colours.accent};"
        f"--card:{colours.card};--muted:{colours.muted};--line:{colours.line};"
        f"--card-alpha:{theme.card_alpha};"
        f"--display:{html.escape(theme.fonts.css_family())};"
        f"--body:{html.escape(theme.fonts.css_family())};}}"
        "body{margin:0;background:#E6E1D8;font-family:var(--body);}"
        ".page{position:relative;margin:24px auto;overflow:hidden;}"
        ".box{position:absolute;box-sizing:border-box;overflow:hidden;}"
        ".text{white-space:pre-wrap;word-break:normal;overflow-wrap:normal;}"
        ".chart-switch{position:absolute;left:8px;top:8px;display:flex;flex-wrap:wrap;gap:4px;}"
        ".chart-switch button{font:12px/1 var(--body);padding:4px 6px;border:1px solid var(--line);"
        "background:var(--card);color:var(--ink);cursor:pointer;}"
        ".chart-switch button.is-on{background:var(--accent);color:#fff;"
        "border-color:var(--accent);}"
    )
    sections = []
    for page in pages:
        bits = []
        for box in page.boxes:
            if box.name == "paper":
                continue
            style = f"left:{box.x}px;top:{box.y}px;width:{box.w}px;height:{box.h}px;"
            if box.kind == "rect":
                red, green, blue = _rgb_tuple(box.fill or colours.card)
                style += f"background:rgba({red},{green},{blue},{box.alpha});"
                if box.name not in ("card", "header_bar", "bar"):
                    style += f"border:1px solid {colours.line};"
                bits.append(f'<div class="box" style="{style}"></div>')
            elif box.kind == "image" and box.image:
                encoded = base64.b64encode(box.image).decode("ascii")
                style += "overflow:hidden;"
                switch = ""
                if box.name == "chart" and box.variants:
                    buttons = [
                        f'<button type="button" class="is-on" data-chart-switch '
                        f'data-kind="{html.escape(box.chart_kind)}" '
                        f'data-src="data:image/png;base64,{encoded}">{html.escape(box.chart_kind)}</button>'
                    ]
                    for name, raw in box.variants.items():
                        alt = base64.b64encode(raw).decode("ascii")
                        buttons.append(
                            f'<button type="button" data-chart-switch '
                            f'data-kind="{html.escape(name)}" '
                            f'data-src="data:image/png;base64,{alt}">{html.escape(name)}</button>'
                        )
                    switch = f'<div class="chart-switch">{"".join(buttons)}</div>'
                bits.append(
                    f'<div class="box" data-name="{html.escape(box.name)}" '
                    f'data-kind="{html.escape(box.chart_kind)}" style="{style}">'
                    f'<img alt="" width="{box.w}" height="{box.h}" '
                    f'style="display:block;width:{box.w}px;height:{box.h}px;" '
                    f'src="data:image/png;base64,{encoded}"/>{switch}</div>'
                )
            elif box.kind == "text":
                weight = "700" if box.bold else "400"
                slant = "italic" if box.italic else "normal"
                family = theme.fonts.css_family()
                style += (
                    f"color:{box.color};font-family:{family};"
                    f"font-size:{box.size}px;line-height:{box.line}px;font-weight:{weight};"
                    f"font-style:{slant};"
                )
                bits.append(
                    f'<div class="box text" data-name="{html.escape(box.name)}" '
                    f'style="{style}">{html.escape(box.text)}</div>'
                )
        sections.append(
            f'<section class="page" style="width:{page.w}px;height:{page.h}px;'
            f'background:{colours.paper};">{"".join(bits)}</section>'
        )
    doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{css}</style></head><body>"
        f"{''.join(sections)}"
        "<script>document.addEventListener('click',function(event){"
        "var button=event.target.closest('[data-chart-switch]');"
        "if(!button)return;"
        "var frame=button.closest('[data-name]');"
        "var img=frame.querySelector('img');"
        "if(button.dataset.src)img.src=button.dataset.src;"
        "frame.setAttribute('data-kind',button.dataset.kind);"
        "frame.querySelectorAll('[data-chart-switch]').forEach(function(item){"
        "item.classList.toggle('is-on',item===button);});"
        "});</script></body></html>"
    )
    return doc.encode("utf-8")


def _rgb_tuple(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
