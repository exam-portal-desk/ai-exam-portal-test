"""
app/routes/web/admin/requests.py
Admin access-requests page (data loaded via AJAX). The JSON API that used
to live alongside this in app/routes/admin/requests.py now lives in
app/routes/api/v01/admin/requests.py.
"""

from flask import render_template, session

from app import entitlements
from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_permission


@admin_bp.route("/requests")
@require_admin_permission("user_management")
def requests_dashboard():
    # Requests and users load over AJAX; only the configured plans are rendered with the page.
    try:
        plans = entitlements.overview()
    except Exception as e:
        print(f"[admin.requests] plans overview unavailable: {e}")
        plans = None
    return render_template("admin/requests.html",
        pending_requests=[],
        history_requests=[],
        users=[],
        plans=plans,
        can_manage_access=entitlements.has_admin_permission(session.get("user_id"), "access_control"))
