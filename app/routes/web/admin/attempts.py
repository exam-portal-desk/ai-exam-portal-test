"""
app/routes/web/admin/attempts.py
Admin attempt-management page (data loaded via AJAX). The JSON API that
used to live alongside this in app/routes/admin/attempts.py now lives in
app/routes/api/v01/admin/attempts.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams


@admin_bp.route("/attempts")
@require_admin_role
def attempts():
    exams = get_all_exams()   # small list, always fine
    return render_template("admin/attempts.html", exams=exams, rows=[])
