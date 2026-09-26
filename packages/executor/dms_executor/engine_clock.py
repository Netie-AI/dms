"""ORACLE-FIX-02 (dms#308) — the answer engine's date, read where the answer ran.

An answer whose SQL reads the clock (``CURRENT_DATE`` and friends) is only
judgeable against an oracle evaluated at the same date. The harness clock is
not that date: prove can run in UTC while the harness runs in MYT. So the date
is read through the same ``submit`` that ran the answer SQL, once before and
once after it, and stamped on the envelope as ``engine_clock``.

The same holds for SQL that carries an ISO date literal: the ask compiler
inlines "today" from the DMS host clock for expiry and audit cutoffs, so the
judge needs the engine's date to evaluate the oracle at. A host date that
differs from the engine date then shows as a row mismatch, not a hidden one.

No clock and no date literal in the SQL: no probe, no stamp. A probe that
fails is stamped ``status=unavailable`` with the reason, never a harness-side
date.

Swap: a Cortex submit result that carries the engine date itself replaces the
two probes; the envelope field stays the same.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from dms_executor.verified_queries import rows_from_submit_result

ENGINE_CLOCK_KEY = "engine_clock"
NOTE_ENGINE_CLOCK_UNAVAILABLE = "engine_clock:unavailable"

CLOCK_SQL_RE = re.compile(
    r"\b(current_date|current_timestamp|current_time|now\s*\(|today\s*\(|"
    r"get_current_timestamp\s*\(|localtimestamp|localtime)",
    re.I,
)
_DATE_LITERAL_RE = re.compile(r"'\d{4}-\d{2}-\d{2}")
PROBE_SQL = (
    "SELECT CAST(CURRENT_DATE AS VARCHAR) AS engine_date, "
    "current_setting('TimeZone') AS engine_timezone"
)


def sql_reads_clock(sql: str | None) -> bool:
    return bool(CLOCK_SQL_RE.search(sql or ""))


def sql_needs_engine_date(sql: str | None) -> bool:
    """Clock call or ISO date literal: the answer is only judgeable at a date."""
    return sql_reads_clock(sql) or bool(_DATE_LITERAL_RE.search(sql or ""))


def _probe(submit: Callable[[str], Any]) -> tuple[str | None, str | None, str | None]:
    """(date, timezone, error). Never raises."""
    try:
        result = submit(PROBE_SQL)
    except Exception as exc:  # noqa: BLE001 - stamped, not raised
        return None, None, f"{type(exc).__name__}"
    if getattr(result, "ok", True) is False:
        return None, None, f"probe_status:{getattr(result, 'status', 'not_ok')}"
    rows = rows_from_submit_result(result)
    if not rows:
        return None, None, "probe_no_rows"
    day = str(rows[0].get("engine_date") or "").strip()[:10]
    tz = str(rows[0].get("engine_timezone") or "").strip() or None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None, tz, "probe_date_unparseable"
    return day, tz, None


class EngineClock:
    """Wraps one ask's ``submit``. Probes around each date-dependent answer SQL."""

    def __init__(self, submit: Callable[[str], Any]) -> None:
        self._submit = submit
        self.stamp: dict[str, Any] | None = None

    def submit(self, sql: str) -> Any:
        if not sql_needs_engine_date(sql):
            return self._submit(sql)
        before, tz, err_b = _probe(self._submit)
        result = self._submit(sql)
        after, tz_after, err_a = _probe(self._submit)
        err = err_b or err_a
        if err:
            self.stamp = {"status": "unavailable", "reason": err}
        else:
            self.stamp = {
                "status": "ok",
                "date_before": before,
                "date_after": after,
                "timezone": tz or tz_after,
                "source": "answer_submit",
            }
        return result

    def apply(self, env: dict[str, Any] | None) -> dict[str, Any] | None:
        """Stamp ``engine_clock`` on an answer envelope. Abstentions carry none."""
        if env is None or self.stamp is None or env.get("abstained"):
            return env
        env[ENGINE_CLOCK_KEY] = dict(self.stamp)
        if self.stamp.get("status") != "ok":
            notes = list(env.get("assumptions") or [])
            notes.append(f"{NOTE_ENGINE_CLOCK_UNAVAILABLE}:{self.stamp.get('reason')}")
            env["assumptions"] = notes
        return env


__all__ = [
    "ENGINE_CLOCK_KEY",
    "NOTE_ENGINE_CLOCK_UNAVAILABLE",
    "PROBE_SQL",
    "EngineClock",
    "sql_needs_engine_date",
    "sql_reads_clock",
]
