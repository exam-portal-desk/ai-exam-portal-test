"""
app/routes/api/v01/admin/access.py
Admin Access Control JSON API (v01): a user's plan, their admin access and their special access. A thin layer over
app.entitlements, which owns every rule; this only parses requests and answers. Every route needs the admin feature Access
control management, and every write is authorised again inside the service against WHO is changing WHOM (see
app/entitlements/authority.py): nobody edits their own record, and only a root administrator edits a privileged one.

  GET    /api/v01/admin/access/plans                              configured plans and features
  GET    /api/v01/admin/access/users/<id>                         a user's plan, admin access, special access and what you may change
  PUT    /api/v01/admin/access/users/<id>/plan                    {"plan": "pro"|null, "expires_at"?|"days"?}
  PUT    /api/v01/admin/access/users/<id>/overrides/<feature>     {"effect": "grant"|"deny", "limits"?, "expires_at"?|"days"?, "reason"?}
                                                                  (<feature> is a user feature = special access, or an admin.* feature = admin access)
  DELETE /api/v01/admin/access/users/<id>/overrides/<feature>
"""

from functools import wraps

from flask import jsonify, request, session

from app import entitlements
from app.db import fetch_one
from app.entitlements import validity
from app.middleware.session_guard import require_admin_permission
from app.routes.api.v01.admin import admin_api_bp
from app.routes.api.v01.admin.users import _is_ghost
from app.utils.datetime_service import now_utc_naive

ACCESS_PERMISSION = "access_control"
_MISSING_TABLE_OR_COLUMN = ("42P01", "42703")


def _fail(message: str, status: int = 400):
    return jsonify({"success": False, "message": message}), status


def _needs_tables(f):
    """Before migrations/20260923_entitlements.sql is applied, answer with what to do instead of a bare 500."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            if getattr(e, "pgcode", None) in _MISSING_TABLE_OR_COLUMN:
                return _fail("Access control is not set up in the database yet. Apply migrations/20260923_entitlements.sql.", 503)
            raise
    return wrapped


def _days(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("days must be a whole number.")


def _expiry(data: dict):
    """`days` (calendar days from today, through 23:59:59 of that day in APP_TIMEZONE) or an ISO `expires_at`; neither means
    no expiry. The policy lives in app/entitlements/validity.py."""
    if data.get("days") not in (None, ""):
        return validity.expiry_from_days(_days(data["days"]), now_utc_naive())
    raw = data.get("expires_at")
    return validity.parse_expires_at(raw) if raw else None


def _target(user_id: int):
    user = fetch_one("SELECT id, username, role FROM users WHERE id=%s", (user_id,))
    if not user:
        return None, _fail("User not found.", 404)
    if _is_ghost(user_id=user["id"], username=user.get("username"), role=user.get("role")):
        return None, _fail("System account cannot be modified.", 403)
    return user, None


def _described(user_id: int):
    return entitlements.describe_user(user_id, session.get("user_id"))


@admin_api_bp.route("/access/plans")
@require_admin_permission(ACCESS_PERMISSION)
def api_access_plans():
    return jsonify({"success": True, **entitlements.overview()})


@admin_api_bp.route("/access/expiry-preview")
@require_admin_permission(ACCESS_PERMISSION)
def api_access_expiry_preview():
    """What `days` would mean right now, so the admin sees the exact expiry before saving."""
    try:
        expires_at = validity.expiry_from_days(_days(request.args.get("days")), now_utc_naive())
    except ValueError as e:
        return _fail(str(e))
    return jsonify({"success": True, **validity.describe(expires_at, now_utc_naive())})


@admin_api_bp.route("/access/users/<int:user_id>")
@require_admin_permission(ACCESS_PERMISSION)
@_needs_tables
def api_access_user(user_id):
    user, error = _target(user_id)
    if error:
        return error
    return jsonify({"success": True, "username": user["username"], **_described(user_id)})


@admin_api_bp.route("/access/users/<int:user_id>/plan", methods=["PUT"])
@require_admin_permission(ACCESS_PERMISSION)
@_needs_tables
def api_access_set_plan(user_id):
    user, error = _target(user_id)
    if error:
        return error
    data = request.get_json() or {}
    plan = data.get("plan") or None
    try:
        expires_at = _expiry(data)
        if expires_at and expires_at <= now_utc_naive():
            raise ValueError("The expiry must be in the future.")
        current = fetch_one("SELECT plan, plan_expires_at FROM users WHERE id=%s", (user_id,)) or {}
        running = current.get("plan_expires_at")
        expires_at = validity.plan_expiry_to_store(plan, expires_at, current.get("plan"), running, now_utc_naive())
        if current.get("plan") == plan and running == expires_at:
            return jsonify({"success": True, "message": "Plan unchanged.", "username": user["username"], **_described(user_id)})
        entitlements.set_user_plan(user_id, plan, expires_at, session.get("user_id"))
    except entitlements.AccessDenied as e:
        return _fail(str(e), 403)
    except (ValueError, entitlements.AccessError) as e:
        return _fail(str(e))
    return jsonify({"success": True, "message": "Plan updated.", "username": user["username"], **_described(user_id)})


@admin_api_bp.route("/access/users/<int:user_id>/overrides/<feature>", methods=["PUT"])
@require_admin_permission(ACCESS_PERMISSION)
@_needs_tables
def api_access_set_override(user_id, feature):
    user, error = _target(user_id)
    if error:
        return error
    data = request.get_json() or {}
    try:
        entitlements.set_override(user_id, feature, str(data.get("effect", "")).lower(), limits=data.get("limits"),
                                  expires_at=_expiry(data), reason=data.get("reason", ""), actor_id=session.get("user_id"))
    except entitlements.AccessDenied as e:
        return _fail(str(e), 403)
    except (ValueError, entitlements.AccessError) as e:
        return _fail(str(e))
    return jsonify({"success": True, "message": "Access updated.", "username": user["username"], **_described(user_id)})


@admin_api_bp.route("/access/users/<int:user_id>/overrides/<feature>", methods=["DELETE"])
@require_admin_permission(ACCESS_PERMISSION)
@_needs_tables
def api_access_clear_override(user_id, feature):
    user, error = _target(user_id)
    if error:
        return error
    try:
        removed = entitlements.clear_override(user_id, feature, session.get("user_id"))
    except entitlements.AccessDenied as e:
        return _fail(str(e), 403)
    except entitlements.AccessError as e:
        return _fail(str(e))
    if not removed:
        return _fail("There is no override for that feature.", 404)
    return jsonify({"success": True, "message": "Removed.", "username": user["username"], **_described(user_id)})
