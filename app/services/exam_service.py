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

import config
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
      1. Flask session key  — exam_data_{exam_id}  (server-side session file
         or Redis session store depending on SESSION_TYPE).
      2. App-level Redis/in-memory cache — keyed by exam_id so it is shared
         across workers. We do NOT clear this layer here because it holds
         static exam+question data that is safe to reuse across attempts and
         users.  Only the per-session answer/timer state needs purging.

    The function is intentionally narrow: it only removes the preloaded data
    block from the Flask session, which is the cache layer that caused the
    bug.  Question data in Redis is read-only and does not carry attempt state.
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


def _process_image(image_path: str) -> Tuple[bool, Optional[str]]:
    try:
        from app.services.drive_service import get_image_url
        return get_image_url(image_path)
    except Exception as e:
        log.warning("[exam_service] _process_image error: %s", e)
        return False, None