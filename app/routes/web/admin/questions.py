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
from app.db.exams import get_exam_by_id, get_exams_for_selector
from app.db.questions import get_questions_by_exam, get_questions_by_exam_page, get_question_by_id, delete_question
from app.utils.sanitize import sanitize_html

QUESTIONS_PAGE_SIZE = 10  # matches the "Show N entries" dropdown's own default


def _sanitize_question(q: dict) -> dict:
    return {
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
        "source_tag":    (q.get("metadata") or {}).get("source_tag",""),
        "row_no":        q.get("row_no"),
    }


@admin_bp.route("/questions", methods=["GET"])
@require_admin_role
def questions_index():
    exams_list = get_exams_for_selector()
    selected   = request.args.get("exam_id", type=int)
    if not selected and exams_list:
        selected = exams_list[0]["id"]
    selected_exam = next((e for e in exams_list if e["id"] == selected), None)

    questions_list = []
    page_data = {"total": 0, "page": 1, "per_page": QUESTIONS_PAGE_SIZE, "total_pages": 1}
    if selected:
        # PERFORMANCE: server-paginated — see get_questions_by_exam_page()'s
        # docstring. This first paint is always just page 1 with no filters;
        # search/type/image/page changes re-fetch via the AJAX partial
        # (api_questions_list in app/routes/api/v01/admin/questions.py)
        # instead of ever loading the exam's complete question set here.
        page_data = get_questions_by_exam_page(selected, page=1, per_page=QUESTIONS_PAGE_SIZE)
        questions_list = [_sanitize_question(q) for q in page_data["questions"]]

    return render_template("admin/questions.html", exams=exams_list,
                           selected_exam_id=selected, selected_exam=selected_exam,
                           questions=questions_list, questions_total=page_data["total"],
                           questions_per_page=page_data["per_page"])


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

    # exam_id deliberately dropped from the exported format — import is now
    # exam-selector-driven (see import_questions_csv() in
    # app/routes/api/v01/admin/questions.py), so a re-imported export never
    # needs it. A CSV that still HAS the column (an old export, or one built
    # by hand) is still accepted on import, just no longer required.
    cols = ["question_text","option_a","option_b","option_c","option_d",
            "correct_answer","question_type","image_path","positive_marks","negative_marks",
            "tolerance","source_tag"]
    rows = []
    for q in qs:
        row = {c: q.get(c,"") for c in cols if c != "source_tag"}
        # source_tag is pulled out of the metadata JSONB specifically —
        # never dump the raw JSON object into a CSV cell.
        row["source_tag"] = (q.get("metadata") or {}).get("source_tag","")
        rows.append(row)
    df = pd.DataFrame(rows)[cols]

    out  = io.StringIO()
    df.to_csv(out, index=False, encoding="utf-8")
    fname = f"questions_{exam.get('name','exam').replace(' ','_')}_{exam_id}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})
