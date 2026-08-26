"""
app/routes/web/profile.py
User portal Profile page.
"""

from flask import Blueprint, render_template, session

from app.middleware.session_guard import require_user_role
from app.db.users import get_user_profile_by_id
from app.db.misc import get_requests_by_user
from app.services.image_storage_service import resolve_profile_photo_url
from app.db.dashboard_events import mark_event_seen
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

    # This section is specifically "Admin Access" (not the old public form's
    # generic bidirectional user<->admin request tool) — the only thing it
    # ever offers is requesting admin access, so the only two things that
    # can block that are "already has admin" and "already has a pending
    # request", not the old form's broader current_access-based menu.
    current_access = str(user.get("role") or "user").strip().lower()
    has_admin_access = "admin" in current_access.split(",")
    access_requests = get_requests_by_user(user.get("username", ""), user.get("email", ""))
    latest_access_request = access_requests[0] if access_requests else None
    if latest_access_request and latest_access_request.get("request_status") in ("completed", "denied"):
        mark_event_seen(int(session["user_id"]), "request_status", latest_access_request["request_id"])
    has_pending_access_request = any(r.get("request_status") == "pending" for r in access_requests)
    can_request_access = not has_admin_access and not has_pending_access_request

    return render_template(
        "profile.html", profile=user, photo_url=photo_url,
        max_photo_kb=config.MAX_PROFILE_PHOTO_SIZE_KB,
        last_login_display=last_login_display,
        latest_access_request=latest_access_request,
        can_request_access=can_request_access,
        has_admin_access=has_admin_access,
    )
