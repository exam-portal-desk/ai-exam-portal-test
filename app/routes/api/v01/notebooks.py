"""
app/routes/api/v01/notebooks.py
Notebooks / pages / assets / public-library JSON API (v01). Relocated from
app/routes/notes.py — logic unchanged, only the URL prefix moved from
/api/notes/... to /api/v01/... (the path suffix after that prefix is
identical to before, e.g. /api/notes/notebooks/<id> -> /api/v01/notebooks/<id>).

The 6 HTML page routes that used to live alongside these (including the
asset-file streaming route, which is NOT relocated here since its path is
also config.STORAGE_LOCAL_URL_PREFIX) live in app/routes/web/notes.py.
"""

from __future__ import annotations

import threading
import uuid

from flask import Blueprint, jsonify, request, session, Response
import json

from app.db.users import set_view_pref
from app.middleware.session_guard import require_user_role
from app.services import notes_service
from app.services import notes_storage_service
from app.utils.notes_validation import NotesValidationError, NotesPermissionError, validate_notebook_id


notes_api_bp = Blueprint("notes_api", __name__, url_prefix="/api/v01")

# ── Notebook import job store ────────────────────────────────────────────
# Same in-memory job + background-thread + polling pattern already used by
# AI question generation (app/routes/api/v01/admin/ai_centre.py) — process-local,
# not shared across multiple worker processes, same known/accepted constraint.
_import_jobs: dict = {}
_import_jobs_lock = threading.Lock()


def _import_job_update(job_id: str, **kwargs):
    with _import_jobs_lock:
        if job_id in _import_jobs:
            _import_jobs[job_id].update(kwargs)


def _run_import(job_id: str, user_id: int, payload: dict):
    def on_progress(phase: str, message: str, percent: int):
        _import_job_update(job_id, phase=phase, message=message, percent=percent)

    try:
        notebook = notes_service.import_notebook(user_id, payload, progress=on_progress)
        _import_job_update(job_id, status="done", phase="complete", percent=100,
                            message="Import complete", notebook=notebook, error=None)
    except (NotesValidationError, ValueError) as exc:
        last = dict(_import_jobs.get(job_id, {}))
        _import_job_update(job_id, status="failed", error=str(exc),
                            message=str(exc), percent=last.get("percent", 0))
    except Exception:
        import traceback
        traceback.print_exc()
        last = dict(_import_jobs.get(job_id, {}))
        _import_job_update(job_id, status="failed",
                            error="Unable to import this notebook. Please try again.",
                            message="Unable to import this notebook. Please try again.",
                            percent=last.get("percent", 0))


def _api_error(message: str, status: int = 400):
    return jsonify({"success": False, "message": message}), status


def _safe_export_filename(title: str | None) -> str:
    """Same sanitization export_notebook_pdf_api already used for its PDF filename —
    shared here so PDF and JSON exports (private and public) always name the downloaded
    file after the notebook the same way, instead of each export route inventing its own."""
    return "".join(ch for ch in (title or "notebook") if ch.isalnum() or ch in " -_").strip() or "notebook"


def _payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise NotesValidationError("Send a valid JSON object.")
    return data


@notes_api_bp.route("/notebooks/view-mode", methods=["PATCH"])
@require_user_role
def set_notes_view_mode_api():
    """Persists the My Notebooks grid/list view preference, in the
    'notes' section of the generic users.view_prefs jsonb column (see
    app.db.users.set_view_pref) — same mechanism the User Portal and
    Admin toggles use, consolidated from a dedicated notes_view_mode
    column by migrations/20260826_consolidate_notes_view_mode.sql."""
    try:
        view_mode = _payload().get("view_mode")
    except NotesValidationError as exc:
        return _api_error(str(exc))
    if view_mode not in ("grid", "list"):
        return _api_error("view_mode must be 'grid' or 'list'.")
    if not set_view_pref(session["user_id"], "notes", view_mode):
        return _api_error("Unable to save your view preference. Please try again.", 500)
    return jsonify({"success": True, "view_mode": view_mode})


@notes_api_bp.route("/notebooks", methods=["POST"])
@require_user_role
def create_notebook_api():
    try:
        notebook = notes_service.create_notebook(session["user_id"], _payload())
        return jsonify({"success": True, "notebook": notebook}), 201
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to create the notebook. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>", methods=["GET", "PATCH", "DELETE"])
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
        notebook = notes_service.get_editor_notebook(user_id, notebook_id)
        if not notebook:
            return _api_error("Notebook not found.", 404)
        return jsonify({"success": True, "notebook": notebook})
    except NotesPermissionError as exc:
        return _api_error(str(exc), 403)
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to process this notebook request. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/restore", methods=["POST"])
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


@notes_api_bp.route("/notebooks/<notebook_id>/shares", methods=["GET", "POST"])
@require_user_role
def notebook_shares_api(notebook_id: str):
    """Owner-only: list who has access (GET) or bulk-share with one or more
    users in one request (POST, body {"shares": [{"user_id","permission"}]})."""
    try:
        user_id = session["user_id"]
        if request.method == "GET":
            shares = notes_service.list_notebook_shares(user_id, notebook_id)
            if shares is None:
                return _api_error("Notebook not found.", 404)
            return jsonify({"success": True, "shares": shares})
        shares = notes_service.share_notebook(user_id, notebook_id, _payload().get("shares", []))
        if shares is None:
            return _api_error("Notebook not found.", 404)
        return jsonify({"success": True, "shares": shares}), 201
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update sharing. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/shares/<int:target_user_id>", methods=["PATCH", "DELETE"])
@require_user_role
def notebook_share_api(notebook_id: str, target_user_id: int):
    """PATCH (owner-only) changes one person's permission. DELETE removes
    access — the owner may remove anyone; a recipient may remove only
    themselves ("leave this shared notebook")."""
    try:
        user_id = session["user_id"]
        if request.method == "PATCH":
            share = notes_service.update_notebook_share(user_id, notebook_id, target_user_id, _payload().get("permission"))
            if not share:
                return _api_error("Share not found.", 404)
            return jsonify({"success": True, "share": share})
        if not notes_service.remove_notebook_share(user_id, notebook_id, target_user_id):
            return _api_error("Share not found.", 404)
        return jsonify({"success": True})
    except NotesPermissionError as exc:
        return _api_error(str(exc), 403)
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update sharing. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/share-search")
@require_user_role
def notebook_share_search_api(notebook_id: str):
    """Owner-only candidate search for the sharing dialog — excludes self
    and anyone who already has a share, verified server-side."""
    term = request.args.get("q", "").strip()
    if len(term) < 2:
        return jsonify({"success": True, "users": []})
    try:
        users = notes_service.search_shareable_users(session["user_id"], notebook_id, term)
        if users is None:
            return _api_error("Notebook not found.", 404)
        return jsonify({"success": True, "users": users})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to search users. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/leave", methods=["POST"])
@require_user_role
def leave_notebook_api(notebook_id: str):
    """A shared user removing their own access — same underlying rule as the
    owner-or-self check in notebook_share_api's DELETE, just without needing
    the caller's own numeric user id in the URL."""
    try:
        user_id = session["user_id"]
        if not notes_service.remove_notebook_share(user_id, notebook_id, user_id):
            return _api_error("You don't have access to this notebook.", 404)
        return jsonify({"success": True})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to leave this notebook. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/pages", methods=["GET", "POST"])
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
    except NotesPermissionError as exc:
        return _api_error(str(exc), 403)
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update pages. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/pages/<page_id>", methods=["PATCH", "DELETE"])
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
    except NotesPermissionError as exc:
        return _api_error(str(exc), 403)
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to update this page. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/pages/<page_id>/objects", methods=["GET", "PUT"])
@require_user_role
def page_objects_api(notebook_id: str, page_id: str):
    try:
        if request.method == "GET":
            objects = notes_service.get_page_objects(session["user_id"], notebook_id, page_id)
            if objects is None:
                return _api_error("Page not found.", 404)
            return jsonify({"success": True, "objects": objects})
        payload = _payload()
        try:
            start_index = max(0, int(payload.get("start_index", 0)))
        except (TypeError, ValueError):
            return _api_error("Invalid save data.")
        result = notes_service.save_page_objects(session["user_id"], notebook_id, page_id, payload.get("objects", []), payload.get("deleted", []), start_index)
        if result is None:
            return _api_error("Page not found.", 404)
        return jsonify({"success": True, "saved": result["saved"], "conflicts": result["conflicts"]})
    except NotesPermissionError as exc:
        return _api_error(str(exc), 403)
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to save your changes. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/assets", methods=["POST"])
@require_user_role
def upload_asset_api(notebook_id: str):
    try:
        notebook = notes_service.get_editor_notebook(session["user_id"], notebook_id)
        if not notebook:
            return _api_error("Notebook not found.", 404)
        notes_service.assert_can_edit(notebook)
        result = notes_storage_service.upload_image(session["user_id"], notebook["id"], request.files.get("image"))
        return jsonify({"success": True, **result}), 201
    except NotesPermissionError as exc:
        return _api_error(str(exc), 403)
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to upload the image. Please try again.", 500)


@notes_api_bp.route("/assets/<asset_id>/url")
@require_user_role
def asset_url_api(asset_id: str):
    try:
        asset = notes_service.resolve_asset_for_serving_by_id(session["user_id"], asset_id)
        if not asset:
            return _api_error("Image not found.", 404)
        return jsonify({"success": True, "url": notes_storage_service.asset_proxy_url(asset["id"])})
    except Exception:
        return _api_error("Unable to load the image. Please try again.", 500)


@notes_api_bp.route("/assets/<asset_id>/file")
@require_user_role
def asset_file_api(asset_id: str):
    """Same-origin byte stream for one asset, by asset_id, regardless of storage backend.

    ROOT CAUSE this exists for: PDF export renders each page on an off-screen canvas and calls
    toDataURL() on it — a browser refuses that ("Tainted canvases may not be exported") the
    moment the canvas has ever drawn a cross-origin image that wasn't loaded with CORS the
    resource's own server also agreed to via Access-Control-Allow-Origin. This app's S3-compatible
    storage backend hands the browser a direct signed URL on the bucket's own domain, which is
    exactly that cross-origin case (and the bucket's CORS policy isn't something this app's code
    can guarantee). Routing the export canvas's image loads through this same-origin endpoint
    instead removes the cross-origin condition entirely — the browser never sees a foreign-origin
    image, so the canvas is never tainted, for either storage backend, without touching how images
    are uploaded, displayed, or signed anywhere else. See getPageObjectsForExport in editor.js.
    """
    from app.storage import get_storage
    asset = notes_service.resolve_asset_for_serving_by_id(session["user_id"], asset_id)
    if not asset:
        return _api_error("Image not found.", 404)
    try:
        content = get_storage().download(asset["storage_path"])
    except Exception:
        return _api_error("Image not found.", 404)
    return Response(content, mimetype=asset.get("content_type") or "application/octet-stream")


@notes_api_bp.route("/library")
@require_user_role
def public_library_api():
    try:
        return jsonify({"success": True, "notebooks": notes_service.public_library(request.args.get("q", ""), session.get("user_id"))})
    except Exception:
        return _api_error("Unable to load the public library. Please try again.", 500)


@notes_api_bp.route("/library/<notebook_id>/<kind>", methods=["POST"])
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


@notes_api_bp.route("/library/<notebook_id>/pages")
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


@notes_api_bp.route("/library/<notebook_id>/pages/<page_id>/objects")
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


@notes_api_bp.route("/library/<notebook_id>/export")
@require_user_role
def export_public_notebook_api(notebook_id: str):
    try:
        exported = notes_service.export_public_notebook(notebook_id)
        if not exported: return _api_error("Public notebook not found.", 404)
        filename = f'{_safe_export_filename((exported.get("notebook") or {}).get("title"))}.json'
        return Response(json.dumps(exported, ensure_ascii=False), mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to export this notebook. Please try again.", 500)


@notes_api_bp.route("/notebooks/import", methods=["POST"])
@require_user_role
def import_notebook_api():
    """Starts the import as a background job and returns immediately — the actual work (and its
    real progress) is tracked via GET /notebooks/import/status/<job_id> below. Only the cheap
    request-shape check happens synchronously here; full schema validation runs inside the job
    itself (see notes_service.import_notebook's "validating" phase) so a bad file still fails
    cleanly through the same progress/status UI instead of a different code path."""
    try:
        payload = _payload()
    except NotesValidationError as exc:
        return _api_error(str(exc))

    user_id = session["user_id"]
    job_id = uuid.uuid4().hex[:12]
    with _import_jobs_lock:
        _import_jobs[job_id] = {
            "status": "running",
            "phase": "validating",
            "message": "Preparing notebook...",
            "percent": 0,
            "notebook": None,
            "error": None,
            "_owner_id": user_id,
        }

    thread = threading.Thread(target=_run_import, args=(job_id, user_id, payload), daemon=True)
    thread.start()
    return jsonify({"success": True, "job_id": job_id})


@notes_api_bp.route("/notebooks/import/status/<job_id>", methods=["GET"])
@require_user_role
def import_notebook_status_api(job_id: str):
    with _import_jobs_lock:
        job = dict(_import_jobs.get(job_id, {}))
    # Not found AND not-yours return the identical 404 — never confirm a job_id exists to
    # anyone but the user who started it (this tracks import progress for a private notebook).
    if not job or job.get("_owner_id") != session["user_id"]:
        return _api_error("Import job not found.", 404)
    job.pop("_owner_id", None)
    return jsonify({"success": True, **job})


@notes_api_bp.route("/notebooks/<notebook_id>/export-pdf", methods=["POST"])
@require_user_role
def export_notebook_pdf_api(notebook_id: str):
    try:
        # Owner export (private/unlisted) or a currently-public notebook viewed read-only —
        # same one export flow/service for both, just two ways to be allowed to read it.
        notebook = notes_service.get_editor_notebook(session["user_id"], notebook_id) or notes_service.public_notebook(notebook_id)
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
        grid_theme = None
        if "gridTheme" in request.form:
            try:
                grid_theme = json.loads(request.form["gridTheme"])
            except ValueError:
                grid_theme = None
        from app.services.pdf_service import build_notebook_pdf
        pdf = build_notebook_pdf(notebook.get("title") or "Notebook", pages, grid_theme)
        safe_name = _safe_export_filename(notebook.get("title"))
        return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to export this notebook as PDF. Please try again.", 500)


@notes_api_bp.route("/notebooks/<notebook_id>/permanent", methods=["DELETE"])
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


@notes_api_bp.route("/notebooks/<notebook_id>/export")
@require_user_role
def export_notebook_api(notebook_id: str):
    try:
        exported = notes_service.export_owned_notebook(session["user_id"], notebook_id)
        if not exported:
            return _api_error("Notebook not found.", 404)
        filename = f'{_safe_export_filename((exported.get("notebook") or {}).get("title"))}.json'
        return Response(json.dumps(exported, ensure_ascii=False), mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except (NotesValidationError, ValueError) as exc:
        return _api_error(str(exc))
    except Exception:
        return _api_error("Unable to export this notebook. Please try again.", 500)
