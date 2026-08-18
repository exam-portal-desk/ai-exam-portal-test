"""
app/routes/web/notes.py
Notes/Notebooks web pages. The JSON API (notebook/page/asset CRUD, public
library, import/export) that used to live alongside these in
app/routes/notes.py now lives in app/routes/api/v01/notebooks.py.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session, Response

import app.config as config

from app.middleware.session_guard import require_user_role
from app.services import notes_service
from app.storage import get_storage
from app.utils.notes_validation import NotesValidationError


notes_bp = Blueprint("notes", __name__)


def _api_error(message: str, status: int = 400):
    return jsonify({"success": False, "message": message}), status


@notes_bp.route("/notes")
@require_user_role
def my_notes():
    notebooks = notes_service.get_my_notebooks(session["user_id"])
    return render_template("notes/index.html", notebooks=notebooks)


@notes_bp.route("/notes/trash")
@require_user_role
def notes_trash():
    notebooks = notes_service.get_my_trash(session["user_id"])
    return render_template(
        "notes/trash.html",
        notebooks=notebooks,
        retention_days=config.NOTES_TRASH_RETENTION_DAYS,
    )


@notes_bp.route("/notes/notebook/<notebook_id>")
@require_user_role
def notebook_editor(notebook_id: str):
    try:
        notebook = notes_service.get_editor_notebook(session["user_id"], notebook_id)
        if not notebook:
            return render_template("error.html", error_code=404, error_message="Notebook not found"), 404
        pages = notes_service.get_pages(session["user_id"], notebook_id)
        return render_template("notes/editor.html", notebook=notebook, pages=pages)
    except (NotesValidationError, ValueError):
        return render_template("error.html", error_code=404, error_message="Notebook not found"), 404


@notes_bp.route("/notes/public/<notebook_id>")
@require_user_role
def public_notebook_viewer(notebook_id: str):
    try:
        notebook = notes_service.public_notebook(notebook_id)
        if not notebook:
            return render_template("error.html", error_code=404, error_message="Notebook not found"), 404
        pages = notes_service.get_public_pages(notebook_id)
        return render_template("notes/editor.html", notebook=notebook, pages=pages, is_public=True)
    except (NotesValidationError, ValueError):
        return render_template("error.html", error_code=404, error_message="Notebook not found"), 404


@notes_bp.route("/notes/library")
@require_user_role
def public_library_page():
    term = request.args.get("q", "")
    return render_template("notes/library.html", notebooks=notes_service.public_library(term, session.get("user_id")), search_term=term)


@notes_bp.route("/notes/asset-file/<path:storage_path>")
@require_user_role
def local_asset_file(storage_path: str):
    """Authorized asset streaming for the local storage backend — mirrors the
    ownership/public-notebook rules enforced for cloud signed URLs.

    NOTE: this path is also config.STORAGE_LOCAL_URL_PREFIX, which may
    already be baked into stored asset URLs — do not rename/version this
    route without also handling existing stored references.
    """
    asset = notes_service.resolve_asset_for_serving(session["user_id"], storage_path)
    if not asset:
        return _api_error("Image not found.", 404)
    try:
        content = get_storage().download(storage_path)
    except Exception:
        return _api_error("Image not found.", 404)
    return Response(content, mimetype=asset.get("content_type") or "application/octet-stream")
