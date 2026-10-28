"""PACK-HEALTH-01 follow-up: a cached pack drops when the file changes or goes away.

A good load used to stay cached until restart. After the files were deleted,
asks kept serving the old pack while /health said ``absent``. After an edit,
asks kept serving the old phrases. The cache is keyed on each file's
modification time and size, and is dropped when either changes or a file is
gone. An unchanged pack stays cached and is not parsed again.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from dms_executor.envelope import assert_envelope_valid
from test_boot_crash_386 import _client, _Cortex, _flags_off

_REAL = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "curated_ceo"
_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _clear() -> None:
    from dms_executor import demo_pack

    demo_pack.score_pack_exact_metrics.cache_clear()
    demo_pack.curated_l0_question_norms.cache_clear()


def _install_real(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("questions.yaml", "oracles.yaml"):
        shutil.copyfile(_REAL / name, root / name)


def _allowlisted_id() -> str:
    from dms_executor import demo_pack

    return sorted(demo_pack.SCORE_PACK_EXACT_IDS)[0]


def _write_synthetic(root: Path, question: str, *, mtime_ns: int | None = None) -> None:
    """One allowlisted id with a synthetic phrase. Not a real pack question."""
    qid = _allowlisted_id()
    root.mkdir(parents=True, exist_ok=True)
    (root / "questions.yaml").write_text(
        f"questions:\n  - id: {qid}\n    question: \"{question}\"\n    expect: l0\n",
        encoding="utf-8",
    )
    (root / "oracles.yaml").write_text(
        f"oracles:\n  {qid}:\n    sql: SELECT COUNT(*) AS n FROM inventory\n",
        encoding="utf-8",
    )
    if mtime_ns is not None:
        for name in ("questions.yaml", "oracles.yaml"):
            os.utime(root / name, ns=(mtime_ns, mtime_ns))


def _questions(metrics: tuple[object, ...]) -> set[str]:
    return {m.question for m in metrics}  # type: ignore[attr-defined]


def test_deleted_pack_is_not_served_and_health_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    _install_real(root)
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    _flags_off(monkeypatch)
    client, exe = _client(_Cortex(), tmp_path / "wh.duckdb")
    try:
        loaded = demo_pack.score_pack_exact_metrics()
        assert loaded
        metric = loaded[0]
        ask = {"question": metric.question, "space_id": _SPACE}
        first = client.post("/v1/chat/ask", json={**ask, "session_id": "ses_before"})
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert_envelope_valid(first_body)
        assert first_body["badge"] == "L1_GOVERNED_METRIC"
        assert client.get("/health").json()["gen_path_climb"]["pack"] == "curated_ceo"

        shutil.rmtree(root)

        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["gen_path_climb"]["pack"] == "absent"
        after = client.post("/v1/chat/ask", json={**ask, "session_id": "ses_after"})
        assert after.status_code == 200, after.text
        after_body = after.json()
        assert_envelope_valid(after_body)
        assert after_body.get("sql_used") != metric.sql
        assert f"governed metric {metric.metric_id}" not in (after_body.get("assumptions") or [])

        assert demo_pack.score_pack_exact_metrics() == ()
        assert demo_pack.curated_l0_question_norms() == frozenset()
        assert demo_pack.match_pack_phrase(metric.question) is None
    finally:
        exe.close()
        _clear()


def test_edited_pack_is_reloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    try:
        _write_synthetic(root, "synthetic probe one", mtime_ns=1_000_000_000_000_000_000)
        assert _questions(demo_pack.score_pack_exact_metrics()) == {"synthetic probe one"}
        assert demo_pack.match_pack_phrase("synthetic probe one") is not None

        _write_synthetic(root, "synthetic probe number two", mtime_ns=1_000_000_002_000_000_000)
        assert _questions(demo_pack.score_pack_exact_metrics()) == {"synthetic probe number two"}
        assert demo_pack.match_pack_phrase("synthetic probe one") is None
        assert demo_pack.match_pack_phrase("synthetic probe number two") is not None
        assert demo_pack.curated_l0_question_norms() == frozenset({"synthetic probe number two"})
    finally:
        _clear()


def test_same_size_edit_with_new_mtime_is_reloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    try:
        _write_synthetic(root, "synthetic probe aaaa", mtime_ns=1_000_000_000_000_000_000)
        size = (root / "questions.yaml").stat().st_size
        assert _questions(demo_pack.score_pack_exact_metrics()) == {"synthetic probe aaaa"}

        _write_synthetic(root, "synthetic probe bbbb", mtime_ns=1_000_000_002_000_000_000)
        assert (root / "questions.yaml").stat().st_size == size
        assert _questions(demo_pack.score_pack_exact_metrics()) == {"synthetic probe bbbb"}
        assert demo_pack.curated_l0_question_norms() == frozenset({"synthetic probe bbbb"})
    finally:
        _clear()


def test_unchanged_pack_stays_cached_without_a_second_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    _install_real(root)
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    parses: list[Path] = []
    real_read = demo_pack._read_pack_file

    def _counting(path: Path, require: object) -> object:
        parses.append(path)
        return real_read(path, require)  # type: ignore[arg-type]

    monkeypatch.setattr(demo_pack, "_read_pack_file", _counting)
    try:
        first = demo_pack.score_pack_exact_metrics()
        norms = demo_pack.curated_l0_question_norms()
        assert first
        assert norms
        seen = len(parses)
        assert seen > 0
        for _ in range(3):
            assert demo_pack.score_pack_exact_metrics() is first
            assert demo_pack.curated_l0_question_norms() is norms
        assert len(parses) == seen
    finally:
        _clear()
