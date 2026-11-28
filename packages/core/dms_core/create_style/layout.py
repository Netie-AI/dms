"""Place layout blocks on the spacing grid.

The block text is stored whole. Wrapping only decides the box height.
A word is never cut to make it fit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from dms_core.create_style.chart_registry import known
from dms_core.create_style.errors import CreateError
from dms_core.create_style.paint import chart_png, icon_png, square_png
from dms_core.create_style.spec import cell_text
from dms_core.create_style.theme import Theme

# ponytail: 0.62em is a wide grotesque guess so the box is taller than the glyphs.
# Ceiling: a much wider face can still clip. Upgrade: measure with the font file.
_CHAR = 0.62


@dataclass
class Box:
    kind: str
    name: str
    x: int
    y: int
    w: int
    h: int
    text: str = ""
    fill: str = ""
    alpha: float = 1.0
    color: str = "#1C1C1C"
    size: int = 14
    line: int = 24
    bold: bool = False
    font: str = "Inter"
    italic: bool = False
    ea: str = ""
    office: str = ""
    chart_kind: str = ""
    image: bytes = b""
    lines: tuple[str, ...] = field(default_factory=tuple)
    variants: dict[str, bytes] = field(default_factory=dict)


@dataclass
class Page:
    w: int
    h: int
    boxes: list[Box]


def wrap_text(text: str, width_px: int, font_px: int) -> tuple[list[str], int]:
    """Wrap on spaces and newlines. The font shrinks before a word is split."""
    if width_px <= 0:
        raise CreateError("layout_overflow")
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    size = font_px
    while size >= 8:
        max_chars = max(1, int(width_px / max(size * _CHAR, 1)))
        lines: list[str] = []
        fits = True
        for part in parts:
            words = _tokens(part, max_chars)
            if words is None:
                fits = False
                break
            if not words:
                lines.append("")
                continue
            cur = ""
            for word in words:
                if not cur:
                    cur = word
                elif len(cur) + 1 + len(word) <= max_chars:
                    cur = cur + " " + word
                else:
                    lines.append(cur)
                    cur = word
            if cur:
                lines.append(cur)
        if fits:
            return (lines or [""]), size
        size -= 1
    raise CreateError("word_does_not_fit")


def _tokens(part: str, max_chars: int) -> list[str] | None:
    """English words stay whole. A space-free run (CJK) breaks on characters."""
    words: list[str] = []
    for word in part.split(" "):
        if not word:
            continue
        if len(word) <= max_chars:
            words.append(word)
            continue
        if any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in word):
            return None
        words.extend(word[index : index + max_chars] for index in range(0, len(word), max_chars))
    return words


def layout_pages(document: dict[str, Any], theme: Theme, canvas: str) -> list[Page]:
    if canvas == "slide":
        from dms_core.create_style.wide import wide_pages

        pages = wide_pages(document, theme)
    else:
        flow = _Flow(theme, canvas)
        for block in document["blocks"]:
            if block["type"] == "chart_page":
                flow.chart_page(block)
            else:
                flow.use_title(document.get("title") or "")
                flow.block(block)
        flow.finish()
        pages = flow.pages
    if not pages:
        raise CreateError("layout_overflow")
    _inside(pages, theme)
    return pages


def snapshot_pages(pages: list[Page]) -> list[dict[str, Any]]:
    dumped = []
    for page in pages:
        boxes = []
        for box in page.boxes:
            item: dict[str, Any] = {
                "name": box.name,
                "x": box.x,
                "y": box.y,
                "w": box.w,
                "h": box.h,
                "text": box.text,
            }
            if box.kind == "image":
                item["image"] = True
                item["square"] = box.w == box.h
            boxes.append(item)
        dumped.append({"w": page.w, "h": page.h, "boxes": boxes})
    return dumped


def _inside(pages: list[Page], theme: Theme) -> None:
    unit = theme.spacing.unit
    margin = theme.spacing.page_margin
    for page in pages:
        for box in page.boxes:
            if box.w % unit or box.h % unit or box.x % unit or box.y % unit:
                raise CreateError("off_grid")
            if box.w <= 0 or box.h <= 0:
                raise CreateError("layout_overflow")
            if box.name == "paper":
                if (box.x, box.y, box.w, box.h) != (0, 0, page.w, page.h):
                    raise CreateError("layout_overflow")
                continue
            # Full-bleed photo, a top band, and an edge frame sit on the page edge.
            if box.name in ("bleed", "band", "scrim", "frame"):
                if box.x < 0 or box.y < 0:
                    raise CreateError("layout_overflow")
                if box.x + box.w > page.w or box.y + box.h > page.h:
                    raise CreateError("layout_overflow")
                continue
            if box.x < margin or box.y < margin:
                raise CreateError("layout_overflow")
            if box.x + box.w > page.w - margin or box.y + box.h > page.h - margin:
                raise CreateError("layout_overflow")


class _Flow:
    def __init__(self, theme: Theme, canvas: str) -> None:
        self.theme = theme
        self.size = theme.slide if canvas == "slide" else theme.doc
        self.unit = theme.spacing.unit
        self.margin = theme.spacing.page_margin
        self.gap = theme.spacing.gap
        self.pad = theme.spacing.card_pad
        self.pages: list[Page] = []
        self.boxes: list[Box] = []
        self.y = 0
        self._titled = False
        self._logo = self._logo_box()
        self._open()

    def _logo_box(self) -> tuple[int, int]:
        if not self.theme.logo_png:
            return (0, 0)
        side = self.unit * 4
        return (side, side)

    def _open(self) -> None:
        self.boxes = [
            Box(
                kind="rect",
                name="paper",
                x=0,
                y=0,
                w=self.size.w,
                h=self.size.h,
                fill=self.theme.colours.paper,
                alpha=1,
            )
        ]
        self.y = self.margin

    def finish(self) -> None:
        if any(box.name != "paper" for box in self.boxes):
            self.pages.append(Page(self.size.w, self.size.h, self.boxes))
        self.boxes = []

    def new_page(self) -> None:
        self.finish()
        self._open()

    @property
    def bottom(self) -> int:
        return self.size.h - self.margin

    @property
    def content_w(self) -> int:
        return self.size.w - 2 * self.margin

    @property
    def content_h(self) -> int:
        return self.size.h - 2 * self.margin

    def ensure(self, height: int) -> None:
        if height > self.content_h:
            raise CreateError("layout_overflow")
        if self.y + height > self.bottom:
            self.new_page()

    def use_title(self, title: str) -> None:
        if self._titled:
            return
        self._titled = True
        if not title and not self.theme.logo_png:
            return
        logo_w, logo_h = self._logo
        title_x = self.margin
        title_w = self.content_w
        lines: list[str] = []
        size = 28
        text_h = 0
        if logo_w:
            title_x = self.margin + logo_w + self.gap
            title_w = self.content_w - logo_w - self.gap
        if title and title_w > self.pad:
            lines, size = wrap_text(title, title_w, 28)
            text_h = _snap(max(len(lines), 1) * 40, self.unit)
        row_h = max(logo_h, text_h)
        if row_h == 0:
            return
        self.ensure(row_h)
        if logo_w:
            raw = square_png(self.theme.logo_png, logo_w)
            self.boxes.append(
                Box(
                    kind="image",
                    name="logo",
                    x=self.margin,
                    y=self.y,
                    w=logo_w,
                    h=logo_h,
                    image=raw,
                )
            )
        if lines:
            self.boxes.append(
                Box(
                    kind="text",
                    name="title",
                    x=title_x,
                    y=self.y,
                    w=title_w,
                    h=text_h,
                    text=title,
                    lines=tuple(lines),
                    size=size,
                    line=40,
                    bold=True,
                    font=self.theme.fonts.display,
                    ea=self.theme.fonts.cjk,
                    office=self.theme.fonts.office,
                    color=self.theme.colours.ink,
                )
            )
        self.y += row_h + self.gap

    def block(self, block: dict[str, Any]) -> None:
        kind = block["type"]
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
        elif kind in (
            "cover",
            "section",
            "quote",
            "agenda",
            "timeline",
            "process",
            "swot",
            "dashboard",
        ):
            self.extra(block)
        else:
            raise CreateError("bad_block")
        self.y += self.gap

    def extra(self, block: dict[str, Any]) -> None:
        """Doc pages keep the same words. Slides use the wide grammar."""
        if block.get("image"):
            caption = block.get("subtitle") or block.get("kicker") or ""
            caption = caption or block.get("title") or block.get("text") or ""
            if caption:
                self.image_below({"image": block["image"], "caption": caption})
            else:
                side = _snap_down(min(320, self.content_w), self.unit)
                self.ensure(side)
                png = square_png(block["image"], side)
                self.boxes.append(
                    Box(
                        kind="image",
                        name="image",
                        x=self.margin,
                        y=self.y,
                        w=side,
                        h=side,
                        image=png,
                    )
                )
                self.y += side
            return
        if block.get("items") and isinstance(block["items"][0], dict):
            self.kpi_strip(block)
            if block.get("chart"):
                self.chart_commentary(
                    {"chart": block["chart"], "commentary": block["items"][0]["headline"]}
                )
            return
        chunks: list[str] = []
        for key in ("kicker", "title", "subtitle", "text", "attribution", "s", "w", "o", "t"):
            piece = block.get(key)
            if isinstance(piece, str) and piece:
                chunks.append(piece)
        items = block.get("items")
        if isinstance(items, list):
            chunks.extend(str(item) for item in items)
        steps = block.get("steps")
        if isinstance(steps, list):
            chunks.extend(str(item) for item in steps)
        events = block.get("events")
        if isinstance(events, list):
            for event in events:
                chunks.append(str(event.get("when", "")) + " " + str(event.get("text", "")))
        text = "\n".join(chunk for chunk in chunks if chunk.strip())
        if not text:
            return
        lines, size = wrap_text(text, self.content_w, 16)
        height = _snap(len(lines) * 24, self.unit)
        self.ensure(height)
        self._text(
            "body", self.margin, self.y, self.content_w, height, text, lines, size, 24, False
        )
        self.y += height

    def image_side(self, block: dict[str, Any]) -> None:
        side = _snap_down(min(280, self.content_w - self.gap - 160), self.unit)
        if side < 96:
            raise CreateError("layout_overflow")
        cap_w = self.content_w - side - self.gap
        lines, size = wrap_text(block["caption"], cap_w - 2 * self.pad, 16)
        text_h = _snap(len(lines) * 24 + 2 * self.pad, self.unit)
        block_h = max(side, text_h)
        self.ensure(block_h)
        png = square_png(block["image"], side)
        self.boxes.append(
            Box(kind="image", name="image", x=self.margin, y=self.y, w=side, h=side, image=png)
        )
        card_y = self.y + _snap_down(max(block_h - text_h, 0) // 2, self.unit)
        self._card(self.margin + side + self.gap, card_y, cap_w, text_h)
        self._text(
            "caption",
            self.margin + side + self.gap + self.pad,
            card_y + self.pad,
            cap_w - 2 * self.pad,
            text_h - 2 * self.pad,
            block["caption"],
            lines,
            size,
            24,
            False,
            italic=True,
        )
        self.y += block_h

    def image_below(self, block: dict[str, Any]) -> None:
        side = _snap_down(min(320, self.content_w), self.unit)
        if side < 96:
            raise CreateError("layout_overflow")
        lines, size = wrap_text(block["caption"], self.content_w - 2 * self.pad, 16)
        cap_h = _snap(len(lines) * 24 + 2 * self.pad, self.unit)
        block_h = side + self.gap + cap_h
        self.ensure(block_h)
        offset = _snap_down((self.content_w - side) // 2, self.unit)
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
        cap_y = self.y + side + self.gap
        self._card(self.margin, cap_y, self.content_w, cap_h)
        self._text(
            "caption",
            self.margin + self.pad,
            cap_y + self.pad,
            self.content_w - 2 * self.pad,
            cap_h - 2 * self.pad,
            block["caption"],
            lines,
            size,
            24,
            False,
            italic=True,
        )
        self.y += block_h

    def two_column(self, block: dict[str, Any]) -> None:
        widths = _split(self.content_w, 2, self.gap, self.unit)
        placed = []
        height = self.unit * 10
        for name, text, width in (
            ("left", block["left"], widths[0]),
            ("right", block["right"], widths[1]),
        ):
            lines, size = wrap_text(text, width - 2 * self.pad, 16)
            text_h = _snap(len(lines) * 24 + 2 * self.pad, self.unit)
            height = max(height, text_h)
            placed.append((name, text, width, lines, size))
        self.ensure(height)
        x = self.margin
        for name, text, width, lines, size in placed:
            self._card(x, self.y, width, height)
            self._text(
                name,
                x + self.pad,
                self.y + self.pad,
                width - 2 * self.pad,
                height - 2 * self.pad,
                text,
                lines,
                size,
                24,
                False,
            )
            x += width + self.gap
        self.y += height

    def kpi_strip(self, block: dict[str, Any]) -> None:
        items = block["items"]
        widths = _split(self.content_w, len(items), self.gap, self.unit)
        prepared = []
        height = 0
        for item, width in zip(items, widths, strict=True):
            inner = width - 2 * self.pad
            headlines, h_size = wrap_text(item["headline"], inner, 16)
            values, v_size = wrap_text(item["value"], inner, 28)
            sources, s_size = wrap_text(item["source"], inner, 12)
            head_h = len(headlines) * 24
            value_h = len(values) * 32
            source_h = len(sources) * 16
            card_h = _snap(
                self.pad + 24 + 8 + head_h + 8 + value_h + 8 + source_h + self.pad, self.unit
            )
            height = max(height, card_h)
            prepared.append(
                (item, width, headlines, h_size, values, v_size, sources, s_size, head_h, value_h)
            )
        self.ensure(height)
        x = self.margin
        icon = icon_png("insight", self.theme.colours.ink)
        for (
            item,
            width,
            headlines,
            h_size,
            values,
            v_size,
            sources,
            s_size,
            head_h,
            value_h,
        ) in prepared:
            self._card(x, self.y, width, height)
            self.boxes.append(
                Box(
                    kind="image",
                    name="icon",
                    x=x + self.pad,
                    y=self.y + self.pad,
                    w=24,
                    h=24,
                    image=icon,
                )
            )
            cursor = self.y + self.pad + 24 + 8
            inner = width - 2 * self.pad
            self._text(
                "headline",
                x + self.pad,
                cursor,
                inner,
                head_h,
                item["headline"],
                headlines,
                h_size,
                24,
                False,
            )
            cursor += head_h + 8
            self._text(
                "value",
                x + self.pad,
                cursor,
                inner,
                value_h,
                item["value"],
                values,
                v_size,
                32,
                True,
                color=self.theme.colours.accent,
            )
            cursor += value_h + 8
            self._text(
                "source",
                x + self.pad,
                cursor,
                inner,
                len(sources) * 16,
                item["source"],
                sources,
                s_size,
                16,
                False,
                color=self.theme.colours.muted,
            )
            x += width + self.gap
        self.y += height

    def chart_commentary(self, block: dict[str, Any]) -> None:
        chart_w = _snap_down(int(self.content_w * 0.58), self.unit)
        text_w = self.content_w - chart_w - self.gap
        if chart_w < 160 or text_w < 160:
            raise CreateError("layout_overflow")
        lines, size = wrap_text(block["commentary"], text_w - 2 * self.pad, 16)
        text_h = _snap(self.pad + 24 + 8 + len(lines) * 24 + self.pad, self.unit)
        chart_h = max(
            320 if 320 <= self.content_h else _snap_down(self.content_h, self.unit), text_h
        )
        chart_h = _snap(chart_h, self.unit)
        self.ensure(chart_h)
        self.boxes.append(self._chart_image(block["chart"], self.margin, self.y, chart_w, chart_h))
        card_y = self.y + _snap_down(max(chart_h - text_h, 0) // 2, self.unit)
        self._card(self.margin + chart_w + self.gap, card_y, text_w, text_h)
        self._text(
            "commentary",
            self.margin + chart_w + self.gap + self.pad,
            card_y + self.pad,
            text_w - 2 * self.pad,
            text_h - 2 * self.pad,
            block["commentary"],
            lines,
            size,
            24,
            False,
        )
        self.y += chart_h

    def table(self, block: dict[str, Any]) -> None:
        columns = block["columns"]
        rows = block["rows"]
        widths = _split(self.content_w, len(columns), 0, self.unit)
        header_h = self._row_height(columns, widths, 14, bold=True)
        pending = list(rows)
        while True:
            if self.y + header_h > self.bottom:
                self.new_page()
            self._table_row(columns, widths, self.y, header_h, header=True)
            self.y += header_h
            progressed = False
            while pending:
                height = self._row_height(
                    [cell_text(cell) for cell in pending[0]], widths, 14, bold=False
                )
                if self.y + height > self.bottom:
                    break
                self._table_row(
                    [cell_text(cell) for cell in pending[0]],
                    widths,
                    self.y,
                    height,
                    header=False,
                )
                self.y += height
                pending.pop(0)
                progressed = True
            if not pending:
                break
            if not progressed:
                raise CreateError("layout_overflow")
            self.new_page()

    def chart_page(self, block: dict[str, Any]) -> None:
        if any(box.name != "paper" for box in self.boxes):
            self.new_page()
        charts = block["charts"]
        widths = _split(self.content_w, 2, self.gap, self.unit)
        heights = _split(self.content_h, 2, self.gap, self.unit)
        for index, chart in enumerate(charts):
            col = index % 2
            row = index // 2
            x = self.margin + col * (widths[0] + self.gap)
            # The second column uses its own width; the step uses the first plus the gap
            # when the two widths differ by one unit.
            if col == 1:
                x = self.margin + widths[0] + self.gap
            y = self.margin + (heights[0] + self.gap) * row
            if row == 1:
                y = self.margin + heights[0] + self.gap
            self._one_chart(chart, x, y, widths[col], heights[row])
        self.new_page()

    def _one_chart(self, chart: dict[str, Any], x: int, y: int, w: int, h: int) -> None:
        self._card(x, y, w, h)
        icon = icon_png("chart", self.theme.colours.ink)
        self.boxes.append(
            Box(kind="image", name="icon", x=x + self.pad, y=y + self.pad, w=24, h=24, image=icon)
        )
        title = chart["title"] or chart["series"][0]["name"]
        title_w = w - 2 * self.pad - 32
        lines, size = wrap_text(title, title_w, 16)
        title_h = len(lines) * 24
        self._text(
            "chart_title",
            x + self.pad + 32,
            y + self.pad,
            title_w,
            title_h,
            title,
            lines,
            size,
            24,
            True,
        )
        plot_y = y + self.pad + max(title_h, 24) + 8
        plot_h = h - (plot_y - y) - self.pad
        plot_w = w - 2 * self.pad
        plot_h = _snap_down(plot_h, self.unit)
        plot_w = _snap_down(plot_w, self.unit)
        if plot_h < 64 or plot_w < 64:
            raise CreateError("layout_overflow")
        self.boxes.append(self._chart_image(chart, x + self.pad, plot_y, plot_w, plot_h))

    def _row_height(self, cells: list[str], widths: list[int], font: int, *, bold: bool) -> int:
        del bold
        height = 32
        for text, width in zip(cells, widths, strict=True):
            lines, _size = wrap_text(text, max(width - 16, self.unit), font)
            height = max(height, _snap(len(lines) * 16 + 16, self.unit))
        if height > self.content_h:
            raise CreateError("layout_overflow")
        return height

    def _table_row(
        self,
        cells: list[str],
        widths: list[int],
        y: int,
        height: int,
        *,
        header: bool,
    ) -> None:
        x = self.margin
        for text, width in zip(cells, widths, strict=True):
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
                colour = "#FFFFFF"
                role = "header"
            else:
                self._card(x, y, width, height)
                colour = self.theme.colours.ink
                role = "cell"
            lines, size = wrap_text(text, max(width - 16, self.unit), 14)
            self._text(
                role,
                x + 8,
                y + 8,
                width - 16,
                height - 16,
                text,
                lines,
                size,
                16,
                header,
                color=colour,
            )
            x += width

    def _card(self, x: int, y: int, w: int, h: int) -> None:
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
                font=self.theme.fonts.display if name == "title" else self.theme.fonts.body,
                ea=self.theme.fonts.cjk,
                office=self.theme.fonts.office,
                color=color or self.theme.colours.ink,
            )
        )

    def _chart_image(self, chart: dict[str, Any], x: int, y: int, w: int, h: int) -> Box:
        """One picture for the model's kind, plus one picture per other kind."""
        chosen = str(chart.get("kind") or "bar")
        png = chart_png(chart, w, h, self.theme)
        variants: dict[str, bytes] = {}
        for name in known():
            if name == chosen:
                continue
            alt = dict(chart)
            alt["kind"] = name
            variants[name] = chart_png(alt, w, h, self.theme)
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


def _snap(value: int, unit: int) -> int:
    if value % unit == 0:
        return value
    return value + (unit - value % unit)


def _snap_down(value: int, unit: int) -> int:
    snapped = value // unit * unit
    return snapped if snapped > 0 else 0


def _split(total: int, count: int, gap: int, unit: int) -> list[int]:
    avail = total - gap * (count - 1)
    base = (avail // count // unit) * unit
    if base <= 0:
        raise CreateError("layout_overflow")
    extra_units = (avail - base * count) // unit
    widths = [base] * count
    for index in range(extra_units):
        widths[index % count] += unit
    return widths


def image_sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
