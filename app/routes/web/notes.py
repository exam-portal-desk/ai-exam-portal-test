"""
app/routes/web/notes.py
Notes/Notebooks web pages. The JSON API (notebook/page/asset CRUD, public
library, import/export) that used to live alongside these in
app/routes/notes.py now lives in app/routes/api/v01/notebooks.py.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session, Response

import app.config as config

from app.db.users import get_notes_view_mode
from app.middleware.session_guard import require_user_role
from app.services import notes_service
from app.storage import get_storage
from app.utils.notes_validation import NotesValidationError
from app.db.dashboard_events import mark_event_seen


notes_bp = Blueprint("notes", __name__)


def _api_error(message: str, status: int = 400):
    return jsonify({"success": False, "message": message}), status


@notes_bp.route("/notes")
@require_user_role
def my_notes():
    user_id = session["user_id"]
    section = request.args.get("section", "created")
    if section not in notes_service.NOTEBOOK_SECTIONS:
        section = "created"
    # Only the active section is ever queried on page load — switching tabs
    # client-side fetches the other sections on demand (see /notes/section).
    notebooks, has_more = notes_service.get_notebook_section(user_id, section)
    return render_template(
        "notes/index.html",
        active_section=section,
        notebooks=notebooks,
        has_more=has_more,
        sections=notes_service.NOTEBOOK_SECTIONS,
        section_labels=notes_service.SECTION_LABELS,
        view_mode=get_notes_view_mode(user_id),
    )


@notes_bp.route("/notes/section")
@require_user_role
def notes_section():
    """Lazily fetches one section's notebooks — used both to switch tabs
    (offset=0) and for that section's own Load More (offset=N). Only the
    requested section is ever queried; the other tabs stay untouched until
    the user actually opens them."""
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    section = request.args.get("section", "created")
    if section not in notes_service.NOTEBOOK_SECTIONS:
        return _api_error("Unknown notebook section.", 400)
    search = request.args.get("q", "").strip()[:100] or None
    notebooks, has_more = notes_service.get_notebook_section(session["user_id"], section, offset=offset, search=search)
    html = render_template("notes/_notebook_cards_fragment.html", notebooks=notebooks, section=section)
    return jsonify({"success": True, "html": html, "has_more": has_more, "count": len(notebooks)})


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
        if notebook.get("access") != "owner" and notebook.get("share_id"):
            mark_event_seen(session["user_id"], "notebook_share", notebook["share_id"])
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
    notebooks, has_more = notes_service.public_library(term, session.get("user_id"))
    return render_template(
        "notes/library.html",
        notebooks=notebooks,
        has_more=has_more,
        search_term=term,
        view_mode=get_notes_view_mode(session["user_id"]),
    )


@notes_bp.route("/notes/library/load-more")
@require_user_role
def library_load_more():
    term = request.args.get("q", "")
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    notebooks, has_more = notes_service.public_library(term, session.get("user_id"), offset=offset)
    html = render_template("notes/_library_cards_fragment.html", notebooks=notebooks)
    return jsonify({"success": True, "html": html, "has_more": has_more, "count": len(notebooks)})


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
