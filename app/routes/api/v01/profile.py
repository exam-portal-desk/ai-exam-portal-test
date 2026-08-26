"""
app/routes/api/v01/profile.py
Profile-photo JSON API — shared by both the User and Admin portals. Neither
require_user_role nor require_admin_role fits here: require_user_role
explicitly rejects admin sessions (redirects/403s them to the admin portal)
and require_admin_role rejects plain user sessions — so this uses the same
lightweight "any authenticated session" check already used by
app/routes/api/v01/images.py for the same portal-agnostic reason.
"""

import os
import uuid
import mimetypes
from functools import wraps

from flask import request, jsonify, session, Blueprint
from werkzeug.utils import secure_filename

from app.db.users import get_user_by_id, update_user
from app.db.misc import get_requests_by_user, create_request
from app.middleware.session_guard import require_user_role
from app.services import image_storage_service
from app.utils.datetime_service import now_utc_naive
import app.config as config

profile_api_bp = Blueprint("profile_api", __name__, url_prefix="/api/v01/profile")

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _require_login(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"success": False, "message": "Authentication required"}), 401
        return f(*args, **kwargs)
    return wrapped


def _upload(file_storage, user_id: int) -> tuple:
    if not file_storage or not file_storage.filename:
        raise ValueError("No file provided.")

    ext = os.path.splitext(secure_filename(file_storage.filename))[1].lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Unsupported image type ({ext or 'unknown'}). Allowed: {', '.join(sorted(ALLOWED_EXTS))}.")

    file_storage.seek(0, os.SEEK_END)
    size_kb = file_storage.tell() / 1024
    if size_kb > config.MAX_PROFILE_PHOTO_SIZE_KB:
        raise ValueError(f"Image exceeds the {config.MAX_PROFILE_PHOTO_SIZE_KB} KB size limit.")
    file_storage.seek(0)

    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    content = file_storage.read()
    return image_storage_service.upload_profile_photo(user_id, content, filename, mime)


@profile_api_bp.route("/photo", methods=["POST"])
@_require_login
def upload_photo():
    user_id = int(session["user_id"])

    try:
        key, url = _upload(request.files.get("photo"), user_id)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        print(f"[profile] upload error: {e}")
        return jsonify({"success": False, "message": "Photo upload failed. Please try again."}), 500

    user = get_user_by_id(user_id)
    old_key = (user or {}).get("profile_photo_key")

    if not update_user(user_id, {"profile_photo_key": key}):
        image_storage_service.delete_profile_photo(key)
        return jsonify({"success": False, "message": "Could not save your photo. Please try again."}), 500

    if old_key and old_key != key:
        image_storage_service.delete_profile_photo(old_key)

    session["profile_photo_key"] = key
    session.modified = True

    return jsonify({"success": True, "photo_url": url})


@profile_api_bp.route("/photo", methods=["DELETE"])
@_require_login
def remove_photo():
    user_id = int(session["user_id"])
    user = get_user_by_id(user_id)
    old_key = (user or {}).get("profile_photo_key")

    update_user(user_id, {"profile_photo_key": None})
    session["profile_photo_key"] = None
    session.modified = True

    if old_key:
        image_storage_service.delete_profile_photo(old_key)

    return jsonify({"success": True})


@profile_api_bp.route("/admin-access-request", methods=["POST"])
@require_user_role
def submit_admin_access_request():
    """Submit a new admin-access request from the logged-in user's own
    Profile page. Reuses the exact same requests_raised table/DB helpers and
    one-pending-request rule as the old public form (app/routes/api/v01/
    access_requests.py's api_submit_access_request) — the only real
    difference is identity: username/email/current role come from the
    session, not from client-supplied fields, since we now have a real
    logged-in user instead of a re-typed username+email pair."""
    user = get_user_by_id(int(session["user_id"]))
    if not user:
        return jsonify({"success": False, "message": "Account not found."}), 404

    data = request.get_json() or {}
    reason = str(data.get("reason", "")).strip()
    if not reason:
        return jsonify({"success": False, "message": "Please provide a reason."}), 400

    username = user["username"]
    email = user["email"]
    current_access = str(user.get("role") or "user").strip().lower()
    requested_access = "admin"

    if "admin" in current_access.split(","):
        return jsonify({"success": False, "message": "You already have admin access."}), 400

    pending = [r for r in get_requests_by_user(username, email) if r.get("request_status") == "pending"]
    if pending:
        return jsonify({"success": False, "message": "You already have a pending request."}), 400

    created = create_request({
        "username": username, "email": email,
        "current_access": current_access, "requested_access": requested_access,
        "request_date": now_utc_naive().isoformat(),
        "request_status": "pending",
        "reason": f"[USER REQUEST] {reason}",
    })
    if not created:
        return jsonify({"success": False, "message": "Failed to save request. Please try again."}), 500

    return jsonify({"success": True, "message": "Request submitted. Please wait for admin approval."})
