"""SCIM-01 invariants. The flag stays off, the bearer is not logged, and
there is no per-company role table.

INVARIANT-CHANGE: SCIM routes, the vault client, and the migration are new.
These checks lock the flag, the hash-only token column, and the absence of a
hard-coded company map.
"""

from __future__ import annotations

import ast
from pathlib import Path

from dms_api.app import create_app

ROOT = Path(__file__).resolve().parents[2]
_LOG_ATTRS = {"debug", "info", "warning", "error", "exception", "critical"}
_BANNED_LOG_NAMES = {"token", "secret", "authorization", "presented"}
_COMPANY_WORDS = ("Finance", "Contoso", "Fabrikam")


def test_flag_off_mounts_no_scim_route(monkeypatch) -> None:
    monkeypatch.delenv("DMS_SCIM", raising=False)
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/scim") or path.startswith("/v1/scim") for path in paths)


def test_scim_sources_do_not_log_the_bearer() -> None:
    paths = [
        ROOT / "apps/api/dms_api/routes/scim.py",
        ROOT / "apps/api/dms_api/scim_vault.py",
        ROOT / "apps/api/dms_api/scim_apply.py",
        ROOT / "packages/core/dms_core/scim/store.py",
        ROOT / "packages/core/dms_core/scim/protocol.py",
    ]
    offenders: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else ""
            if name not in _LOG_ATTRS:
                continue
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                if isinstance(arg, ast.Name) and arg.id in _BANNED_LOG_NAMES:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_migration_has_no_plaintext_token_column() -> None:
    text = (ROOT / "alembic/versions/0013_scim.py").read_text(encoding="utf-8")
    assert "token_sha256" in text
    assert "cred_ref" in text
    lowered = text.lower()
    assert "secret text" not in lowered
    assert "bearer text" not in lowered
    assert "token text" not in lowered


def test_product_scim_has_no_company_role_table() -> None:
    root = ROOT / "packages/core/dms_core/scim"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in _COMPANY_WORDS:
            assert word not in text, path.name
    vault = (ROOT / "apps/api/dms_api/scim_vault.py").read_text(encoding="utf-8")
    assert "class OpenVaultScim" in vault
    assert "/api/keys" in vault
    assert "open(" not in vault
