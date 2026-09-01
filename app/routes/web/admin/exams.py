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
from app.db.attempts import get_exam_attempts_count
from app.utils.helpers import (
    parse_max_attempts, parse_passing_percentage, parse_start_time, parse_exam_date,
    parse_scheduled_minutes_field, parse_instructions_field,
)
from app.db.categories import get_all_categories
from app.db.users import get_view_prefs

EXAMS_PAGE_SIZE = 20


def _parse_scheduled_fields(form):
    """Returns (scheduled_mode, prep_window_minutes, completion_buffer_minutes,
    allow_manual_submission). Raises ValueError (same convention as the
    other exam-form parsers in app/utils/helpers.py) on invalid input —
    shared by create and edit so the two validate identically and never
    drift apart. prep/buffer are None, and allow_manual_submission is the
    schema default (True) when scheduled_mode is off — that field is only
    ever read for a Scheduled Exam (see is_manual_submission_allowed() in
    app/services/exam_service.py), so its value is irrelevant for a
    Manual exam, but keeping it True there matches "manual submission has
    always been allowed" for every exam this setting doesn't apply to."""
    scheduled_mode = form.get("scheduled_mode") in ("1", "on", "true")
    if not scheduled_mode:
        return False, None, None, True
    prep_min = parse_scheduled_minutes_field(form.get("prep_window_minutes"), "Preparation window")
    buffer_min = parse_scheduled_minutes_field(form.get("completion_buffer_minutes"), "Completion buffer")
    allow_manual_submission = form.get("allow_manual_submission") in ("1", "on", "true")
    return True, prep_min, buffer_min, allow_manual_submission


def _parse_available_after_datetime(form, scheduled_mode: bool) -> bool:
    """"Available After Selected Date/Time" (Normal Exam only — see
    is_exam_window_open() in app/services/exam_service.py, the only place
    this is ever read, which is never called for a Scheduled Exam).
    SECURITY: forced False whenever scheduled_mode is True, regardless of
    what the form sends — the admin UI already hides this checkbox for a
    Scheduled Exam, but that's a convenience only; this is the actual
    guarantee that a Scheduled Exam's strict time-window lifecycle can
    never be affected by it."""
    if scheduled_mode:
        return False
    return form.get("available_after_datetime") in ("1", "on", "true")


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
            duration = int(form.get("duration") or 60)
            if duration <= 0:
                raise ValueError("Duration must be greater than 0 minutes")
            scheduled_mode, prep_min, buffer_min, allow_manual_submission = _parse_scheduled_fields(form)
            instructions = parse_instructions_field(form.get("instructions"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("admin.exams"))
        available_after_datetime = _parse_available_after_datetime(form, scheduled_mode)

        create_exam({
            "name":           form.get("name","").strip(),
            "date":           exam_date,
            "start_time":     start_time,
            "duration":       duration,
            "total_questions":int(form.get("total_questions") or 0),
            # SECURITY: a Scheduled Exam's status is NEVER taken from the
            # form, regardless of what the client sends — the admin UI
            # hides/disables the normal status selector for scheduled
            # exams, but that alone is a UI convenience, not enforcement.
            # 'scheduled' is a fixed marker meaning "effective status is
            # computed from the schedule, not stored here" — see
            # app/services/exam_service.py get_effective_status().
            "status":         "scheduled" if scheduled_mode else form.get("status","draft").strip(),
            "instructions":   instructions,
            "positive_marks": form.get("positive_marks","1").strip(),
            "negative_marks": form.get("negative_marks","0").strip(),
            "max_attempts":   max_att,
            "result_mode":    form.get("result_mode","instant").strip(),
            "result_delay":   int(form.get("result_delay") or 0),
            "results_released": False,
            "category_id": int(form.get("category_id") or 0) or None,
            "subcategory_id": int(form.get("subcategory_id") or 0) or None,
            "passing_percentage": passing_pct,
            "scheduled_mode": scheduled_mode,
            "prep_window_minutes": prep_min,
            "completion_buffer_minutes": buffer_min,
            "allow_manual_submission": allow_manual_submission,
            "available_after_datetime": available_after_datetime,
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
            dur = int(form.get("duration") or 0)
            if dur <= 0:
                raise ValueError("Duration must be greater than 0 minutes")
            tot = int(form.get("total_questions") or 0)
            scheduled_mode, prep_min, buffer_min, allow_manual_submission = _parse_scheduled_fields(form)
            instructions = parse_instructions_field(form.get("instructions"))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("admin.exams"))
        available_after_datetime = _parse_available_after_datetime(form, scheduled_mode)

        # SAFETY: once real attempts exist against this exam, flipping
        # Manual<->Scheduled mode would retroactively change which rules
        # governed those attempts — reject outright.
        was_scheduled = bool(exam.get("scheduled_mode"))
        if scheduled_mode != was_scheduled and get_exam_attempts_count(exam_id) > 0:
            flash("This exam already has attempts on record, so its Manual/Scheduled mode can no longer be changed.", "danger")
            return redirect(url_for("admin.exams"))

        # SAFETY: once a Scheduled Exam's window has opened (live/closing),
        # ended (completed), or been cancelled, its schedule can no longer
        # be silently rewritten out from under students who may already
        # have — or have already completed — an attempt under the old
        # timing. Non-timing fields (name, instructions, marks, ...) stay
        # freely editable regardless; a still-Upcoming scheduled exam, or
        # any manual exam (which never had this restriction), is
        # unaffected.
        if was_scheduled:
            from app.services.exam_service import get_effective_status
            current_effective = get_effective_status(exam)
            timing_changed = (
                exam_date != exam.get("date")
                or start_time != exam.get("start_time")
                or dur != int(exam.get("duration") or 0)
                or prep_min != exam.get("prep_window_minutes")
                or buffer_min != exam.get("completion_buffer_minutes")
                # Flipping whether students can voluntarily submit early
                # mid-exam would make a button appear/disappear for
                # students already answering under the old assumption —
                # locked alongside the schedule itself for the same reason.
                or bool(allow_manual_submission) != bool(exam.get("allow_manual_submission", True))
            )
            if current_effective != "upcoming" and timing_changed:
                flash(
                    f"This scheduled exam is already {current_effective} — its schedule can no "
                    "longer be changed. Other details can still be edited.",
                    "danger",
                )
                return redirect(url_for("admin.exams"))

        updates = {
            "name":           form.get("name","").strip(),
            "date":           exam_date,
            "start_time":     start_time,
            "duration":       dur,
            "total_questions":tot,
            "instructions":   instructions,
            "positive_marks": form.get("positive_marks","").strip(),
            "negative_marks": form.get("negative_marks","").strip(),
            "max_attempts":   max_att,
            "result_mode":    form.get("result_mode","instant").strip(),
            "result_delay":   int(form.get("result_delay") or 0),
            "category_id": int(form.get("category_id") or 0) or None,
            "subcategory_id": int(form.get("subcategory_id") or 0) or None,
            "passing_percentage": passing_pct,
            "scheduled_mode": scheduled_mode,
            "prep_window_minutes": prep_min,
            "completion_buffer_minutes": buffer_min,
            "allow_manual_submission": allow_manual_submission,
            "available_after_datetime": available_after_datetime,
        }
        if scheduled_mode:
            # SECURITY — see the matching comment in exams()/create_exam()
            # above: never take status from the form for a scheduled exam.
            # Preserve 'cancelled' if that's what it already was (the
            # dedicated cancel/uncancel action is the only other writer of
            # this column for a scheduled exam); otherwise (re)assert the
            # normal 'scheduled' marker.
            updates["status"] = "cancelled" if str(exam.get("status", "")).lower().strip() == "cancelled" else "scheduled"
        else:
            updates["status"] = form.get("status", "").strip()

        if update_exam(exam_id, updates):
            flash("Exam updated successfully.", "success")
            return redirect(url_for("admin.exams"))

        flash("Failed to save exam changes.", "danger")
        return redirect(url_for("admin.exams"))

    return render_template("admin/edit_exam.html", exam=exam, categories=categories)
