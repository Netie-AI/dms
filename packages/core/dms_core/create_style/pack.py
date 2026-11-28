"""Shared OOXML helpers. Zip timestamps are fixed so a render is repeatable."""

from __future__ import annotations

import io
import zipfile

_ILLEGAL = {i: None for i in range(32) if i not in (9, 10, 13)}


def xml_text(text: str) -> str:
    cleaned = str(text).translate(_ILLEGAL)
    return cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def emu(px: int) -> int:
    return px * 9525


def alpha_val(alpha: float) -> str:
    return str(int(round(alpha * 100000)))


def rgb(colour: str) -> str:
    return colour.lstrip("#").upper()


def write_zip(parts: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(parts):
            info = zipfile.ZipInfo(filename=name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            payload = parts[name]
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            archive.writestr(info, payload)
    return buf.getvalue()
