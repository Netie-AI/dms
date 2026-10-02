"""Plan E gate-coverage audit: tests that show an answer path skipping a gate.

Each test in this folder is expected to FAIL on the commit it was written
against. A failure is the proof that the gap exists; a pass means the gap
closed. Every test carries ``@pytest.mark.gate_gap`` naming the matrix row,
the gate, and the ticket that tracks it ("new" when PRD has not routed it).

Select them with ``pytest -m gate_gap tests/gate_matrix``. The matrix itself
is docs/subagents_findings/gate-matrix.md.
"""

from __future__ import annotations


def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "gate_gap(row, gate, ticket): Plan E gap test - fails while the answer "
        "path at `row` skips `gate`; `ticket` tracks it",
    )
