"""Load a theme file. Colours, fonts, logo, and spacing come from the file."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms_core.create_style.errors import CreateError

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FONT = re.compile(r"^[A-Za-z][A-Za-z0-9 ]{0,39}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,31}$")
# The pitch grid is 8. Margin, gap, and pad are multiples of it.
_UNITS = frozenset({8})
_TOP = frozenset({"name", "colours", "card_alpha", "fonts", "logo_b64", "spacing", "slide", "doc"})


@dataclass(frozen=True)
class Colours:
    ink: str
    paper: str
    accent: str
    card: str
    muted: str
    line: str


@dataclass(frozen=True)
class Fonts:
    display: str
    body: str
    stack: tuple[str, ...] = ()
    cjk: str = ""
    office: str = ""
    generic: str = "sans-serif"

    def css_family(self) -> str:
        """Quoted stack, then the CJK face, then a generic family."""
        names: list[str] = []
        for name in (self.display, self.body, *self.stack):
            if name and name not in names:
                names.append(name)
        if self.cjk and self.cjk not in names:
            names.append(self.cjk)
        parts = []
        for name in names:
            parts.append(f'"{name}"' if " " in name else name)
        parts.append(self.generic or "sans-serif")
        return ", ".join(parts)


@dataclass(frozen=True)
class Spacing:
    unit: int
    page_margin: int
    gap: int
    card_pad: int


@dataclass(frozen=True)
class Canvas:
    w: int
    h: int


@dataclass(frozen=True)
class Theme:
    name: str
    colours: Colours
    fonts: Fonts
    spacing: Spacing
    slide: Canvas
    doc: Canvas
    card_alpha: float
    logo_png: bytes


def default_theme_path() -> Path:
    return Path(__file__).resolve().parent / "themes" / "pitch.json"


def theme_fields() -> dict[str, list[str]]:
    """Keys a brand file may set. No sample copy."""
    return {
        "colours": ["ink", "paper", "accent", "card", "muted", "line"],
        "fonts": ["display", "body", "stack", "cjk", "office", "generic"],
        "spacing": ["unit", "page_margin", "gap", "card_pad"],
        "slide": ["w", "h"],
        "doc": ["w", "h"],
        "top": ["name", "card_alpha", "logo_b64"],
    }


def load_theme(override: dict[str, Any] | None = None, path: Path | None = None) -> Theme:
    """Start from the packaged pitch file, then a brand file, then a dict."""
    data = _read_json(default_theme_path())
    if path is not None:
        data = _merge(data, _read_json(Path(path)))
    if override:
        if not isinstance(override, dict):
            raise CreateError("theme_field")
        data = _merge(data, override)
    return _parse(data)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CreateError("theme_unreadable") from None
    if not isinstance(raw, dict):
        raise CreateError("theme_field")
    return raw


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if key not in _TOP:
            raise CreateError("theme_field")
        current = out.get(key)
        if isinstance(current, dict):
            if not isinstance(value, dict):
                raise CreateError("theme_field")
            child = dict(current)
            for sub, piece in value.items():
                if sub not in child:
                    raise CreateError("theme_field")
                child[sub] = piece
            out[key] = child
        else:
            out[key] = value
    return out


def _colour(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX.match(value) is None:
        raise CreateError("theme_" + name)
    return value.upper()


def _font(value: object) -> str:
    if not isinstance(value, str) or _FONT.match(value) is None:
        raise CreateError("theme_font")
    return value


def _font_or_blank(value: object) -> str:
    if value in (None, ""):
        return ""
    return _font(value)


def _generic(value: object) -> str:
    if value in (None, ""):
        return "sans-serif"
    if value not in ("sans-serif", "serif"):
        raise CreateError("theme_font")
    return str(value)


def _stack(value: object, display: str) -> tuple[str, ...]:
    if value is None:
        return (display,)
    if not isinstance(value, list) or not value or len(value) > 8:
        raise CreateError("theme_font")
    return tuple(_font(item) for item in value)


def _on_grid(value: object, unit: int, *, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CreateError("theme_spacing")
    if value < low or value > high or value % unit != 0:
        raise CreateError("theme_spacing")
    return value


def _parse(data: dict[str, Any]) -> Theme:
    name = data.get("name")
    if not isinstance(name, str) or _NAME.match(name) is None:
        raise CreateError("theme_name")
    colours_raw = data.get("colours")
    if not isinstance(colours_raw, dict):
        raise CreateError("theme_field")
    colours = Colours(
        ink=_colour(colours_raw.get("ink"), "colour"),
        paper=_colour(colours_raw.get("paper"), "colour"),
        accent=_colour(colours_raw.get("accent"), "colour"),
        card=_colour(colours_raw.get("card"), "colour"),
        muted=_colour(colours_raw.get("muted"), "colour"),
        line=_colour(colours_raw.get("line"), "colour"),
    )
    fonts_raw = data.get("fonts")
    if not isinstance(fonts_raw, dict):
        raise CreateError("theme_field")
    display = _font(fonts_raw.get("display"))
    body = _font(fonts_raw.get("body"))
    fonts = Fonts(
        display=display,
        body=body,
        stack=_stack(fonts_raw.get("stack"), display),
        cjk=_font_or_blank(fonts_raw.get("cjk")),
        office=_font_or_blank(fonts_raw.get("office")) or display,
        generic=_generic(fonts_raw.get("generic")),
    )
    spacing_raw = data.get("spacing")
    if not isinstance(spacing_raw, dict):
        raise CreateError("theme_field")
    unit = spacing_raw.get("unit")
    if isinstance(unit, bool) or not isinstance(unit, int) or unit not in _UNITS:
        raise CreateError("theme_spacing")
    spacing = Spacing(
        unit=unit,
        page_margin=_on_grid(spacing_raw.get("page_margin"), unit, low=unit * 2, high=unit * 16),
        gap=_on_grid(spacing_raw.get("gap"), unit, low=unit, high=unit * 8),
        card_pad=_on_grid(spacing_raw.get("card_pad"), unit, low=unit, high=unit * 8),
    )
    alpha = data.get("card_alpha")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise CreateError("theme_alpha")
    card_alpha = float(alpha)
    if card_alpha < 0.05 or card_alpha > 1:
        raise CreateError("theme_alpha")
    slide = _canvas(data.get("slide"), spacing.unit, low_w=640, high_w=1920, low_h=360, high_h=1200)
    doc = _canvas(data.get("doc"), spacing.unit, low_w=640, high_w=1400, low_h=640, high_h=2000)
    logo = _logo(data.get("logo_b64"))
    return Theme(
        name=name,
        colours=colours,
        fonts=fonts,
        spacing=spacing,
        slide=slide,
        doc=doc,
        card_alpha=card_alpha,
        logo_png=logo,
    )


def _canvas(
    value: object, unit: int, *, low_w: int, high_w: int, low_h: int, high_h: int
) -> Canvas:
    if not isinstance(value, dict):
        raise CreateError("theme_page")
    return Canvas(
        w=_on_grid(value.get("w"), unit, low=low_w, high=high_w),
        h=_on_grid(value.get("h"), unit, low=low_h, high=high_h),
    )


def _logo(value: object) -> bytes:
    if value is None or value == "":
        return b""
    if not isinstance(value, str):
        raise CreateError("theme_logo")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise CreateError("theme_logo") from None
    if not raw or len(raw) > 500_000:
        raise CreateError("theme_logo")
    return raw
