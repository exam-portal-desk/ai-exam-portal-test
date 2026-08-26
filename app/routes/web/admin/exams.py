"""
app/routes/web/admin/exams.py
Admin exam create/edit pages. The JSON API (delete, release-results) that
used to live alongside these in app/routes/admin/exams.py now lives in
app/routes/api/v01/admin/exams.py.
"""

from flask import render_template, request, redirect, url_for, flash, session

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exam_by_id, create_exam, update_exam, get_exams_page
from app.utils.helpers import parse_max_attempts, parse_passing_percentage, parse_start_time, parse_exam_date
from app.db.categories import get_all_categories
from app.db.users import get_view_prefs

EXAMS_PAGE_SIZE = 20


@admin_bp.route("/exams", methods=["GET", "POST"])
@require_admin_role
def exams():
    categories = get_all_categories()
    if request.method == "POST":
        form = request.form
        try:
            max_att = parse_max_attempts(form.get("max_attempts",""))
            passing_pct = parse_passing_percentage(form.get("passing_percentage",""))
            exam_date = parse_exam_date(form.get("date",""))
            start_time = parse_start_time(form.get("start_time",""))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("admin.exams"))

        create_exam({
            "name":           form.get("name","").strip(),
            "date":           exam_date,
            "start_time":     start_time,
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
            "subcategory_id": int(form.get("subcategory_id") or 0) or None,
            "passing_percentage": passing_pct,
        })
        flash("Exam created successfully.", "success")
        return redirect(url_for("admin.exams"))

    page_data = get_exams_page(page=1, per_page=EXAMS_PAGE_SIZE)
    return render_template(
        "admin/exams.html",
        exams=page_data["exams"], categories=categories,
        exams_total=page_data["total"], exams_total_pages=page_data["total_pages"],
        exams_per_page=EXAMS_PAGE_SIZE,
        # Default 'list' (not 'grid') to preserve this page's original
        # default — the dense table, not the summary cards — for anyone
        # who hasn't explicitly chosen a view yet.
        view_mode=get_view_prefs(session["user_id"]).get("exams", "list"),
    )


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
            passing_pct = parse_passing_percentage(form.get("passing_percentage",""))
            exam_date = parse_exam_date(form.get("date",""))
            start_time = parse_start_time(form.get("start_time",""))
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
            "date":           exam_date,
            "start_time":     start_time,
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
            "subcategory_id": int(form.get("subcategory_id") or 0) or None,
            "passing_percentage": passing_pct,
        }):
            flash("Exam updated successfully.", "success")
            return redirect(url_for("admin.exams"))

        flash("Failed to save exam changes.", "danger")
        return redirect(url_for("admin.edit_exam", exam_id=exam_id))

    return render_template("admin/edit_exam.html", exam=exam, categories=categories)
