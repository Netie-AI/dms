"""Plan E gate-coverage audit: tests that show an answer path skipping a gate.

Each test in this folder is expected to FAIL on the commit it was written
against. A failure is the proof that the gap exists; a pass means the gap
closed. Every test carries ``@pytest.mark.gate_gap`` naming the matrix row,
the gate, and the ticket that tracks it ("new" when PRD has not routed it).

Select them with ``pytest -m gate_gap tests/gate_matrix``. The matrix itself
is docs/subagents_findings/gate-matrix.md.

Write them with ``_harness.gap`` (strict xfail on AssertionError only) and put
the sibling request on an already-gated path behind ``_harness.control``. See
the ``_harness`` module docstring for the recipes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# `from tests.x import` is shadowed by a site-packages package named `tests`, so
# sibling modules are imported by bare name, the way tests/test_bronze_grant_01.py does.
HARNESS_DIR = Path(__file__).resolve().parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))


def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "gate_gap(row, gate, ticket, gap_id): Plan E gap test - fails while the "
        "answer path at `row` skips `gate`; `ticket` tracks it; `gap_id` names it",
    )


@pytest.fixture(autouse=True, scope="module")
def _offline_import_guard(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Make ``import dms_api`` safe for every test in this folder.

    ``dms_api/__init__`` imports ``dms_api.app``, which builds a module-level app
    and runs ``Executor.startup()``: it seeds the default warehouse under the repo
    and probes localhost:5000 for an OpenVault, fetching a signing key if one
    answers. Pin both before any test body can import it.
    """
    import dms_executor

    mp = pytest.MonkeyPatch()
    mp.setenv("DMS_WAREHOUSE_DB", str(tmp_path_factory.mktemp("gm_import") / "import_guard.duckdb"))
    mp.setattr(dms_executor, "probe_openvault", lambda **_k: (None, ""))
    yield
    mp.undo()


@pytest.fixture()
def harness_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., object]]:
    """``harness_factory(**kw)`` builds a harness; every one built is closed."""
    from _harness import build_harness

    built = []

    def make(**kw):  # type: ignore[no-untyped-def]
        h = build_harness(tmp_path, monkeypatch, **kw)
        built.append(h)
        return h

    yield make
    for h in built:
        h.close()


@pytest.fixture()
def harness(harness_factory):  # type: ignore[no-untyped-def]
    """Default harness: live ask, fallback off, recording Cortex, seeded tmp warehouse."""
    return harness_factory()
