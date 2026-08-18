"""
app/routes/web/admin/storage.py
Admin Object Storage dashboard page shell. All data (browse/search/preview/
delete) is loaded client-side via app/routes/api/v01/admin/storage.py, so
this route only needs to report which backend is currently active.
"""

from flask import render_template

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
import app.config as config


@admin_bp.route("/object-storage")
@require_admin_role
def object_storage():
    backend_label = {"local": "Local filesystem", "s3": "S3-compatible"}.get(
        config.STORAGE_BACKEND, config.STORAGE_BACKEND
    )
    return render_template(
        "admin/object_storage.html",
        backend=config.STORAGE_BACKEND,
        backend_label=backend_label,
        bucket=config.STORAGE_BUCKET if config.STORAGE_BACKEND == "s3" else config.STORAGE_LOCAL_ROOT,
    )
