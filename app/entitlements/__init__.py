"""
app/entitlements
Plans, feature access and limits. Ask the questions through this package only:

    from app.entitlements import has_access, limit_for

    has_access(user_id, "notebook")
    limit_for(user_id, "ai_explanation", "per_day")          # None = unlimited

What a plan contains is configuration (config/entitlements.json, see config/ENTITLEMENTS.md); admin roles stay in
users.role and are never implied by a plan.
"""

from app.entitlements.errors import AccessDenied, AccessError  # noqa: F401
from app.entitlements.service import (  # noqa: F401
    admin_denied_reason,
    authorize_role_change,
    check,
    check_limit,
    clear_override,
    describe_user,
    has_access,
    has_admin_permission,
    limit_for,
    locked_reason,
    overview,
    set_override,
    set_user_plan,
    summaries_for,
    user_plan,
)
