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
    Load exam + questions from Supabase, process images,
    and cache the result in the Flask session.
    Returns (success, message).
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
            "correct_answer": q.get("correct_answer", ""),
            "positive_marks": q.get("positive_marks", 1),
            "negative_marks": q.get("negative_marks", 0),
            "image_path":     q.get("image_path", ""),
            "has_image":      False,
            "image_url":      None,
        }

        image_path = q.get("image_path", "")
        if image_path and str(image_path).strip() not in ("", "nan", "None"):
            has_img, img_url = _process_image(image_path)
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

def compute_exam_action_state(user_id: int, exam: Dict) -> Dict:
    """Start/Resume/attempts-exhausted decision, extracted from the logic
    that used to be inlined in app/routes/web/exams.py:exam_instructions()."""
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

    if active_attempt:
        can_start = False

    return {
        "active_attempt": active_attempt,
        "attempts_left": attempts_left,
        "max_attempts": max_attempts,
        "attempts_exhausted": attempts_exhausted,
        "can_start": can_start,
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
    """
    from datetime import datetime as _dt, timedelta
    from app.utils.datetime_service import now_app_tz, app_timezone

    try:
        naive_start = _dt.strptime(f"{exam.get('date')} {exam.get('start_time')}", "%Y-%m-%d %H:%M")
    except Exception:
        return {
            "start_dt": None, "start_iso": None, "end_iso": None,
            "start_time_ampm": "", "end_time_ampm": "",
            "has_started": False, "has_ended": False,
        }

    start_dt = naive_start.replace(tzinfo=app_timezone())
    duration_min = safe_int(exam.get("duration"), 60) or 60
    end_dt = start_dt + timedelta(minutes=duration_min)

    end_time_ampm = _ampm(end_dt)
    if end_dt.date() != start_dt.date():
        end_time_ampm += " (+1d)"

    now = now_app_tz()

    return {
        "start_dt": start_dt,
        "start_iso": start_dt.isoformat(),
        "end_iso": end_dt.isoformat(),
        "start_time_ampm": _ampm(start_dt),
        "end_time_ampm": end_time_ampm,
        "has_started": now >= start_dt,
        "has_ended": now >= end_dt,
    }


def _process_image(image_path: str) -> Tuple[bool, Optional[str]]:
    try:
        from app.services.image_storage_service import resolve_question_image_url
        return resolve_question_image_url(image_path)
    except Exception as e:
        log.warning("[exam_service] _process_image error: %s", e)
        return False, None