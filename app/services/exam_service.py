"""
app/services/exam_service.py
Business logic for the exam flow:
  - Preloading and caching exam data
  - Answer checking (MCQ / MSQ / NUMERIC)
  - Score calculation

FIX: Added purge_exam_session_cache() — called by exam.py's _purge_exam_session()
on every submission and fresh-start so the session-stored exam_data block
(questions, exam info) can never bleed from one attempt into the next.
"""

import time
import logging
from typing import Tuple, List, Dict, Optional

from flask import session

import app.config as config
from app.db.exams import get_exam_by_id
from app.db.questions import get_questions_by_exam
from app.utils.helpers import safe_float, safe_int
from app.utils.cache import (
    get as cache_get,
    set as cache_set,
    delete as cache_delete,
    is_force_refresh,
    set_force_refresh,
    clear_image,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Answer checking
# ─────────────────────────────────────────────

def check_answer(given, correct, question_type: str, tolerance: float = 0.1) -> bool:
    if question_type == "MCQ":
        if given is None or correct is None:
            return False
        return str(given).strip().upper() == str(correct).strip().upper()

    if question_type == "MSQ":
        if not given or not correct:
            return False
        given_set = (
            {x.strip().upper() for x in given.split(",")}
            if isinstance(given, str)
            else {str(x).strip().upper() for x in given}
        )
        correct_set = (
            {x.strip().upper() for x in correct.split(",")}
            if isinstance(correct, str)
            else {str(x).strip().upper() for x in correct}
        )
        return given_set == correct_set

    if question_type == "NUMERIC":
        if given is None or correct is None:
            return False
        try:
            return abs(float(str(given).strip()) - float(str(correct).strip())) <= tolerance
        except (ValueError, TypeError):
            return False

    return False


def calculate_question_score(
    is_correct: bool,
    positive_marks,
    negative_marks,
) -> float:
    pos = safe_float(positive_marks, 1.0)
    neg = safe_float(negative_marks, 0.0)
    return pos if is_correct else (-neg if neg else 0.0)


# ─────────────────────────────────────────────
# Session cache purge (new)
# ─────────────────────────────────────────────

def purge_exam_session_cache(exam_id: int) -> None:
    """
    Evict every cache layer associated with exam_id for the current session.

    Layers addressed:
      1. Flask session key  — exam_data_{exam_id}  (server-side session file,
         per SESSION_TYPE).
      2. App-level in-memory cache — keyed by exam_id so it is shared across
         requests. We do NOT clear this layer here because it holds static
         exam+question data that is safe to reuse across attempts and users.
         Only the per-session answer/timer state needs purging.

    The function is intentionally narrow: it only removes the preloaded data
    block from the Flask session, which is the cache layer that caused the
    bug. Cached question data is read-only and does not carry attempt state.
    """
    key = f"exam_data_{exam_id}"
    if key in session:
        session.pop(key, None)
        session.modified = True
        log.debug("[exam_service] Purged session cache key=%s", key)


# ─────────────────────────────────────────────
# Exam data preloading
# ─────────────────────────────────────────────

def get_cached_exam_data(exam_id: int) -> Optional[Dict]:
    """
    Return valid cached exam data from the Flask session, or None.

    IMPORTANT: This function reads from session, NOT from a shared cache.
    The session key is written by preload_exam_data() and purged by
    purge_exam_session_cache(). It can only be present if the current
    request's session was explicitly populated for this exam_id.
    """
    # Respect force-refresh (admin publish)
    if is_force_refresh() or session.get("force_refresh"):
        session.pop(f"exam_data_{exam_id}", None)
        return None

    cached = session.get(f"exam_data_{exam_id}")
    if not cached:
        return None

    required = {"exam_info", "questions", "total_questions", "exam_id"}
    if not required.issubset(cached.keys()):
        session.pop(f"exam_data_{exam_id}", None)
        return None

    if cached.get("exam_id") != exam_id:
        session.pop(f"exam_data_{exam_id}", None)
        return None

    if not isinstance(cached.get("questions"), list) or not cached["questions"]:
        session.pop(f"exam_data_{exam_id}", None)
        return None

    return cached


def preload_exam_data(exam_id: int) -> Tuple[bool, str]:
    """
    Load exam + questions, resolve images, and cache a RENDER-ONLY
    representation in the Flask session (never the answer key — see below).
    Returns (success, message).

    SECURITY: the cached representation deliberately omits correct_answer.
    Scoring (app/routes/web/exams.py submit_exam()) already re-fetches
    questions fresh from the DB and never reads this cache, so the answer
    key was previously present here unused — dead weight that only
    increased the blast radius of anything that might ever render this
    cache to the client. This matters more now that Scheduled Exams can
    populate this same cache during the pre-attempt preparation window,
    before an exam_attempts row even exists (see is_prep_window_open()) —
    the preparation phase must never hold answer-key data.

    PERFORMANCE: image resolution now runs concurrently via
    resolve_question_image_urls_bulk() instead of one storage.exists()
    round trip per question in a loop — this was the actual bottleneck
    behind "Start Now feels slow" for image-heavy exams (each round trip
    is an S3 HEAD request on the S3-compatible backend), and it's exactly
    what a Scheduled Exam's preparation window exists to hide.
    """
    start = time.time()
    force_refresh = is_force_refresh() or bool(session.get("force_refresh"))

    if force_refresh:
        session.pop(f"exam_data_{exam_id}", None)
        clear_image()

    questions = get_questions_by_exam(exam_id)
    if not questions:
        return False, f"No questions found for exam {exam_id}"

    exam_data = get_exam_by_id(exam_id)
    if not exam_data:
        return False, "Exam metadata not found"

    from app.services.image_storage_service import resolve_question_image_urls_bulk

    image_paths = [
        str(q.get("image_path", "")).strip()
        for q in questions
        if q.get("id") and str(q.get("image_path", "")).strip() not in ("", "nan", "None")
    ]
    image_url_map = resolve_question_image_urls_bulk(image_paths)

    processed: List[Dict] = []
    for q in questions:
        if not q.get("id"):
            continue

        pq = {
            "id":             q["id"],
            "question_text":  q.get("question_text", ""),
            "option_a":       q.get("option_a", ""),
            "option_b":       q.get("option_b", ""),
            "option_c":       q.get("option_c", ""),
            "option_d":       q.get("option_d", ""),
            "question_type":  q.get("question_type", "MCQ"),
            "positive_marks": q.get("positive_marks", 1),
            "negative_marks": q.get("negative_marks", 0),
            "image_path":     q.get("image_path", ""),
            "has_image":      False,
            "image_url":      None,
            "metadata":       q.get("metadata") or None,
        }

        image_path = str(q.get("image_path", "")).strip()
        if image_path and image_path not in ("", "nan", "None"):
            has_img, img_url = image_url_map.get(image_path, (False, None))
            pq["has_image"] = has_img
            pq["image_url"] = img_url

        processed.append(pq)

    if not processed:
        return False, "No questions could be processed"

    session[f"exam_data_{exam_id}"] = {
        "exam_info":       exam_data,
        "questions":       processed,
        "total_questions": len(processed),
        "exam_id":         exam_id,
    }
    session.permanent = True

    if force_refresh:
        set_force_refresh(False)
        session.pop("force_refresh", None)
        try:
            from flask import current_app
            current_app.config.pop("FORCE_REFRESH_TIMESTAMP", None)
        except Exception:
            pass

    session.modified = True
    log.info("[exam_service] Preloaded exam=%s in %.2fs — %d questions",
             exam_id, time.time() - start, len(processed))
    return True, f"Successfully loaded {len(processed)} questions"


# ─────────────────────────────────────────────
# Exam action-state / time-window (shared by exam_instructions() and the
# Student Dashboard's "Today's Exams" cards — single source of truth so both
# apply the exact same Start/Resume/attempts-exhausted rules)
# ─────────────────────────────────────────────

def is_exam_window_open(exam: Dict) -> bool:
    """MANUAL-EXAM rule — never called directly for a Scheduled Exam (see
    is_official_window_open() below, which delegates here for manual exams
    and uses a different, time-only rule for scheduled ones).

    A Manual Exam is startable if and only if the admin has explicitly set
    its status to "ongoing" (and its real scheduled window hasn't actually
    finished — a stale "ongoing" label from days ago doesn't stay open
    forever). Any other status ("upcoming", "draft", "completed", ...)
    NEVER auto-unlocks, no matter how much time has passed — only the
    admin's own status change does.

    FIX (2026-08-30): this previously ALSO auto-unlocked once the exam's
    scheduled date/time had passed, regardless of status — i.e. an exam
    the admin explicitly left "Upcoming" would silently become startable
    the instant its configured time arrived, on the notification popup,
    the instructions page, AND the /start endpoint itself, with no admin
    action at all. That directly violated "Manual exams must not change
    automatically" for STARTABILITY, not just for the status label's own
    value — the admin's explicit control was being silently overridden.
    The one case this must still keep blocking (from the original access-
    control fix) is starting/entering an exam whose status was never
    updated by the admin to reflect a real Upcoming/Completed state —
    that's still fully covered here: "upcoming" and "completed" both
    return False unconditionally, exactly as before.

    FEATURE (2026-08-30): "Available after selected date/time"
    (exams.available_after_datetime, Manual Exams only, default False —
    see migrations/20260830_normal_exam_available_after_datetime.sql).
    When True, the exam's configured date/time no longer expires
    STARTABILITY — an admin-set "ongoing" status is sufficient on its own,
    even long after the configured window has passed, so an admin can
    reuse the same exam by flipping its status back to Live without
    having to edit the date/time at all. When False (the default), the
    date/time window still expires it exactly as before. This flag is
    never read for a Scheduled Exam (which never calls this function —
    see is_official_window_open() below) and never loosens the attempt-
    count limit, which is enforced independently by the caller."""
    status = str(exam.get("status", "")).lower().strip()
    if status != "ongoing":
        return False
    if exam.get("available_after_datetime"):
        return True
    window = get_exam_time_window(exam)
    return not bool(window.get("has_ended"))


# ─────────────────────────────────────────────
# Scheduled Exam — effective status & window gates
#
# Query-time computed status (no scheduler/cron): every function below
# reads scheduled_mode first and, when it's false/absent, defers entirely
# to the pre-existing manual-exam logic (is_exam_window_open / the literal
# status column) — a Manual Exam's behaviour is byte-for-byte unchanged by
# any of this existing. The exams.status column keeps its normal meaning
# for manual exams; for a scheduled exam it only ever holds 'scheduled'
# (normal — effective status is computed here, never read from this
# column) or 'cancelled' (explicit admin override, set only by the
# dedicated cancel action — never by the general status selector, which
# the admin UI hides for scheduled exams).
# ─────────────────────────────────────────────

def is_prep_window_open(exam: Dict) -> bool:
    """Scheduled Exams only — may the student reach the instructions page
    and trigger resource preparation right now? Always False for manual
    exams (they have no preparation-window concept at all). Stays open
    through the live period too (not just the lead-in minutes) so a
    student arriving after official start without having pre-loaded can
    still preload on demand — it closes at official end, since there is
    nothing left to usefully prepare for once the exam window itself has
    closed."""
    if not exam.get("scheduled_mode"):
        return False
    if str(exam.get("status", "")).lower().strip() == "cancelled":
        return False
    window = get_exam_time_window(exam)
    prep_start, end_dt = window.get("prep_start_dt"), window.get("end_dt")
    if not prep_start or not end_dt:
        return False
    return prep_start <= window["now"] < end_dt


def is_official_window_open(exam: Dict) -> bool:
    """Single authoritative "may a FRESH attempt be created right now"
    check for BOTH exam modes — the one function app/routes/api/v01/
    exams.py:start_exam() and compute_exam_action_state() both call.

    Manual exams: delegates unchanged to is_exam_window_open() — this
    function existing changes nothing about manual-exam behaviour.

    Scheduled exams: purely time-driven — [scheduled_start, official_end)
    — never the status column (except the explicit 'cancelled' override),
    and never includes the completion buffer. The buffer is submission-
    acceptance time only (see is_submission_window_open()); it must never
    let a late student start a fresh attempt."""
    if not exam.get("scheduled_mode"):
        return is_exam_window_open(exam)
    if str(exam.get("status", "")).lower().strip() == "cancelled":
        return False
    window = get_exam_time_window(exam)
    start_dt, end_dt = window.get("start_dt"), window.get("end_dt")
    if not start_dt or not end_dt:
        return False
    return start_dt <= window["now"] < end_dt


def is_submission_window_open(exam: Dict) -> bool:
    """May a submission/answer-sync be ACCEPTED right now? Manual exams:
    always True — there is no exam-wide external deadline for a manual
    exam beyond each attempt's own personal timer (unchanged). Scheduled
    exams: open through official_end + completion_buffer — the buffer
    exists so a slightly-late submission (slow network, tab throttling)
    still lands and gets scored; it is never extra time for a student to
    keep answering (see get_effective_deadline(), which caps the timer
    itself at official_end with no buffer added)."""
    if not exam.get("scheduled_mode"):
        return True
    window = get_exam_time_window(exam)
    buffer_end = window.get("buffer_end_dt")
    if not buffer_end:
        return True
    return window["now"] < buffer_end


def get_effective_status(exam: Dict) -> str:
    """Single source of truth for an exam's displayed/bucketed status —
    used everywhere an exam is filtered/labelled (dashboard tabs, admin
    list, notifications). Manual exams: the literal status column,
    verbatim — the ONLY thing that may ever decide a manual exam's bucket,
    exactly as before this feature existed. Scheduled exams: computed
    purely from now vs. the schedule; the status column is never read for
    display (only for the 'cancelled' override). Only three values are
    ever returned for a live schedule — upcoming / ongoing / completed —
    matching the dashboard's three buckets; the "closing" period (past
    official end, still inside the buffer) reports as 'ongoing' since
    there is no fourth bucket and the exam is still legitimately accepting
    late submissions during it."""
    if not exam.get("scheduled_mode"):
        return str(exam.get("status", "draft")).lower().strip()
    if str(exam.get("status", "")).lower().strip() == "cancelled":
        return "cancelled"
    window = get_exam_time_window(exam)
    start_dt, buffer_end = window.get("start_dt"), window.get("buffer_end_dt")
    if not start_dt or not buffer_end:
        return "upcoming"
    now = window["now"]
    if now < start_dt:
        return "upcoming"
    if now < buffer_end:
        return "ongoing"
    return "completed"


def get_effective_deadline(exam: Dict, attempt_start):
    """The authoritative instant this attempt's timer must reach zero.
    Manual exams: attempt_start + duration, exactly as before — untouched.
    Scheduled exams: capped at the official end — a late-starting student
    never gains time by starting late (their deadline is still whatever's
    left of the official window) and never loses time versus a full-length
    attempt that started exactly on time. The completion buffer is never
    added here — it only affects whether a submission/sync is ACCEPTED
    (is_submission_window_open()), never how long the student's own clock
    runs."""
    from datetime import timedelta

    duration_min = safe_int(exam.get("duration"), 60) or 60
    personal_deadline = attempt_start + timedelta(minutes=duration_min)
    if not exam.get("scheduled_mode"):
        return personal_deadline
    window = get_exam_time_window(exam)
    official_end = window.get("end_dt")
    return min(personal_deadline, official_end) if official_end else personal_deadline


def compute_exam_action_state(user_id: int, exam: Dict) -> Dict:
    """Start/Resume/attempts-exhausted decision, extracted from the logic
    that used to be inlined in app/routes/web/exams.py:exam_instructions().

    SECURITY: can_start requires is_official_window_open(exam) — for a
    manual exam this is exactly the pre-existing is_exam_window_open()
    check (unchanged); for a scheduled exam it's the schedule-only check,
    so an exam that hasn't reached its scheduled start yet, or whose
    official window has already closed, never offers a Start button. This
    mirrors the backend gate in the /start API so the UI and the
    enforcement it depends on can never disagree."""
    from app.db.attempts import get_active_attempt, get_completed_attempts_count

    exam_id = int(exam["id"])
    active_attempt = get_active_attempt(user_id, exam_id)
    completed_count = get_completed_attempts_count(user_id, exam_id)
    max_attempts = safe_int(exam.get("max_attempts"), 0)

    if max_attempts > 0:
        attempts_left = max(max_attempts - completed_count, 0)
        attempts_exhausted = (attempts_left == 0)
        can_start = not attempts_exhausted
    else:
        attempts_left = None
        attempts_exhausted = False
        can_start = True

    is_scheduled = bool(exam.get("scheduled_mode"))
    window = get_exam_time_window(exam)
    has_started = bool(window.get("has_started"))
    has_ended = bool(window.get("has_ended"))
    prep_open = is_prep_window_open(exam) if is_scheduled else False

    if not active_attempt and not is_official_window_open(exam):
        can_start = False

    if active_attempt:
        can_start = False

    return {
        "active_attempt": active_attempt,
        "attempts_left": attempts_left,
        "max_attempts": max_attempts,
        "attempts_exhausted": attempts_exhausted,
        "can_start": can_start,
        "has_started": has_started,
        "has_ended": has_ended,
        "window": window,
        "is_scheduled": is_scheduled,
        "prep_open": prep_open,
        "effective_status": get_effective_status(exam),
    }


def _ampm(dt) -> str:
    """'22:00' -> '10:00 PM' (no leading zero on the hour)."""
    s = dt.strftime("%I:%M %p")
    return s[1:] if s.startswith("0") else s


def get_exam_time_window(exam: Dict) -> Dict:
    """Derives the exam's real start/end instants from date+start_time+
    duration — the only fields an exam actually stores (no dedicated
    end_time column). date/start_time are confirmed HTML5 date/time inputs
    stored verbatim as YYYY-MM-DD / HH:MM wall-clock strings in
    config.APP_TIMEZONE, safe to parse and attach that zone to directly.

    Returns absolute, timezone-aware instants (start_iso/end_iso) rather
    than a relative "seconds remaining" snapshot — the client computes and
    continuously re-derives the countdown from these against its own clock,
    so it stays correct across refreshes, popup reopens, or any caching,
    with nothing time-sensitive ever persisted or reused stale.

    has_started/has_ended reflect the exam's REAL scheduled window — not the
    admin-set `status` column (which this app never flips automatically) —
    so a student can correctly start an exam the instant its real start time
    arrives even if an admin hasn't manually relabelled it "ongoing" yet.

    For a Scheduled Exam (scheduled_mode truthy), also derives:
      prep_start_dt/prep_start_iso — scheduled_start - prep_window_minutes
      buffer_end_dt/buffer_end_iso — end_dt + completion_buffer_minutes
    Both are None for a manual exam or when the required minutes fields
    aren't set — every caller treats None as "that boundary doesn't apply".
    """
    from datetime import datetime as _dt, timedelta
    from app.utils.datetime_service import now_app_tz, app_timezone

    now = now_app_tz()

    try:
        naive_start = _dt.strptime(f"{exam.get('date')} {exam.get('start_time')}", "%Y-%m-%d %H:%M")
    except Exception:
        return {
            "now": now,
            "start_dt": None, "end_dt": None, "start_iso": None, "end_iso": None,
            "start_time_ampm": "", "end_time_ampm": "",
            "has_started": False, "has_ended": False,
            "prep_start_dt": None, "prep_start_iso": None,
            "buffer_end_dt": None, "buffer_end_iso": None,
        }

    start_dt = naive_start.replace(tzinfo=app_timezone())
    duration_min = safe_int(exam.get("duration"), 60) or 60
    end_dt = start_dt + timedelta(minutes=duration_min)

    end_time_ampm = _ampm(end_dt)
    if end_dt.date() != start_dt.date():
        end_time_ampm += " (+1d)"

    result = {
        "now": now,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "start_iso": start_dt.isoformat(),
        "end_iso": end_dt.isoformat(),
        "start_time_ampm": _ampm(start_dt),
        "end_time_ampm": end_time_ampm,
        "has_started": now >= start_dt,
        "has_ended": now >= end_dt,
        "prep_start_dt": None, "prep_start_iso": None,
        "buffer_end_dt": None, "buffer_end_iso": None,
    }

    if exam.get("scheduled_mode"):
        prep_min = safe_int(exam.get("prep_window_minutes"), 0) or 0
        buffer_min = safe_int(exam.get("completion_buffer_minutes"), 0) or 0
        prep_start_dt = start_dt - timedelta(minutes=prep_min)
        buffer_end_dt = end_dt + timedelta(minutes=buffer_min)
        result["prep_start_dt"] = prep_start_dt
        result["prep_start_iso"] = prep_start_dt.isoformat()
        result["buffer_end_dt"] = buffer_end_dt
        result["buffer_end_iso"] = buffer_end_dt.isoformat()

    return result


def _process_image(image_path: str) -> Tuple[bool, Optional[str]]:
    try:
        from app.services.image_storage_service import resolve_question_image_url
        return resolve_question_image_url(image_path)
    except Exception as e:
        log.warning("[exam_service] _process_image error: %s", e)
        return False, None


# ─────────────────────────────────────────────
# Scheduled Exam — manual-submission control + server-authoritative
# auto-finalization at the deadline
#
# Design summary (see migrations/20260830_scheduled_auto_submit.sql for the
# schema this relies on):
#   - exam_attempts.effective_deadline is computed ONCE, at attempt
#     creation, and stored — never recomputed on every check. This is what
#     lets the sweep below find due attempts with a single indexed WHERE
#     clause instead of loading every in-progress attempt into Python to
#     recompute each one's deadline.
#   - exam_attempts.answers_draft is the durable, server-side copy of the
#     student's in-progress answers, kept current by sync_exam_answers()
#     on every autosave — NOT just the Flask session, which a background
#     process (or a crashed/closed browser) can never reach.
#   - Finalization is a strict two-phase, idempotent lifecycle:
#       in_progress --(atomic claim, WHERE status='in_progress')--> finalizing
#       finalizing  --(score + persist results/responses)--> completed
#     The claim step is what makes concurrent finalizers safe: a manual
#     submit request and the background sweep racing for the same attempt
#     can never both win it — exactly one UPDATE ... WHERE status=
#     'in_progress' succeeds, the other gets 0 rows back and treats that
#     as "already being handled", never as an error.
# ─────────────────────────────────────────────

def is_manual_submission_allowed(exam: Dict) -> bool:
    """Whether a student may voluntarily end this exam before its
    effective deadline, right now. Manual Exams: always True — this
    setting only exists for Scheduled Exams and is never read for a
    Manual one (exams.allow_manual_submission defaults True for every
    existing/manual row, but the scheduled_mode check makes that default
    irrelevant for them regardless — this function never even looks at
    it unless scheduled_mode is set)."""
    if not exam.get("scheduled_mode"):
        return True
    return bool(exam.get("allow_manual_submission", True))


def compute_deadline_utc_naive(exam: Dict, attempt_start_utc_naive):
    """The exam_attempts.effective_deadline value to store for a freshly
    created attempt — get_effective_deadline() (above) already implements
    the real rule (manual: start+duration; scheduled: capped at the
    official end) but works in APP_TIMEZONE-aware datetimes, matching how
    the student-facing timer computes remaining_seconds; this converts
    that instant to the UTC-naive form every other timestamp column in
    this schema already uses (start_time/end_time/completed_at), so a
    plain UTC-naive `now` comparison is all any caller ever needs."""
    from datetime import timezone
    from app.utils.datetime_service import to_app_tz

    deadline_app_tz = get_effective_deadline(exam, to_app_tz(attempt_start_utc_naive))
    return deadline_app_tz.astimezone(timezone.utc).replace(tzinfo=None)


def finalize_exam_attempt(attempt_id: int) -> Tuple[bool, Optional[int], str]:
    """Score and persist the final result for ONE attempt, from its
    durable answers_draft — the single routine used by BOTH the student-
    initiated submit route and the background auto-submit sweep, so
    manual and automatic submission are scored identically.

    PRECONDITION: the caller has ALREADY atomically claimed this attempt
    (in_progress -> finalizing) via claim_attempt_for_finalization() or
    claim_due_attempts_batch() — this function never performs that
    transition itself, only reads a row it expects to already be
    'finalizing' and performs the final finalizing -> completed step once
    scoring/persistence has genuinely succeeded. This ordering is what
    guarantees an attempt is never observably "completed" before its
    responses are safely written.

    IDEMPOTENT: if a results row already exists for this attempt_id (a
    previous finalization run got as far as writing results/responses but
    crashed before the final status flip), this does not rescore or
    duplicate-insert — it just completes the status transition using the
    existing result. Safe to call again any number of times for the same
    already-claimed attempt.

    Returns (success, result_id_or_None, message).
    """
    from app.db.attempts import get_attempt_by_id, complete_attempt
    from app.db.results import create_result, create_responses_bulk, get_result_by_attempt_id
    from app.utils.datetime_service import now_utc_naive
    import json as _json

    attempt = get_attempt_by_id(attempt_id)
    if not attempt:
        return False, None, "Attempt not found"

    if attempt.get("status") == "completed":
        existing = get_result_by_attempt_id(attempt_id)
        return True, (int(existing["id"]) if existing else None), "Already completed"

    if attempt.get("status") != "finalizing":
        # Not our turn — either still genuinely in_progress (caller forgot
        # to claim it first) or another finalizer's claim is mid-flight.
        # Never score/persist from here; the caller is responsible for
        # having claimed it before calling this.
        return False, None, f"Attempt not claimed for finalization (status={attempt.get('status')})"

    existing = get_result_by_attempt_id(attempt_id)
    if existing:
        # A previous run already scored and persisted this attempt but
        # never reached the final status flip (e.g. crashed right after
        # create_responses_bulk) — recover by completing it now, without
        # touching the already-correct result/responses.
        complete_attempt(attempt_id)
        return True, int(existing["id"]), "Recovered from partial finalization"

    exam_id    = int(attempt["exam_id"])
    student_id = int(attempt["student_id"])
    exam = get_exam_by_id(exam_id)
    questions = get_questions_by_exam(exam_id)
    if not exam or not questions:
        return False, None, "Exam or questions missing"

    raw_draft = attempt.get("answers_draft")
    if isinstance(raw_draft, str):
        try:
            answers = _json.loads(raw_draft) or {}
        except Exception:
            answers = {}
    else:
        answers = raw_draft or {}

    total_q = len(questions)
    correct_ans = incorrect_ans = 0
    total_score = max_score = 0.0
    responses: List[Dict] = []
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
            "exam_id":        exam_id,
            "question_type":  qtype,
            "given_answer":   _json.dumps(given) if isinstance(given, list) else str(given or ""),
            "correct_answer": _json.dumps(corr) if isinstance(corr, list) else str(corr or ""),
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

    now = now_utc_naive()
    try:
        from datetime import datetime as _dt
        start_raw = attempt["start_time"]
        start_dt = start_raw if hasattr(start_raw, "year") else _dt.fromisoformat(
            str(start_raw).replace("Z", "").replace("+00:00", "")
        )
        time_taken = max(0, int((now - start_dt).total_seconds() / 60))
    except Exception:
        time_taken = 0

    created = create_result({
        "student_id":           student_id,
        "exam_id":              exam_id,
        "attempt_id":           attempt_id,
        "score":                int(round(total_score)),
        "max_score":            int(round(max_score)),
        "percentage":           round(percentage, 2),
        "grade":                grade,
        "completed_at":         now.strftime("%Y-%m-%d %H:%M:%S"),
        "time_taken_minutes":   time_taken,
        "correct_answers":      correct_ans,
        "incorrect_answers":    incorrect_ans,
        "unanswered_questions": unanswered,
        "total_questions":      total_q,
    })
    if not created:
        # Could be the unique-index backstop firing because another
        # finalizer won a genuine simultaneous race — re-check rather than
        # surfacing a hard failure; if a result exists now, this is the
        # same "recover and complete" path as above.
        existing = get_result_by_attempt_id(attempt_id)
        if existing:
            complete_attempt(attempt_id)
            return True, int(existing["id"]), "Recovered after insert conflict"
        return False, None, "Failed to save result"

    result_id = int(created["id"])
    for r in responses:
        r["result_id"] = result_id
    create_responses_bulk(responses)

    complete_attempt(attempt_id)
    log.info("[exam_service] Finalized attempt_id=%s result_id=%s student=%s exam=%s",
             attempt_id, result_id, student_id, exam_id)
    return True, result_id, "Finalized"