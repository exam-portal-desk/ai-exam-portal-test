"""
app/routes/result.py
Result viewing, response analysis, pending result page, and PDF export.
"""

import json
from datetime import datetime
from io import BytesIO

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, session, request, Response, abort, send_file,
)

from app.middleware.session_guard import require_user_role
from app.db.exams import get_exam_by_id
from app.db.questions import get_questions_by_exam
from app.db.results import get_result_by_id, get_results_by_user, get_responses_by_result
from app.services.result_service import can_user_see_result

result_bp = Blueprint("result", __name__)


# ─────────────────────────────────────────────
# Result page
# ─────────────────────────────────────────────

@result_bp.route("/result/<int:exam_id>", defaults={"result_id": None})
@result_bp.route("/result/<int:exam_id>/<int:result_id>")
@require_user_role
def result(exam_id, result_id):
    user_id  = session["user_id"]
    exam     = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    result_data = _resolve_result(user_id, exam_id, result_id)
    if not result_data:
        flash("Result not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    visible, reason = can_user_see_result(exam, result_data)
    if not visible:
        return render_template("result_pending.html", exam=exam,
                               reason=reason, result_id=result_data["id"],
                               from_history=False)

    if result_data.get("completed_at"):
        try:
            result_data["completed_at"] = datetime.fromisoformat(
                str(result_data["completed_at"])
            ).strftime("%d %B %Y %I:%M:%S %p")
        except Exception:
            pass

    return render_template("result.html", result=result_data, exam=exam,
                           from_history=request.args.get("from_history","0") == "1")


# ─────────────────────────────────────────────
# Response analysis page
# ─────────────────────────────────────────────

@result_bp.route("/response/<int:exam_id>", defaults={"result_id": None})
@result_bp.route("/response/<int:exam_id>/<int:result_id>")
@require_user_role
def response_page(exam_id, result_id):
    user_id      = session["user_id"]
    from_history = request.args.get("from_history","0") == "1"

    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    result_data = _resolve_result(user_id, exam_id, result_id)
    if not result_data:
        flash("Result not found.", "error")
        return redirect(url_for("dashboard.results_history"))

    visible, reason = can_user_see_result(exam, result_data)
    if not visible:
        return render_template("result_pending.html", exam=exam,
                               reason=reason, result_id=result_data["id"],
                               from_history=from_history)

    rid       = int(result_data["id"])
    responses = get_responses_by_result(rid)
    questions = get_questions_by_exam(exam_id)
    q_map     = {int(q["id"]): q for q in questions}

    from app.services.drive_service import get_image_url as _get_img_url

    # ── Bulk-resolve unique image paths first ──
    unique_paths = set()
    for resp in responses:
        qid = int(resp.get("question_id", 0))
        q   = q_map.get(qid, {})
        if not q:
            continue
        ip = q.get("image_path", "")
        if ip and str(ip).strip() not in ("", "nan", "None"):
            unique_paths.add(str(ip).strip())

    image_url_map = {}
    for path in unique_paths:
        has_img, img_url = _get_img_url(path)
        image_url_map[path] = (has_img, img_url)

    parsed_responses = []
    for resp in responses:
        qid    = int(resp.get("question_id", 0))
        q      = q_map.get(qid, {})
        if not q:
            continue
        image_path = q.get("image_path", "")
        if image_path and str(image_path).strip() not in ("", "nan", "None"):
            has_img, img_url = image_url_map.get(str(image_path).strip(), (False, None))
            q["has_image"] = has_img
            q["image_url"] = img_url
        else:
            q["has_image"] = False
            q["image_url"] = None
        qtype  = str(resp.get("question_type","MCQ"))
        given  = str(resp.get("given_answer","") or "")
        corr   = str(resp.get("correct_answer","") or "")

        def _parse_ans(raw, qt):
            if not raw or raw in ("None","nan",""):
                return None
            try:
                if qt == "MSQ":
                    return json.loads(raw) if raw.startswith("[") else [x.strip() for x in raw.split(",")]
            except Exception:
                pass
            return raw

        given_parsed = _parse_ans(given, qtype) or "Not Answered"
        corr_parsed  = _parse_ans(corr, qtype) or "N/A"

        is_att = resp.get("is_attempted", True)
        if isinstance(is_att, str): is_att = is_att.lower() in ("true","1","yes")
        is_cor = resp.get("is_correct", False)
        if isinstance(is_cor, str): is_cor = is_cor.lower() in ("true","1","yes")

        parsed_responses.append({
            "question":      q,
            "given_answer":  given_parsed,
            "correct_answer": corr_parsed,
            "is_correct":    is_cor,
            "is_attempted":  is_att,
            "marks_obtained": float(resp.get("marks_obtained",0) or 0),
            "question_type": qtype,
        })

    return render_template("response.html", exam=exam, result=result_data,
                           responses=parsed_responses, from_history=from_history)


# ─────────────────────────────────────────────
# Pending result
# ─────────────────────────────────────────────

@result_bp.route("/result-pending/<int:exam_id>", defaults={"result_id": None})
@result_bp.route("/result-pending/<int:exam_id>/<int:result_id>")
@require_user_role
def result_pending(exam_id, result_id):
    user_id = session["user_id"]
    exam    = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    if not result_id:
        result_id = session.get("latest_result_id")

    result_data = None
    if result_id:
        result_data = get_result_by_id(result_id)
        if result_data and int(result_data.get("student_id",0)) != user_id:
            result_data = None

    if not result_data:
        flash("Result not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    visible, reason = can_user_see_result(exam, result_data)
    if visible:
        return redirect(url_for("result.result", exam_id=exam_id, result_id=result_id))

    return render_template("result_pending.html", exam=exam, reason=reason,
                           result_id=result_id, from_history=False)


# ─────────────────────────────────────────────
# Student PDF export
# ─────────────────────────────────────────────

@result_bp.route("/response-pdf/<int:exam_id>")
@require_user_role
def response_pdf(exam_id):
    from app.services.pdf_service import build_student_response_pdf

    user_id  = session["user_id"]
    username = session.get("username", "Student")

    exam        = get_exam_by_id(exam_id)
    result_data = _resolve_result(user_id, exam_id, None)
    if not result_data:
        flash("Result not found.", "error")
        return redirect(url_for("dashboard.results_history"))

    responses = get_responses_by_result(int(result_data["id"]))
    questions = get_questions_by_exam(exam_id)
    q_map     = {int(q["id"]): q for q in questions}

    pdf = build_student_response_pdf(
        result=result_data,
        exam=exam,
        responses=responses,
        questions_map=q_map,
        student_name=session.get("full_name", username),
        username=username,
    )

    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=exam_{exam_id}_response_{username}.pdf"},
    )


# ─────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────

def _resolve_result(user_id, exam_id, result_id):
    """Find the right result record for a user+exam, trying multiple strategies."""
    if result_id:
        r = get_result_by_id(result_id)
        if r and int(r.get("student_id",0)) == user_id:
            return r
        return None

    # Try session
    sid = session.get("latest_result_id")
    if sid:
        r = get_result_by_id(sid)
        if r and int(r.get("exam_id",0)) == exam_id and int(r.get("student_id",0)) == user_id:
            return r

    # Fallback: most recent result for this user+exam
    all_r = get_results_by_user(user_id)
    exam_r = [x for x in all_r if int(x.get("exam_id",0)) == exam_id]
    if exam_r:
        exam_r.sort(key=lambda x: x.get("completed_at",""), reverse=True)
        return exam_r[0]

    return None