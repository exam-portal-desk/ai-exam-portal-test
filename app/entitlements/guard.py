"""
app/entitlements/guard.py
Route-level enforcement of the student plans. A signed-in student who may not use a feature gets a 403: the locked page for a
browser, JSON for an API call. Anyone not signed in is left to the normal login guards, and so is an admin-portal session
(admin permissions are checked by require_admin_permission).

    @require_user_role
    @require_feature("notebook")          # one route
    def my_notes(): ...

    gate_blueprint(assistant_api_bp, "ai_assistant")    # every route of a blueprint
"""

from functools import wraps
from typing import Optional

from flask import jsonify, render_template, session

from app import entitlements


def _denial(feature: str):
    from app.middleware.session_guard import _is_api_request
    info = entitlements.locked_reason(int(session["user_id"]), feature)
    message = f"{info['feature_label']} is not included in your {info['plan_label']} plan."
    if info["required_plan"]:
        message += f" It is part of {info['required_plan']}."
    if _is_api_request():
        return jsonify({"success": False, "status": "error", "message": message, "feature_locked": True, **info}), 403
    return render_template("feature_locked.html", info=info, message=message, active_nav=""), 403


def check_request(feature: str):
    """None when the request may go on, otherwise the response to send."""
    uid = session.get("user_id")
    if not uid or session.get("admin_id"):
        return None
    return None if entitlements.has_access(int(uid), feature) else _denial(feature)


def require_feature(feature: str):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            denied = check_request(feature)
            return denied if denied is not None else f(*args, **kwargs)
        return wrapped
    return decorator


def gate_blueprint(blueprint, feature: str) -> None:
    @blueprint.before_request
    def _gate() -> Optional[object]:
        return check_request(feature)
