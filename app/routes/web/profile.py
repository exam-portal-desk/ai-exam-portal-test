"""
app/routes/web/profile.py
User portal Profile page.
"""

from flask import Blueprint, render_template, session

from app.middleware.session_guard import require_user_role
from app.db.users import get_user_profile_by_id
from app.db.misc import get_requests_by_user, available_role_requests, split_request_reason
from app.services.image_storage_service import resolve_profile_photo_url
from app.db.dashboard_events import mark_event_seen
from app.utils.helpers import split_full_name
import app.config as config

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/profile")
@require_user_role
def my_profile():
    user = get_user_profile_by_id(int(session["user_id"])) or {}
    photo_url = resolve_profile_photo_url(user.get("profile_photo_key"))
    # "last_login_display" is set (possibly to None, on a genuine first
    # login) by every login path that's been updated for this feature — if
    # the key is present at all, it's authoritative, even when None/falsy.
    # Only fall back to the (possibly-just-overwritten) DB column for a
    # session that predates this feature and never got the key set.
    if "last_login_display" in session:
        last_login_display = session["last_login_display"]
    else:
        last_login_display = user.get("last_login")

    # "Admin Access" — what this card offers now follows available_role_requests() (app/db/misc.py),
    # the one shared rule: a user-only account may ask for admin-only or both; a dual-role
    # account may ask to drop back to user-only; an admin-only account never reaches this page at
    # all (require_user_role), so that case never needs handling here.
    current_access = str(user.get("role") or "user").strip().lower()
    available_requests = available_role_requests(current_access)
    has_admin_access = "admin" in current_access.split(",")
    access_requests = get_requests_by_user(user.get("username", ""), user.get("email", ""))
    latest_access_request = access_requests[0] if access_requests else None
    if latest_access_request and latest_access_request.get("request_status") in ("completed", "denied"):
        mark_event_seen(int(session["user_id"]), "request_status", latest_access_request["request_id"])
    # (user_reason, admin_response), parsed from the one raw reason column so the template can
    # show them as two separate lines instead of one machine-tagged blob — see split_request_reason.
    latest_request_reason = split_request_reason(latest_access_request.get("reason", "")) if latest_access_request else ("", "")
    has_pending_access_request = any(r.get("request_status") == "pending" for r in access_requests)
    can_request_access = bool(available_requests) and not has_pending_access_request

    first_name, last_name = split_full_name(user.get("full_name"))
    return render_template(
        "profile.html", profile=user, photo_url=photo_url,
        max_photo_kb=config.MAX_PROFILE_PHOTO_SIZE_KB,
        last_login_display=last_login_display,
        latest_access_request=latest_access_request,
        latest_request_reason=latest_request_reason,
        can_request_access=can_request_access,
        available_requests=available_requests,
        # Read fresh from the DB on every load (never inferred from a request's historical
        # "Approved" status, and never from the session, which can lag a role change until
        # re-login — see invalidate_session calls in app/routes/api/v01/admin/{requests,users}.py)
        # so this card can never show privileges the account doesn't actually currently have.
        current_access=current_access,
        has_admin_access=has_admin_access,
        first_name=first_name, last_name=last_name,
    )
