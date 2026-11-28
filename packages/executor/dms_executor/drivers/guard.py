"""Run the connect allow-list before a driver reads."""

from __future__ import annotations

from dms_executor.drivers import DriverError


def refuse(kind: str, sql: str, grantable: set[str]) -> None:
    """Raise ``DriverError`` when ``sql`` is not one granted read."""
    from dms_executor.connectors import ConnectQueryError, guarded_reason

    try:
        reason = guarded_reason(sql, grantable=set(grantable), kind=kind)
    except ConnectQueryError as exc:
        raise DriverError(exc.code) from None
    if reason:
        raise DriverError(reason[:80])
