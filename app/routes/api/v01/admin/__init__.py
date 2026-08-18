"""
app/routes/api/v01/admin/__init__.py
Aggregates all admin JSON/AJAX API sub-blueprints into a single
`admin_api_bp`, mounted at /api/v01/admin in the app factory. The HTML
counterparts live under app/routes/web/admin/.
"""

from flask import Blueprint

admin_api_bp = Blueprint("admin_api", __name__, url_prefix="/api/v01/admin")

# Import and register every admin API sub-module.
# Each module attaches its routes to admin_api_bp via @admin_api_bp.route(...)
from app.routes.api.v01.admin import (  # noqa: E402, F401
    exams,
    questions,
    users,
    analytics,
    attempts,
    requests,
    images,
    ai_centre,
    categories,
    storage,
)
