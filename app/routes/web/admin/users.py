"""
app/routes/web/admin/users.py
Admin user-management page (data loaded via AJAX). The JSON API that
used to live alongside this in app/routes/admin/users.py now lives in
app/routes/api/v01/admin/users.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role


@admin_bp.route("/users/manage")
@require_admin_role
def users_manage():
    return render_template("admin/users_manage.html", users=[])
