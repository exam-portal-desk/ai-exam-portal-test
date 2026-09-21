"""
app/routes/web/admin/ai_configuration.py
Admin "AI Configuration" page shell. Everything on the page (providers,
models, features, active assignments) is loaded client-side from
GET /api/v01/admin/ai-config, so no provider or model is named here or in
the template.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_permission


@admin_bp.route("/ai-configuration", methods=["GET"])
@require_admin_permission("ai_configuration")
def ai_configuration():
    return render_template("admin/ai_configuration.html")
