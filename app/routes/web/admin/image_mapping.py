"""
app/routes/web/admin/image_mapping.py
Admin "Question Image Mapping" page — a single view that renders the page
shell; every subsequent step (subject search, question search/pagination,
image library browsing, the bulk save) is driven by JSON APIs in
app/routes/api/v01/admin/image_mapping.py (plus the existing Object
Storage listing endpoint for the image library itself).
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exams_for_selector


@admin_bp.route("/image-mapping", methods=["GET"])
@require_admin_role
def image_mapping_page():
    # Same lightweight, one-round-trip exam list Manage Questions' own
    # exam picker uses — no question/image data loaded until the admin
    # actually picks an exam and (then) a subject.
    exams = get_exams_for_selector()
    return render_template("admin/image_mapping.html", exams=exams)
