"""Load the 2024-06 Mini-Dev release. The zip is downloaded at run time.

The questions and the databases are not committed. Layout of the public zip
(Last-Modified 2024-06-20):

    minidev/MINIDEV/mini_dev_sqlite.json
    minidev/MINIDEV/dev_databases/<db_id>/<db_id>.sqlite

Eleven databases, 500 questions. A smaller file is a smoke run only when
``--limit`` is set.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Public 2024-06 package. About 800MB. Never vendor it.
ZIP_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"
MINIDEV_N = 500
MINIDEV_DBS = 11


def load_questions(path: Path) -> list[dict[str, Any]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        rows = parsed.get("questions") or parsed.get("data") or []
    else:
        rows = parsed
    if not isinstance(rows, list):
        raise ValueError("questions_json")
    return [row for row in rows if isinstance(row, dict)]


def question_id(row: Mapping[str, Any]) -> str:
    raw = row.get("question_id", row.get("id"))
    return "" if raw is None else str(raw)


def db_id_of(row: Mapping[str, Any]) -> str:
    raw = row.get("db_id")
    return "" if raw is None else str(raw)


def gold_sql_of(row: Mapping[str, Any]) -> str:
    for key in ("SQL", "sql", "gold_sql"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def asked_text(row: Mapping[str, Any], *, evidence: bool) -> str:
    """The dataset question. Evidence is the dataset field, appended only when asked."""
    text = row.get("question")
    question = text if isinstance(text, str) else ""
    if not evidence:
        return question
    extra = row.get("evidence")
    if not isinstance(extra, str) or not extra.strip():
        return question
    return question + "\nEvidence: " + extra.strip()


def validate_full(questions: Sequence[Mapping[str, Any]], *, limit: int | None) -> str | None:
    """None when the file is the 500 by 11 release, or when ``limit`` marks a smoke."""
    if limit is not None:
        return None
    if len(questions) != MINIDEV_N:
        return f"release_n:{len(questions)}"
    dbs = {db_id_of(row) for row in questions}
    dbs.discard("")
    if len(dbs) != MINIDEV_DBS:
        return f"release_dbs:{len(dbs)}"
    return None


def locate_release(root: Path) -> tuple[Path, Path]:
    """``(questions json, db root)`` under an unpacked zip or a flat directory."""
    json_candidates = (
        root / "minidev" / "MINIDEV" / "mini_dev_sqlite.json",
        root / "MINIDEV" / "mini_dev_sqlite.json",
        root / "mini_dev_sqlite.json",
    )
    db_candidates = (
        root / "minidev" / "MINIDEV" / "dev_databases",
        root / "MINIDEV" / "dev_databases",
        root,
    )
    questions = next((path for path in json_candidates if path.is_file()), None)
    if questions is None:
        raise FileNotFoundError("mini_dev_sqlite.json")
    db_root = next(
        (
            path
            for path in db_candidates
            if path.is_dir() and (any(path.glob("*/*.sqlite")) or any(path.glob("*.sqlite")))
        ),
        None,
    )
    if db_root is None:
        raise FileNotFoundError("dev_databases")
    return questions, db_root


def _safe_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError("zip_path")
    zf.extractall(dest)


def unpack_release(zip_path: Path, dest: Path) -> tuple[Path, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        _safe_zip(zf, dest)
    return locate_release(dest)


def fetch_release(dest: Path, url: str = ZIP_URL) -> tuple[Path, Path]:
    """Stream the zip into ``dest``. No timeout. Does not commit the bytes."""
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / "minidev.zip"
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=None) as response, zip_path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    return unpack_release(zip_path, dest)
