"""Support matrix read from the driver modules. Not a hand-maintained engine table."""

from __future__ import annotations

from dataclasses import dataclass

from kits._boot import boot

boot()

# Kinds the plan names and this repo does not implement. The test fails if one
# of these shows up in the driver list, so a new connector cannot be claimed here
# by silence.
ABSENT_KINDS = ("snowflake", "bigquery", "databricks", "redshift")


@dataclass(frozen=True)
class EngineSpec:
    kind: str
    flag: str
    flag_mode: str
    port: str
    when: str
    module: str


def _specs() -> list[EngineSpec]:
    from dms_core.control_plane.connect_store import _KINDS
    from dms_executor.db_connector import (
        _DEFAULT_PORTS,
        DEFAULT_ODBC_DRIVER,
        SQLITE_ROOT_ENV,
    )
    from dms_executor.drivers import gauss, oracle

    aliases = sorted(set(_KINDS) - set(_DEFAULT_PORTS))
    alias_note = ""
    if aliases:
        named = ", ".join(f"`{name}`" for name in aliases)
        alias_note = f"; connect book kind {named} uses this driver"
    rows: list[EngineSpec] = []
    for kind, port in _DEFAULT_PORTS.items():
        if kind == "sqlite":
            rows.append(
                EngineSpec(
                    kind=kind,
                    flag=SQLITE_ROOT_ENV,
                    flag_mode="path",
                    port="file",
                    when="the env names the directory that holds the file",
                    module="packages/executor/dms_executor/db_connector.py",
                )
            )
            continue
        if kind == "gauss":
            rows.append(
                EngineSpec(
                    kind=kind,
                    flag=gauss.FLAG,
                    flag_mode="digit",
                    port=str(port),
                    when="digit 1 only",
                    module="packages/executor/dms_executor/drivers/gauss.py",
                )
            )
            continue
        if kind == "oracle":
            rows.append(
                EngineSpec(
                    kind=kind,
                    flag=oracle.FLAG,
                    flag_mode="digit",
                    port=str(port),
                    when="digit 1 only",
                    module="packages/executor/dms_executor/drivers/oracle.py",
                )
            )
            continue
        when = "built in"
        if kind == "sqlserver":
            when = f"built in; encrypt defaults on ({DEFAULT_ODBC_DRIVER})"
        elif kind == "mysql" and alias_note:
            when = "built in" + alias_note
        rows.append(
            EngineSpec(
                kind=kind,
                flag="",
                flag_mode="always",
                port=str(port),
                when=when,
                module="packages/executor/dms_executor/db_connector.py",
            )
        )
    return rows


def engine_specs() -> list[EngineSpec]:
    return _specs()


def render_engine_table() -> str:
    lines = [
        "| Engine | `kind` | Flag | Port | Accepted when |",
        "|---|---|---|---|---|",
    ]
    for spec in engine_specs():
        flag = f"`{spec.flag}`" if spec.flag else "(none)"
        lines.append(f"| `{spec.kind}` | `{spec.kind}` | {flag} | {spec.port} | {spec.when} |")
    absent = ", ".join(f"`{name}`" for name in ABSENT_KINDS)
    lines.append(
        f"| not a connector | {absent} | (none) |  | no driver module and not in the connect book |"
    )
    return "\n".join(lines)


def classify(kind: str) -> tuple[str, bool, str]:
    """Return ``(driver_kind, accepted_now, reason)``.

    ``accepted_now`` follows the flag that is on in this process. A kind with
    no driver is not accepted and the reason says there is no connector.
    """
    from dms_executor.db_connector import _DEFAULT_PORTS, SQLITE_ROOT_ENV

    text = kind.strip().lower()
    specs = {spec.kind: spec for spec in engine_specs()}
    if text == "mariadb" and "mysql" in specs:
        return "mysql", True, ""
    spec = specs.get(text)
    if spec is None:
        return "", False, f"no connector for kind {text}"
    if spec.flag_mode == "always":
        return spec.kind, True, ""
    if spec.flag_mode == "digit":
        from dms_executor.drivers import flag_on

        if flag_on(spec.flag):
            return spec.kind, True, ""
        return spec.kind, False, f"{spec.flag} off"
    if spec.flag_mode == "path":
        import os

        if os.environ.get(SQLITE_ROOT_ENV, "").strip():
            return spec.kind, True, ""
        return spec.kind, False, f"{spec.flag} off"
    if text not in _DEFAULT_PORTS:
        return "", False, f"no connector for kind {text}"
    return text, False, f"no connector for kind {text}"
