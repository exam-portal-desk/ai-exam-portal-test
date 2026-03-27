"""
app/routes/dashboard.py
User-facing dashboard, results history, and student analytics.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, session

from app.middleware.session_guard import require_user_role
from app.db.exams import get_all_exams
from app.db.results import get_results_by_user
from app.services.result_service import can_user_see_result, calculate_student_analytics

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@require_user_role
def dashboard():
    user_id = session["user_id"]

    category_id = session.get("selected_category_id")
    if not category_id:
        from flask import redirect, url_for
        return redirect(url_for("categories.select_category"))

    from app.db.categories import get_category_by_id
    selected_cat = get_category_by_id(category_id)
    if not selected_cat:
        session.pop("selected_category_id", None)
        from flask import redirect, url_for
        return redirect(url_for("categories.select_category"))

    from app.db.exams import get_exams_by_category
    all_exams    = get_exams_by_category(category_id)
    user_results = get_results_by_user(user_id)

    upcoming, ongoing, completed = [], [], []

    for exam in all_exams:
        status = str(exam.get("status", "draft")).lower().strip()
        ed = {
            "id":              int(exam.get("id", 0)),
            "name":            exam.get("name", "Unnamed Exam"),
            "date":            exam.get("date", ""),
            "start_time":      exam.get("start_time", ""),
            "duration":        exam.get("duration", 60),
            "total_questions": exam.get("total_questions", 0),
            "status":          status,
            "instructions":    exam.get("instructions", ""),
            "positive_marks":  exam.get("positive_marks", "1"),
            "negative_marks":  exam.get("negative_marks", "0"),
        }
        if status == "upcoming":
            upcoming.append(ed)
        elif status == "ongoing":
            ongoing.append(ed)
        elif status == "completed":
            completed.append(ed)

    # Attach result summary to completed exams
    result_map = {}
    for r in user_results:
        eid = int(r.get("exam_id", 0))
        if eid not in result_map or r.get("completed_at","") > result_map[eid].get("completed_at",""):
            result_map[eid] = r

    for exam in completed:
        r = result_map.get(int(exam["id"]))
        if r:
            exam["result"] = f"{r.get('score')}/{r.get('max_score')} ({r.get('grade','N/A')})"
        else:
            exam["result"] = "Pending"

    # Summary stats
    pcts = [float(r["percentage"]) for r in user_results if r.get("percentage") is not None]
    avg_score      = f"{sum(pcts)/len(pcts):.1f}%" if pcts else "--"
    total_attempted = len(user_results)

    return render_template(
        "dashboard.html",
        upcoming_exams=upcoming,
        ongoing_exams=ongoing,
        completed_exams=completed,
        avg_score=avg_score,
        total_attempted=total_attempted,
        selected_category=selected_cat,
    )


@dashboard_bp.route("/results_history")
@require_user_role
def results_history():
    from datetime import datetime
    user_id  = session["user_id"]
    results  = get_results_by_user(user_id)
    exams    = get_all_exams()
    exam_map = {int(e["id"]): e for e in exams}

    result_list = []
    for r in results:
        eid       = int(r.get("exam_id", 0))
        exam_data = exam_map.get(eid, {})

        is_visible, pending_reason = can_user_see_result(exam_data, r)

        completed_at_raw = r.get("completed_at","")
        try:
            completed_at = datetime.fromisoformat(completed_at_raw).strftime("%d %B %Y %I:%M:%S %p")
        except Exception:
            completed_at = completed_at_raw

        result_list.append({
            "id":                 int(r.get("id", 0)),
            "exam_id":            eid,
            "exam_name":          exam_data.get("name", f"Exam {eid}"),
            "completed_at":       completed_at,
            "score":              r.get("score", 0),
            "max_score":          r.get("max_score", 0),
            "percentage":         round(float(r.get("percentage", 0)), 2),
            "grade":              r.get("grade", "N/A"),
            "time_taken_minutes": r.get("time_taken_minutes", 0),
            "correct_answers":    int(r.get("correct_answers", 0)),
            "incorrect_answers":  int(r.get("incorrect_answers", 0)),
            "unanswered_questions": int(r.get("unanswered_questions", 0)),
            "result_visible":     is_visible,
            "pending_reason":     pending_reason,
        })

    result_list.sort(key=lambda x: x["completed_at"], reverse=True)
    return render_template("results_history.html", results=result_list)


@dashboard_bp.route("/analytics")
@require_user_role
def student_analytics():
    user_id = session["user_id"]
    results = get_results_by_user(user_id)
    exams   = get_all_exams()
    exam_map = {str(e["id"]): e for e in exams}

    # Only include results the user can currently see
    visible = [
        r for r in results
        if can_user_see_result(exam_map.get(str(r.get("exam_id","")), {}), r)[0]
    ]

    if not visible:
        flash("No results data available yet.", "info")
        return render_template("student_analytics.html", analytics_data={}, has_data=False)

    analytics = calculate_student_analytics(visible, exams, user_id)
    return render_template(
        "student_analytics.html",
        analytics_data=analytics,
        has_data=True,
        username=session.get("username"),
    )