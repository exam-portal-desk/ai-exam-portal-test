"""
app/routes/api/v01/images.py
Auth-gated streaming for category/question/profile/chat-background images —
the same route for either storage backend (get_storage().download() works
identically for local or S3). Category/question images have no per-user
ownership concept — any authenticated session may view them. This is the
only URL app/services/image_storage_service.py ever hands back to the
browser; the underlying storage/bucket URL (a raw S3 presigned URL, on the
S3 backend) is never exposed to the client.
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
