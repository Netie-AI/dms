"""UX-TRUST-01. Trust reads score files. Studio polls answer. Ontology counts return.

No serving change: DMS_ASK_STREAM and DMS_STUDIO_ADJUST stay off. Flag off, the
ask body is covered in test_stream_stages_01. This file checks the three pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.control_plane.onto_store import PostgresOntologyStore
from fastapi.testclient import TestClient

_SPACE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_ASK = "ux-trust-ask-0001"


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    get_settings.cache_clear()
    return TestClient(create_app())


def _fail_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(base_url: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "source": "cortex",
            "url": path,
            "error": "http_404",
            "hint": '{"detail":"Not Found"}',
            "data": None,
        }

    monkeypatch.setattr("dms_api.routes.trust.cortex_get", fake_get)


def _isolate(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(folder))
    monkeypatch.setenv("DMS_GRADE52_DIR", str(folder))


def test_no_artifact_is_an_empty_state_not_raw_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    _fail_engine(monkeypatch)
    body = _client(monkeypatch).get("/v1/trust/summary").json()
    assert body["ok"] is True
    assert body["empty"] is True
    assert body["empty_title"] == "No benchmark run yet"
    assert "grade52.py" in body["how_to_run"]
    assert body["claim"]["supported"] is False
    assert "confidently_wrong" not in body["claim"]
    assert body["claim"]["statement"] == "Not measured yet"
    assert "0" not in body["claim"]["statement"]
    blob = json.dumps(body)
    assert '{"detail"' not in blob
    assert "Not Found" not in blob


def test_summary_copies_counts_from_the_file_and_does_not_pass_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    _fail_engine(monkeypatch)
    (tmp_path / "grade52.json").write_text(
        json.dumps(
            {
                "mode": "stub",
                "run": "fixture",
                "pack": "fixture",
                "wrong": 1,
                "correct": 2,
                "n": 4,
                "abstain": 1,
                "cases": [
                    {
                        "id": "c1",
                        "bucket": "wrong",
                        "question": "do not show this question",
                        "sql": "SELECT 1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = _client(monkeypatch)
    body = client.get("/v1/trust/summary").json()
    assert body["empty"] is False
    assert body["claim"]["supported"] is False
    assert body["claim"]["confidently_wrong"] == 1
    assert body["claim"]["corpus_n"] == 4
    corpus = body["runs"]["corpus"]
    assert corpus["file"] == "grade52.json"
    assert corpus["totals"] == {"total": 4, "correct": 2, "wrong": 1, "abstain": 1}
    assert corpus["rates"]["correct"] == 0.5
    assert corpus["rates"]["abstain"] == 0.25
    assert corpus["passed"] is None
    assert "Not Found" not in json.dumps(body)
    detail = client.get("/v1/trust/runs/corpus").json()
    assert detail["ok"] is True
    assert detail["items"] == [{"id": "c1", "bucket": "wrong", "outcome": "wrong"}]
    blob = json.dumps(detail)
    assert "do not show this question" not in blob
    assert "SELECT" not in blob


def test_a_file_without_counts_does_not_become_a_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    _fail_engine(monkeypatch)
    (tmp_path / "score_bird.json").write_text('{"kind":"note"}', encoding="utf-8")
    body = _client(monkeypatch).get("/v1/trust/summary").json()
    assert body["empty"] is True
    assert body["runs"] == {}


def test_missing_wrong_is_not_reported_as_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file that never stored WRONG must not become a clean zero."""
    _isolate(monkeypatch, tmp_path)
    _fail_engine(monkeypatch)
    (tmp_path / "grade52.json").write_text(
        json.dumps({"mode": "stub", "correct": 2, "n": 4, "abstain": 1}),
        encoding="utf-8",
    )
    body = _client(monkeypatch).get("/v1/trust/summary").json()
    totals = body["runs"]["corpus"]["totals"]
    assert "wrong" not in totals
    assert "confidently_wrong" not in body["claim"]
    assert body["claim"]["statement"] == "Not measured yet"
    assert "0" not in body["claim"]["statement"]
    assert "0 wrong" not in json.dumps(body["claim"])


def test_flag_off_events_and_adjust_status_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    events = client.get(f"/v1/studio/ask/{_ASK}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert '"enabled":false' in events.text
    assert "stage" not in events.text
    status = client.get("/v1/studio/adjust/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": False}
    assert client.post("/v1/studio/adjust/run", json={}).status_code == 404
    assert client.get("/v1/studio/ask/nope/events").status_code == 404


def test_ontology_counts_are_ints_on_one_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _client(monkeypatch).get(f"/v1/ontology?space_id={_SPACE}").json()
    assert body["ok"] is False
    assert body["reason"] == "empty_store"
    counts = body["counts"]
    for key in (
        "object_types",
        "link_types",
        "action_tools",
        "action_events",
        "functions",
        "sensitive_properties",
    ):
        assert type(counts[key]) is int
        assert counts[key] == 0


def test_ontology_store_connect_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_connect(conninfo: str, **kwargs: Any) -> None:
        seen["kwargs"] = kwargs
        raise OSError("down")

    monkeypatch.setattr(
        "dms_core.control_plane.onto_store.psycopg.connect", fake_connect
    )
    store = PostgresOntologyStore(
        "postgresql://127.0.0.1/none",
        tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )
    with pytest.raises(OSError):
        store._conn()
    assert seen["kwargs"]["connect_timeout"] == 3


def test_ui_empty_states_are_in_the_pages() -> None:
    root = Path(__file__).resolve().parents[1]
    trust = (root / "apps/ui/src/pages/TrustPage.tsx").read_text(encoding="utf-8")
    onto = (root / "apps/ui/src/pages/OntologyPage.tsx").read_text(encoding="utf-8")
    api = (root / "apps/ui/src/lib/api.ts").read_text(encoding="utf-8")
    assert 'data-testid="trust-empty"' in trust
    assert 'data-testid="trust-unmeasured"' in trust
    assert "No benchmark run yet" in trust
    assert "Not measured yet" in trust
    assert "wrongCountLabel(" in trust
    assert "0 wrong" not in trust
    assert "totals.wrong ?? 0" not in trust
    assert "publicTrustHint(" in trust
    assert 'data-testid="ontology-counts"' in onto
    assert 'data-testid="ontology-loading"' in onto
    assert 'data-testid="ontology-retry"' in onto
    assert "ontologyHeldView(" in onto
    assert "ontologyCountsMeasured(" in onto
    assert "ontologyBundle(" in api
    assert 'ontologyReadPath("objects"' not in api
