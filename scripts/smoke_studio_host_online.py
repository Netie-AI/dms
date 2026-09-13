"""DEMO-HOST-02 (#164) measured host-online smoke via IAP/CF.

Not ``verify_demo_live.py``. That script certifies prove/laptop loopback (already
31/31). This one walks the Platform Studio origin and, when you are on the prove
host, the tunneled loopback. No defaults to ``127.0.0.1:8090``. Unset env is
CONFIG or BLOCKED, never a silent PASS.

Does not bind ``0.0.0.0``. Does not open public ``:8090``. Does not touch the
lake / ``LIVE_KEY_ID``. Does not claim EPIC-008 COMPLETE. Temp trycloudflare
hostnames ROTATE until founder GO durable ``TUNNEL_TOKEN``.

Cursor cloud is not the certifying seat for VPC-only loopback. Platform/tunnel
owns ``LOCAL_TUNNEL_PORT`` (``http://127.0.0.1:{port}/health`` on prove).

    STUDIO_ORIGIN=https://<current-cf-host> \\
    DMS_API_BASE=https://<current-cf-host>/api \\
    LOCAL_TUNNEL_PORT=8090 \\
    python scripts/smoke_studio_host_online.py

``LOCAL_TUNNEL_PORT`` only when this process is on prove (or a laptop with a
local Access/tcp forward). From a cloud seat, leave it unset: loopback is
BLOCKED with ``error.type=env.unset``, owner=Platform/tunnel.

Exit: 0 PASS / 1 FAIL / 2 CONFIG / 3 BLOCKED
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

OWNER = "Platform/tunnel"
ASK_QUESTION = "Top 5 selling SKUs by revenue"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3

HEALTH_KEYS = ("status", "product", "ask_mode", "demo_fallback", "database")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    studio_origin: str
    api_base: str
    local_tunnel_port: int | None

    @property
    def loopback_health_url(self) -> str | None:
        if self.local_tunnel_port is None:
            return None
        return f"http://127.0.0.1:{self.local_tunnel_port}/health"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    error_type: str | None = None
    owner: str | None = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        tag = check.status
        extra = f" error.type={check.error_type}" if check.error_type else ""
        who = f" owner={check.owner}" if check.owner else ""
        print(f"  [{tag}] {check.name}{extra}{who}")
        if check.detail:
            print(f"         {check.detail}")
        self.checks.append(check)

    def verdict(self) -> str:
        if any(c.status == "FAIL" for c in self.checks):
            return "FAIL"
        if any(c.status == "BLOCKED" for c in self.checks):
            return "BLOCKED"
        if not self.checks or any(c.status != "PASS" for c in self.checks):
            return "BLOCKED"
        return "PASS"


def _strip(value: str | None) -> str:
    return (value or "").strip()


def _forbid_wildcard(label: str, url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in {"0.0.0.0", "*", "::", "[::]"}:
        raise ConfigError(
            f"{label} hostname {host!r} is a public bind. DMS :8090 stays "
            "127.0.0.1. Do not use 0.0.0.0."
        )


def load_config(env: dict[str, str]) -> Config:
    origin = _strip(env.get("STUDIO_ORIGIN"))
    api = _strip(env.get("DMS_API_BASE") or env.get("STUDIO_API_BASE"))
    port_raw = _strip(env.get("LOCAL_TUNNEL_PORT"))
    missing: list[str] = []
    if not origin:
        missing.append("STUDIO_ORIGIN")
    if not api:
        missing.append("DMS_API_BASE (or STUDIO_API_BASE)")
    if missing:
        port_note = (
            " LOCAL_TUNNEL_PORT also unset (loopback will BLOCKED; "
            f"owner={OWNER})."
            if not port_raw
            else ""
        )
        raise ConfigError(
            "unset " + ", ".join(missing) + "." + port_note + " No 127.0.0.1:8090 default."
        )
    origin = origin.rstrip("/")
    api = api.rstrip("/")
    _forbid_wildcard("STUDIO_ORIGIN", origin)
    _forbid_wildcard("DMS_API_BASE", api)
    port: int | None = None
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(f"LOCAL_TUNNEL_PORT must be an integer, got {port_raw!r}") from exc
        if not (1 <= port <= 65535):
            raise ConfigError(f"LOCAL_TUNNEL_PORT out of range: {port}")
    return Config(studio_origin=origin, api_base=api, local_tunnel_port=port)


def error_type_of(*, exc: BaseException | None = None, body: Any = None, fallback: str = "unknown") -> str:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            for key in ("type", "code"):
                val = detail.get(key)
                if val:
                    return str(val)
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        typed = body.get("type") or body.get("code")
        if typed:
            return str(typed)
    if exc is not None:
        return type(exc).__name__
    return fallback


def _read_http(
    url: str, *, timeout: float, method: str = "GET", data: bytes | None = None, content_type: str | None = None
) -> tuple[int, str, str]:
    headers = {"User-Agent": "dms-smoke-studio-host-online/164", "Accept": "*/*"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return int(resp.status), ctype, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp is not None else b""
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return int(exc.code), ctype, raw.decode("utf-8", errors="replace")


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _snippet(text: str, n: int = 180) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= n else compact[: n - 3] + "..."


def check_origin(cfg: Config, report: Report) -> None:
    url = cfg.studio_origin + "/"
    try:
        status, ctype, body = _read_http(url, timeout=20)
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "GET STUDIO_ORIGIN /",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    html_ok = status == 200 and "html" in ctype.lower() and "netie" in body.lower()
    report.add(
        Check(
            "GET STUDIO_ORIGIN /",
            "PASS" if html_ok else "FAIL",
            f"status={status} ctype={ctype} {_snippet(body)}",
            error_type=None if html_ok else "unexpected_origin_body",
        )
    )


def check_studio(cfg: Config, report: Report) -> None:
    url = cfg.studio_origin + "/studio"
    try:
        status, ctype, body = _read_http(url, timeout=20)
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "GET STUDIO_ORIGIN /studio",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    ok = status == 200 and "html" in ctype.lower()
    report.add(
        Check(
            "GET STUDIO_ORIGIN /studio",
            "PASS" if ok else "FAIL",
            f"status={status} ctype={ctype} {_snippet(body)}",
            error_type=None if ok else "unexpected_studio_body",
        )
    )


def _health_payload_ok(body: dict[str, Any]) -> tuple[bool, str]:
    db = body.get("database") if isinstance(body.get("database"), dict) else {}
    keys = [k for k in HEALTH_KEYS if k in body]
    detail = (
        f"keys={sorted(body.keys())} status={body.get('status')} product={body.get('product')} "
        f"ask_mode={body.get('ask_mode')} demo_fallback={body.get('demo_fallback')} "
        f"database.backend={db.get('backend')} persistent={db.get('persistent')}"
    )
    ok = (
        body.get("status") == "ok"
        and body.get("product") == "dms"
        and body.get("ask_mode") == "live"
        and body.get("demo_fallback") is False
        and db.get("backend") == "postgres"
        and db.get("persistent") is True
    )
    return ok, detail + f" present={keys}"


def check_api_health(cfg: Config, report: Report) -> None:
    url = cfg.api_base + "/health"
    try:
        status, ctype, text = _read_http(url, timeout=20)
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "GET DMS_API_BASE /health",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    body = _maybe_json(text)
    if not isinstance(body, dict):
        report.add(
            Check(
                "GET DMS_API_BASE /health",
                "FAIL",
                f"status={status} ctype={ctype} (not JSON; use origin/api not SPA root) {_snippet(text)}",
                error_type="health_not_json",
            )
        )
        return
    ok, detail = _health_payload_ok(body)
    report.add(
        Check(
            "GET DMS_API_BASE /health",
            "PASS" if status == 200 and ok else "FAIL",
            f"status={status} {detail}",
            error_type=None if status == 200 and ok else "health_keys",
        )
    )


def check_loopback(cfg: Config, report: Report) -> None:
    if cfg.local_tunnel_port is None:
        report.add(
            Check(
                "GET 127.0.0.1:LOCAL_TUNNEL_PORT /health",
                "BLOCKED",
                "LOCAL_TUNNEL_PORT unset. VPC/prove loopback is not certified from this seat.",
                error_type="env.unset",
                owner=OWNER,
            )
        )
        return
    url = cfg.loopback_health_url
    assert url is not None
    print(f"         loopback url={url} (THIS machine, not the CF hostname)")
    try:
        status, ctype, text = _read_http(url, timeout=8)
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "GET 127.0.0.1:LOCAL_TUNNEL_PORT /health",
                "BLOCKED",
                str(exc)[:300],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    body = _maybe_json(text)
    if not isinstance(body, dict):
        report.add(
            Check(
                "GET 127.0.0.1:LOCAL_TUNNEL_PORT /health",
                "FAIL",
                f"status={status} ctype={ctype} {_snippet(text)}",
                error_type="health_not_json",
            )
        )
        return
    ok, detail = _health_payload_ok(body)
    report.add(
        Check(
            "GET 127.0.0.1:LOCAL_TUNNEL_PORT /health",
            "PASS" if status == 200 and ok else "FAIL",
            f"status={status} {detail}",
            error_type=None if status == 200 and ok else "health_keys",
        )
    )


def check_ask(cfg: Config, report: Report) -> None:
    url = cfg.api_base + "/v1/chat/ask"
    payload = json.dumps(
        {"question": ASK_QUESTION, "space_id": FINANCE, "session_id": "ses_demo_host_02"}
    ).encode()
    try:
        status, ctype, text = _read_http(
            url, timeout=90, method="POST", data=payload, content_type="application/json"
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            Check(
                "POST /v1/chat/ask envelope",
                "BLOCKED",
                str(exc)[:400],
                error_type=error_type_of(exc=exc),
                owner=OWNER,
            )
        )
        return
    body = _maybe_json(text)
    if status != 200 or not isinstance(body, dict) or "badge" not in body:
        report.add(
            Check(
                "POST /v1/chat/ask envelope",
                "BLOCKED",
                f"status={status} {_snippet(text, 400)}",
                error_type=error_type_of(body=body if isinstance(body, dict) else None, fallback="ask_not_envelope"),
                owner=OWNER,
            )
        )
        return
    try:
        from dms_executor.envelope import assert_envelope_valid

        assert_envelope_valid(body)
        light = "assert_envelope_valid"
    except ImportError:
        if body.get("abstained") is True and body.get("badge") != "ABSTAIN":
            report.add(
                Check(
                    "POST /v1/chat/ask envelope",
                    "FAIL",
                    "green badge on abstention prose (E1) — P0",
                    error_type="e1_green_on_abstain",
                )
            )
            return
        if body.get("badge") not in {"ABSTAIN", "L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED"} and body.get(
            "badge"
        ):
            report.add(
                Check(
                    "POST /v1/chat/ask envelope",
                    "FAIL",
                    f"badge={body.get('badge')!r}",
                    error_type="e5_badge",
                )
            )
            return
        light = "light (assert_envelope_valid not importable)"
    except AssertionError as exc:
        report.add(
            Check(
                "POST /v1/chat/ask envelope",
                "FAIL",
                str(exc)[:400],
                error_type="assert_envelope_valid",
            )
        )
        return
    report.add(
        Check(
            "POST /v1/chat/ask envelope",
            "PASS",
            f"{light} badge={body.get('badge')} abstained={body.get('abstained')} "
            f"values={len(body.get('values') or [])} sources={len(body.get('contributing_sources') or [])} "
            f"audit_id={body.get('audit_id') or body.get('answer_id')}",
        )
    )


def run(env: dict[str, str]) -> int:
    print("DEMO-HOST-02 host-online smoke (#164). Not verify_demo_live. Not EPIC-008 COMPLETE.")
    print("Hostname ROTATE RISK until founder GO durable TUNNEL_TOKEN.")
    print(f"Depends on Platform tunnel. Loopback owner={OWNER}. No public :8090.")
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        print(f"CONFIG: {exc}")
        print("VERDICT: CONFIG")
        return EXIT_CONFIG
    print(f"STUDIO_ORIGIN={cfg.studio_origin}")
    print(f"DMS_API_BASE={cfg.api_base}")
    print(f"LOCAL_TUNNEL_PORT={cfg.local_tunnel_port or '(unset)'}")
    report = Report()
    check_origin(cfg, report)
    check_studio(cfg, report)
    check_api_health(cfg, report)
    check_loopback(cfg, report)
    check_ask(cfg, report)
    verdict = report.verdict()
    print("=" * 60)
    print(f"VERDICT: {verdict}")
    if verdict != "PASS":
        print(f"Do not invent PASS. Owner for tunnel/host: {OWNER}.")
        print("Do not invent COMPLETE for EPIC-008.")
    code = {"PASS": EXIT_PASS, "FAIL": EXIT_FAIL, "BLOCKED": EXIT_BLOCKED}[verdict]
    return code


def self_check() -> None:
    try:
        load_config({})
        raise AssertionError("empty env must CONFIG")
    except ConfigError as exc:
        msg = str(exc)
        assert "STUDIO_ORIGIN" in msg, msg
        assert "DMS_API_BASE" in msg, msg
        assert "127.0.0.1:8090" in msg, msg
    try:
        load_config({"STUDIO_ORIGIN": "https://example.trycloudflare.com"})
        raise AssertionError("missing API base must CONFIG")
    except ConfigError as exc:
        assert "DMS_API_BASE" in str(exc)
    try:
        load_config(
            {
                "STUDIO_ORIGIN": "http://0.0.0.0:3000",
                "DMS_API_BASE": "http://127.0.0.1:8090",
            }
        )
        raise AssertionError("0.0.0.0 origin must CONFIG")
    except ConfigError as exc:
        assert "0.0.0.0" in str(exc)
    try:
        load_config(
            {
                "STUDIO_ORIGIN": "https://example.trycloudflare.com",
                "DMS_API_BASE": "http://0.0.0.0:8090",
            }
        )
        raise AssertionError("0.0.0.0 API must CONFIG")
    except ConfigError as exc:
        assert "0.0.0.0" in str(exc)
    cfg = load_config(
        {
            "STUDIO_ORIGIN": "https://example.trycloudflare.com",
            "DMS_API_BASE": "https://example.trycloudflare.com/api",
        }
    )
    assert cfg.local_tunnel_port is None
    assert cfg.loopback_health_url is None
    assert cfg.api_base.endswith("/api")
    assert "127.0.0.1:8090" not in cfg.api_base
    cfg2 = load_config(
        {
            "STUDIO_ORIGIN": "https://example.trycloudflare.com",
            "DMS_API_BASE": "https://example.trycloudflare.com/api",
            "LOCAL_TUNNEL_PORT": "8090",
        }
    )
    assert cfg2.loopback_health_url == "http://127.0.0.1:8090/health"
    assert "0.0.0.0" not in cfg2.loopback_health_url
    print("self-check ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--self-check", action="store_true", help="fail-closed config tests; no network")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    return run(dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
