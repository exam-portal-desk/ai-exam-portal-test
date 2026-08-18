"""
app/routes/api/v01/admin/images.py
Admin image bulk-upload JSON API (v01). Relocated from
app/routes/admin/images.py (the POST branch of /admin/upload-images).

  POST /admin/upload-images -> POST /api/v01/admin/images
"""

import os
import mimetypes

from flask import request, jsonify
from werkzeug.utils import secure_filename

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import get_subject_by_folder_id
from app.services import image_storage_service
from app.utils.cache import set_force_refresh
import app.config as config


@admin_api_bp.route("/images", methods=["POST"])
@require_admin_role
def upload_images_api():
    # The form still submits the subject's Drive folder id (unchanged UI) —
    # resolve it to the subject's name, which is the storage key prefix
    # ("SubjectName/filename.ext", matching questions.image_path already).
    folder_id = request.form.get("subject_folder_id", "").strip()
    files     = request.files.getlist("images")

    if not folder_id:
        return jsonify({"success": False, "message": "No folder selected."}), 400
    if not files:
        return jsonify({"success": False, "message": "No files received."}), 400

    subject = get_subject_by_folder_id(folder_id)
    if not subject:
        return jsonify({"success": False, "message": "Subject not found for selected folder."}), 404
    subject_name = subject["subject_name"]

    uploaded = 0
    failed   = []

    for f in files:
        if not f or not f.filename:
            continue

        safe_name = secure_filename(f.filename)
        ext       = os.path.splitext(safe_name)[1].lower()

        if ext not in config.ALLOWED_IMAGE_EXTS:
            failed.append({"filename": safe_name, "error": f"Not allowed ({ext})"})
            continue

        f.seek(0, os.SEEK_END)
        size_kb = f.tell() / 1024
        f.seek(0)

        if size_kb > config.MAX_IMAGE_SIZE_KB:
            failed.append({"filename": safe_name, "error": f"Exceeds {config.MAX_IMAGE_SIZE_KB} KB"})
            continue

        try:
            mime, _ = mimetypes.guess_type(safe_name)
            content = f.read()
            image_storage_service.upload_question_image(
                subject_name, safe_name, content, mime or "application/octet-stream"
            )
            uploaded += 1
        except Exception as e:
            failed.append({"filename": safe_name, "error": str(e)})

    # Force the next resolve of any image under this subject to re-check
    # existence/URL rather than serving a stale cached miss/URL.
    set_force_refresh(True)

    return jsonify({"success": True, "uploaded": uploaded, "failed": failed}), 200
