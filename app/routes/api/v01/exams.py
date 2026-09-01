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

    # ── SECURITY: authoritative time-window gate (fresh-start only) ─────────
    # Resuming an existing in-progress attempt (handled above) is always
    # allowed — it was legitimately created while the window was open.
    # Starting a NEW attempt requires the exam to genuinely be open right
    # now. This is the real backend enforcement point: the UI in
    # exam_instructions.html hides/enables the Start button for the same
    # reason, but that alone is not enforcement — this endpoint can be
    # called directly, so it must independently re-verify eligibility here
    # rather than trusting the caller. Same rule as
    # compute_exam_action_state() (app/services/exam_service.py) — one
    # authoritative definition (is_official_window_open), reused, so a
    # Scheduled Exam uses its schedule-only rule and a Manual Exam keeps
    # using is_exam_window_open() exactly as before.
    from app.services.exam_service import is_official_window_open, get_exam_time_window, get_effective_status
    if not is_official_window_open(exam):
        window = get_exam_time_window(exam)
        if exam.get("scheduled_mode"):
            # A Scheduled Exam's "Start Now" button stays visually active
            # even before the official start (requirement: clicking early
            # must show a countdown, not a dead-end error). too_early with
            # scheduled_start_iso lets the frontend drive that countdown
            # off a server-authoritative timestamp rather than inventing
            # one client-side; the client re-attempts /start at zero.
            effective = get_effective_status(exam)
            if effective == "upcoming" and window.get("start_iso"):
                log.info("[exam] Early /start on scheduled exam=%s user=%s — countdown to %s",
                          exam_id, user_id, window.get("start_iso"))
                return jsonify({
                    "success": False,
                    "too_early": True,
                    "scheduled_start_iso": window.get("start_iso"),
                    "message": f"This exam will begin at {window.get('start_time_ampm') or exam.get('start_time','')} on {exam.get('date','')}.",
                }), 403
            message = (
                "This exam has been cancelled." if effective == "cancelled"
                else "This exam has ended and can no longer be started."
            )
        elif not window.get("has_started"):
            message = (
                f"This exam hasn't started yet. Scheduled for "
                f"{window.get('start_time_ampm') or exam.get('start_time','')} on {exam.get('date','')}."
            )
        else:
            message = "This exam has ended and can no longer be started."
        log.warning("[exam] Blocked fresh-start outside window: user=%s exam=%s status=%s",
                    user_id, exam_id, exam.get("status"))
        return jsonify({"success": False, "message": message}), 403

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
    start_dt_utc = now_utc_naive()
    start_iso    = start_dt_utc.strftime("%Y-%m-%d %H:%M:%S")

    # SERVER-AUTHORITATIVE DEADLINE: computed once, right now, from the
    # exact same rule the student-facing timer uses (get_effective_deadline
    # — manual: start+duration; scheduled: capped at the official end) and
    # stored on the attempt so the background auto-submit sweep can find
    # and finalize it later without ever needing the student's browser,
    # session, or local clock. See app/services/exam_service.py
    # finalize_exam_attempt() / app/services/auto_submit_service.py.
    from app.services.exam_service import compute_deadline_utc_naive
    effective_deadline = compute_deadline_utc_naive(exam, start_dt_utc)

    created = create_exam_attempt({
        "student_id":     int(user_id),
        "exam_id":        int(exam_id),
        "attempt_number": next_att_num,
        "status":         "in_progress",
        "start_time":     start_iso,
        "end_time":       None,
        "effective_deadline": effective_deadline.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if not created:
        # RACE RECOVERY: idx_exam_attempts_one_in_progress (a partial unique
        # index on student_id,exam_id WHERE status='in_progress') can reject
        # this INSERT if a concurrent request (double-click, two tabs, a
        # retried countdown-triggered call) already created the in-progress
        # attempt between our get_active_attempt() check above and this
        # insert. Re-check rather than surfacing a bare 500 — the other
        # request's attempt is exactly as valid as this one would have been.
        race_active = get_active_attempt(user_id, exam_id)
        if race_active:
            session["latest_attempt_id"] = int(race_active["id"])
            session["exam_start_time"]   = race_active.get("start_time")
            if "exam_answers" not in session:
                session["exam_answers"] = {}
            if "marked_for_review" not in session:
                session["marked_for_review"] = []
            session.permanent = True
            session.modified  = True
            log.info("[exam] Race-recovered concurrent attempt id=%s for user=%s exam=%s",
                     race_active["id"], user_id, exam_id)
            return jsonify({
                "success":      True,
                "redirect_url": url_for("exam.exam_page", exam_id=exam_id),
                "resumed":      True,
                "attempt_id":   race_active["id"],
            })
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
    # SECURITY: two DISTINCT window checks, not one relaxed check —
    # is_prep_window_open() (Scheduled Exams only, opens prep_window_minutes
    # before the official start) OR is_official_window_open() (the same gate
    # /start uses). An active attempt (resume) is always allowed to preload.
    # For a Manual Exam is_prep_window_open() is always False, so this is
    # exactly the old is_exam_window_open()-only check — unchanged behaviour.
    user_id = session["user_id"]
    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found."}), 404
    if not get_active_attempt(user_id, exam_id):
        from app.services.exam_service import is_official_window_open, is_prep_window_open
        if not (is_prep_window_open(exam) or is_official_window_open(exam)):
            return jsonify({"success": False, "message": "This exam is not currently open."}), 403

    cached = get_cached_exam_data(exam_id)
    if cached:
        return jsonify({"success": True, "cached": True,
                        "question_count": cached["total_questions"]})
    ok, msg = preload_exam_data(exam_id)
    return jsonify({"success": ok, "message": msg, "cached": False}), (200 if ok else 400)


@exam_api_bp.route("/<int:exam_id>/answers", methods=["POST"])
@require_user_role
def sync_exam_answers(exam_id):
    # SECURITY: for a Scheduled Exam, reject answer syncs once the
    # submission-acceptance cutoff (official_end + completion_buffer) has
    # passed — the buffer tolerates a slow/late sync, it does not extend
    # indefinitely. Manual exams: is_submission_window_open() is always
    # True, so this is a no-op for them (unchanged behaviour).
    from app.services.exam_service import is_submission_window_open
    exam = get_exam_by_id(exam_id)
    if exam and not is_submission_window_open(exam):
        return jsonify({"success": False, "message": "This exam's submission window has closed."}), 403

    data = request.get_json() or {}
    answers = data.get("answers", {})
    session["exam_answers"]      = answers
    session["marked_for_review"] = data.get("markedForReview", [])
    session.modified = True

    # DURABILITY: also persist to the attempt row itself, not just the
    # Flask session — this is what lets the background auto-submit sweep
    # (and server-side re-scoring after a browser crash/close) see the
    # student's real answers with no dependency on their session/browser
    # ever coming back. Conditioned on status='in_progress' inside
    # update_attempt_answers_draft(): if this attempt was already claimed
    # for finalization (the deadline arrived right as this sync landed),
    # the write is safely dropped rather than racing the scorer.
    attempt_id = session.get("latest_attempt_id")
    if attempt_id:
        from app.db.attempts import update_attempt_answers_draft
        update_attempt_answers_draft(int(attempt_id), answers)

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

    from app.services.exam_service import (
        get_exam_time_window, get_effective_status, is_prep_window_open, is_official_window_open,
    )
    # SECURITY / CORRECTNESS: is_official_window_open() is the SAME check
    # /start uses (it's what actually creates the attempt) — reused here
    # so "can_start_new" genuinely means "a fresh attempt would succeed
    # right now" for BOTH exam modes, not just "attempts aren't
    # exhausted". A client (e.g. the notification popup's countdown-to-
    # live promotion) that only checked attempts-remaining would show a
    # "Start" button before the window was actually open.
    window_open = is_official_window_open(exam)
    attempts_left_ok = (max_att == 0 or completed < max_att)

    # Scheduled Exam info for the instructions-page countdown/prep UI to
    # poll — server-authoritative timestamps only, the client never
    # computes these itself. Empty for Manual Exams (nothing to poll).
    scheduled_info = {}
    if exam.get("scheduled_mode"):
        window = get_exam_time_window(exam)
        scheduled_info = {
            "scheduled_mode":       True,
            "effective_status":     get_effective_status(exam),
            "prep_open":            is_prep_window_open(exam),
            "official_open":        window_open,
            "scheduled_start_iso":  window.get("start_iso"),
            "official_end_iso":     window.get("end_iso"),
        }

    if latest:
        return jsonify({
            "has_active_attempt": True,
            "attempt_id":         int(latest["id"]),
            "attempt_number":     int(latest.get("attempt_number", 0)),
            "start_time":         latest.get("start_time"),
            "completed_count":    completed,
            "max_attempts":       max_att,
            "attempts_remaining": (max_att - completed) if max_att > 0 else -1,
            **scheduled_info,
        })
    return jsonify({
        "has_active_attempt": False,
        "completed_count":    completed,
        "max_attempts":       max_att,
        "attempts_remaining": (max_att - completed) if max_att > 0 else -1,
        "can_start_new":      attempts_left_ok and window_open,
        # Exposed separately from can_start_new so a client polling this
        # (the notification popup's countdown-to-live promotion) can tell
        # "genuinely exhausted" (window_open=True, attempts used up) apart
        # from "not actually open yet" (e.g. a Manual Exam whose scheduled
        # time has passed but the admin hasn't set it to Ongoing) — both
        # collapse to can_start_new=False, but they need different labels.
        "window_open":        window_open,
        **scheduled_info,
    })


# ─────────────────────────────────────────────
# Liveness ping (was POST /_ping)
# ─────────────────────────────────────────────

@ping_api_bp.route("/ping", methods=["POST"])
def ping():
    if "user_id" in session:
        return "", 204
    return jsonify({"reason": "no_session"}), 401
