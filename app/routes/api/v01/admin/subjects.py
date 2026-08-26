"""
app/routes/api/v01/admin/subjects.py
Admin subjects JSON API (v01): paginated/searchable list only — create/
edit/delete stay on the plain-form web routes in app/routes/web/admin/subjects.py.
"""

from flask import jsonify, request, render_template_string

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import get_subjects_page

_ROWS_TPL = (
    '{% from "admin/_subject_rows.html" import render_subject_row, render_subject_edit_modal %}'
    '{% for subject in subjects %}{{ render_subject_row(subject) }}{% endfor %}'
    '|||SPLIT|||'
    '{% for subject in subjects %}{{ render_subject_edit_modal(subject) }}{% endfor %}'
)


@admin_api_bp.route("/subjects", methods=["GET"])
@require_admin_role
def api_subjects_list():
    result = get_subjects_page(
        search=request.args.get("q", "").strip(),
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 20),
    )

    if request.args.get("partial"):
        rendered = render_template_string(_ROWS_TPL, subjects=result["subjects"])
        rows_html, modals_html = rendered.split("|||SPLIT|||")
        result["rows_html"] = rows_html
        result["modals_html"] = modals_html
        del result["subjects"]

    return jsonify(result)
