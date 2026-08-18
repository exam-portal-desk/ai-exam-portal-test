"""
app/routes/web/admin/questions.py
Admin question-management page, single-question delete (plain form POST
+ redirect), and CSV export download. The JSON/AJAX API that used to
live alongside these in app/routes/admin/questions.py now lives in
app/routes/api/v01/admin/questions.py.
"""

import io
import pandas as pd
from flask import render_template, request, redirect, url_for, flash, Response

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams, get_exam_by_id
from app.db.questions import get_questions_by_exam, get_question_by_id, delete_question
from app.utils.sanitize import sanitize_html


def _exam_list():
    return [{"id": int(e["id"]), "name": e.get("name", f"Exam {e['id']}")} for e in get_all_exams()]


@admin_bp.route("/questions", methods=["GET"])
@require_admin_role
def questions_index():
    exams_list = _exam_list()
    selected   = request.args.get("exam_id", type=int)
    if not selected and exams_list:
        selected = exams_list[0]["id"]

    questions_list = []
    if selected:
        for q in get_questions_by_exam(selected):
            questions_list.append({
                "id":            int(q["id"]),
                "exam_id":       int(q["exam_id"]),
                "question_text": sanitize_html(q.get("question_text","")),
                "option_a":      sanitize_html(q.get("option_a","")),
                "option_b":      sanitize_html(q.get("option_b","")),
                "option_c":      sanitize_html(q.get("option_c","")),
                "option_d":      sanitize_html(q.get("option_d","")),
                "correct_answer":q.get("correct_answer",""),
                "question_type": q.get("question_type","MCQ"),
                "image_path":    q.get("image_path",""),
                "positive_marks":q.get("positive_marks","4"),
                "negative_marks":q.get("negative_marks","1"),
                "tolerance":     q.get("tolerance",""),
            })

    return render_template("admin/questions.html", exams=exams_list,
                           selected_exam_id=selected, questions=questions_list)


@admin_bp.route("/questions/delete/<int:question_id>", methods=["POST"])
@require_admin_role
def delete_question_route(question_id):
    q = get_question_by_id(question_id)
    exam_id = int(q["exam_id"]) if q else None
    ok = delete_question(question_id)
    flash("Question deleted." if ok else "Failed to delete.", "info" if ok else "danger")
    return redirect(url_for("admin.questions_index", exam_id=exam_id) if exam_id
                    else url_for("admin.questions_index"))


@admin_bp.route("/questions/export-csv/<int:exam_id>")
@require_admin_role
def export_questions_csv(exam_id):
    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("admin.questions_index"))

    qs = get_questions_by_exam(exam_id)
    if not qs:
        flash("No questions found.", "warning")
        return redirect(url_for("admin.questions_index", exam_id=exam_id))

    cols = ["exam_id","question_text","option_a","option_b","option_c","option_d",
            "correct_answer","question_type","image_path","positive_marks","negative_marks","tolerance"]
    rows = [{c: q.get(c,"") for c in cols} for q in qs]
    df   = pd.DataFrame(rows)[cols]

    out  = io.StringIO()
    df.to_csv(out, index=False, encoding="utf-8")
    fname = f"questions_{exam.get('name','exam').replace(' ','_')}_{exam_id}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})
