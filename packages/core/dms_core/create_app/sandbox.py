"""Run a generated backend with a stripped environment and a host allowlist.

The child sees the data-client URL, its token, and optionally the OpenVault
URL. Database URLs, provider keys, and the parent environment are not copied.
Sockets to any other host fail closed before a connection is opened.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dms_core.create_app.connectors import dry_run_action

_KEEP_ENV = ("PATH", "LANG", "LC_ALL", "TZ")
QueryFn = Callable[[str], dict[str, Any]]


def host_of(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname
    return host if isinstance(host, str) and host else None


def child_env(*, data_url: str, token: str, ov_url: str | None, pythonpath: str) -> dict[str, str]:
    """The only environment the sandbox process receives."""
    env: dict[str, str] = {}
    for key in _KEEP_ENV:
        value = os.environ.get(key)
        if isinstance(value, str) and value:
            env[key] = value
    env["PYTHONPATH"] = pythonpath
    env["PYTHONUNBUFFERED"] = "1"
    env["DMS_APP_DATA_URL"] = data_url
    env["DMS_APP_TOKEN"] = token
    if ov_url:
        env["OPENVAULT_URL"] = ov_url
    return env


def write_guard(dest: Path, allow_hosts: set[str]) -> None:
    hosts = ", ".join(repr(host) for host in sorted(allow_hosts))
    text = (
        '"""Block sockets that are not the DMS data client or OpenVault."""\n'
        "import socket\n"
        f"_ALLOW = frozenset({{{hosts}}})\n"
        "_create = socket.create_connection\n"
        "_connect = socket.socket.connect\n"
        "\n"
        "def _host(address):\n"
        "    if isinstance(address, tuple) and address:\n"
        "        return str(address[0])\n"
        "    return str(address)\n"
        "\n"
        "def _guarded_create(address, *args, **kwargs):\n"
        "    if _host(address) not in _ALLOW:\n"
        "        raise OSError('sandbox_network_denied')\n"
        "    return _create(address, *args, **kwargs)\n"
        "\n"
        "def _guarded_connect(self, address):\n"
        "    if _host(address) not in _ALLOW:\n"
        "        raise OSError('sandbox_network_denied')\n"
        "    return _connect(self, address)\n"
        "\n"
        "socket.create_connection = _guarded_create\n"
        "socket.socket.connect = _guarded_connect\n"
    )
    (dest / "dms_app_guard.py").write_text(text, encoding="utf-8")


def run_isolated(
    script: str,
    *,
    allow_hosts: set[str],
    data_url: str = "http://127.0.0.1:9",
    token: str = "sandbox-token",
    ov_url: str | None = None,
    timeout: float = 8,
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` as ``main.py`` inside a fresh directory."""
    import tempfile

    dest = Path(tempfile.mkdtemp(prefix="dms-app-"))
    write_guard(dest, allow_hosts)
    (dest / "main.py").write_text(script, encoding="utf-8")
    env = child_env(data_url=data_url, token=token, ov_url=ov_url, pythonpath=str(dest))
    code = "import dms_app_guard; import runpy; runpy.run_path('main.py', run_name='__main__')"
    try:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=dest,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return subprocess.CompletedProcess(
            args=[sys.executable], returncode=124, stdout=stdout, stderr=stderr or "boot_timeout"
        )


class _DataHandler(BaseHTTPRequestHandler):
    token: str = ""
    query_fn: QueryFn | None = None
    actions: list[dict[str, Any]] = []
    grants: set[str] = set()
    audit: list[dict[str, Any]] = []
    calls: list[str] = []

    def log_message(self, fmt: str, *args: Any) -> None:
        del fmt, args

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        presented = self.headers.get("X-Dms-App-Token", "")
        if not _same(presented, self.token):
            self._send(401, {"ok": False, "code": "token_rejected"})
            return
        if self.path == "/query":
            self.calls.append("query")
            raw_sql = body.get("sql")
            sql = raw_sql if isinstance(raw_sql, str) else ""
            # Class attribute, not an instance method: a function on the class
            # would bind to the handler and swallow the SQL.
            fn = type(self).query_fn
            result = (
                fn(sql) if fn is not None else {"ok": False, "code": "query_unbound", "rows": []}
            )
            self._send(200, result)
            return
        if self.path == "/actions/dry-run":
            self.calls.append("dry_run")
            raw_id = body.get("action_id")
            action_id = raw_id if isinstance(raw_id, str) else ""
            payload = body.get("payload") if isinstance(body.get("payload"), dict) else None
            result = dry_run_action(
                actions=self.actions,
                action_id=action_id,
                payload=payload,
                grants=self.grants,
                actor="sandbox",
                audit=self.audit,
            )
            self._send(200, result)
            return
        if self.path == "/actions/confirm":
            self.calls.append("confirm")
            self._send(403, {"ok": False, "code": "confirmation_requires_user", "applied": False})
            return
        self._send(404, {"ok": False, "code": "not_found"})

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _same(left: str, right: str) -> bool:
    import hashlib
    import hmac

    a = hashlib.sha256(left.encode()).hexdigest()
    b = hashlib.sha256(right.encode()).hexdigest()
    return hmac.compare_digest(a, b)


def _handler(
    token: str,
    query_fn: QueryFn,
    actions: list[dict[str, Any]],
    grants: set[str],
    audit: list[dict[str, Any]],
    calls: list[str],
) -> type[_DataHandler]:
    class Bound(_DataHandler):
        pass

    Bound.token = token
    Bound.query_fn = query_fn
    Bound.actions = actions
    Bound.grants = grants
    Bound.audit = audit
    Bound.calls = calls
    return Bound


def judge_backend(
    dest: Path,
    *,
    token: str,
    grants: set[str],
    actions: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    query_fn: QueryFn,
    audit: list[dict[str, Any]],
    ov_url: str | None = None,
    timeout: float = 8,
) -> dict[str, Any]:
    """Boot the backend, run declared queries, and dry-run actions.

    A confirm call from the child is a failure. Apply stays on the user route.
    """
    errors: list[str] = []
    calls: list[str] = []
    allow = {"127.0.0.1"}
    ov_host = host_of(ov_url) if ov_url else None
    if ov_host:
        allow.add(ov_host)
    write_guard(dest, allow)
    handler = _handler(token, query_fn, actions, grants, audit, calls)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound_host, port = server.server_address[:2]
    host = bound_host.decode() if isinstance(bound_host, bytes) else str(bound_host)
    data_url = "http://" + host + ":" + str(port)
    env = child_env(data_url=data_url, token=token, ov_url=ov_url, pythonpath=str(dest))
    code = "import dms_app_guard; import runpy; runpy.run_path('server.py', run_name='__main__')"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=dest,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        code_exit = proc.returncode
    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = "boot_timeout"
        code_exit = 124
    finally:
        server.shutdown()
        server.server_close()
    if code_exit != 0 or "DMS_APP_READY" not in stdout:
        errors.append("boot_failed")
    if "query" not in calls:
        errors.append("query_missing")
    for query in queries:
        sql = query.get("sql") if isinstance(query, dict) else ""
        if not isinstance(sql, str):
            errors.append("query_missing")
            continue
        result = query_fn(sql)
        if not result.get("ok"):
            errors.append("query_failed:" + str(result.get("code") or "query_failed"))
    if "dry_run" not in calls and actions:
        errors.append("action_dry_run_missing")
    if "confirm" in calls:
        errors.append("action_applied_during_judge")
    for action in actions:
        raw_id = action.get("id")
        action_id = raw_id if isinstance(raw_id, str) else ""
        result = dry_run_action(
            actions=actions,
            action_id=action_id,
            payload=None,
            grants=grants,
            actor="judge",
            audit=audit,
        )
        if result.get("applied"):
            errors.append("action_applied_during_judge")
        if result.get("code") == "ungranted":
            errors.append("action_ungranted")
    secret_bits = [token]
    scrubbed = stderr
    for bit in secret_bits:
        scrubbed = scrubbed.replace(bit, "redacted")
    return {
        "ok": not errors,
        "errors": errors,
        "exit": code_exit,
        "calls": list(calls),
        "stderr": scrubbed[:500],
    }
