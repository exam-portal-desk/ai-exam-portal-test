"""
app/routes/misc.py
Miscellaneous routes: home, footer pages, debug endpoints.
"""

from flask import Blueprint, render_template, jsonify, session
import os

from app.middleware.session_guard import require_admin_role

misc_bp = Blueprint("misc", __name__)


@misc_bp.route("/")
def home():
    return render_template("index.html")


# Footer / static info pages
for _name, _path in [
    ("privacy_policy",  "privacy_policy.html"),
    ("terms_of_service","terms_of_service.html"),
    ("account_deletion_policy", "account_deletion_policy.html"),
    ("support",         "support.html"),
    ("contact",         "contact.html"),
    ("about",           "about.html"),
]:
    def _make_view(template):
        def _view():
            return render_template(template)
        return _view

    misc_bp.add_url_rule(
        f"/{_name.replace('_','-')}",
        endpoint=_name,
        view_func=_make_view(_path),
    )


@misc_bp.route("/debug/env-check")
@require_admin_role
def debug_env_check():
    import app.config as config

    env_status = {}
    for var in ["SECRET_KEY", "DATABASE_URL"]:
        env_status[var] = {"status": "Present" if os.environ.get(var) else "MISSING"}

    storage_status = _storage_health()
    return jsonify({
        "environment": env_status,
        "storage": {"backend": config.STORAGE_BACKEND, **storage_status},
    })


@misc_bp.route("/debug/service-status")
@require_admin_role
def debug_service_status():
    import app.config as config
    status = _storage_health()
    return jsonify({"storage_backend": config.STORAGE_BACKEND, **status})


def _storage_health() -> dict:
    try:
        from app.storage import get_storage
        get_storage().list_objects(limit=1)
        return {"status": "OK"}
    except Exception as e:
        return {"status": f"Error: {e}"}


@misc_bp.route("/api-docs")
def api_docs():
    return render_template("api_docs.html")