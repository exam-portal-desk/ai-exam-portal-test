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

from flask import Blueprint, jsonify, render_template, request, session

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


@dashboard_api_bp.route("/student/updates/dismiss", methods=["POST"])
@require_user_role
def dismiss_update():
    """Explicit "mark as seen" for popup items that have no natural page to
    view (e.g. a declined connection request never creates a conversation
    to open) — see requirement to provide a dismiss action for these.
    Always scoped to the calling user's own session, so there's nothing to
    authorize beyond that: a user can only ever mark their own notification
    rows seen, which has no effect on anyone else's data or access."""
    from app.db.dashboard_events import mark_event_seen

    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type") or "").strip()
    event_key = data.get("event_key")
    if not event_type or event_key in (None, ""):
        return jsonify({"success": False}), 400

    mark_event_seen(session["user_id"], event_type, event_key)
    return jsonify({"success": True})
