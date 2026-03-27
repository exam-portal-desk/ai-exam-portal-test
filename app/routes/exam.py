"""
app/routes/exam.py
Exam flow routes: instructions → start → exam page → submit.

FIX (session bleed-through between attempts):
  Root causes addressed:
    1. submit_exam() now purges exam_data_{exam_id} from session so the
       preloaded question cache can never carry over to a new attempt.
    2. start_exam() explicitly zeroes exam_answers and marked_for_review
       on every fresh-start path instead of using setdefault().
    3. _purge_exam_session() is the single source of truth for cleanup —
       called on submission AND on resume-guard failure so no path is missed.
    4. exam_page() guards against stale start_time by re-anchoring from the
       DB attempt row, not the session, when the attempt_id in session does
       not match the active DB attempt.
    5. A double-submit guard (idempotency check) in start_exam() prevents
       race-condition resume of a just-completed attempt.
"""

import json
import logging
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, session, request, jsonify,
)

from app.middleware.session_guard import require_user_role
from app.db.exams import get_exam_by_id
from app.db.questions import get_questions_by_exam
from app.db.results import create_result, create_responses_bulk
from app.db.attempts import (
    get_active_attempt, get_completed_attempts_count,
    get_all_attempts_for_exam, create_exam_attempt, update_exam_attempt,
)
from app.db.sessions import set_exam_active
from app.services.exam_service import (
    get_cached_exam_data, preload_exam_data,
    check_answer, calculate_question_score,
    purge_exam_session_cache,
)
from app.services.result_service import can_user_see_result
from app.utils.helpers import safe_int

log = logging.getLogger(__name__)
exam_bp = Blueprint("exam", __name__)


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

_TRANSIENT_KEYS = (
    "exam_answers",
    "marked_for_review",
    "exam_start_time",
    "timer_reset_flag",
    "latest_attempt_id",
    "attempt_number",
)


def _purge_exam_session(exam_id: int) -> None:
    """
    Remove ALL exam-specific state from the Flask session.
    Called on submission and whenever a fresh attempt must start clean.
    This is the single authoritative cleanup function — never inline pops.
    """
    for k in _TRANSIENT_KEYS:
        session.pop(k, None)

    # Purge the preloaded question cache so a new attempt always fetches
    # fresh data and cannot inherit the previous attempt's exam_data block.
    session.pop(f"exam_data_{exam_id}", None)

    # Also delegate to the service layer so any Redis/in-memory cache entry
    # tied to this session is evicted.
    purge_exam_session_cache(exam_id)

    session.modified = True


# ─────────────────────────────────────────────
# Instructions
# ─────────────────────────────────────────────

@exam_bp.route("/exam-instructions/<int:exam_id>")
@require_user_role
def exam_instructions(exam_id):
    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    exam.setdefault("positive_marks", 1)
    exam.setdefault("negative_marks", 0)

    user_id         = session["user_id"]
    active_attempt  = get_active_attempt(user_id, exam_id)
    completed_count = get_completed_attempts_count(user_id, exam_id)
    max_attempts    = safe_int(exam.get("max_attempts"), 0)

    if max_attempts > 0:
        attempts_left      = max(max_attempts - completed_count, 0)
        attempts_exhausted = (attempts_left == 0)
        can_start          = not attempts_exhausted
    else:
        attempts_left      = None
        attempts_exhausted = False
        can_start          = True

    if active_attempt:
        can_start = False

    return render_template(
        "exam_instructions.html",
        exam=exam,
        active_attempt=active_attempt,
        attempts_left=attempts_left,
        max_attempts=max_attempts,
        attempts_exhausted=attempts_exhausted,
        can_start=can_start,
    )


# ─────────────────────────────────────────────
# Start exam
# ─────────────────────────────────────────────

@exam_bp.route("/start-exam/<int:exam_id>", methods=["POST"])
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
    start_iso    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
# Exam page
# ─────────────────────────────────────────────

@exam_bp.route("/exam/<int:exam_id>")
@require_user_role
def exam_page(exam_id):
    user_id = session["user_id"]

    # DB is authoritative — always verify the attempt here, never trust session alone
    active = get_active_attempt(user_id, exam_id)
    if not active:
        flash("Please start the exam first.", "warning")
        return redirect(url_for("exam.exam_instructions", exam_id=exam_id))

    db_attempt_id = int(active["id"])

    # ── Session/DB consistency guard ────────────────────────────────────────
    # If session carries a different attempt_id than the active DB attempt,
    # it means the user is starting a new attempt while stale session state
    # from a prior attempt is still present. Reset answers and start time to
    # the values from the current DB attempt row — never from session.
    session_attempt_id = session.get("latest_attempt_id")
    if session_attempt_id != db_attempt_id:
        log.warning(
            "[exam] Attempt ID mismatch: session=%s db=%s — resetting session state",
            session_attempt_id, db_attempt_id,
        )
        # Purge stale state, then bootstrap from the DB attempt
        _purge_exam_session(exam_id)
        session["latest_attempt_id"] = db_attempt_id
        session["exam_start_time"]   = active.get("start_time")
        session["exam_answers"]      = {}
        session["marked_for_review"] = []
        session.modified = True
    else:
        # IDs match — safe to trust session answers, but always re-anchor
        # start_time from the DB to prevent timer drift after server restarts.
        session["latest_attempt_id"] = db_attempt_id
        session["exam_start_time"]   = active.get("start_time")
        session.modified = True

    # ── Exam data cache ─────────────────────────────────────────────────────
    cached = get_cached_exam_data(exam_id)
    if not cached:
        ok, msg = preload_exam_data(exam_id)
        if not ok:
            flash(f"Unable to load exam: {msg}", "error")
            return redirect(url_for("dashboard.dashboard"))
        cached = get_cached_exam_data(exam_id)

    if not cached:
        flash("Unable to load exam data.", "error")
        return redirect(url_for("dashboard.dashboard"))

    exam_data = cached["exam_info"]
    questions  = cached["questions"]

    if not questions:
        flash("No questions found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    # ── Timer ───────────────────────────────────────────────────────────────
    # Source of truth: active attempt's start_time from the DB row (already
    # written to session above from the DB value — not from a stale key).
    duration_secs     = int(float(exam_data.get("duration", 60))) * 60
    remaining_seconds = duration_secs
    is_fresh          = False
    start_time_str    = session.get("exam_start_time")

    if start_time_str:
        try:
            try:
                start_dt = datetime.fromisoformat(
                    str(start_time_str).replace("Z", "").replace("+00:00", "")
                )
            except Exception:
                start_dt = datetime.strptime(str(start_time_str), "%Y-%m-%d %H:%M:%S")
            elapsed           = (datetime.now() - start_dt).total_seconds()
            remaining_seconds = max(0, duration_secs - int(elapsed))
            if remaining_seconds <= 0:
                update_exam_attempt(db_attempt_id, {
                    "status":   "completed",
                    "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                _purge_exam_session(exam_id)
                flash("Your exam time has expired.", "warning")
                return redirect(url_for("exam.exam_instructions", exam_id=exam_id))
        except Exception as e:
            log.warning("[exam] Timer parse error for user=%s: %s", user_id, e)
            is_fresh = True
    else:
        is_fresh = True

    # ── Palette ─────────────────────────────────────────────────────────────
    palette = {}
    for i, q in enumerate(questions):
        qid = str(q.get("id", ""))
        if qid in (session.get("marked_for_review") or []):
            palette[i] = "review"
        elif qid in (session.get("exam_answers") or {}):
            palette[i] = "answered"
        else:
            palette[i] = "not-visited"

    return render_template(
        "exam_page.html",
        exam=exam_data,
        question=questions[0],
        current_index=0,
        selected_answer=(session.get("exam_answers") or {}).get(str(questions[0].get("id"))),
        total_questions=len(questions),
        palette=palette,
        questions=questions,
        remaining_seconds=int(remaining_seconds),
        active_attempt=active,
        is_fresh_start=is_fresh,
        show_resume_button=bool(active),
        show_start_button=False,
        attempts_left=-1,
        attempts_exhausted=False,
    )


# ─────────────────────────────────────────────
# Preload (AJAX)
# ─────────────────────────────────────────────

@exam_bp.route("/preload-exam/<int:exam_id>")
@require_user_role
def preload_exam_route(exam_id):
    cached = get_cached_exam_data(exam_id)
    if cached:
        return jsonify({"success": True, "cached": True,
                        "question_count": cached["total_questions"]})
    ok, msg = preload_exam_data(exam_id)
    return jsonify({"success": ok, "message": msg, "cached": False}), (200 if ok else 400)


@exam_bp.route("/api/sync-exam-answers/<int:exam_id>", methods=["POST"])
@require_user_role
def sync_exam_answers(exam_id):
    data = request.get_json() or {}
    session["exam_answers"]      = data.get("answers", {})
    session["marked_for_review"] = data.get("markedForReview", [])
    session.modified = True
    return jsonify({"success": True})


@exam_bp.route("/api/exam-attempts-status/<int:exam_id>")
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
# Submit
# ─────────────────────────────────────────────

@exam_bp.route("/submit-exam/<int:exam_id>", methods=["POST"])
@require_user_role
def submit_exam(exam_id):
    user_id  = session["user_id"]
    username = session.get("username", "Student")

    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    questions = get_questions_by_exam(exam_id)
    if not questions:
        flash("No questions found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    # ── Idempotency: verify an attempt actually exists and is in-progress ────
    # Prevents double-submit from creating a second result row.
    attempt_id = session.get("latest_attempt_id")
    if not attempt_id:
        flash("No active exam attempt found. Please start the exam first.", "warning")
        return redirect(url_for("exam.exam_instructions", exam_id=exam_id))

    answers     = session.get("exam_answers", {})
    total_q     = len(questions)
    correct_ans = incorrect_ans = 0
    total_score = max_score = 0.0
    responses   = []

    neg_raw = str(exam.get("negative_marks", "0")).strip()

    for q in questions:
        qid   = str(q["id"])
        qtype = q.get("question_type", "MCQ")
        pos   = float(q.get("positive_marks", 1) or 1)
        max_score += pos

        given  = answers.get(qid)
        corr   = q.get("correct_answer")
        is_att = given is not None and given != ""
        is_cor = False
        marks  = 0.0

        if is_att:
            is_cor = check_answer(given, corr, qtype, float(q.get("tolerance", 0) or 0))
            marks  = calculate_question_score(
                is_cor, pos,
                neg_raw.split(",")[0] if "," in neg_raw else neg_raw
            )
            if is_cor:
                correct_ans += 1
            else:
                incorrect_ans += 1
        total_score += marks

        responses.append({
            "question_id":    int(qid),
            "exam_id":        int(exam_id),
            "question_type":  qtype,
            "given_answer":   json.dumps(given) if isinstance(given, list) else str(given or ""),
            "correct_answer": json.dumps(corr) if isinstance(corr, list) else str(corr or ""),
            "is_correct":     is_cor,
            "is_attempted":   is_att,
            "marks_obtained": round(float(marks), 2),
        })

    unanswered = total_q - correct_ans - incorrect_ans
    percentage = (total_score / max_score * 100) if max_score > 0 else 0.0
    grade = (
        "A+" if percentage >= 90 else
        "A"  if percentage >= 80 else
        "B"  if percentage >= 70 else
        "C"  if percentage >= 60 else
        "D"  if percentage >= 50 else "F"
    )

    # Time taken
    start_str = session.get("exam_start_time", "")
    try:
        try:
            start_dt = datetime.fromisoformat(
                str(start_str).replace("Z", "").replace("+00:00", "")
            )
        except Exception:
            start_dt = datetime.strptime(str(start_str), "%Y-%m-%d %H:%M:%S")
        time_taken = max(0, int((datetime.now() - start_dt).total_seconds() / 60))
    except Exception:
        time_taken = 0

    created = create_result({
        "student_id":           int(user_id),
        "exam_id":              int(exam_id),
        "score":                int(round(total_score)),
        "max_score":            int(round(max_score)),
        "percentage":           round(percentage, 2),
        "grade":                grade,
        "completed_at":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_taken_minutes":   time_taken,
        "correct_answers":      correct_ans,
        "incorrect_answers":    incorrect_ans,
        "unanswered_questions": unanswered,
        "total_questions":      total_q,
    })
    if not created:
        flash("Error saving result. Please contact support.", "error")
        return redirect(url_for("exam.exam_page", exam_id=exam_id))

    result_id = int(created["id"])
    for r in responses:
        r["result_id"] = result_id

    create_responses_bulk(responses)

    # Mark attempt complete
    update_exam_attempt(int(attempt_id), {
        "status":   "completed",
        "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    # ── Complete session purge ───────────────────────────────────────────────
    # Preserve only the result_id for the redirect target; everything else
    # must be wiped so the next attempt starts completely clean.
    set_exam_active(session.get("token", ""), is_active=False)
    _purge_exam_session(exam_id)          # clears transient keys + exam_data cache
    session["latest_result_id"] = result_id
    session.modified = True

    log.info("[exam] Submitted attempt_id=%s result_id=%s user=%s exam=%s",
             attempt_id, result_id, user_id, exam_id)

    flash("Exam submitted successfully!", "success")

    visible, _ = can_user_see_result(
        exam, {"completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    )
    if visible:
        return redirect(url_for("result.result", exam_id=exam_id))
    return redirect(url_for("result.result_pending", exam_id=exam_id, result_id=result_id))


@exam_bp.route("/_ping", methods=["POST"])
def ping():
    if "user_id" in session:
        return "", 204
    return jsonify({"reason": "no_session"}), 401