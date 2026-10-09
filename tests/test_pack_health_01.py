"""A scoring file on disk is not the pack.

Health stays absent, and the ten product metrics still answer, whether or
not a scoring directory exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q
from dms_executor.envelope import assert_envelope_valid
from test_boot_crash_386 import _client, _Cortex, _flags_off

_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_health_stays_absent_and_base_metric_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import demo_pack

    assert demo_pack.curated_pack_present() is False
    assert demo_pack.curated_pack_status().name == "absent"
    assert demo_pack.curated_pack_status().error_class is None
    assert demo_pack.score_pack_exact_metrics() == ()
    _flags_off(monkeypatch)
    client, exe = _client(_Cortex(), tmp_path / "wh.duckdb")
    try:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["gen_path_climb"]["pack"] == "absent"
        served = client.post(
            "/v1/chat/ask",
            json={
                "question": SPEND_BY_COUNTRY_Q,
                "space_id": _SPACE,
                "session_id": "ses_base",
            },
        )
        assert served.status_code == 200, served.text
        body = served.json()
        assert_envelope_valid(body)
        assert body["badge"] == "L1_GOVERNED_METRIC"
        assert body["abstained"] is False
    finally:
        exe.close()
