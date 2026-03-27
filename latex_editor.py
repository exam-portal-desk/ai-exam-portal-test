from flask import Blueprint, render_template
from app.middleware.session_guard import require_admin_role

latex_bp = Blueprint('latex_editor', __name__, url_prefix='/admin')

@latex_bp.route('/latex_editor')
@require_admin_role
def latex_editor():
    return render_template('admin/latex_editor.html')