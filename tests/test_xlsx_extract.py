"""XLSX-ORCH-11 / dms#31 — extract resulting xlsx into the artifact store.

Fixture is built here (openpyxl Workbook.save in tests/ only — same precedent
as test_answer_oracle.py). The check must run on any machine and never skip
(R-0002). Production packages still must not author a workbook (hard rule 5).

Does not require Pointer, Excel, Copilot, MCP, Postgres, or network.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

try:
    from openpyxl import Workbook
except ImportError as exc:  # named fail, never skip
    raise AssertionError(f"openpyxl required for xlsx extract gate: {exc}") from exc

from dms_executor.xlsx_extract import extract_resulting_xlsx, get_artifact


def _write_workbook(path: Path, sheet_names: list[str]) -> Path:
    wb = Workbook()
    first = wb.active
    assert first is not None
    first.title = sheet_names[0]
    first.append(["fixture", "col"])
    first.append([1, "a"])
    for name in sheet_names[1:]:
        ws = wb.create_sheet(name)
        ws.append(["fixture", "col"])
        ws.append([1, "a"])
    wb.save(path)
    return path


def test_extract_returns_durable_byte_faithful_id_and_later_read(
    tmp_path: Path,
) -> None:
    src = _write_workbook(
        tmp_path / "frtr_result.xlsx",
        ["Cover", "OnTime Export", "OnTime Analysis", "Presentation Chart"],
    )
    raw = src.read_bytes()
    want = hashlib.sha256(raw).hexdigest()
    space_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    out = extract_resulting_xlsx(
        src, space_id=space_id, root=tmp_path, database_url=""
    )

    assert out["id"]
    assert out["path"]
    assert out["kind"] == "xlsx_result"
    assert out["sha256"] == want
    stored = Path(out["path"]).read_bytes()
    assert hashlib.sha256(stored).hexdigest() == want
    assert stored == raw
    assert out["complete"] is True
    assert out["missing_families"] == []
    assert "cover" in out["present_families"]
    assert "ontime_export" in out["present_families"]
    assert "analysis" in out["present_families"]
    assert "presentation_chart" in out["present_families"]

    later = get_artifact(out["id"], root=tmp_path)
    assert later is not None
    assert later["id"] == out["id"]
    assert later["path"] == out["path"]
    assert Path(later["path"]).is_file()
    assert hashlib.sha256(Path(later["path"]).read_bytes()).hexdigest() == want


def test_missing_sheet_families_reported_incomplete_not_green(tmp_path: Path) -> None:
    src = _write_workbook(tmp_path / "partial.xlsx", ["Cover"])
    out = extract_resulting_xlsx(
        src,
        space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        root=tmp_path,
        database_url="",
    )
    assert out["complete"] is False
    assert "ontime_export" in out["missing_families"]
    assert "analysis" in out["missing_families"]
    assert "presentation_chart" in out["missing_families"]
    assert "cover" in out["present_families"]
    later = get_artifact(out["id"], root=tmp_path)
    assert later is not None
    assert later["complete"] is False


def test_truncated_store_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write_workbook(
        tmp_path / "full.xlsx",
        ["Cover", "OnTime Export", "OnTime Analysis", "Presentation Chart"],
    )

    def _truncate(key: str, data: bytes, *, root: Path) -> str:
        dest = root / "blobs" / "sha256" / key[:2] / key[2:4] / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data[:40])
        return str(dest.as_posix())

    monkeypatch.setattr("dms_executor.xlsx_extract._blob_put", _truncate)
    with pytest.raises(RuntimeError, match="store_truncated"):
        extract_resulting_xlsx(
            src,
            space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            root=tmp_path,
            database_url="",
        )
