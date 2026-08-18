"""
app/routes/web/admin/latex_editor.py
Previously its own standalone Blueprint (app/routes/admin/latex_editor.py,
latex_bp with its own url_prefix='/admin') despite living alongside 12
files that all share admin_bp — merged into admin_bp here for consistency.
Endpoint is now admin.latex_editor instead of latex_editor.latex_editor;
no template or JS referenced the old endpoint name (only the literal URL
/admin/latex_editor, which is unchanged).
"""

from flask import render_template
from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role


@admin_bp.route('/latex_editor')
@require_admin_role
def latex_editor():
    return render_template('admin/latex_editor.html')
