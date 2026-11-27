"""R1-RUNTIME-01 self-check. Not part of the ask SQL path.

``compute.py`` logs the insights timeout and does not name this flag.
R1-UI-COPY-01 treats that file as ask-route source.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from cortex_client.compute import insights_timeout_s
from cortex_client.insights import is_published_demo_key

_log = logging.getLogger(__name__)

RUNTIME_SELFCHECK_ENV = "DMS_RUNTIME_SELFCHECK"
RUNTIME_TIMEOUT_FLOOR_S = 30.0
BANNER_RUNTIME_DEGRADED = "runtime_degraded"
_CALLER_SERVICE = "service"
_CALLER_LOOPBACK = "loopback"
_CALLER_UNKNOWN = "unknown"
_SERVICE_KEY_PREFIX = "ov_"
_ON = frozenset({"1", "true", "yes", "on"})


def _flag_on(env: Mapping[str, str] | None, name: str) -> bool:
    src = os.environ if env is None else env
    return str(src.get(name) or "").strip().lower() in _ON


def runtime_selfcheck_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Off unless the flag is a true token."""
    return _flag_on(env, RUNTIME_SELFCHECK_ENV)


def classify_cortex_caller(api_key: str | None) -> str:
    """``service``, ``loopback``, or ``unknown``. The key value is not returned.

    ponytail: loopback is the published demo viewer digest only. A third
    identity needs Cortex ``/health`` ``runtime.cortex_caller_kind``. Upgrade:
    trust that field when Cortex reports it, still without copying the key id.
    """
    if api_key is None or not str(api_key).strip():
        return _CALLER_UNKNOWN
    if is_published_demo_key(api_key):
        return _CALLER_LOOPBACK
    if str(api_key).strip().startswith(_SERVICE_KEY_PREFIX):
        return _CALLER_SERVICE
    return _CALLER_UNKNOWN


def runtime_is_degraded(*, caller_kind: str, timeout_s: float) -> bool:
    """Loopback caller, or an effective insights timeout under 30s."""
    return caller_kind == _CALLER_LOOPBACK or float(timeout_s) < RUNTIME_TIMEOUT_FLOOR_S


def _caller_key(api_key: str | None, env: Mapping[str, str] | None) -> str | None:
    if api_key is not None:
        return api_key
    src = os.environ if env is None else env
    return src.get("CORTEX_API_KEY")


def runtime_health_fields(
    api_key: str | None,
    *,
    cortex_reachable: bool,
    ov_reachable: bool,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Flag off: ``{}``. Flag on: the ``runtime`` block, and the banner when set.

    No key value and no key id. ``ov_reachable`` is the field name from the
    ticket; it is not a caller key.
    """
    if not runtime_selfcheck_enabled(env):
        return {}
    timeout_s = insights_timeout_s(env)
    kind = classify_cortex_caller(_caller_key(api_key, env))
    fields: dict[str, Any] = {
        "runtime": {
            "insights_timeout_s": timeout_s,
            "cortex_reachable": bool(cortex_reachable),
            "cortex_caller_kind": kind,
            "ov_reachable": bool(ov_reachable),
        }
    }
    if runtime_is_degraded(caller_kind=kind, timeout_s=timeout_s):
        fields["runtime_degraded"] = True
        fields["banner_reason"] = BANNER_RUNTIME_DEGRADED
    return fields


def attach_runtime_banner(
    env: dict[str, Any],
    *,
    api_key: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Flag off, or a healthy runtime: the same dict.

    Flag on and degraded: a copy with ``runtime_degraded`` and ``banner_reason``.
    Does not read the question, the prompt, the SQL, or the route.
    """
    src = os.environ if environ is None else environ
    if not runtime_selfcheck_enabled(src):
        return env
    timeout_s = insights_timeout_s(src)
    kind = classify_cortex_caller(_caller_key(api_key, src))
    if not runtime_is_degraded(caller_kind=kind, timeout_s=timeout_s):
        return env
    out = dict(env)
    out["runtime_degraded"] = True
    out["banner_reason"] = BANNER_RUNTIME_DEGRADED
    return out


def log_runtime_selfcheck(
    api_key: str | None, env: Mapping[str, str] | None = None
) -> None:
    """Flag on and degraded: one warning. The key is not logged."""
    if not runtime_selfcheck_enabled(env):
        return
    timeout_s = insights_timeout_s(env)
    kind = classify_cortex_caller(api_key)
    if runtime_is_degraded(caller_kind=kind, timeout_s=timeout_s):
        _log.warning(
            "runtime_degraded caller_kind=%s insights_timeout_s=%s",
            kind,
            timeout_s,
        )
