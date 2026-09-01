"""
app/routes/api/v01/admin/exams.py
Admin exam JSON API (v01): delete + release-results. Relocated from
app/routes/admin/exams.py.

  POST /admin/exams/delete/<id>           -> DELETE /api/v01/admin/exams/<id>
  POST /admin/exams/<id>/release-results  -> POST   /api/v01/admin/exams/<id>/release-results
"""

from flask import jsonify, flash, request, render_template_string

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exam_by_id, release_exam_results, get_exams_page, set_scheduled_exam_cancelled
from app.db.categories import get_all_categories
from app.db import fetch_all, execute
from app.utils.datetime_service import format_calendar_date
from app.utils.instructions_formatter import render_exam_instructions

_ROWS_TPL = (
    '{% from "admin/_exam_rows.html" import render_exam_row, render_exam_card, render_exam_edit_modal %}'
    '{% for exam in exams %}{{ render_exam_row(exam) }}{% endfor %}'
    '|||SPLIT|||'
    '{% for exam in exams %}{{ render_exam_card(exam) }}{% endfor %}'
    '|||SPLIT|||'
    '{% for exam in exams %}{{ render_exam_edit_modal(exam, categories) }}{% endfor %}'
)


@admin_api_bp.route("/exams", methods=["GET"])
@require_admin_role
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
@require_admin_role
def preview_instructions():
    """Renders the same HTML the student-facing pages will show — the one
    renderer (app/utils/instructions_formatter.py) is reused verbatim here
    so the admin's live preview can never drift from the real thing."""
    text = (request.get_json(silent=True) or {}).get("instructions", "")
    return jsonify({"html": str(render_exam_instructions(text))})


@admin_api_bp.route("/exams/<int:exam_id>", methods=["DELETE"])
@require_admin_role
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
@require_admin_role
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
@require_admin_role
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
