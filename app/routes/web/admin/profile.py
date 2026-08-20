"""
app/routes/web/admin/profile.py
Admin portal Profile page.
"""

from flask import render_template, session

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.users import get_user_profile_by_id
from app.services.image_storage_service import resolve_profile_photo_url
import app.config as config


@admin_bp.route("/profile")
@require_admin_role
def profile():
    user = get_user_profile_by_id(int(session["user_id"])) or {}
    photo_url = resolve_profile_photo_url(user.get("profile_photo_key"))
    return render_template(
        "admin/profile.html", profile=user, photo_url=photo_url,
        max_photo_kb=config.MAX_PROFILE_PHOTO_SIZE_KB,
    )
