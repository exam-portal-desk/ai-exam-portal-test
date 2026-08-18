"""
app/routes/web/admin/images.py
Admin image upload page. The upload JSON endpoint that used to live on
the same URL (POST /admin/upload-images) now lives at
POST /api/v01/admin/images in app/routes/api/v01/admin/images.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import get_all_subjects
import app.config as config


@admin_bp.route("/upload-images", methods=["GET"])
@require_admin_role
def upload_images_page():
    subjects_list = [
        {
            "id":        int(s.get("id", 0)),
            "name":      str(s.get("subject_name", "")).strip(),
            "folder_id": str(s.get("subject_folder_id", "")).strip(),
        }
        for s in get_all_subjects()
        if str(s.get("subject_folder_id", "")).strip()
    ]

    return render_template(
        "admin/upload_images.html",
        subjects=subjects_list,
        load_error=None if subjects_list else "No subjects found.",
        max_image_size_kb=config.MAX_IMAGE_SIZE_KB,
    )
