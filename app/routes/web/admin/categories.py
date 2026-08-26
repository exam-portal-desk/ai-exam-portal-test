"""
app/routes/web/admin/categories.py
Admin categories page. The JSON API (create/update/delete/list) that
used to live alongside this in app/routes/admin/categories.py now lives
in app/routes/api/v01/admin/categories.py.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.categories import get_categories_page
from app.services.image_storage_service import resolve_category_image_url
import app.config as config

CATEGORIES_PAGE_SIZE = 20


@admin_bp.route("/categories")
@require_admin_role
def categories():
    page_data = get_categories_page(page=1, per_page=CATEGORIES_PAGE_SIZE)
    for cat in page_data["categories"]:
        cat["image_url"] = resolve_category_image_url(cat)
    return render_template(
        "admin/categories.html",
        categories=page_data["categories"], max_image_size_kb=config.MAX_IMAGE_SIZE_KB,
        categories_total=page_data["total"], categories_total_pages=page_data["total_pages"],
        categories_per_page=CATEGORIES_PAGE_SIZE,
    )
