"""
app/routes/api/v01/images.py
Auth-gated streaming for category/question images on the local storage
backend (STORAGE_BACKEND=local). Category/question images have no per-user
ownership concept — any authenticated session may view them. S3-backed
images never hit this route; they resolve straight to a presigned URL (see
app/services/image_storage_service.py).
"""

import mimetypes

from flask import Blueprint, session, jsonify, Response

from app.storage import get_storage

images_api_bp = Blueprint("images_api", __name__, url_prefix="/api/v01/images")


@images_api_bp.route("/asset/<path:key>")
def get_local_asset(key):
    if not session.get("user_id"):
        return jsonify({"success": False, "message": "Authentication required"}), 401

    try:
        content = get_storage().download(key)
    except Exception:
        return jsonify({"success": False, "message": "Image not found"}), 404

    mime_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    resp = Response(content, mimetype=mime_type)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp
