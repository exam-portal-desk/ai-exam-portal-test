"""
app/routes/misc.py
Miscellaneous routes: home, footer pages, debug endpoints.
"""

from flask import Blueprint, render_template, jsonify, session, Response, url_for
import os
import mimetypes

from app.middleware.session_guard import require_admin_role
import app.config as config

misc_bp = Blueprint("misc", __name__)


@misc_bp.route("/")
def home():
    return render_template(
        "index.html",
        ceo_name=config.CEO_NAME,
        ceo_title=config.CEO_TITLE,
        ceo_image_url=url_for("misc.ceo_photo") if config.CEO_IMAGE_KEY else None,
    )


# ─────────────────────────────────────────────
# Public landing-page asset: the Founder/CEO photo. Unlike
# app/routes/api/v01/images.py (auth-gated — every other image in the app
# has a logged-in viewer), this one MUST be reachable by anonymous visitors
# since it's on the public landing page — but it still never exposes the
# underlying storage key/URL to the browser, exactly the same "resolve
# through the app, never hand out a raw bucket URL" pattern. The actual key
# comes from config.CEO_IMAGE_KEY (env var), never hardcoded here.
#
# Cached in-process after the first request — this is a single, essentially
# static asset (an admin manually swaps CEO_IMAGE_KEY + restarts to change
# it), so there is no reason a public page getting real traffic should hit
# object storage on every single load. Falls back to re-fetching if the
# bytes were never successfully cached (e.g. storage was briefly down).
# ─────────────────────────────────────────────
_ceo_photo_cache = {"key": None, "bytes": None, "mime": None}


@misc_bp.route("/assets/ceo-photo")
def ceo_photo():
    key = config.CEO_IMAGE_KEY
    if not key:
        return jsonify({"success": False, "message": "Not configured"}), 404

    cached = _ceo_photo_cache
    if cached["key"] != key or cached["bytes"] is None:
        try:
            from app.storage import get_storage
            content = get_storage().download(key)
        except Exception:
            return jsonify({"success": False, "message": "Image not found"}), 404
        cached["key"] = key
        cached["bytes"] = content
        cached["mime"] = mimetypes.guess_type(key)[0] or "image/jpeg"

    resp = Response(cached["bytes"], mimetype=cached["mime"])
    # Public + long-lived: a static founder photo, safe for browsers/CDNs to
    # cache aggressively rather than re-requesting on every landing-page view.
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return resp


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