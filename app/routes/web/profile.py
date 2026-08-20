"""
app/routes/web/profile.py
User portal Profile page.
"""

from flask import Blueprint, render_template, session

from app.middleware.session_guard import require_user_role
from app.db.users import get_user_profile_by_id
from app.services.image_storage_service import resolve_profile_photo_url
import app.config as config

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/profile")
@require_user_role
def my_profile():
    user = get_user_profile_by_id(int(session["user_id"])) or {}
    photo_url = resolve_profile_photo_url(user.get("profile_photo_key"))
    return render_template(
        "profile.html", profile=user, photo_url=photo_url,
        max_photo_kb=config.MAX_PROFILE_PHOTO_SIZE_KB,
    )
