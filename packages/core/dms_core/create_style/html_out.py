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
        f"--display:{html.escape(theme.fonts.display)};--body:{html.escape(theme.fonts.body)};}}"
        "body{margin:0;background:#E6E1D8;font-family:var(--body),sans-serif;}"
        ".page{position:relative;margin:24px auto;overflow:hidden;}"
        ".box{position:absolute;box-sizing:border-box;overflow:hidden;}"
        ".text{white-space:pre-wrap;word-break:normal;overflow-wrap:normal;}"
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
                style += f"border:1px solid {colours.line};"
                bits.append(f'<div class="box" style="{style}"></div>')
            elif box.kind == "image" and box.image:
                encoded = base64.b64encode(box.image).decode("ascii")
                style += "overflow:hidden;"
                bits.append(
                    f'<div class="box" data-name="{html.escape(box.name)}" style="{style}">'
                    f'<img alt="" width="{box.w}" height="{box.h}" '
                    f'style="display:block;width:{box.w}px;height:{box.h}px;" '
                    f'src="data:image/png;base64,{encoded}"/></div>'
                )
            elif box.kind == "text":
                weight = "700" if box.bold else "400"
                style += (
                    f"color:{box.color};font-family:{html.escape(box.font)},sans-serif;"
                    f"font-size:{box.size}px;line-height:{box.line}px;font-weight:{weight};"
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
        f"{''.join(sections)}</body></html>"
    )
    return doc.encode("utf-8")


def _rgb_tuple(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
