"""
app/routes/api/v01/exams.py
Exam flow JSON/AJAX API (v01): start, preload, answer-sync, attempt-status.
Relocated from app/routes/exam.py — logic unchanged, only the URL prefix
moved under /api/v01/exams. The HTML pages (instructions/exam/submit) and
the shared _purge_exam_session() helper live in app/routes/web/exams.py —
imported from there so there is exactly one authoritative session-cleanup
function, per that module's own "never inline pops" comment.

Also includes the generic /api/v01/ping session-liveness check (previously
POST /_ping).
"""

import logging
from flask import Blueprint, url_for, session, request, jsonify

from app.middleware.session_guard import require_user_role
from app.db.exams import get_exam_by_id
from app.db.attempts import (
    get_active_attempt, get_completed_attempts_count,
    get_all_attempts_for_exam, create_exam_attempt,
)
from app.db.sessions import set_exam_active
from app.services.exam_service import get_cached_exam_data, preload_exam_data
from app.utils.helpers import safe_int
from app.utils.datetime_service import now_utc_naive
from app.routes.web.exams import _purge_exam_session

log = logging.getLogger(__name__)
exam_api_bp = Blueprint("exam_api", __name__, url_prefix="/api/v01/exams")
ping_api_bp = Blueprint("ping_api", __name__, url_prefix="/api/v01")


# ─────────────────────────────────────────────
# Start exam
# ─────────────────────────────────────────────

@exam_api_bp.route("/<int:exam_id>/start", methods=["POST"])
@require_user_role
def start_exam(exam_id):
    user_id = session["user_id"]

    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found."})

    # ── Idempotency guard: check for a genuine in-progress attempt ──────────
    # We query the DB authoritatively — never trust session state alone for
    # this decision. The DB is the single source of truth for attempt status.
    active = get_active_attempt(user_id, exam_id)

    if active:
        # Resume path — genuine mid-exam return.
        # Overwrite session unconditionally so any leftover stale state
        # from a previous attempt cannot bleed through.
        session["latest_attempt_id"] = int(active["id"])
        session["exam_start_time"]   = active.get("start_time")
        # FIX: use explicit assignment, NOT setdefault — setdefault silently
        # preserves old answers if the key already exists.
        if "exam_answers" not in session:
            session["exam_answers"] = {}
        if "marked_for_review" not in session:
            session["marked_for_review"] = []
        session.permanent = True
        session.modified  = True
        log.info("[exam] Resuming attempt id=%s for user=%s exam=%s",
                 active["id"], user_id, exam_id)
        return jsonify({
            "success":      True,
            "redirect_url": url_for("exam.exam_page", exam_id=exam_id),
            "resumed":      True,
            "attempt_id":   active["id"],
        })

    # ── Fresh-start path ────────────────────────────────────────────────────
    # Guarantee a completely clean slate before creating the new attempt.
    # This handles the case where submit_exam() succeeded in the DB but the
    # session cleanup failed (e.g. server restart between submit and redirect).
    _purge_exam_session(exam_id)

    # Check attempt limit against the DB (completed count is already authoritative)
    completed = get_completed_attempts_count(user_id, exam_id)
    max_att   = safe_int(exam.get("max_attempts"), 0)
    if max_att > 0 and completed >= max_att:
        return jsonify({"success": False, "message": f"Maximum attempts ({max_att}) reached."})

    # Next attempt number — derive from DB, never from session
    all_atts     = get_all_attempts_for_exam(user_id, exam_id)
    next_att_num = max((int(a.get("attempt_number", 0)) for a in all_atts), default=0) + 1
    start_iso    = now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")

    created = create_exam_attempt({
        "student_id":     int(user_id),
        "exam_id":        int(exam_id),
        "attempt_number": next_att_num,
        "status":         "in_progress",
        "start_time":     start_iso,
        "end_time":       None,
    })
    if not created:
        log.error("[exam] create_exam_attempt failed for user=%s exam=%s", user_id, exam_id)
        return jsonify({"success": False, "message": "Failed to create exam attempt."}), 500

    attempt_id = int(created["id"])

    # Write fresh state explicitly — never rely on previous values surviving
    session["latest_attempt_id"] = attempt_id
    session["exam_start_time"]   = start_iso
    session["exam_answers"]      = {}
    session["marked_for_review"] = []
    session["timer_reset_flag"]  = True
    session["attempt_number"]    = next_att_num
    session.permanent = True
    session.modified  = True

    set_exam_active(session.get("token", ""), exam_id=exam_id, result_id=attempt_id, is_active=True)

    log.info("[exam] Fresh attempt id=%s number=%s for user=%s exam=%s",
             attempt_id, next_att_num, user_id, exam_id)
    return jsonify({
        "success":        True,
        "redirect_url":   url_for("exam.exam_page", exam_id=exam_id),
        "resumed":        False,
        "attempt_id":     attempt_id,
        "attempt_number": next_att_num,
        "fresh_start":    True,
    })


# ─────────────────────────────────────────────
# Preload (AJAX)
# ─────────────────────────────────────────────

@exam_api_bp.route("/<int:exam_id>/preload")
@require_user_role
def preload_exam_route(exam_id):
    cached = get_cached_exam_data(exam_id)
    if cached:
        return jsonify({"success": True, "cached": True,
                        "question_count": cached["total_questions"]})
    ok, msg = preload_exam_data(exam_id)
    return jsonify({"success": ok, "message": msg, "cached": False}), (200 if ok else 400)


@exam_api_bp.route("/<int:exam_id>/answers", methods=["POST"])
@require_user_role
def sync_exam_answers(exam_id):
    data = request.get_json() or {}
    session["exam_answers"]      = data.get("answers", {})
    session["marked_for_review"] = data.get("markedForReview", [])
    session.modified = True
    return jsonify({"success": True})


@exam_api_bp.route("/<int:exam_id>/attempt-status")
@require_user_role
def api_exam_attempts_status(exam_id):
    user_id = session["user_id"]
    exam    = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"error": "exam_not_found"}), 404

    max_att   = safe_int(exam.get("max_attempts"), 0)
    completed = get_completed_attempts_count(user_id, exam_id)
    latest    = get_active_attempt(user_id, exam_id)

    if latest:
        return jsonify({
            "has_active_attempt": True,
            "attempt_id":         int(latest["id"]),
            "attempt_number":     int(latest.get("attempt_number", 0)),
            "start_time":         latest.get("start_time"),
            "completed_count":    completed,
            "max_attempts":       max_att,
            "attempts_remaining": (max_att - completed) if max_att > 0 else -1,
        })
    return jsonify({
        "has_active_attempt": False,
        "completed_count":    completed,
        "max_attempts":       max_att,
        "attempts_remaining": (max_att - completed) if max_att > 0 else -1,
        "can_start_new":      (max_att == 0 or completed < max_att),
    })


# ─────────────────────────────────────────────
# Liveness ping (was POST /_ping)
# ─────────────────────────────────────────────

@ping_api_bp.route("/ping", methods=["POST"])
def ping():
    if "user_id" in session:
        return "", 204
    return jsonify({"reason": "no_session"}), 401
