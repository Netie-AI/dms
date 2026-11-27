"""R1-RULES-OUT-01. Question-text rules stay on the allowlist, which only shrinks.

The guard walks packages/ and apps/. A regex call or an ``in`` test whose
operand traces to the question argument is a site. Today's sites are the
ceiling. A later change may remove a line. It may not add a key.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Day-1 ceiling of scripts/verify/word_rule_allowlist.ceiling. A new key
# changes this hash and fails. Removing a site updates the allowlist only.
CEILING_SHA256 = "02ed7d5c6646656f091e259ac161913f547e1bf276b21ac209ec5ff319bb56e5"


def _inventory():
    path = str(ROOT / "scripts" / "verify")
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module("rule_inventory")


def test_planted_revenue_membership_is_caught(tmp_path: Path) -> None:
    inv = _inventory()
    planted = tmp_path / "planted.py"
    planted.write_text(
        "def f(question):\n    if 'revenue' in question:\n        return 1\n",
        encoding="utf-8",
    )
    sites = inv.scan_file(planted, rel="planted.py")
    assert sites
    assert any(site.kind == "in" and "revenue" in site.snippet for site in sites)
    module = tmp_path / "loose.py"
    module.write_text("if 'revenue' in question:\n    raise SystemExit(1)\n", encoding="utf-8")
    loose = inv.scan_file(module, rel="loose.py")
    assert any(site.kind == "in" and "revenue" in site.snippet for site in loose)


def test_repo_sites_match_the_allowlist_and_the_ceiling_does_not_grow() -> None:
    inv = _inventory()
    sites = inv.scan_roots()
    allow = inv.ALLOWLIST_PATH.read_text(encoding="utf-8")
    ceiling = inv.CEILING_PATH.read_text(encoding="utf-8")
    assert inv.ceiling_sha256(ceiling) == CEILING_SHA256
    assert inv.violations(sites, allow_text=allow, ceiling_text=ceiling) == []
    assert {site.key for site in sites} <= inv.ceiling_keys(ceiling)
    assert len(sites) == len(inv.allowlist_keys(allow))


def test_a_new_allowlist_key_is_rejected() -> None:
    inv = _inventory()
    sites = inv.scan_roots()
    allow = inv.ALLOWLIST_PATH.read_text(encoding="utf-8")
    ceiling = inv.CEILING_PATH.read_text(encoding="utf-8")
    extra = allow + "packages/planted.py:2\tin\tf\t'revenue' in question\t#1\n"
    messages = inv.violations(sites, allow_text=extra, ceiling_text=ceiling)
    assert any("not in the day-1 ceiling" in message for message in messages)
    assert any("remove it" in message for message in messages)


def test_inventory_check_exits_clean() -> None:
    inv = _inventory()
    assert inv.main(["--check"]) == 0
