"""PACK-HEALTH-01: an unreadable pack is not a loaded pack, and a miss is not cached.

An on-disk file that cannot be read (invalid YAML, wrong shape, permission
error) used to make ``/health`` say ``curated_ceo`` and ``POST /v1/chat/ask``
return 503. It is an empty pack, same as a missing one. ``/health`` says
``unreadable`` and names the exception class, not the file bytes.

An empty, absent, or unreadable lookup is not cached. The next lookup sees a
pack that appears or is fixed. A successful non-empty load stays cached while
the files keep the same modification time and size.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q
from dms_executor.envelope import assert_envelope_valid
from test_boot_crash_386 import _client, _Cortex, _flags_off

_REAL = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "curated_ceo"
_SENTINEL = "PACK_HEALTH_SENTINEL_do_not_leak"
_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_FILE_QUESTION = "How many SKUs do we have in inventory?"


def _clear() -> None:
    from dms_executor import demo_pack

    demo_pack.score_pack_exact_metrics.cache_clear()
    demo_pack.curated_l0_question_norms.cache_clear()


def _install_real(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("questions.yaml", "oracles.yaml"):
        shutil.copyfile(_REAL / name, root / name)


def _yaml_error_name(text: str) -> str:
    try:
        yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - the class under test
        return type(exc).__name__
    raise AssertionError("yaml sample was readable")


def _write_unreadable(root: Path, kind: str) -> str:
    root.mkdir(parents=True, exist_ok=True)
    if kind == "yaml":
        text = f"questions: [{_SENTINEL}\n"
        (root / "questions.yaml").write_text(text, encoding="utf-8")
        (root / "oracles.yaml").write_text("{}\n", encoding="utf-8")
        return _yaml_error_name(text)
    if kind == "shape":
        (root / "questions.yaml").write_text(f"- {_SENTINEL}\n", encoding="utf-8")
        (root / "oracles.yaml").write_text("{}\n", encoding="utf-8")
        return "ValueError"
    (root / "questions.yaml").write_text(f"{_SENTINEL}: [\n", encoding="utf-8")
    (root / "oracles.yaml").write_text("{}\n", encoding="utf-8")
    return "PermissionError"


def _deny_pack_reads(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    real = Path.read_text

    def _read(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.is_relative_to(root):
            raise PermissionError(_SENTINEL)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read)


def _ask(client: Any, question: str, session_id: str) -> Any:
    return client.post(
        "/v1/chat/ask",
        json={"question": question, "space_id": _SPACE, "session_id": session_id},
    )


@pytest.mark.parametrize("kind", ["yaml", "shape", "permission"])
def test_unreadable_pack_health_and_ask_are_not_503(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    expected = _write_unreadable(root, kind)
    if kind == "permission":
        _deny_pack_reads(monkeypatch, root)
    _flags_off(monkeypatch)
    client, exe = _client(_Cortex(), tmp_path / "wh.duckdb")
    try:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        climb = health.json()["gen_path_climb"]
        assert climb["pack"] == "unreadable", climb
        assert climb["pack_error"] == expected, climb
        assert climb["pack_error"].isidentifier()
        assert _SENTINEL not in health.text

        filed = _ask(client, _FILE_QUESTION, f"ses_file_{kind}")
        assert filed.status_code == 200, filed.text
        body = filed.json()
        assert_envelope_valid(body)
        assert body["badge"] == "ABSTAIN"
        assert body["abstained"] is True
        assert "demo_pack_unavailable" not in filed.text
        assert _SENTINEL not in filed.text

        served = _ask(client, SPEND_BY_COUNTRY_Q, f"ses_base_{kind}")
        assert served.status_code == 200, served.text
        served_body = served.json()
        assert_envelope_valid(served_body)
        assert served_body["badge"] == "L1_GOVERNED_METRIC"
        assert served_body["abstained"] is False
        assert _SENTINEL not in served.text
    finally:
        exe.close()
        _clear()


@pytest.mark.parametrize("kind", ["absent", "empty", "unreadable"])
def test_miss_is_not_cached_and_a_later_pack_is_picked_up(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    try:
        if kind == "empty":
            root.mkdir()
            (root / "questions.yaml").write_text("{}\n", encoding="utf-8")
            (root / "oracles.yaml").write_text("{}\n", encoding="utf-8")
        elif kind == "unreadable":
            root.mkdir()
            (root / "questions.yaml").write_text("questions: [\n", encoding="utf-8")
            (root / "oracles.yaml").write_text("{}\n", encoding="utf-8")
        assert demo_pack.score_pack_exact_metrics() == ()
        assert demo_pack.curated_l0_question_norms() == frozenset()
        _install_real(root)
        loaded = demo_pack.score_pack_exact_metrics()
        assert loaded
        assert loaded == demo_pack.load_score_pack_metrics()
        assert demo_pack.curated_l0_question_norms()
        assert demo_pack.curated_pack_status().name == "curated_ceo"
        assert demo_pack.curated_pack_status().error_class is None
    finally:
        _clear()


def test_status_distinguishes_absent_empty_and_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: root)
    try:
        assert demo_pack.curated_pack_status().name == "absent"
        assert demo_pack.curated_pack_status().error_class is None
        root.mkdir()
        (root / "questions.yaml").write_text("{}\n", encoding="utf-8")
        (root / "oracles.yaml").write_text("{}\n", encoding="utf-8")
        assert demo_pack.curated_pack_status().name == "curated_ceo"
        assert demo_pack.curated_pack_status().error_class is None
        (root / "questions.yaml").write_text("questions: [\n", encoding="utf-8")
        status = demo_pack.curated_pack_status()
        assert status.name == "unreadable"
        assert status.error_class
        assert status.error_class.isidentifier()
    finally:
        _clear()


def test_valid_pack_caches_and_still_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged files serve from the cache. An edit serves the new SQL.

    This test exists only on this PR. It used to expect the old cached SQL
    after the pack dir disappeared. That was the stale behaviour.
    """
    from dms_executor import demo_pack

    _clear()
    root = tmp_path / "pack"
    _install_real(root)
    for name in ("questions.yaml", "oracles.yaml"):
        os.utime(root / name, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
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
        assert demo_pack.score_pack_exact_metrics() is first
        assert demo_pack.curated_l0_question_norms() is norms
        assert len(parses) == seen
        metric = first[0]
        hit = demo_pack.lookup_pack_metric(metric.question, grantable=set(metric.tables))
        assert hit is not None
        assert hit.metric_id == metric.metric_id
        assert hit.sql == metric.sql
        assert len(parses) == seen

        doc = yaml.safe_load((root / "oracles.yaml").read_text(encoding="utf-8"))
        block = doc["oracles"][metric.metric_id]
        block["sql"] = str(block["sql"]).rstrip() + " AND 1 = 1"
        (root / "oracles.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        os.utime(root / "oracles.yaml", ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        edited = demo_pack.lookup_pack_metric(metric.question, grantable=set(metric.tables))
        assert edited is not None
        assert edited.metric_id == metric.metric_id
        assert edited.sql != metric.sql
        assert edited.sql.endswith("AND 1 = 1")
        assert len(parses) > seen
    finally:
        _clear()
