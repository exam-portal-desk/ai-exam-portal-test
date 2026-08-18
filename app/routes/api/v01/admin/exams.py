"""
app/routes/api/v01/admin/exams.py
Admin exam JSON API (v01): delete + release-results. Relocated from
app/routes/admin/exams.py.

  POST /admin/exams/delete/<id>           -> DELETE /api/v01/admin/exams/<id>
  POST /admin/exams/<id>/release-results  -> POST   /api/v01/admin/exams/<id>/release-results
"""

from flask import jsonify, flash

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exam_by_id, release_exam_results
from app.db import fetch_all, execute


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
