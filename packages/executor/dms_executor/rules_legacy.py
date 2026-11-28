"""DMS_RULES_LEGACY. Default on.

Unset or any token other than ``0`` keeps today's rule serves: the L1
phrase pack, ontology compile, and the question-word lanes. ``0`` is the
pure-AI switch. Those lanes do not submit and do not serve. Grants,
abstain, ontology, and a model SQL leg stay.

Only the digit ``0`` turns the switch off. A typo stays on, so a bad env
cannot drop the answers the pack currently serves.
"""

from __future__ import annotations

import os
from typing import Any

ENV = "DMS_RULES_LEGACY"


def rules_legacy_enabled(env: Any = None) -> bool:
    """True unless ``DMS_RULES_LEGACY`` is the digit ``0``."""
    src = os.environ if env is None else env
    raw = src.get(ENV) if hasattr(src, "get") else None
    if raw is None:
        return True
    return str(raw).strip() != "0"


def rules_off_lane() -> bool:
    """Question-word and compile lanes do not decide.

    ``DMS_RULES_OFF=1`` is the existing hold. ``DMS_RULES_LEGACY=0`` is the
    same hold, and the pure-AI switch for the lanes that flag does not cover.
    """
    if os.environ.get("DMS_RULES_OFF", "0").strip() == "1":
        return True
    return not rules_legacy_enabled()


def hold_rule_serve() -> bool:
    """L1 pack and compile must not submit.

    ``DMS_AI_FIRST`` already holds those serves. ``DMS_RULES_LEGACY=0`` holds
    them too, without turning the rest of the AI-first profile on by itself.
    """
    if not rules_legacy_enabled():
        return True
    from dms_executor.ai_first import ai_first_enabled

    return ai_first_enabled()
