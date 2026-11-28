"""Resolve a theme font stack to a file on this machine.

Office files name the face. When the file is missing, the next face in the
stack is used for the preview, then a generic face. No face is required.
"""

from __future__ import annotations

from pathlib import Path

# Preferred file, then italic, then bold, then bold italic. Missing files skip.
_FILES: dict[str, dict[str, str]] = {
    "Inter": {
        "regular": "/usr/share/fonts/truetype/macos/Inter-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/macos/Inter-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/macos/Inter-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/macos/Inter-BoldItalic.ttf",
    },
    "Arial": {
        "regular": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/noto/NotoSans-BoldItalic.ttf",
    },
    "Helvetica Neue": {
        "regular": "/usr/share/fonts/truetype/macos/Inter-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/macos/Inter-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/macos/Inter-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/macos/Inter-BoldItalic.ttf",
    },
    "Times New Roman": {
        "regular": "/usr/share/fonts/truetype/croscore/Tinos-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/croscore/Tinos-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/croscore/Tinos-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/croscore/Tinos-BoldItalic.ttf",
    },
    "Liberation Serif": {
        "regular": "/usr/share/fonts/truetype/croscore/Tinos-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/croscore/Tinos-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/croscore/Tinos-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/croscore/Tinos-BoldItalic.ttf",
    },
    "Tinos": {
        "regular": "/usr/share/fonts/truetype/croscore/Tinos-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/croscore/Tinos-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/croscore/Tinos-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/croscore/Tinos-BoldItalic.ttf",
    },
    "Noto Sans CJK SC": {
        "regular": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/noto/NotoSans-BoldItalic.ttf",
    },
    "Source Han Sans SC": {
        "regular": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/noto/NotoSans-BoldItalic.ttf",
    },
    "Microsoft YaHei": {
        "regular": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/noto/NotoSans-BoldItalic.ttf",
    },
    "Noto Serif CJK SC": {
        "regular": "/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf",
        "italic": "/usr/share/fonts/truetype/noto/NotoSerif-Italic.ttf",
        "bold": "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
        "bolditalic": "/usr/share/fonts/truetype/noto/NotoSerif-BoldItalic.ttf",
    },
}


def face_file(names: tuple[str, ...], *, bold: bool, italic: bool) -> str:
    """First installed file in the stack. Empty when none of them is here."""
    key = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    fallback = "bold" if bold else "regular"
    for name in names:
        files = _FILES.get(name)
        if not files:
            continue
        for choice in (key, fallback, "regular"):
            path = files.get(choice) or ""
            if path and Path(path).is_file():
                return path
    return ""
