"""Wide slide grammar: consulting, corporate red, research, glass, minimal.

Slots stay the model's words. This file only places them.
"""

from __future__ import annotations

from typing import Any

from dms_core.create_style.chart_registry import known
from dms_core.create_style.errors import CreateError
from dms_core.create_style.layout import Box, Page, _snap, _snap_down, wrap_text
from dms_core.create_style.paint import chart_png, square_png
from dms_core.create_style.spec import cell_text
from dms_core.create_style.theme import Theme

_SOLO = frozenset(
    {
        "cover",
        "section",
        "quote",
        "agenda",
        "timeline",
        "process",
        "swot",
        "dashboard",
        "chart_page",
    }
)
_IBAR = frozenset({"huawei-red", "cn-corporate"})


def wide_pages(document: dict[str, Any], theme: Theme) -> list[Page]:
    deck = _Deck(theme)
    title = document.get("title") or ""
    for block in document["blocks"]:
        deck.place(block, title)
    deck.finish()
    return deck.pages


def _on_accent(accent: str) -> str:
    red = int(accent[1:3], 16)
    green = int(accent[3:5], 16)
    blue = int(accent[5:7], 16)
    if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 160:
        return "#1B1B1F"
    return "#FFFFFF"


def _chart_box(chart: dict[str, Any], x: int, y: int, w: int, h: int, theme: Theme) -> Box:
    chosen = str(chart.get("kind") or "bar")
    png = chart_png(chart, w, h, theme)
    variants: dict[str, bytes] = {}
    for name in known():
        if name == chosen:
            continue
        alt = dict(chart)
        alt["kind"] = name
        variants[name] = chart_png(alt, w, h, theme)
    return Box(
        kind="image",
        name="chart",
        x=x,
        y=y,
        w=w,
        h=h,
        image=png,
        chart_kind=chosen,
        variants=variants,
    )


class _Deck:
    def __init__(self, theme: Theme) -> None:
        self.theme = theme
        self.w = theme.slide.w
        self.h = theme.slide.h
        self.unit = theme.spacing.unit
        self.margin = theme.spacing.page_margin
        self.gap = theme.spacing.gap
        self.pad = theme.spacing.card_pad
        self.pages: list[Page] = []
        self.boxes: list[Box] = []
        self.y = 0
        self.open = False
        self.titled = False

    def place(self, block: dict[str, Any], title: str) -> None:
        kind = str(block["type"])
        if kind in _SOLO:
            self.finish()
            self.open_page(chrome=kind != "cover")
            if kind == "cover":
                self.cover(block, title)
            elif kind == "section":
                self.section(block)
            elif kind == "quote":
                self.quote(block)
            elif kind == "agenda":
                self.agenda(block, title)
            elif kind == "timeline":
                self.timeline(block, title)
            elif kind == "process":
                self.process(block, title)
            elif kind == "swot":
                self.swot(block, title)
            elif kind == "dashboard":
                self.dashboard(block, title)
            else:
                self.chart_page(block)
            self.finish()
            return
        if not self.open:
            self.open_page()
        self.add_title(title)
        if kind == "image_side":
            self.image_side(block)
        elif kind == "image_below":
            self.image_below(block)
        elif kind == "two_column":
            self.two_column(block)
        elif kind == "kpi_strip":
            self.kpi_strip(block)
        elif kind == "chart_commentary":
            self.chart_commentary(block)
        elif kind == "table":
            self.table(block)
        else:
            raise CreateError("bad_block")

    def finish(self) -> None:
        if not self.open:
            return
        useful = [box for box in self.boxes if box.name not in ("paper", "band", "frame")]
        if useful:
            self.pages.append(Page(self.w, self.h, self.boxes))
        self.open = False
        self.boxes = []

    def open_page(self, *, chrome: bool = True) -> None:
        paper = self.theme.colours.paper
        self.boxes = [
            Box(kind="rect", name="paper", x=0, y=0, w=self.w, h=self.h, fill=paper, alpha=1)
        ]
        if chrome:
            self._chrome()
        self.y = self.margin
        self.open = True

    def _chrome(self) -> None:
        accent = self.theme.colours.accent
        if self.theme.name == "research-note":
            self.boxes.append(
                Box(kind="rect", name="band", x=0, y=0, w=self.w, h=8, fill=accent, alpha=1)
            )
        if self.theme.name == "huawei-red":
            self.boxes.append(
                Box(kind="rect", name="frame", x=0, y=0, w=self.w, h=8, fill=accent, alpha=1)
            )
            self.boxes.append(
                Box(
                    kind="rect",
                    name="frame",
                    x=0,
                    y=self.h - 8,
                    w=self.w,
                    h=8,
                    fill=accent,
                    alpha=1,
                )
            )
            self.boxes.append(
                Box(kind="rect", name="frame", x=0, y=0, w=8, h=self.h, fill=accent, alpha=1)
            )
            self.boxes.append(
                Box(
                    kind="rect",
                    name="frame",
                    x=self.w - 8,
                    y=0,
                    w=8,
                    h=self.h,
                    fill=accent,
                    alpha=1,
                )
            )

    def room(self, height: int) -> None:
        if height > self.h - 2 * self.margin:
            raise CreateError("layout_overflow")
        if self.y + height <= self.h - self.margin:
            return
        self.finish()
        self.open_page()

    def content_w(self) -> int:
        return self.w - 2 * self.margin

    def add_title(self, title: str) -> None:
        if self.titled or not title:
            return
        self.titled = True
        x = self.margin
        if self.theme.logo_png:
            png = square_png(self.theme.logo_png, 32)
            self.boxes.append(
                Box(kind="image", name="logo", x=x, y=self.y, w=32, h=32, image=png)
            )
            x += 48
        if self.theme.name in _IBAR:
            self.boxes.append(
                Box(
                    kind="rect",
                    name="ibar",
                    x=x,
                    y=self.y,
                    w=8,
                    h=48,
                    fill=self.theme.colours.accent,
                    alpha=1,
                )
            )
            x += 24
        width = self.w - self.margin - x
        lines, size = wrap_text(title, width, 36)
        self._text("title", x, self.y, width, 64, title, lines, size, 40, True)
        self.y += 88

    def _card(self, x: int, y: int, w: int, h: int) -> None:
        if self.theme.name == "minimal-art":
            return
        self.boxes.append(
            Box(
                kind="rect",
                name="card",
                x=x,
                y=y,
                w=w,
                h=h,
                fill=self.theme.colours.card,
                alpha=self.theme.card_alpha,
            )
        )

    def _text(
        self,
        name: str,
        x: int,
        y: int,
        w: int,
        h: int,
        text: str,
        lines: list[str],
        size: int,
        line: int,
        bold: bool,
        *,
        color: str = "",
        italic: bool = False,
    ) -> None:
        self.boxes.append(
            Box(
                kind="text",
                name=name,
                x=x,
                y=y,
                w=w,
                h=h,
                text=text,
                lines=tuple(lines),
                size=size,
                line=line,
                bold=bold,
                italic=italic,
                font=(
                    self.theme.fonts.display
                    if name in ("title", "value")
                    else self.theme.fonts.body
                ),
                ea=self.theme.fonts.cjk,
                office=self.theme.fonts.office,
                color=color or self.theme.colours.ink,
            )
        )

    def _side(self) -> int:
        guess = _snap_down(int(self.content_w() * 0.4), self.unit)
        return min(448, guess)

    def _hug(self, lines: list[str], size: int, max_w: int) -> int:
        """A short caption gets a card that fits the line, not the whole column."""
        longest = max((len(line) for line in lines), default=1)
        need = int(longest * size * 0.62) + 2 * self.pad
        width = _snap(need, self.unit)
        if width > max_w:
            width = _snap_down(max_w, self.unit)
        floor = self.unit * 16
        if width < floor:
            width = min(max_w, floor)
        return width

    def image_side(self, block: dict[str, Any]) -> None:
        side = self._side()
        if side < 96:
            raise CreateError("layout_overflow")
        cap_w = self.content_w() - side - self.gap
        lines, size = wrap_text(block["caption"], max(cap_w - 2 * self.pad, self.unit), 18)
        card_w = cap_w if len(lines) > 1 else self._hug(lines, size, cap_w)
        inner = card_w - 2 * self.pad
        lines, size = wrap_text(block["caption"], inner, 18)
        text_h = _snap(len(lines) * 28 + 2 * self.pad, self.unit)
        row = max(side, text_h)
        self.room(row)
        png = square_png(block["image"], side)
        self.boxes.append(
            Box(kind="image", name="image", x=self.margin, y=self.y, w=side, h=side, image=png)
        )
        card_y = self.y + _snap_down(max(row - text_h, 0) // 2, self.unit)
        card_x = self.margin + side + self.gap
        self._card(card_x, card_y, card_w, text_h)
        label = block.get("label") or ""
        top = card_y + self.pad
        if label:
            self._text("label", card_x + self.pad, top, inner, 24, label, [label], 13, 16, True)
            top += 24
        self._text(
            "caption",
            card_x + self.pad,
            top,
            inner,
            text_h - (top - card_y) - self.pad,
            block["caption"],
            lines,
            size,
            28,
            False,
            italic=True,
        )
        self.y += row + self.gap

    def image_below(self, block: dict[str, Any]) -> None:
        side = self._side()
        wrap_at = max(self.content_w() - 2 * self.pad, self.unit)
        lines, size = wrap_text(block["caption"], wrap_at, 18)
        card_w = self.content_w() if len(lines) > 1 else self._hug(lines, size, self.content_w())
        inner = card_w - 2 * self.pad
        lines, size = wrap_text(block["caption"], inner, 18)
        text_h = _snap(len(lines) * 28 + 2 * self.pad, self.unit)
        row = side + self.gap + text_h
        self.room(row)
        offset = _snap_down((self.content_w() - side) // 2, self.unit)
        png = square_png(block["image"], side)
        self.boxes.append(
            Box(
                kind="image",
                name="image",
                x=self.margin + offset,
                y=self.y,
                w=side,
                h=side,
                image=png,
            )
        )
        card_y = self.y + side + self.gap
        card_x = self.margin + _snap_down((self.content_w() - card_w) // 2, self.unit)
        self._card(card_x, card_y, card_w, text_h)
        self._text(
            "caption",
            card_x + self.pad,
            card_y + self.pad,
            inner,
            text_h - 2 * self.pad,
            block["caption"],
            lines,
            size,
            28,
            False,
            italic=True,
        )
        self.y += row + self.gap

    def two_column(self, block: dict[str, Any]) -> None:
        width = _equal(self.content_w(), 2, self.gap, self.unit)
        left, left_size = wrap_text(block["left"], width - 2 * self.pad, 18)
        right, right_size = wrap_text(block["right"], width - 2 * self.pad, 18)
        lines_n = max(len(left), len(right))
        height = _snap(lines_n * 28 + 2 * self.pad + 24, self.unit)
        self.room(height)
        for index, (key, label_key, lines, size) in enumerate(
            (
                ("left", "left_label", left, left_size),
                ("right", "right_label", right, right_size),
            )
        ):
            x = self.margin + index * (width + self.gap)
            self._card(x, self.y, width, height)
            cursor = self.y + self.pad
            label = block.get(label_key) or ""
            if label:
                self._text(
                    "label",
                    x + self.pad,
                    cursor,
                    width - 2 * self.pad,
                    24,
                    label,
                    [label],
                    13,
                    16,
                    True,
                    color=self.theme.colours.accent,
                )
                cursor += 24
            self._text(
                key,
                x + self.pad,
                cursor,
                width - 2 * self.pad,
                height - (cursor - self.y) - self.pad,
                block[key],
                lines,
                size,
                28,
                False,
            )
        self.y += height + self.gap

    def kpi_strip(self, block: dict[str, Any]) -> None:
        items = block["items"]
        width = _equal(self.content_w(), len(items), self.gap, self.unit)
        height = 200 if self.theme.name == "minimal-art" else 176
        self.room(height)
        value_size = 72 if self.theme.name == "minimal-art" else 40
        for index, item in enumerate(items):
            x = self.margin + index * (width + self.gap)
            self._card(x, self.y, width, height)
            inner = width - 2 * self.pad
            cursor = self.y + self.pad
            headlines, h_size = wrap_text(item["headline"], inner, 16)
            self._text(
                "headline",
                x + self.pad,
                cursor,
                inner,
                32,
                item["headline"],
                headlines,
                h_size,
                24,
                False,
            )
            cursor += 40
            self._text(
                "value",
                x + self.pad,
                cursor,
                inner,
                80 if value_size > 40 else 48,
                item["value"],
                [item["value"]],
                value_size,
                48,
                True,
                color=self.theme.colours.accent,
            )
            cursor += 88 if value_size > 40 else 56
            sources, s_size = wrap_text(item["source"], inner, 13)
            self._text(
                "source",
                x + self.pad,
                cursor,
                inner,
                24,
                item["source"],
                sources,
                s_size,
                16,
                False,
                italic=True,
                color=self.theme.colours.muted,
            )
        self.y += height + self.gap

    def chart_commentary(self, block: dict[str, Any]) -> None:
        chart_w = _snap_down(int(self.content_w() * 0.62), self.unit)
        text_w = self.content_w() - chart_w - self.gap
        if chart_w < 160 or text_w < 120:
            raise CreateError("layout_overflow")
        inner = text_w - 2 * self.pad
        lines, size = wrap_text(block["commentary"], inner, 18)
        text_h = _snap(len(lines) * 28 + 2 * self.pad, self.unit)
        chart_h = 400 if 400 <= self.h - 2 * self.margin else _snap_down(self.h // 3, self.unit)
        row = max(chart_h, text_h)
        self.room(row)
        self.boxes.append(
            _chart_box(block["chart"], self.margin, self.y, chart_w, chart_h, self.theme)
        )
        card_y = self.y + _snap_down(max(row - text_h, 0) // 2, self.unit)
        card_x = self.margin + chart_w + self.gap
        self._card(card_x, card_y, text_w, text_h)
        self._text(
            "commentary",
            card_x + self.pad,
            card_y + self.pad,
            inner,
            text_h - 2 * self.pad,
            block["commentary"],
            lines,
            size,
            28,
            False,
        )
        self.y += row + self.gap

    def table(self, block: dict[str, Any]) -> None:
        columns = block["columns"]
        rows = block["rows"]
        widths = _split(self.content_w(), len(columns), self.unit)
        peaks = _peaks(columns, rows)
        pending = list(rows)
        while True:
            header_h = 48
            self.room(header_h)
            self._table_row(columns, widths, self.y, header_h, header=True, peaks=peaks)
            self.y += header_h
            progressed = False
            while pending:
                cells = [cell_text(cell) for cell in pending[0]]
                height = 56
                if self.y + height > self.h - self.margin:
                    break
                self._table_row(
                    cells, widths, self.y, height, header=False, peaks=peaks, raw=pending[0]
                )
                self.y += height
                pending.pop(0)
                progressed = True
            if not pending:
                break
            if not progressed:
                raise CreateError("layout_overflow")
            self.finish()
            self.open_page()

    def _table_row(
        self,
        cells: list[str],
        widths: list[int],
        y: int,
        height: int,
        *,
        header: bool,
        peaks: list[float],
        raw: list[object] | None = None,
    ) -> None:
        x = self.margin
        ink = _on_accent(self.theme.colours.accent) if header else self.theme.colours.ink
        for index, (text, width) in enumerate(zip(cells, widths, strict=True)):
            if header:
                self.boxes.append(
                    Box(
                        kind="rect",
                        name="header_bar",
                        x=x,
                        y=y,
                        w=width,
                        h=height,
                        fill=self.theme.colours.accent,
                        alpha=1,
                    )
                )
            else:
                self._card(x, y, width, height)
                if raw is not None and peaks[index] > 0:
                    number = _as_number(raw[index])
                    if number is not None:
                        span = max(width - 16, self.unit)
                        bar_w = _snap_down(int(abs(number) / peaks[index] * span), self.unit)
                        if bar_w >= self.unit:
                            self.boxes.append(
                                Box(
                                    kind="rect",
                                    name="bar",
                                    x=x + 8,
                                    y=y + height - 16,
                                    w=bar_w,
                                    h=8,
                                    fill=self.theme.colours.accent,
                                    alpha=0.35,
                                )
                            )
            lines, size = wrap_text(text, max(width - 16, self.unit), 14)
            text_h = height - 16 if header else 24
            self._text(
                "header" if header else "cell",
                x + 8,
                y + 8,
                width - 16,
                text_h,
                text,
                lines,
                size,
                16,
                header,
                color=ink,
            )
            x += width

    def chart_page(self, block: dict[str, Any]) -> None:
        charts = block["charts"]
        cell_w = _equal(self.content_w(), 2, self.gap, self.unit)
        cell_h = _equal(self.h - 2 * self.margin, 2, self.gap, self.unit)
        for index, chart in enumerate(charts):
            col = index % 2
            row = index // 2
            x = self.margin + col * (cell_w + self.gap)
            y = self.margin + row * (cell_h + self.gap)
            self._one_chart(chart, x, y, cell_w, cell_h)

    def _one_chart(self, chart: dict[str, Any], x: int, y: int, w: int, h: int) -> None:
        self._card(x, y, w, h)
        title = chart["title"] or chart["series"][0]["name"]
        inner = w - 2 * self.pad
        lines, size = wrap_text(title, inner, 16)
        self._text("chart_title", x + self.pad, y + 16, inner, 32, title, lines, size, 24, True)
        plot_w = _snap_down(w - 2 * self.pad, self.unit)
        plot_h = _snap_down(h - 80, self.unit)
        if plot_w < 64 or plot_h < 64:
            raise CreateError("layout_overflow")
        self.boxes.append(
            _chart_box(chart, x + self.pad, y + 56, plot_w, plot_h, self.theme)
        )

    def cover(self, block: dict[str, Any], title: str) -> None:
        self.boxes.append(
            Box(
                kind="image",
                name="bleed",
                x=0,
                y=0,
                w=self.w,
                h=self.h,
                image=block["image"],
            )
        )
        self._chrome()
        scrim_w = _snap_down(int(self.w * 0.38), self.unit)
        self.boxes.append(
            Box(
                kind="rect",
                name="scrim",
                x=0,
                y=0,
                w=scrim_w,
                h=self.h,
                fill=self.theme.colours.paper,
                alpha=0.88,
            )
        )
        x = self.margin
        y = self.margin + 160
        width = scrim_w - self.margin - 24
        if width < 80:
            width = self.content_w()
        kicker = block.get("kicker") or ""
        if kicker:
            self._text(
                "kicker",
                x,
                y,
                width,
                32,
                kicker,
                [kicker],
                16,
                24,
                True,
                color=self.theme.colours.accent,
            )
            y += 40
        if title:
            lines, size = wrap_text(title, width, 48)
            text_h = _snap(len(lines) * 56, self.unit)
            self._text("title", x, y, width, text_h, title, lines, size, 56, True)
            y += text_h + 16
        subtitle = block.get("subtitle") or ""
        if subtitle:
            lines, size = wrap_text(subtitle, width, 20)
            text_h = _snap(len(lines) * 28, self.unit)
            self._text(
                "subtitle",
                x,
                y,
                width,
                text_h,
                subtitle,
                lines,
                size,
                28,
                False,
                italic=True,
            )

    def section(self, block: dict[str, Any]) -> None:
        y = self.margin + 240
        kicker = block.get("kicker") or ""
        if kicker:
            self._text(
                "kicker",
                self.margin,
                y,
                self.content_w(),
                32,
                kicker,
                [kicker],
                16,
                24,
                True,
                color=self.theme.colours.accent,
            )
            y += 48
        title = block["title"]
        lines, size = wrap_text(title, self.content_w(), 64)
        text_h = _snap(max(len(lines), 1) * 72, self.unit)
        self._text("title", self.margin, y, self.content_w(), text_h, title, lines, size, 72, True)

    def quote(self, block: dict[str, Any]) -> None:
        width = _snap_down(int(self.content_w() * 0.72), self.unit)
        x = self.margin + _snap_down((self.content_w() - width) // 2, self.unit)
        lines, size = wrap_text(block["text"], width, 36)
        text_h = _snap(len(lines) * 48, self.unit)
        y = self.margin + _snap_down((self.h - 2 * self.margin - text_h) // 3, self.unit)
        self._text(
            "quote",
            x,
            y,
            width,
            text_h,
            block["text"],
            lines,
            size,
            48,
            False,
            italic=True,
        )
        attribution = block.get("attribution") or ""
        if attribution:
            self._text(
                "attribution",
                x,
                y + text_h + 24,
                width,
                32,
                attribution,
                [attribution],
                16,
                24,
                False,
                color=self.theme.colours.muted,
            )

    def agenda(self, block: dict[str, Any], title: str) -> None:
        self.add_title(title)
        items = block["items"]
        height = 72
        width = self.content_w()
        for index, item in enumerate(items):
            self.room(height)
            self._card(self.margin, self.y, width, height)
            number = f"{index + 1:02d}"
            self._text(
                "index",
                self.margin + self.pad,
                self.y + 24,
                64,
                32,
                number,
                [number],
                20,
                28,
                True,
                color=self.theme.colours.accent,
            )
            lines, size = wrap_text(item, width - 120, 20)
            self._text(
                "item",
                self.margin + 96,
                self.y + 24,
                width - 120,
                32,
                item,
                lines,
                size,
                28,
                False,
            )
            self.y += height + 16

    def timeline(self, block: dict[str, Any], title: str) -> None:
        self.add_title(title)
        events = block["events"]
        width = _equal(self.content_w(), len(events), self.gap, self.unit)
        line_y = self.y + 80
        self.boxes.append(
            Box(
                kind="rect",
                name="rule",
                x=self.margin,
                y=line_y,
                w=self.content_w(),
                h=8,
                fill=self.theme.colours.accent,
                alpha=1,
            )
        )
        for index, event in enumerate(events):
            x = self.margin + index * (width + self.gap)
            self.boxes.append(
                Box(
                    kind="rect",
                    name="mark",
                    x=x,
                    y=line_y - 8,
                    w=24,
                    h=24,
                    fill=self.theme.colours.accent,
                    alpha=1,
                )
            )
            when = event["when"]
            self._text("when", x, line_y + 32, width, 32, when, [when], 16, 24, True)
            lines, size = wrap_text(event["text"], width, 16)
            text_h = _snap(len(lines) * 24, self.unit)
            self._text(
                "event", x, line_y + 72, width, text_h, event["text"], lines, size, 24, False
            )

    def process(self, block: dict[str, Any], title: str) -> None:
        self.add_title(title)
        steps = block["steps"]
        width = _equal(self.content_w(), len(steps), self.gap, self.unit)
        height = 200
        self.room(height)
        for index, step in enumerate(steps):
            x = self.margin + index * (width + self.gap)
            self._card(x, self.y, width, height)
            number = f"{index + 1:02d}"
            self._text(
                "index",
                x + self.pad,
                self.y + self.pad,
                width - 2 * self.pad,
                40,
                number,
                [number],
                28,
                32,
                True,
                color=self.theme.colours.accent,
            )
            lines, size = wrap_text(step, width - 2 * self.pad, 16)
            self._text(
                "step",
                x + self.pad,
                self.y + self.pad + 48,
                width - 2 * self.pad,
                height - self.pad - 56,
                step,
                lines,
                size,
                24,
                False,
            )
        self.y += height

    def swot(self, block: dict[str, Any], title: str) -> None:
        self.add_title(title)
        cell_w = _equal(self.content_w(), 2, self.gap, self.unit)
        cell_h = 240
        pairs = (("s", "S"), ("w", "W"), ("o", "O"), ("t", "T"))
        for index, (key, label) in enumerate(pairs):
            col = index % 2
            row = index // 2
            x = self.margin + col * (cell_w + self.gap)
            y = self.y + row * (cell_h + self.gap)
            self._card(x, y, cell_w, cell_h)
            self._text(
                "label",
                x + self.pad,
                y + self.pad,
                64,
                32,
                label,
                [label],
                20,
                28,
                True,
                color=self.theme.colours.accent,
            )
            lines, size = wrap_text(block[key], cell_w - 2 * self.pad, 16)
            self._text(
                key,
                x + self.pad,
                y + self.pad + 40,
                cell_w - 2 * self.pad,
                cell_h - self.pad - 48,
                block[key],
                lines,
                size,
                24,
                False,
            )

    def dashboard(self, block: dict[str, Any], title: str) -> None:
        self.add_title(title)
        self.kpi_strip(block)
        if block.get("chart"):
            note = block["chart"].get("title") or block["items"][0]["headline"]
            self.chart_commentary({"chart": block["chart"], "commentary": note})


def _equal(total: int, count: int, gap: int, unit: int) -> int:
    avail = total - gap * (count - 1)
    size = (avail // count // unit) * unit
    if size <= 0:
        raise CreateError("layout_overflow")
    return size


def _split(total: int, count: int, unit: int) -> list[int]:
    base = (total // count // unit) * unit
    if base <= 0:
        raise CreateError("layout_overflow")
    extra = (total - base * count) // unit
    widths = [base] * count
    for index in range(extra):
        widths[index % count] += unit
    return widths


def _peaks(columns: list[str], rows: list[list[object]]) -> list[float]:
    peaks = [0.0] * len(columns)
    for row in rows:
        for index, cell in enumerate(row):
            number = _as_number(cell)
            if number is not None:
                peaks[index] = max(peaks[index], abs(number))
    return peaks


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
