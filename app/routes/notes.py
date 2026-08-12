"""Authenticated student Notes routes and Phase 1 notebook APIs."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session, Response
import json

import config

from app.middleware.session_guard import require_user_role
from app.services import notes_service
from app.services import notes_storage_service
from app.storage import get_storage
from app.utils.notes_validation import NotesValidationError, validate_notebook_id


notes_bp = Blueprint("notes", __name__)


def _api_error(message: str, status: int = 400):
    return jsonify({"success": False, "message": message}), status


def _payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise NotesValidationError("Send a valid JSON object.")
    return data


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


@notes_bp.route("/api/notes/notebooks", methods=["POST"])
@require_user_role
def create_notebook_api():
    try:
        notebook = notes_service.create_notebook(session["user_id"], _payload())
        return jsonify({"success": True, "notebook": notebook}), 201
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to create the notebook. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>", methods=["GET", "PATCH", "DELETE"])
@require_user_role
def notebook_api(notebook_id: str):
    try:
        notebook_id = validate_notebook_id(notebook_id)
        user_id = session["user_id"]
        if request.method == "PATCH":
            notebook = notes_service.update_notebook(user_id, notebook_id, _payload())
            if not notebook:
                return _api_error("Notebook not found.", 404)
            return jsonify({"success": True, "notebook": notebook})
        if request.method == "DELETE":
            if not notes_service.delete_notebook(user_id, notebook_id):
                return _api_error("Notebook not found.", 404)
            return jsonify({"success": True, "message": "Notebook moved to Trash."})
        from app.db.notes import get_owned_notebook
        notebook = get_owned_notebook(notebook_id, user_id)
        if not notebook:
            return _api_error("Notebook not found.", 404)
        return jsonify({"success": True, "notebook": notebook})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to process this notebook request. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/restore", methods=["POST"])
@require_user_role
def restore_notebook_api(notebook_id: str):
    try:
        notebook = notes_service.restore_notebook(session["user_id"], notebook_id)
        if not notebook:
            return _api_error("Notebook not found in Trash.", 404)
        return jsonify({"success": True, "notebook": notebook, "message": "Notebook restored."})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to restore the notebook. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/pages", methods=["GET", "POST"])
@require_user_role
def pages_api(notebook_id: str):
    try:
        if request.method == "GET":
            if not notes_service.get_editor_notebook(session["user_id"], notebook_id):
                return _api_error("Notebook not found.", 404)
            pages = notes_service.get_pages(session["user_id"], notebook_id)
            return jsonify({"success": True, "pages": pages})
        page = notes_service.create_page(session["user_id"], notebook_id, _payload().get("title"))
        if not page:
            return _api_error("Notebook not found.", 404)
        return jsonify({"success": True, "page": page}), 201
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update pages. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/pages/<page_id>", methods=["PATCH", "DELETE"])
@require_user_role
def page_api(notebook_id: str, page_id: str):
    try:
        if request.method == "PATCH":
            page = notes_service.update_page(session["user_id"], notebook_id, page_id, _payload().get("title"))
            if not page:
                return _api_error("Page not found.", 404)
            return jsonify({"success": True, "page": page})
        if not notes_service.delete_page(session["user_id"], notebook_id, page_id):
            return _api_error("Page not found.", 404)
        return jsonify({"success": True})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update this page. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/pages/<page_id>/objects", methods=["GET", "PUT"])
@require_user_role
def page_objects_api(notebook_id: str, page_id: str):
    try:
        if request.method == "GET":
            objects = notes_service.get_page_objects(session["user_id"], notebook_id, page_id)
            if objects is None:
                return _api_error("Page not found.", 404)
            return jsonify({"success": True, "objects": objects})
        payload = _payload()
        objects = notes_service.save_page_objects(session["user_id"], notebook_id, page_id, payload.get("objects", []), payload.get("deleted_ids", []))
        if objects is None:
            return _api_error("Page not found.", 404)
        return jsonify({"success": True, "objects": objects})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to save your changes. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/assets", methods=["POST"])
@require_user_role
def upload_asset_api(notebook_id: str):
    try:
        notebook = notes_service.get_editor_notebook(session["user_id"], notebook_id)
        if not notebook:
            return _api_error("Notebook not found.", 404)
        result = notes_storage_service.upload_image(session["user_id"], notebook["id"], request.files.get("image"))
        return jsonify({"success": True, **result}), 201
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to upload the image. Please try again.", 500)


@notes_bp.route("/api/notes/assets/<asset_id>/url")
@require_user_role
def asset_url_api(asset_id: str):
    try:
        from app.db.notes import get_owned_asset
        asset = get_owned_asset(asset_id, session["user_id"])
        if not asset:
            return _api_error("Image not found.", 404)
        return jsonify({"success": True, "url": notes_storage_service.signed_asset_url(asset)})
    except Exception:
        return _api_error("Unable to load the image. Please try again.", 500)


@notes_bp.route("/notes/asset-file/<path:storage_path>")
@require_user_role
def local_asset_file(storage_path: str):
    """Authorized asset streaming for the local storage backend — mirrors the
    ownership/public-notebook rules enforced for cloud signed URLs."""
    asset = notes_service.resolve_asset_for_serving(session["user_id"], storage_path)
    if not asset:
        return _api_error("Image not found.", 404)
    try:
        content = get_storage().download(storage_path)
    except Exception:
        return _api_error("Image not found.", 404)
    return Response(content, mimetype=asset.get("content_type") or "application/octet-stream")


@notes_bp.route("/api/notes/library")
@require_user_role
def public_library_api():
    try:
        return jsonify({"success": True, "notebooks": notes_service.public_library(request.args.get("q", ""), session.get("user_id"))})
    except Exception:
        return _api_error("Unable to load the public library. Please try again.", 500)


@notes_bp.route("/api/notes/library/<notebook_id>/<kind>", methods=["POST"])
@require_user_role
def public_engagement_api(notebook_id: str, kind: str):
    if kind not in {"like", "bookmark"}: return _api_error("Unsupported action.", 404)
    try:
        result = notes_service.toggle_public_engagement(session["user_id"], notebook_id, kind)
        if not result: return _api_error("Public notebook not found.", 404)
        return jsonify({"success": True, **result})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update this notebook. Please try again.", 500)


@notes_bp.route("/api/notes/library/<notebook_id>/pages")
@require_user_role
def public_pages_api(notebook_id: str):
    try:
        pages = notes_service.get_public_pages(notebook_id)
        if pages is None: return _api_error("Notebook not found.", 404)
        return jsonify({"success": True, "pages": pages})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to load pages. Please try again.", 500)


@notes_bp.route("/api/notes/library/<notebook_id>/pages/<page_id>/objects")
@require_user_role
def public_page_objects_api(notebook_id: str, page_id: str):
    try:
        objects = notes_service.get_public_page_objects(notebook_id, page_id)
        if objects is None: return _api_error("Page not found.", 404)
        return jsonify({"success": True, "objects": objects})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to load this page. Please try again.", 500)


@notes_bp.route("/api/notes/library/<notebook_id>/export")
@require_user_role
def export_public_notebook_api(notebook_id: str):
    try:
        exported = notes_service.export_public_notebook(notebook_id)
        if not exported: return _api_error("Public notebook not found.", 404)
        filename = "notes-notebook-export.json"
        return Response(json.dumps(exported, ensure_ascii=False), mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to export this notebook. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/import", methods=["POST"])
@require_user_role
def import_notebook_api():
    try:
        notebook = notes_service.import_notebook(session["user_id"], _payload())
        return jsonify({"success": True, "notebook": notebook}), 201
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to import this notebook. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/export-pdf", methods=["POST"])
@require_user_role
def export_notebook_pdf_api(notebook_id: str):
    try:
        notebook = notes_service.get_editor_notebook(session["user_id"], notebook_id)
        if not notebook:
            return _api_error("Notebook not found.", 404)
        if "pages" in request.form:
            try:
                pages = json.loads(request.form["pages"])
            except ValueError:
                return _api_error("Send a valid JSON object.", 400)
        else:
            pages = _payload().get("pages", [])
        if not pages:
            return _api_error("Nothing to export.", 400)
        from app.services.pdf_service import build_notebook_pdf
        pdf = build_notebook_pdf(notebook.get("title") or "Notebook", pages)
        safe_name = "".join(ch for ch in (notebook.get("title") or "notebook") if ch.isalnum() or ch in " -_").strip() or "notebook"
        return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to export this notebook as PDF. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/permanent", methods=["DELETE"])
@require_user_role
def permanently_delete_notebook_api(notebook_id: str):
    try:
        if not notes_service.permanently_delete_notebook(session["user_id"], notebook_id):
            return _api_error("Notebook not found in Trash.", 404)
        return jsonify({"success": True, "message": "Notebook permanently deleted."})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to permanently delete this notebook. Please try again.", 500)


@notes_bp.route("/api/notes/notebooks/<notebook_id>/export")
@require_user_role
def export_notebook_api(notebook_id: str):
    try:
        exported = notes_service.export_owned_notebook(session["user_id"], notebook_id)
        if not exported:
            return _api_error("Notebook not found.", 404)
        filename = "notes-notebook-export.json"
        return Response(json.dumps(exported, ensure_ascii=False), mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to export this notebook. Please try again.", 500)