"""
app/routes/api/v01/admin/preferences.py
Admin-side counterpart to app/routes/api/v01/portal.py's view-mode
endpoint — same generic users.view_prefs jsonb column/mechanism, just
gated by require_admin_role instead of require_user_role, so an
admin-only session (no student "user" role) can still save its own
grid/list preferences (e.g. the Exams page toggle).
"""

from flask import request, jsonify, session

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.users import set_view_pref


@admin_api_bp.route("/view-mode", methods=["PATCH"])
@require_admin_role
def api_admin_set_view_mode():
    data = request.get_json(silent=True) or {}
    section = str(data.get("section") or "").strip()
    view_mode = data.get("view_mode")
    if not section or view_mode not in ("grid", "list"):
        return jsonify({"success": False, "message": "section and a valid view_mode are required"}), 400

    if not set_view_pref(session["user_id"], section, view_mode):
        return jsonify({"success": False, "message": "Unable to save your view preference. Please try again."}), 500
    return jsonify({"success": True, "section": section, "view_mode": view_mode})
