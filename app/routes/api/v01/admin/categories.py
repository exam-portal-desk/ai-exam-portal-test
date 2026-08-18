"""
app/routes/api/v01/admin/categories.py
Admin categories JSON API (v01). Relocated from app/routes/admin/categories.py.
Logic unchanged; REST cleanup applied to the URL/verb mapping:

  POST   /admin/categories/create        -> POST   /api/v01/admin/categories
  POST   /admin/categories/update/<id>   -> PATCH  /api/v01/admin/categories/<id>
  POST   /admin/categories/delete/<id>   -> DELETE /api/v01/admin/categories/<id>
  GET    /admin/api/categories           -> GET    /api/v01/admin/categories
"""

import os
import uuid
import mimetypes

from flask import request, jsonify
from werkzeug.utils import secure_filename

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.categories import (
    get_all_categories, get_category_by_id,
    create_category, update_category, delete_category,
    category_has_exams,
)
from app.services import image_storage_service
import app.config as config

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _upload(file_storage) -> tuple:
    """Upload a category image via the provider-independent storage layer.
    Returns (storage_key, url) — same shape as before, written to
    categories.drive_file_id / categories.image_url respectively.
    Raises ValueError on validation failure (bad type/oversized), so the
    route can report it instead of silently dropping the image."""
    if not file_storage or not file_storage.filename:
        return None, None

    ext = os.path.splitext(secure_filename(file_storage.filename))[1].lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Unsupported image type ({ext or 'unknown'}). Allowed: {', '.join(sorted(ALLOWED_EXTS))}.")

    file_storage.seek(0, os.SEEK_END)
    size_kb = file_storage.tell() / 1024
    if size_kb > config.MAX_IMAGE_SIZE_KB:
        raise ValueError(f"Image exceeds the {config.MAX_IMAGE_SIZE_KB} KB size limit.")
    file_storage.seek(0)

    original_name = secure_filename(file_storage.filename)
    filename = f"{os.path.splitext(original_name)[0]}_{uuid.uuid4().hex[:8]}{ext}"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    content = file_storage.read()
    return image_storage_service.upload_category_image(content, filename, mime)


def _delete_stored_image(storage_key: str):
    image_storage_service.delete_category_image(storage_key)


@admin_api_bp.route("/categories", methods=["GET"])
@require_admin_role
def api_categories():
    cats = get_all_categories()
    for cat in cats:
        cat["image_url"] = image_storage_service.resolve_category_image_url(cat)
    return jsonify({"categories": cats})


@admin_api_bp.route("/categories", methods=["POST"])
@require_admin_role
def create_category_route():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "Name is required"}), 400

    try:
        file_id, url = _upload(request.files.get("image"))
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        print(f"[admin.categories] upload error: {e}")
        return jsonify({"success": False, "message": "Image upload failed. Please try again."}), 500

    result = create_category({
        "name": name,
        "drive_file_id": file_id,
        "image_url": url,
    })
    if not result:
        return jsonify({"success": False, "message": "Failed — name may already exist"}), 400

    return jsonify({"success": True, "category": result})


@admin_api_bp.route("/categories/<int:cat_id>", methods=["PATCH"])
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
        try:
            file_id, url = _upload(new_file)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        except Exception as e:
            print(f"[admin.categories] upload error: {e}")
            return jsonify({"success": False, "message": "Image upload failed. Please try again."}), 500
        if file_id:
            _delete_stored_image(cat.get("drive_file_id"))
            updates["drive_file_id"] = file_id
            updates["image_url"] = url

    if not updates:
        return jsonify({"success": False, "message": "Nothing to update"}), 400

    ok = update_category(cat_id, updates)
    return jsonify({"success": ok})


@admin_api_bp.route("/categories/<int:cat_id>", methods=["DELETE"])
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

    _delete_stored_image(cat.get("drive_file_id"))
    ok = delete_category(cat_id)
    return jsonify({"success": ok})
