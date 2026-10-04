"""
app/routes/api/v01/admin/exams.py
Admin exam JSON API (v01): delete + release-results. Relocated from
app/routes/admin/exams.py.

  POST /admin/exams/delete/<id>           -> DELETE /api/v01/admin/exams/<id>
  POST /admin/exams/<id>/release-results  -> POST   /api/v01/admin/exams/<id>/release-results
"""

import os
import threading
import uuid

from flask import jsonify, flash, request, render_template_string, session, send_file

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_permission
from app.db.exams import (
    get_exam_by_id, release_exam_results, get_exams_page, set_scheduled_exam_cancelled,
    get_exams_by_ids_full, bulk_update_exam_fields,
)
from app.db.categories import get_all_categories
from app.db.subcategories import get_subcategory_by_id
from app.db import fetch_all, execute
from app.utils.datetime_service import format_calendar_date
from app.utils.instructions_formatter import render_exam_instructions
from app.utils.helpers import (
    parse_max_attempts, parse_passing_percentage, parse_instructions_field, parse_exam_date,
)
from app.services.exam_service import get_effective_status
from app.services import exam_export_service

_ROWS_TPL = (
    '{% from "admin/_exam_rows.html" import render_exam_row, render_exam_card, render_exam_edit_modal %}'
    '{% for exam in exams %}{{ render_exam_row(exam) }}{% endfor %}'
    '|||SPLIT|||'
    '{% for exam in exams %}{{ render_exam_card(exam) }}{% endfor %}'
    '|||SPLIT|||'
    '{% for exam in exams %}{{ render_exam_edit_modal(exam, categories) }}{% endfor %}'
)


@admin_api_bp.route("/exams", methods=["GET"])
@require_admin_permission("exam_management")
def api_exams_list():
    result = get_exams_page(
        search=request.args.get("q", "").strip(),
        category_id=request.args.get("category_id") or None,
        subcategory_id=request.args.get("subcategory_id") or None,
        status=request.args.get("status", "").strip(),
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 20),
    )
    for e in result["exams"]:
        e["date_display"] = format_calendar_date(e.get("date"))

    if request.args.get("partial"):
        # Server-rendered HTML for the row/card/edit-modal markup, so the
        # AJAX-paginated page never drifts from the initial server render —
        # one Jinja macro set (admin/_exam_rows.html) for both.
        rendered = render_template_string(_ROWS_TPL, exams=result["exams"], categories=get_all_categories())
        rows_html, cards_html, modals_html = rendered.split("|||SPLIT|||")
        result["rows_html"] = rows_html
        result["cards_html"] = cards_html
        result["modals_html"] = modals_html
        del result["exams"]

    return jsonify(result)


@admin_api_bp.route("/exams/preview-instructions", methods=["POST"])
@require_admin_permission("exam_management")
def preview_instructions():
    """Renders the same HTML the student-facing pages will show — the one
    renderer (app/utils/instructions_formatter.py) is reused verbatim here
    so the admin's live preview can never drift from the real thing."""
    text = (request.get_json(silent=True) or {}).get("instructions", "")
    return jsonify({"html": str(render_exam_instructions(text))})


@admin_api_bp.route("/exams/<int:exam_id>", methods=["DELETE"])
@require_admin_permission("exam_management")
def delete_exam_route(exam_id):
    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found"}), 404

    try:
        # Single set-based DELETE instead of one DELETE per result row
        # (flagged in the architecture audit as an N+1 write pattern).
        execute("DELETE FROM responses WHERE result_id IN (SELECT id FROM results WHERE exam_id=%s)", (exam_id,))
        execute("DELETE FROM results WHERE exam_id=%s", (exam_id,))
        execute("DELETE FROM exam_attempts WHERE exam_id=%s", (exam_id,))
        q_ids = [q["id"] for q in fetch_all("SELECT id FROM questions WHERE exam_id=%s", (exam_id,))]
        if q_ids:
            execute("DELETE FROM question_discussions WHERE question_id = ANY(%s)", (q_ids,))
            execute("DELETE FROM discussion_counts WHERE question_id = ANY(%s)", (q_ids,))
        execute("DELETE FROM questions WHERE exam_id=%s", (exam_id,))
        execute("DELETE FROM exams WHERE id=%s", (exam_id,))

        flash("Exam deleted successfully.", "info")
        return jsonify({"success": True, "message": f"Exam '{exam['name']}' deleted."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@admin_api_bp.route("/exams/<int:exam_id>/release-results", methods=["POST"])
@require_admin_permission("exam_management")
def release_results(exam_id):
    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found"}), 404

    new_state = not bool(exam.get("results_released"))
    if release_exam_results(exam_id, release=new_state):
        msg = (f"Results for '{exam['name']}' have been "
               + ("released." if new_state else "unreleased."))
        return jsonify({"success": True, "message": msg, "released": new_state})
    return jsonify({"success": False, "message": "Failed to update results."}), 500


@admin_api_bp.route("/exams/<int:exam_id>/cancel", methods=["POST"])
@require_admin_permission("exam_management")
def cancel_scheduled_exam(exam_id):
    """Toggle a Scheduled Exam's explicit 'cancelled' override — the only
    other writer of exams.status for a scheduled exam besides the create/
    edit routes' automatic 'scheduled' marker (app/db/exams.py:
    set_scheduled_exam_cancelled). Never applicable to a Manual Exam, which
    already has its own Upcoming/Ongoing/Completed status control."""
    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found"}), 404
    if not exam.get("scheduled_mode"):
        return jsonify({"success": False, "message": "Only a Scheduled Exam can be cancelled this way."}), 400

    new_state = str(exam.get("status", "")).lower().strip() != "cancelled"
    if set_scheduled_exam_cancelled(exam_id, cancelled=new_state):
        msg = f"'{exam['name']}' has been " + ("cancelled." if new_state else "restored to Scheduled.")
        return jsonify({"success": True, "message": msg, "cancelled": new_state})
    return jsonify({"success": False, "message": "Failed to update exam."}), 500


_BULK_ALLOWED_FIELDS = {
    "instructions", "max_attempts", "result_mode", "result_delay",
    "passing_percentage", "date", "duration", "status",
    "category_id", "subcategory_id", "positive_marks", "negative_marks",
}


@admin_api_bp.route("/exams/bulk-update", methods=["POST"])
@require_admin_permission("exam_management")
def bulk_update_exams_route():
    """Apply an admin-selected subset of fields across many exams in one
    request. A field is only ever touched when its own "Change this field"
    checkbox was ticked client-side, so the request body simply omits any
    field the admin didn't select — an unselected field can never be
    overwritten because it never appears in `fields` at all.

    Every value is re-validated with the exact same parsers/rules
    edit_exam() uses for a single exam (app/routes/web/admin/exams.py), and
    the same lifecycle locks apply per exam: a Scheduled Exam's status is
    never hand-set (mirrors edit_exam() never taking status from the form
    when scheduled_mode is true), and a Scheduled Exam's Date/Duration are
    frozen once it's no longer Upcoming (mirrors edit_exam()'s
    timing_changed guard — Date and Duration share the exact same
    eligibility check here since they're both "schedule" fields locked
    together). An exam ineligible for one of those fields is skipped for
    that field only (reported back as a warning) — every other requested
    field still applies to it, and every other exam in the batch is
    unaffected.

    Writes go through bulk_update_exam_fields(): one UPDATE per distinct
    field group requested (not per exam), all inside one transaction, so
    a failure partway through rolls back everything already applied in
    this call rather than leaving a half-updated batch."""
    body = request.get_json(silent=True) or {}
    exam_ids_raw = body.get("exam_ids") or []
    fields = body.get("fields") or {}

    if not isinstance(exam_ids_raw, list) or not exam_ids_raw:
        return jsonify({"success": False, "message": "No exams selected."}), 400
    if len(exam_ids_raw) > 200:
        return jsonify({"success": False, "message": "Too many exams selected (max 200 per bulk update)."}), 400
    try:
        exam_ids = [int(x) for x in exam_ids_raw]
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid exam id in selection."}), 400

    if not isinstance(fields, dict) or not fields:
        return jsonify({"success": False, "message": "No fields selected to update."}), 400
    unknown = set(fields.keys()) - _BULK_ALLOWED_FIELDS
    if unknown:
        return jsonify({"success": False, "message": f"Field(s) not allowed for bulk update: {', '.join(sorted(unknown))}"}), 400

    exams_by_id = get_exams_by_ids_full(exam_ids)
    missing = [str(i) for i in exam_ids if str(i) not in exams_by_id]
    if missing:
        return jsonify({"success": False, "message": f"Exam(s) not found: {', '.join(missing)}"}), 404
    exam_rows = [exams_by_id[str(i)] for i in exam_ids]

    parsed = {}
    try:
        if "instructions" in fields:
            parsed["instructions"] = parse_instructions_field(fields.get("instructions"))
        if "max_attempts" in fields:
            parsed["max_attempts"] = parse_max_attempts(fields.get("max_attempts"))
        if "passing_percentage" in fields:
            parsed["passing_percentage"] = parse_passing_percentage(fields.get("passing_percentage"))
        if "date" in fields:
            parsed["date"] = parse_exam_date(fields.get("date"))
        if "duration" in fields:
            dur = int(fields.get("duration") or 0)
            if dur <= 0:
                raise ValueError("Duration must be greater than 0 minutes")
            parsed["duration"] = dur
        if "status" in fields:
            status = str(fields.get("status", "")).strip()
            if status not in ("upcoming", "ongoing", "completed"):
                raise ValueError("Status must be one of: upcoming, ongoing, completed")
            parsed["status"] = status
        if "positive_marks" in fields:
            pm = str(fields.get("positive_marks", "")).strip()
            if not pm:
                raise ValueError("Positive marks are required.")
            try:
                float(pm)
            except ValueError:
                raise ValueError("Positive marks must be a number")
            parsed["positive_marks"] = pm
        if "negative_marks" in fields:
            nm = str(fields.get("negative_marks", "")).strip()
            if not nm:
                raise ValueError("Negative marks are required.")
            try:
                float(nm)
            except ValueError:
                raise ValueError("Negative marks must be a number")
            parsed["negative_marks"] = nm
        if "result_mode" in fields:
            rmode = str(fields.get("result_mode", "")).strip()
            if rmode not in ("instant", "delayed", "manual"):
                raise ValueError("Result mode must be one of: instant, delayed, manual")
            parsed["result_mode"] = rmode
            parsed["result_delay"] = int(fields.get("result_delay") or 0) if rmode == "delayed" else 0
        if "category_id" in fields or "subcategory_id" in fields:
            subcat_id = int(fields.get("subcategory_id") or 0) or None
            cat_id = int(fields.get("category_id") or 0) or None
            if subcat_id:
                subcat = get_subcategory_by_id(subcat_id)
                if not subcat:
                    raise ValueError("Selected subcategory was not found.")
                parsed["subcategory_id"] = subcat_id
                parsed["category_id"] = subcat["category_id"]
            elif cat_id:
                parsed["category_id"] = cat_id
                parsed["subcategory_id"] = None
            else:
                raise ValueError("Select a category (and optionally a subcategory).")
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400

    warnings = []
    date_ids, duration_ids, status_ids = [], [], []
    for exam in exam_rows:
        is_scheduled = bool(exam.get("scheduled_mode"))
        # Date and Duration are both "schedule" fields — a Scheduled Exam
        # locks them together the instant it's no longer Upcoming (mirrors
        # edit_exam()'s timing_changed guard), so they share one check.
        if "date" in parsed or "duration" in parsed:
            eff = get_effective_status(exam)
            locked = is_scheduled and eff != "upcoming"
            if locked:
                for field in ("date", "duration"):
                    if field in parsed:
                        warnings.append({
                            "exam_id": exam["id"], "exam_name": exam["name"], "field": field,
                            "reason": f"This scheduled exam is already {eff} — its schedule can no longer be changed.",
                        })
            else:
                if "date" in parsed:
                    date_ids.append(exam["id"])
                if "duration" in parsed:
                    duration_ids.append(exam["id"])
        if "status" in parsed:
            if is_scheduled:
                warnings.append({
                    "exam_id": exam["id"], "exam_name": exam["name"], "field": "status",
                    "reason": "This is a Scheduled Exam — its status is computed automatically, not set manually.",
                })
            else:
                status_ids.append(exam["id"])

    all_ids = [e["id"] for e in exam_rows]
    ops = []

    def add_op(cols_values: dict, ids):
        if not ids:
            return
        sql = ", ".join(f"{c}=%s" for c in cols_values)
        ops.append({"sql": sql, "params": list(cols_values.values()), "ids": ids})

    if "instructions" in parsed:
        add_op({"instructions": parsed["instructions"]}, all_ids)
    if "max_attempts" in parsed:
        add_op({"max_attempts": parsed["max_attempts"]}, all_ids)
    if "passing_percentage" in parsed:
        add_op({"passing_percentage": parsed["passing_percentage"]}, all_ids)
    if "positive_marks" in parsed:
        add_op({"positive_marks": parsed["positive_marks"]}, all_ids)
    if "negative_marks" in parsed:
        add_op({"negative_marks": parsed["negative_marks"]}, all_ids)
    if "result_mode" in parsed:
        add_op({"result_mode": parsed["result_mode"], "result_delay": parsed["result_delay"]}, all_ids)
    if "category_id" in parsed:
        add_op({"category_id": parsed["category_id"], "subcategory_id": parsed.get("subcategory_id")}, all_ids)
    if "date" in parsed:
        add_op({"date": parsed["date"]}, date_ids)
    if "duration" in parsed:
        add_op({"duration": parsed["duration"]}, duration_ids)
    if "status" in parsed:
        add_op({"status": parsed["status"]}, status_ids)

    touched_ids = set()
    for op in ops:
        touched_ids.update(op["ids"])

    try:
        bulk_update_exam_fields(ops)
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to update exams: {e}"}), 500

    return jsonify({
        "success": True,
        "updated_exam_count": len(touched_ids),
        "changed_fields": sorted(fields.keys()),
        "warnings": warnings,
    })


# ── Bulk exam export/import job store ───────────────────────────────────────
# Same in-memory job + background-thread + polling pattern already used by Notes' notebook
# import (app/routes/api/v01/notebooks.py) and AI question generation (admin/ai_centre.py) —
# process-local, not shared across multiple worker processes, same known/accepted constraint;
# see app/services/exam_export_service.py's own module docstring for why this is preferred over
# introducing Celery/Redis for this app.
_export_jobs: dict = {}
_export_jobs_lock = threading.Lock()
_import_jobs: dict = {}
_import_jobs_lock = threading.Lock()


def _job_update(store: dict, lock: threading.Lock, job_id: str, **kwargs) -> None:
    with lock:
        if job_id in store:
            store[job_id].update(kwargs)


def _run_export_job(job_id: str, exam_ids: list) -> None:
    def on_progress(phase: str, message: str, percent: int):
        _job_update(_export_jobs, _export_jobs_lock, job_id, phase=phase, message=message, percent=percent)

    out_path = exam_export_service.export_out_path(job_id)
    try:
        summary = exam_export_service.run_export(exam_ids, out_path, progress=on_progress)
        _job_update(_export_jobs, _export_jobs_lock, job_id, status="done", phase="complete",
                    percent=100, message="Export complete", summary=summary, error=None)
    except ValueError as exc:
        _job_update(_export_jobs, _export_jobs_lock, job_id, status="failed", error=str(exc), message=str(exc))
    except Exception:
        import traceback; traceback.print_exc()
        _job_update(_export_jobs, _export_jobs_lock, job_id, status="failed",
                    error="Unable to export these exams. Please try again.",
                    message="Unable to export these exams. Please try again.")


def _run_import_job(job_id: str, zip_path: str) -> None:
    def on_progress(phase: str, message: str, percent: int):
        _job_update(_import_jobs, _import_jobs_lock, job_id, phase=phase, message=message, percent=percent)

    try:
        report = exam_export_service.run_import(zip_path, progress=on_progress)
        _job_update(_import_jobs, _import_jobs_lock, job_id, status="done", phase="complete",
                    percent=100, message="Import complete", report=report, error=None)
    except ValueError as exc:
        _job_update(_import_jobs, _import_jobs_lock, job_id, status="failed", error=str(exc), message=str(exc))
    except Exception:
        import traceback; traceback.print_exc()
        _job_update(_import_jobs, _import_jobs_lock, job_id, status="failed",
                    error="Unable to import this file. Please try again.",
                    message="Unable to import this file. Please try again.")
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


@admin_api_bp.route("/exams/export", methods=["POST"])
@require_admin_permission("exam_management")
def start_exam_export():
    """Starts a bulk export as a background job and returns immediately — progress (and, once
    done, the download link) is tracked via GET /exams/export/status/<job_id> below. Mirrors
    Notes' notebook-import job shape exactly (see notebooks.py's import_notebook_api)."""
    body = request.get_json(silent=True) or {}
    exam_ids_raw = body.get("exam_ids") or []
    if not isinstance(exam_ids_raw, list) or not exam_ids_raw:
        return jsonify({"success": False, "message": "No exams selected."}), 400
    if len(exam_ids_raw) > 200:
        return jsonify({"success": False, "message": "Too many exams selected (max 200 per export)."}), 400
    try:
        exam_ids = [int(x) for x in exam_ids_raw]
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid exam id in selection."}), 400

    job_id = uuid.uuid4().hex[:12]
    with _export_jobs_lock:
        _export_jobs[job_id] = {
            "status": "running", "phase": "preparing", "message": "Preparing export...",
            "percent": 0, "summary": None, "error": None, "_owner_id": session["user_id"],
        }
    threading.Thread(target=_run_export_job, args=(job_id, exam_ids), daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})


@admin_api_bp.route("/exams/export/status/<job_id>", methods=["GET"])
@require_admin_permission("exam_management")
def exam_export_status(job_id: str):
    with _export_jobs_lock:
        job = dict(_export_jobs.get(job_id, {}))
    # Not-found and not-yours return the identical 404 — never confirm a job_id exists to anyone
    # but the admin who started it (same privacy rule as the notebook-import status route).
    if not job or job.get("_owner_id") != session["user_id"]:
        return jsonify({"success": False, "message": "Export job not found."}), 404
    job.pop("_owner_id", None)
    if job.get("status") == "done":
        job["download_url"] = f"/api/v01/admin/exams/export/download/{job_id}"
    return jsonify({"success": True, "job": job})


@admin_api_bp.route("/exams/export/download/<job_id>", methods=["GET"])
@require_admin_permission("exam_management")
def exam_export_download(job_id: str):
    with _export_jobs_lock:
        job = dict(_export_jobs.get(job_id, {}))
    if not job or job.get("_owner_id") != session["user_id"] or job.get("status") != "done":
        return jsonify({"success": False, "message": "Export not found or not ready."}), 404
    path = exam_export_service.export_out_path(job_id)
    if not os.path.isfile(path):
        return jsonify({"success": False, "message": "Export file has expired. Please export again."}), 404
    return send_file(path, mimetype="application/zip", as_attachment=True,
                      download_name=f"exams-export-{job_id}.zip")


@admin_api_bp.route("/exams/import", methods=["POST"])
@require_admin_permission("exam_management")
def start_exam_import():
    """Starts a bulk import as a background job — same shape as the export job above. Only the
    cheap file-presence check happens synchronously here; the file itself (manifest shape, every
    exam/question row) is validated inside the job, same reasoning as Notes' notebook import."""
    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded."}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".zip"):
        return jsonify({"success": False, "message": "File must be a .zip export."}), 400

    job_id = uuid.uuid4().hex[:12]
    zip_path = exam_export_service.import_in_path(job_id)
    f.save(zip_path)

    with _import_jobs_lock:
        _import_jobs[job_id] = {
            "status": "running", "phase": "validating", "message": "Reading export file...",
            "percent": 0, "report": None, "error": None, "_owner_id": session["user_id"],
        }
    threading.Thread(target=_run_import_job, args=(job_id, zip_path), daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})


@admin_api_bp.route("/exams/import/status/<job_id>", methods=["GET"])
@require_admin_permission("exam_management")
def exam_import_status(job_id: str):
    with _import_jobs_lock:
        job = dict(_import_jobs.get(job_id, {}))
    if not job or job.get("_owner_id") != session["user_id"]:
        return jsonify({"success": False, "message": "Import job not found."}), 404
    job.pop("_owner_id", None)
    return jsonify({"success": True, "job": job})
