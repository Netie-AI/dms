"""Airflow connection id to a DMS client. The token is the connection password.

No secret is written in this module. Without Airflow installed, ``get_connection``
raises and tests pass a stand-in.
"""

from __future__ import annotations

from typing import Any

from dms_sdk.client import DmsClient

try:
    from airflow.hooks.base import BaseHook
except ImportError:  # pragma: no cover - CI does not install the optional extra
    BaseHook = object  # type: ignore[misc, assignment]


def base_url_from_conn(conn: Any) -> str:
    scheme = getattr(conn, "schema", None) or "https"
    host = getattr(conn, "host", None) or ""
    port = getattr(conn, "port", None)
    if not host:
        raise ValueError("connection_host_missing")
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


class DmsHook(BaseHook):
    conn_name_attr = "conn_id"
    default_conn_name = "dms_default"
    conn_type = "dms"
    hook_name = "DMS"

    def __init__(self, conn_id: str) -> None:
        self.conn_id = conn_id
        if BaseHook is not object:
            super().__init__()

    def get_connection(self) -> Any:
        if BaseHook is object:
            raise RuntimeError("airflow_connection_unavailable")
        return BaseHook.get_connection(self.conn_id)

    def get_client(self) -> DmsClient:
        conn = self.get_connection()
        password = getattr(conn, "password", None) or None
        return DmsClient(base_url=base_url_from_conn(conn), token=password)
