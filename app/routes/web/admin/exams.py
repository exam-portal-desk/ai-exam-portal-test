"""
app/routes/web/admin/exams.py
Admin exam create/edit pages. The JSON API (delete, release-results) that
used to live alongside these in app/routes/admin/exams.py now lives in
app/routes/api/v01/admin/exams.py.
"""

from flask import render_template, request, redirect, url_for, flash

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams, get_exam_by_id, create_exam, update_exam
from app.utils.helpers import parse_max_attempts
from app.db.categories import get_all_categories


@admin_bp.route("/exams", methods=["GET", "POST"])
@require_admin_role
def exams():
    categories = get_all_categories()
    if request.method == "POST":
        form = request.form
        try:
            max_att = parse_max_attempts(form.get("max_attempts",""))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("admin.exams"))

        create_exam({
            "name":           form.get("name","").strip(),
            "date":           form.get("date","").strip(),
            "start_time":     form.get("start_time","").strip(),
            "duration":       int(form.get("duration") or 60),
            "total_questions":int(form.get("total_questions") or 0),
            "status":         form.get("status","draft").strip(),
            "instructions":   form.get("instructions","").strip(),
            "positive_marks": form.get("positive_marks","1").strip(),
            "negative_marks": form.get("negative_marks","0").strip(),
            "max_attempts":   max_att,
            "result_mode":    form.get("result_mode","instant").strip(),
            "result_delay":   int(form.get("result_delay") or 0),
            "results_released": False,
            "category_id": int(form.get("category_id") or 0) or None,
        })
        flash("Exam created successfully.", "success")
        return redirect(url_for("admin.exams"))

    return render_template("admin/exams.html", exams=get_all_exams(), categories=categories)


@admin_bp.route("/exams/edit/<int:exam_id>", methods=["GET", "POST"])
@require_admin_role
def edit_exam(exam_id):
    categories=get_all_categories()
    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("admin.exams"))

    if request.method == "POST":
        form = request.form
        try:
            max_att = parse_max_attempts(form.get("max_attempts",""))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("admin.edit_exam", exam_id=exam_id))

        try:
            dur  = int(form.get("duration") or 0)
            tot  = int(form.get("total_questions") or 0)
        except ValueError:
            flash("Duration and Total Questions must be integers.", "danger")
            return redirect(url_for("admin.edit_exam", exam_id=exam_id))

        if update_exam(exam_id, {
            "name":           form.get("name","").strip(),
            "date":           form.get("date","").strip(),
            "start_time":     form.get("start_time","").strip(),
            "duration":       dur,
            "total_questions":tot,
            "status":         form.get("status","").strip(),
            "instructions":   form.get("instructions","").strip(),
            "positive_marks": form.get("positive_marks","").strip(),
            "negative_marks": form.get("negative_marks","").strip(),
            "max_attempts":   max_att,
            "result_mode":    form.get("result_mode","instant").strip(),
            "result_delay":   int(form.get("result_delay") or 0),
            "category_id": int(form.get("category_id") or 0) or None,
        }):
            flash("Exam updated successfully.", "success")
            return redirect(url_for("admin.exams"))

        flash("Failed to save exam changes.", "danger")
        return redirect(url_for("admin.edit_exam", exam_id=exam_id))

    return render_template("admin/edit_exam.html", exam=exam, categories=categories)
