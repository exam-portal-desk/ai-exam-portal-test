"""
app/routes/web/exams.py
Exam flow web pages: instructions -> exam page -> submit (redirect flow).
The JSON/AJAX endpoints that used to live alongside these in
app/routes/exam.py (start, preload, sync-answers, attempts-status, ping)
now live in app/routes/api/v01/exams.py.

FIX (session bleed-through between attempts):
  Root causes addressed:
    1. submit_exam() now purges exam_data_{exam_id} from session so the
       preloaded question cache can never carry over to a new attempt.
    2. start_exam() [api/v01/exams.py] explicitly zeroes exam_answers and
       marked_for_review on every fresh-start path instead of using
       setdefault().
    3. _purge_exam_session() is the single source of truth for cleanup —
       called on submission AND on resume-guard failure so no path is missed.
    4. exam_page() guards against stale start_time by re-anchoring from the
       DB attempt row, not the session, when the attempt_id in session does
       not match the active DB attempt.
    5. A double-submit guard (idempotency check) in start_exam() prevents
       race-condition resume of a just-completed attempt.
"""

import logging
from datetime import datetime
from app.utils.datetime_service import now_utc_naive, to_app_tz, now_app_tz

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, session, request,
)

from app.middleware.session_guard import require_user_role
from app.db.exams import get_exam_by_id
from app.db.questions import get_questions_by_exam
from app.db.results import get_result_by_attempt_id
from app.db.attempts import (
    get_active_attempt, get_attempt_by_id,
    claim_attempt_for_finalization, update_attempt_answers_draft,
)
from app.db.sessions import set_exam_active
from app.services.exam_service import (
    get_cached_exam_data, preload_exam_data,
    purge_exam_session_cache, compute_exam_action_state,
    get_effective_deadline, finalize_exam_attempt, is_manual_submission_allowed,
)
from app.services.result_service import can_user_see_result
from app.db.dashboard_events import mark_event_seen

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

    # Also delegate to the service layer so any in-memory cache entry
    # tied to this session is evicted.
    purge_exam_session_cache(exam_id)

    session.modified = True


# ─────────────────────────────────────────────
# Instructions
# ─────────────────────────────────────────────

def _exam_action_context(exam_id):
    """Shared context-building for exam_instructions() and exam_kiosk() —
    both pages show the same Start/Resume/Prepare decision for the same
    exam, just in different chrome, and must never be able to disagree
    with each other about it. Returns None if the exam doesn't exist."""
    exam = get_exam_by_id(exam_id)
    if not exam:
        return None

    exam.setdefault("positive_marks", 1)
    exam.setdefault("negative_marks", 0)

    user_id = session["user_id"]
    mark_event_seen(user_id, "new_exam", exam_id)

    state = compute_exam_action_state(user_id, exam)

    # SOURCE OF TRUTH for "has this student already started preparing this
    # Scheduled Exam" — the Flask session's preload cache (get_cached_exam_data,
    # written by preload_exam_data() when the student first clicks Prepare)
    # already IS server-side, session-backed state that survives a refresh —
    # unlike a JS variable, which a refresh wipes. Reusing it here means the
    # instructions/kiosk page can tell, on every render, whether to auto-
    # resume the countdown/launch flow instead of showing the "click
    # Prepare" gate again, with no new storage needed. False whenever
    # there's nothing to resume (never prepared, or an active attempt
    # already exists — that renders the Resume Exam branch instead, which
    # never reads this flag).
    prep_already_started = bool(state["is_scheduled"] and get_cached_exam_data(exam_id))

    return {
        "exam": exam,
        "active_attempt": state["active_attempt"],
        "attempts_left": state["attempts_left"],
        "max_attempts": state["max_attempts"],
        "attempts_exhausted": state["attempts_exhausted"],
        "can_start": state["can_start"],
        "has_started": state["has_started"],
        "has_ended": state["has_ended"],
        "window": state["window"],
        "is_scheduled": state["is_scheduled"],
        "prep_open": state["prep_open"],
        "effective_status": state["effective_status"],
        "prep_already_started": prep_already_started,
    }


@exam_bp.route("/exam-instructions/<int:exam_id>")
@require_user_role
def exam_instructions(exam_id):
    ctx = _exam_action_context(exam_id)
    if ctx is None:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))
    return render_template("exam_instructions.html", **ctx)


# ─────────────────────────────────────────────
# Scheduled Exam Kiosk — dedicated fullscreen preparation/exam/completion
# window. Manual Exams never use this: they keep the original popup-window
# + exam_page.html's own fullscreen gate, completely unchanged. See
# templates/exam_kiosk.html for why this exists as its own window rather
# than reusing the (persistent, always-navigable) Instructions page as the
# fullscreen host.
# ─────────────────────────────────────────────

@exam_bp.route("/exam-kiosk/<int:exam_id>")
@require_user_role
def exam_kiosk(exam_id):
    ctx = _exam_action_context(exam_id)
    if ctx is None:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))
    if not ctx["is_scheduled"]:
        # The Kiosk only exists for Scheduled Exams — a Manual Exam
        # reaching this URL (stale link, manual navigation) belongs on the
        # normal instructions page instead.
        return redirect(url_for("exam.exam_instructions", exam_id=exam_id))

    # Same decision the Instructions page's action button already makes
    # (attempts_exhausted -> active_attempt -> can_start -> cancelled ->
    # prep_open -> ended), just resolved once here into "is there anything
    # for the Kiosk to do" plus a plain-language reason when there isn't —
    # actual enforcement is unchanged and still lives entirely in
    # /api/v01/exams/<id>/start and /preload, never in this presentation
    # logic.
    kiosk_ready = (not ctx["attempts_exhausted"]) and (
        ctx["active_attempt"] or ctx["can_start"] or (
            ctx["effective_status"] != "cancelled" and not ctx["has_started"] and ctx["prep_open"]
        )
    )
    terminal_icon = terminal_title = terminal_msg = None
    if not kiosk_ready:
        if ctx["attempts_exhausted"]:
            terminal_icon, terminal_title = "ban", "Attempts Exhausted"
            terminal_msg = "You have used all your allowed attempts for this exam."
        elif ctx["effective_status"] == "cancelled":
            terminal_icon, terminal_title = "ban", "Exam Cancelled"
            terminal_msg = "This scheduled exam has been cancelled by the administrator."
        elif ctx["has_ended"]:
            terminal_icon, terminal_title = "flag-checkered", "Exam Has Ended"
            terminal_msg = "This exam's window has closed and it can no longer be started."
        else:
            terminal_icon, terminal_title = "clock", "Not Open Yet"
            win = ctx["window"] or {}
            when = f"{ctx['exam'].get('date','')} at {win.get('start_time_ampm') or ctx['exam'].get('start_time','')}"
            terminal_msg = f"This exam isn't open for preparation yet. Scheduled for {when}."

    # standalone=True: no top nav / sidebar chrome (same mechanism
    # base.html already uses for /exam/ pages) — the Kiosk is meant to be
    # a clean, distraction-free window with nothing behind it once it's
    # fullscreen.
    return render_template(
        "exam_kiosk.html", standalone=True,
        kiosk_ready=kiosk_ready,
        terminal_icon=terminal_icon, terminal_title=terminal_title, terminal_msg=terminal_msg,
        **ctx,
    )


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
    # get_effective_deadline() is manual-exam-identical to the old
    # start+duration math (personal_deadline, untouched); for a Scheduled
    # Exam it additionally caps the deadline at the official end, so a
    # late-starting student never gains time — see its docstring for the
    # worked examples this implements.
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
            deadline_dt       = get_effective_deadline(exam_data, to_app_tz(start_dt))
            remaining_seconds = max(0, int((deadline_dt - now_app_tz()).total_seconds()))
            if remaining_seconds <= 0:
                # The student's own deadline has already passed by the time
                # this page (re)loaded — e.g. they closed the tab and came
                # back late, or refreshed right at zero. Finalize properly
                # (score + persist results/responses) rather than the old
                # behaviour of just flipping status with no result ever
                # created. One last fold-in of whatever the session still
                # holds happens BEFORE claiming (while status is still
                # 'in_progress', the only state update_attempt_answers_draft
                # writes in) — it's the freshest answer state this request
                # can offer, in case it's newer than the last autosave.
                # claim_attempt_for_finalization() is then atomic and
                # idempotent: if the background auto-submit sweep already
                # claimed/finalized this attempt in the meantime, this finds
                # nothing to claim and skips straight to the same redirect
                # — never double-scores.
                update_attempt_answers_draft(db_attempt_id, session.get("exam_answers") or {})
                claimed = claim_attempt_for_finalization(db_attempt_id)
                if claimed:
                    finalize_exam_attempt(db_attempt_id)
                _purge_exam_session(exam_id)
                flash("Your exam time has expired. Your attempt has been submitted automatically.", "warning")
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
        manual_submission_allowed=is_manual_submission_allowed(exam_data),
    )


# ─────────────────────────────────────────────
# Submit
# ─────────────────────────────────────────────

@exam_bp.route("/submit-exam/<int:exam_id>", methods=["POST"])
@require_user_role
def submit_exam(exam_id):
    """Handles BOTH a genuine manual submit and the client-side timer's
    auto-submit-at-zero (they hit this exact same route — see
    templates/exam_page.html autoSubmitExam()). Either way, the real
    work is: claim the attempt (in_progress -> finalizing, atomically —
    see claim_attempt_for_finalization()), then score+persist via the
    same finalize_exam_attempt() the background auto-submit sweep uses.
    This makes a manual submit, a client-timer auto-submit, and the
    server-side sweep all converge on one identical, idempotent code
    path — whichever of them reaches a given attempt first is the only
    one that actually finalizes it; every later/concurrent caller sees
    it's no longer 'in_progress' and treats that as success, not an error.
    """
    user_id = session["user_id"]

    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard.dashboard"))

    attempt_id = session.get("latest_attempt_id")
    if not attempt_id:
        flash("No active exam attempt found. Please start the exam first.", "warning")
        return redirect(url_for("exam.exam_instructions", exam_id=exam_id))
    attempt_id = int(attempt_id)

    def _respond_with_existing_result(info_message: str):
        """Shared idempotent-exit path: this attempt is no longer
        in_progress (already finalized by someone else — the sweep, an
        earlier request, a duplicate submit) — show the existing result
        rather than erroring or rescoring."""
        existing = get_result_by_attempt_id(attempt_id)
        set_exam_active(session.get("token", ""), is_active=False)
        _purge_exam_session(exam_id)
        if not existing:
            # Claimed but not yet finalized (the claimer is mid-flight,
            # almost certainly the sweep, milliseconds away) — nothing to
            # show yet; sending the student to the exam page will re-run
            # exam_page()'s own not-in_progress handling, which redirects
            # them to instructions with an accurate state a moment later.
            flash("Your exam is being finalized. Please check back in a moment.", "info")
            return redirect(url_for("exam.exam_instructions", exam_id=exam_id))
        flash(info_message, "success")
        visible, _ = can_user_see_result(exam, existing)
        if visible:
            return redirect(url_for("result.result", exam_id=exam_id))
        return redirect(url_for("result.result_pending", exam_id=exam_id, result_id=existing["id"]))

    attempt = get_attempt_by_id(attempt_id)
    if not attempt or int(attempt.get("student_id") or 0) != int(user_id) or int(attempt.get("exam_id") or 0) != int(exam_id):
        flash("Invalid exam attempt.", "error")
        return redirect(url_for("exam.exam_instructions", exam_id=exam_id))

    if attempt.get("status") != "in_progress":
        return _respond_with_existing_result("Exam already submitted.")

    # SECURITY (Feature: Scheduled Exam manual-submission control) — only
    # blocks a genuinely EARLY voluntary submit; once the deadline has
    # actually passed this is allowed through regardless of the setting,
    # since at that point it's no longer "early" whether it was triggered
    # by the student's own timer or arrived with no button at all. The
    # frontend hides the Submit button for this case, but per this app's
    # established pattern that's a UI convenience only — this is the real
    # enforcement, re-checked independently of what the client sent.
    now = now_utc_naive()
    # DB rows normalize every timestamp column to an ISO string on read
    # (see _normalize_row() in app/db/__init__.py) — effective_deadline
    # comes back as str, not datetime, so it must be parsed before it can
    # be compared against `now`.
    deadline_raw = attempt.get("effective_deadline")
    deadline = datetime.fromisoformat(str(deadline_raw)) if deadline_raw else None
    if exam.get("scheduled_mode") and not is_manual_submission_allowed(exam):
        if deadline and now < deadline:
            flash(
                "Manual submission is not enabled for this exam — it will submit "
                "automatically when the exam time ends.",
                "warning",
            )
            return redirect(url_for("exam.exam_page", exam_id=exam_id))

    # Fold in the freshest session answers one last time before claiming —
    # this only writes while status is still 'in_progress' (true here),
    # so it can never race the scorer that reads answers_draft afterward.
    update_attempt_answers_draft(attempt_id, session.get("exam_answers") or {})

    claimed = claim_attempt_for_finalization(attempt_id)
    if not claimed:
        # Someone else (almost certainly the background sweep) claimed it
        # in the tiny window between the status check above and now.
        return _respond_with_existing_result("Exam already submitted.")

    ok, result_id, msg = finalize_exam_attempt(attempt_id)
    if not ok:
        # Left in 'finalizing' — the auto-submit sweep's stale-reclaim
        # will retry it shortly; this request still needs to tell the
        # student something reasonable rather than silently erroring.
        log.error("[exam] submit_exam finalize failed attempt_id=%s: %s", attempt_id, msg)
        flash("We're still saving your exam — please check your results in a moment.", "warning")
        set_exam_active(session.get("token", ""), is_active=False)
        _purge_exam_session(exam_id)
        return redirect(url_for("dashboard.dashboard"))

    set_exam_active(session.get("token", ""), is_active=False)
    _purge_exam_session(exam_id)
    session["latest_result_id"] = result_id
    session.modified = True

    log.info("[exam] Submitted attempt_id=%s result_id=%s user=%s exam=%s",
             attempt_id, result_id, user_id, exam_id)

    flash("Exam submitted successfully!", "success")

    visible, _ = can_user_see_result(
        exam, {"completed_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")}
    )
    if visible:
        return redirect(url_for("result.result", exam_id=exam_id))
    return redirect(url_for("result.result_pending", exam_id=exam_id, result_id=result_id))
