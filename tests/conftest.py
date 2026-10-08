"""Opt existing ranking-serve tests into the ontology lane.

Product default for ``DMS_LANE_ONTOLOGY_RANKED`` is off. C-LOOP-B tests that
prove the off path set the env to 0 themselves.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ontology_ranked_lane_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "1")
