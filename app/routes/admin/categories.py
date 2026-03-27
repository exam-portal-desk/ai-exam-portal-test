import os
import io
import uuid
import mimetypes

from flask import render_template, request, jsonify
from werkzeug.utils import secure_filename
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.categories import (
    get_all_categories, get_category_by_id,
    create_category, update_category, delete_category,
    category_has_exams,
)
from app.services.drive_service import get_drive_service_for_upload
import config

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_MB = 5


def _upload(file_storage) -> tuple:
    if not file_storage or not file_storage.filename:
        return None, None

    ext = os.path.splitext(secure_filename(file_storage.filename))[1].lower()
    if ext not in ALLOWED_EXTS:
        return None, None

    file_storage.seek(0, os.SEEK_END)
    if file_storage.tell() / (1024 * 1024) > MAX_MB:
        return None, None
    file_storage.seek(0)

    folder_id = config.DRIVE_CATEGORY_FOLDER_ID
    if not folder_id:
        print("[admin.categories] DRIVE_CATEGORY_FOLDER_ID not set in environment")
        return None, None

    try:
        svc = get_drive_service_for_upload()
    except Exception as e:
        print(f"[admin.categories] Drive service error: {e}")
        return None, None

    original_name = secure_filename(file_storage.filename)
    filename = f"{os.path.splitext(original_name)[0]}_{uuid.uuid4().hex[:8]}{ext}"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # Save to temp file — same pattern as images.py upload
    tmp_path = os.path.join(config.UPLOAD_TMP_DIR, filename)
    file_storage.save(tmp_path)
    fh = None

    try:
        fh = open(tmp_path, "rb")
        media = MediaIoBaseUpload(fh, mimetype=mime, resumable=True)

        res = svc.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()

        file_id = res.get("id") if isinstance(res, dict) else None
        if not file_id:
            return None, None

        svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w400"
        return file_id, url

    except (HttpError, Exception) as e:
        print(f"[admin.categories] Drive upload error: {e}")
        return None, None
    finally:
        try:
            if fh and not fh.closed:
                fh.close()
        except Exception:
            pass
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _delete_drive_file(drive_file_id: str):
    if not drive_file_id:
        return
    try:
        svc = get_drive_service_for_upload()
        svc.files().delete(fileId=drive_file_id).execute()
    except Exception as e:
        print(f"[admin.categories] drive delete: {e}")


@admin_bp.route("/categories")
@require_admin_role
def categories():
    return render_template("admin/categories.html", categories=get_all_categories())


@admin_bp.route("/categories/create", methods=["POST"])
@require_admin_role
def create_category_route():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "Name is required"}), 400

    file_id, url = _upload(request.files.get("image"))

    result = create_category({
        "name": name,
        "drive_file_id": file_id,
        "image_url": url,
    })
    if not result:
        return jsonify({"success": False, "message": "Failed — name may already exist"}), 400

    return jsonify({"success": True, "category": result})


@admin_bp.route("/categories/update/<int:cat_id>", methods=["POST"])
@require_admin_role
def update_category_route(cat_id):
    cat = get_category_by_id(cat_id)
    if not cat:
        return jsonify({"success": False, "message": "Category not found"}), 404

    updates = {}
    name = request.form.get("name", "").strip()
    if name:
        updates["name"] = name

    new_file = request.files.get("image")
    if new_file and new_file.filename:
        file_id, url = _upload(new_file)
        if file_id:
            _delete_drive_file(cat.get("drive_file_id"))
            updates["drive_file_id"] = file_id
            updates["image_url"] = url

    if not updates:
        return jsonify({"success": False, "message": "Nothing to update"}), 400

    ok = update_category(cat_id, updates)
    return jsonify({"success": ok})


@admin_bp.route("/categories/delete/<int:cat_id>", methods=["POST"])
@require_admin_role
def delete_category_route(cat_id):
    cat = get_category_by_id(cat_id)
    if not cat:
        return jsonify({"success": False, "message": "Category not found"}), 404

    if category_has_exams(cat_id):
        return jsonify({
            "success": False,
            "message": "Cannot delete — exams are linked to this category. Reassign them first.",
        }), 400

    _delete_drive_file(cat.get("drive_file_id"))
    ok = delete_category(cat_id)
    return jsonify({"success": ok})


@admin_bp.route("/api/categories")
@require_admin_role
def api_categories():
    return jsonify({"categories": get_all_categories()})