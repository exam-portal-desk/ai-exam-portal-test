"""
app/routes/web/admin/categories.py
Admin categories page. The JSON API (create/update/delete/list) that
used to live alongside this in app/routes/admin/categories.py now lives
in app/routes/api/v01/admin/categories.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.categories import get_all_categories
from app.services.image_storage_service import resolve_category_image_url
import app.config as config


@admin_bp.route("/categories")
@require_admin_role
def categories():
    cats = get_all_categories()
    for cat in cats:
        cat["image_url"] = resolve_category_image_url(cat)
    return render_template("admin/categories.html", categories=cats, max_image_size_kb=config.MAX_IMAGE_SIZE_KB)
