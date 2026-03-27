"""
app/routes/admin/exams.py
Admin exam management routes (CRUD + result release).
"""

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import (
    get_all_exams, get_exam_by_id,
    create_exam, update_exam, delete_exam,
    release_exam_results,
)
from app.db.results import get_results_by_exam
from app.db import supabase
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


@admin_bp.route("/exams/delete/<int:exam_id>", methods=["POST"])
@require_admin_role
def delete_exam_route(exam_id):
    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found"}), 404

    try:
        results = get_results_by_exam(exam_id)
        for r in results:
            supabase.table("responses").delete().eq("result_id", r["id"]).execute()
        supabase.table("results").delete().eq("exam_id", exam_id).execute()
        supabase.table("exam_attempts").delete().eq("exam_id", exam_id).execute()
        q_ids = [q["id"] for q in (supabase.table("questions").select("id").eq("exam_id", exam_id).execute().data or [])]
        if q_ids:
            supabase.table("question_discussions").delete().in_("question_id", q_ids).execute()
            supabase.table("discussion_counts").delete().in_("question_id", q_ids).execute()
        supabase.table("questions").delete().eq("exam_id", exam_id).execute()
        supabase.table("exams").delete().eq("id", exam_id).execute()
        
        flash("Exam deleted successfully.", "info")
        return jsonify({"success": True, "message": f"Exam '{exam['name']}' deleted."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@admin_bp.route("/exams/<int:exam_id>/release-results", methods=["POST"])
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