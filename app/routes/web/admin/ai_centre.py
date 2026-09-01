"""
app/routes/web/admin/ai_centre.py
AI Command Centre page. The JSON API (generate/status/save/export) that
used to live alongside this in app/routes/admin/ai_centre.py now lives
in app/routes/api/v01/admin/ai_centre.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams, get_exams_for_selector


@admin_bp.route("/ai-command-centre", methods=["GET"])
@require_admin_role
def ai_command_centre():
    return render_template("admin/ai_command_centre.html", exams=get_all_exams())


@admin_bp.route("/ai-command-centre/csv-upload", methods=["GET"])
@require_admin_role
def csv_upload():
    """Standalone CSV Upload & Editor page (was a modal inside AI Command
    Centre) — parsing/preview/edit stays entirely client-side, this route
    just serves the dedicated page shell. exams is only needed for the
    manual-CSV-upload path's target-exam picker (see the exam-id security
    fix in app/routes/api/v01/admin/questions.py) — the AI-generated path
    derives its target exam from the job itself instead."""
    return render_template("admin/csv_upload.html", exams=get_exams_for_selector())
