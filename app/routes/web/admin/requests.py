"""
app/routes/web/admin/requests.py
Admin access-requests page (data loaded via AJAX). The JSON API that used
to live alongside this in app/routes/admin/requests.py now lives in
app/routes/api/v01/admin/requests.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role


@admin_bp.route("/requests")
@require_admin_role
def requests_dashboard():
    # Koi data nahi bhejte — AJAX se aayega
    return render_template("admin/requests.html",
        pending_requests=[],
        history_requests=[],
        users=[])
