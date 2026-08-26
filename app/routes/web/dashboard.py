"""
app/routes/web/dashboard.py
User-facing dashboard, results history, and student analytics.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, session

from app.middleware.session_guard import require_user_role
from app.db.exams import get_all_exams
from app.db.results import get_results_by_user
from app.db.users import get_view_prefs
from app.services.result_service import can_user_see_result, calculate_student_analytics, build_result_map, build_exam_card

dashboard_bp = Blueprint("dashboard", __name__)

DASHBOARD_PAGE_SIZE = 12


@dashboard_bp.route("/dashboard")
@require_user_role
def dashboard():
    user_id = session["user_id"]

    category_id = session.get("selected_category_id")
    if not category_id:
        return redirect(url_for("categories.select_category"))

    from app.db.categories import get_category_by_id
    selected_cat = get_category_by_id(category_id)
    if not selected_cat:
        session.pop("selected_category_id", None)
        return redirect(url_for("categories.select_category"))

    from app.db.subcategories import get_subcategory_by_id
    from app.db.exams import get_exams_by_subcategory_page
    subcategory_id = session.get("selected_subcategory_id")
    selected_subcat = get_subcategory_by_id(subcategory_id) if subcategory_id else None

    user_results = get_results_by_user(user_id)
    result_map = build_result_map(user_results)

    # Bounded per-tab fetch — never the whole subcategory's exam list at
    # once. Each tab shows its true total (for the badge count) but only
    # loads DASHBOARD_PAGE_SIZE rows; "Load more" (app/routes/api/v01/
    # portal.py) fetches subsequent pages on demand.
    empty_page = {"exams": [], "total": 0, "page": 1, "per_page": DASHBOARD_PAGE_SIZE, "total_pages": 1}
    if selected_subcat:
        ongoing_page   = get_exams_by_subcategory_page(subcategory_id, status="ongoing",   page=1, per_page=DASHBOARD_PAGE_SIZE)
        upcoming_page  = get_exams_by_subcategory_page(subcategory_id, status="upcoming",  page=1, per_page=DASHBOARD_PAGE_SIZE)
        completed_page = get_exams_by_subcategory_page(subcategory_id, status="completed", page=1, per_page=DASHBOARD_PAGE_SIZE)
    else:
        # A category with no subcategories yet (admin hasn't created any)
        # has nothing to browse — show an empty dashboard rather than
        # redirect-loop back through the (also empty) subcategory picker.
        ongoing_page = upcoming_page = completed_page = empty_page

    ongoing   = [build_exam_card(e, result_map) for e in ongoing_page["exams"]]
    upcoming  = [build_exam_card(e) for e in upcoming_page["exams"]]
    completed = [build_exam_card(e, result_map) for e in completed_page["exams"]]

    # Summary stats
    pcts = [float(r["percentage"]) for r in user_results if r.get("percentage") is not None]
    avg_score      = f"{sum(pcts)/len(pcts):.1f}%" if pcts else "--"
    total_attempted = len(user_results)

    from app.services.dashboard_service import get_greeting, should_auto_show_updates

    return render_template(
        "dashboard.html",
        upcoming_exams=upcoming,
        ongoing_exams=ongoing,
        completed_exams=completed,
        upcoming_total=upcoming_page["total"], upcoming_total_pages=upcoming_page["total_pages"],
        ongoing_total=ongoing_page["total"], ongoing_total_pages=ongoing_page["total_pages"],
        completed_total=completed_page["total"], completed_total_pages=completed_page["total_pages"],
        dashboard_per_page=DASHBOARD_PAGE_SIZE,
        avg_score=avg_score,
        total_attempted=total_attempted,
        selected_category=selected_cat,
        selected_subcategory=selected_subcat,
        view_mode=get_view_prefs(user_id).get("dashboard", "grid"),
        greeting=get_greeting(),
        auto_show_updates=should_auto_show_updates(),
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

        result_list.append({
            "id":                 int(r.get("id", 0)),
            "exam_id":            eid,
            "exam_name":          exam_data.get("name", f"Exam {eid}"),
            "completed_at":       r.get("completed_at",""),
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
