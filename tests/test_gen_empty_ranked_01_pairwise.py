"""Pairwise with RANK-WINDOW-01 (#392).

Skip only when ``cortex_client.qualifiers.rank_window_shape_reason`` cannot
be imported. That name is the #392 shape check. On a head that has it, every
case runs: if the rank-window lane abstains, the generate_empty ranked
fallback must not serve the question.

``rank_window_ask`` is forced to None so the check is the fallback, not the
rank-window abstain itself. A missing ``rank_window_ask`` while the shape
symbol imports is a failure, not a second skip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_gen_empty_ranked_01 import _blob, _executor

_SYMBOL = "cortex_client.qualifiers.rank_window_shape_reason"
_SKIP_REASON = (
    f"{_SYMBOL} cannot be imported; RANK-WINDOW-01 (#392) is not on this head"
)

try:
    from cortex_client.qualifiers import rank_window_shape_reason as _shape_reason
except ImportError:
    _shape_reason = None

pytestmark = pytest.mark.skipif(_shape_reason is None, reason=_SKIP_REASON)

# Stray-digit and window phrasings from #392 tests
# test_rank_window_02_trailing_filter.py and test_rank_window_03_stray_digit.py.
_STEM = "excluding top 3, next 5 skus by revenue"
_PHRASINGS = (
    "excluding top 3, next 5 skus by revenue at WH-B",
    "excluding top 3, next 5 skus by revenue for chemicals",
    "ranks 4-8 skus by revenue at garaj",
    "excluding top 3, next 5 skus by lowest revenue",
    f"{_STEM} at garaj",
    f"{_STEM} for RAW",
    f"{_STEM} excluding chemicals",
    f"{_STEM} in kuala lumpur",
    f"{_STEM} in Q3",
    f"{_STEM} only RAW",
    f"{_STEM} at site B",
    f"{_STEM} from SUP-01",
    f"{_STEM} with low stock",
    f"{_STEM} ascending",
    f"{_STEM} near the port",
    f"{_STEM} last month",
    f"{_STEM} among imported items",
    f"{_STEM} please",
    f"{_STEM} that are perishable",
    "ranks 4-8 skus by sales above quota",
    "excluding top 3, next 5 skus by turnover before tax",
    "excluding top 3, next 5 skus by kg despite returns",
    "excluding top 3, next 5 skus by quantity sold indoors",
    f"{_STEM} xyzzyplugh",
    "ranks 6-10 skus by revenue during the monsoon",
    "excluding top 3, next 5 skus by asc revenue",
    "excluding top 3, next 5 skus by bottom revenue",
    "excluding top 3, next 5 skus by least revenue",
    "excluding top 3, next 5 skus by smallest revenue",
    "excluding top 3, next 5 skus by worst revenue",
    "excluding top 3 and sku 10023, next 5 skus by revenue",
    "excluding top 3 and 10023, next 5 skus by revenue",
    "excluding top 3 and sku 1001 and sku 1002, next 5 skus by revenue",
    "excluding top 3 and sku-03, next 5 skus by revenue",
    "ranks 4-8 skus by revenue, 3 and 7 only",
    "ranks 4-8 skus by revenue, 2 only",
    "excluding top 3, next 5 skus from 3 by revenue",
    "next 5 skus by revenue excluding 3",
    "top 5 skus by revenue, not 10023",
    "excluding top 3, next 5 skus by revenue, and 10023",
    "what are the ranks 4-8 skus by sales, for 88",
    "excluding top 3, next 5 skus by revenue, from 42 please",
    "ranks 6 to 10 skus by turnover, with 7 only",
    "excluding top 3, next 5 skus by quantity sold, to 15",
    "after the top 3, the next 5 skus by revenue, only 1001",
    "positions 4 to 8 skus by revenue, for 6",
    "excluding top 3, next 5 skus by kg, and 03",
    "skus ranked 6 to 10 by sales, and 202",
    "excluding top 3, show the next 5 skus by revenue, from 4",
)


@pytest.mark.parametrize("question", _PHRASINGS)
def test_ranked_fallback_does_not_serve_when_rank_window_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    assert _shape_reason is not None
    reason = _shape_reason(question)
    assert reason is not None, question
    import dms_executor.generative_ask as gen_ask

    assert hasattr(gen_ask, "rank_window_ask"), (
        f"{_SYMBOL} imported but dms_executor.generative_ask.rank_window_ask is missing"
    )
    monkeypatch.setattr(gen_ask, "rank_window_ask", lambda *_a, **_k: None)

    env: dict[str, Any] = _executor(tmp_path).live_ask(
        question, session_id="pairwise", ask_path="generative"
    )
    blob = _blob(env)
    assert "fallback:generate_empty" in blob, blob
    assert env["abstained"] is True, blob
    assert env["badge"] != "L2_VALIDATED"
    assert env["rows"] == []
    assert env.get("sql_used") in (None, "")
