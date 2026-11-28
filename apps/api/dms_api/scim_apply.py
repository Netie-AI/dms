"""Apply a directory user to the SSO role, the budget, and the space grant.

The role comes from the saved map. A missing map leaves the person as a
viewer with no space filter. Deactivation revokes sessions in the same call.
"""

from __future__ import annotations

from dms_core.governance.store import Assignment
from dms_core.governance.store import get_store as gov_store
from dms_core.scim.store import MapRule, User
from dms_core.scim.store import get_store as scim_store

from dms_api.auth_sso import AuthError
from dms_api.auth_sso import enabled as sso_on
from dms_api.settings import Settings


def apply_directory(_settings: Settings, user: User, rule: MapRule | None) -> str:
    """Return ``linked`` or ``sso_skipped``. Does not read or log a bearer."""
    linked = "sso_skipped"
    if sso_on() and "@" in user.user_name:
        from dms_api.auth_sso import disable_user, enable_user, grant_scim_user

        try:
            uid = grant_scim_user(
                email=user.user_name,
                role=user.role,
                tenant_id=user.tenant_id,
            )
        except AuthError:
            uid = user.sso_user_id
        if uid:
            user.sso_user_id = uid
            if user.active:
                enable_user(uid)
            else:
                disable_user(uid)
            scim_store().save_user(user)
            linked = "linked"
    if rule is not None:
        gov_store().upsert_assignment(
            Assignment(
                tenant_id=user.tenant_id,
                user_id=user.sso_user_id or user.id,
                email=user.user_name,
                role=rule.gov_role,
                daily_usd=rule.daily_usd,
                monthly_usd=rule.monthly_usd,
                daily_tokens=rule.daily_tokens,
                monthly_tokens=rule.monthly_tokens,
                allow_ask=rule.allow_ask,
                allow_create=rule.allow_create,
                allow_connectors=rule.allow_connectors,
                updated_by="scim",
            )
        )
    return linked
