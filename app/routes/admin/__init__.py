"""
app/routes/admin/__init__.py
Aggregates all admin sub-blueprints into a single `admin_bp`
that is registered at /admin in the app factory.
"""

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, template_folder="../../../templates")

# Import and register every admin sub-module.
# Each module attaches its routes to admin_bp via @admin_bp.route(...)
from app.routes.admin import (  # noqa: E402, F401
    auth,
    dashboard,
    exams,
    questions,
    subjects,
    users,
    results,
    attempts,
    requests,
    images,
    ai_centre,
    categories,
)