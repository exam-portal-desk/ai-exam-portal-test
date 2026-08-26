"""
app/routes/api/v01/dashboard.py
Lightweight JSON endpoint backing the GLOBAL "Your Updates" notification
popup: one call returns the rendered popup HTML (partials/updates_panel.html)
plus an item count for the notifications badge. USER-scoped, not
category/portal-scoped — works identically whether or not a category is
selected yet, which is what lets it appear before category selection.
Powers the automatic popup at portal entry, the manual notifications-bell
open, and the badge count — one fetch, three consumers (see
templates/base.html).
"""

from flask import Blueprint, jsonify, render_template, session

from app.middleware.session_guard import require_user_role
from app.services.dashboard_service import (
    get_dashboard_summary, count_items, get_greeting, get_today_display,
)

dashboard_api_bp = Blueprint("dashboard_api", __name__, url_prefix="/api/v01")


@dashboard_api_bp.route("/student/updates")
@require_user_role
def student_updates():
    dash = get_dashboard_summary(session["user_id"])
    html = render_template("partials/updates_panel.html", dash=dash)
    first_name = (session.get("full_name") or "").split()[0] if session.get("full_name") else ""
    return jsonify({
        "success": True,
        "html": html,
        "count": count_items(dash),
        "greeting_title": f"{get_greeting()}, {first_name}".rstrip(", "),
        "greeting_sub": get_today_display(),
    })
