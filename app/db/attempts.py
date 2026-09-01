"""
app/db/attempts.py
All PostgreSQL queries for the `exam_attempts` table.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, execute_returning, set_clause, insert_returning


_COLS = "id,student_id,exam_id,attempt_number,status,start_time,end_time"
# Adds the auto-submit-relevant columns — kept as a separate constant
# (rather than folded into _COLS) so every existing _COLS caller's row
# shape is completely unchanged; only the new finalization code paths
# below need answers_draft/effective_deadline/finalization_claimed_at.
_FULL_COLS = _COLS + ",answers_draft,effective_deadline,finalization_claimed_at"


def get_attempts_status_counts() -> Dict[str, int]:
    """Attempt count per status (completed / in_progress / ...) — one
    aggregate query, for the admin dashboard's Attempts chart."""
    try:
        rows = fetch_all("SELECT status, COUNT(*) AS count FROM exam_attempts GROUP BY status")
        return {r["status"]: r["count"] for r in rows}
    except Exception as e:
        print(f"[db.attempts] get_attempts_status_counts error: {e}")
        return {}


def get_top_attempted_exams(limit: int = 5) -> List[Dict]:
    """Most-attempted exams (top N by attempt count) — one aggregate JOIN
    query, for the dashboard's Most Attempted Exams chart."""
    try:
        return fetch_all(
            "SELECT ex.name AS name, COUNT(a.id) AS count FROM exam_attempts a "
            "JOIN exams ex ON ex.id = a.exam_id "
            "GROUP BY ex.id, ex.name ORDER BY count DESC LIMIT %s",
            (limit,),
        )
    except Exception as e:
        print(f"[db.attempts] get_top_attempted_exams error: {e}")
        return []


def get_active_attempt(user_id: int, exam_id: int) -> Optional[Dict]:
    """Return the most recent in_progress attempt for this user+exam."""
    try:
        return fetch_one(
            f"SELECT {_COLS} FROM exam_attempts WHERE student_id=%s AND exam_id=%s AND status=%s "
            "ORDER BY id DESC LIMIT 1",
            (user_id, exam_id, "in_progress"),
        )
    except Exception as e:
        print(f"[db.attempts] get_active_attempt error: {e}")
        return None


def get_latest_attempt(user_id: int, exam_id: int) -> Optional[Dict]:
    """Return the most recent attempt regardless of status."""
    try:
        return fetch_one(
            f"SELECT {_COLS} FROM exam_attempts WHERE student_id=%s AND exam_id=%s ORDER BY id DESC LIMIT 1",
            (user_id, exam_id),
        )
    except Exception as e:
        print(f"[db.attempts] get_latest_attempt error: {e}")
        return None


def get_exam_attempts_count(exam_id: int) -> int:
    """Total attempts (any student, any status) ever made on this exam —
    used to block unsafe changes (e.g. flipping Manual/Scheduled mode, or
    rescheduling a Scheduled Exam's timing) once real attempts already
    exist against it."""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM exam_attempts WHERE exam_id=%s", (exam_id,))
        return row["count"] if row else 0
    except Exception as e:
        print(f"[db.attempts] get_exam_attempts_count error: {e}")
        return 0


def get_completed_attempts_count(user_id: int, exam_id: int) -> int:
    try:
        row = fetch_one(
            "SELECT COUNT(*) AS count FROM exam_attempts WHERE student_id=%s AND exam_id=%s AND status=%s",
            (user_id, exam_id, "completed"),
        )
        return row["count"] if row else 0
    except Exception as e:
        print(f"[db.attempts] get_completed_attempts_count error: {e}")
        return 0


def get_all_attempts_for_exam(user_id: int, exam_id: int) -> List[Dict]:
    """All attempts (any status) for next attempt_number calculation."""
    try:
        return fetch_all(
            "SELECT id,attempt_number FROM exam_attempts WHERE student_id=%s AND exam_id=%s",
            (user_id, exam_id),
        )
    except Exception as e:
        print(f"[db.attempts] get_all_attempts_for_exam error: {e}")
        return []


def create_exam_attempt(attempt_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("exam_attempts", attempt_data)
    except Exception as e:
        print(f"[db.attempts] create_exam_attempt error: {e}")
        return None


def update_exam_attempt(attempt_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE exam_attempts SET {sc} WHERE id=%s", params + [attempt_id])
        return True
    except Exception as e:
        print(f"[db.attempts] update_exam_attempt error: {e}")
        return False


def get_attempts_summary() -> List[Dict]:
    """
    Return all attempts with student+exam IDs for the admin attempts page.
    """
    try:
        return fetch_all("SELECT student_id,exam_id,status FROM exam_attempts")
    except Exception as e:
        print(f"[db.attempts] get_attempts_summary error: {e}")
        return []


# ─────────────────────────────────────────────
# Auto-submit / finalization (Scheduled Exam manual-submission-control
# feature) — every write below is a single, targeted, conditional UPDATE
# (never a bare unconditional one) so that whichever caller reaches a
# given attempt first — a student's manual submit, the background sweep,
# a retried/duplicate request — is the only one that actually acts; every
# loser sees 0 rows affected and treats that as "already handled" rather
# than erroring. See app/services/exam_service.py:finalize_exam_attempt()
# for the full claim -> score -> complete lifecycle this supports.
# ─────────────────────────────────────────────

def get_attempt_by_id(attempt_id: int) -> Optional[Dict]:
    """Full row (including answers_draft/effective_deadline) for a single
    attempt — the authoritative fetch used before finalizing, never trust
    a session-cached copy for anything status/deadline-related."""
    try:
        return fetch_one(f"SELECT {_FULL_COLS} FROM exam_attempts WHERE id=%s", (attempt_id,))
    except Exception as e:
        print(f"[db.attempts] get_attempt_by_id error: {e}")
        return None


def update_attempt_answers_draft(attempt_id: int, answers: Dict) -> bool:
    """Durably persist the student's current answers for this attempt —
    called on every autosave during the exam (see sync_exam_answers()),
    so a server-side process (or the server re-scoring after a browser
    crash) always has a real, recent answer set to work from instead of
    only whatever last reached the Flask session.

    Conditioned on status='in_progress': if this attempt has already been
    claimed for finalization (or completed) by the time this write lands
    — the race the caller must expect whenever a sync arrives right at
    the deadline — the write is silently dropped rather than corrupting
    an attempt that's already being scored. Returns whether the write
    actually landed, purely informational for the caller/logs."""
    try:
        # Plain dicts adapt to JSON automatically — see app/db/__init__.py's
        # psycopg2.extensions.register_adapter(dict, Json), already relied
        # on elsewhere (e.g. Notes' jsonb columns) — no explicit Json()
        # wrapper needed here.
        return execute(
            "UPDATE exam_attempts SET answers_draft=%s WHERE id=%s AND status='in_progress'",
            (answers or {}, attempt_id),
        ) > 0
    except Exception as e:
        print(f"[db.attempts] update_attempt_answers_draft error: {e}")
        return False


def claim_attempt_for_finalization(attempt_id: int) -> Optional[Dict]:
    """Atomically transition ONE specific attempt in_progress -> finalizing.
    Used by the student-initiated submit route. Returns the claimed row
    (full columns) on success, or None if it was already claimed/completed
    by someone else (the background sweep, a duplicate request) — the
    caller must treat None as "not an error, just already being/been
    handled", never retry-insert."""
    try:
        rows = execute_returning(
            f"UPDATE exam_attempts SET status='finalizing', finalization_claimed_at=(NOW() AT TIME ZONE 'UTC') "
            f"WHERE id=%s AND status='in_progress' RETURNING {_FULL_COLS}",
            (attempt_id,),
        )
        return rows[0] if rows else None
    except Exception as e:
        print(f"[db.attempts] claim_attempt_for_finalization error: {e}")
        return None


def claim_due_attempts_batch(batch_size: int, stale_minutes: int = 2) -> List[Dict]:
    """The auto-submit sweep's single claiming query — atomically flips up
    to `batch_size` overdue attempts from in_progress to finalizing (or
    re-claims ones stuck in finalizing for longer than `stale_minutes`,
    e.g. a worker that crashed mid-score) and returns exactly the rows it
    claimed. FOR UPDATE SKIP LOCKED means a second concurrent sweep
    (another process/worker) simply skips whatever this call already has
    row-locked, instead of blocking on it or double-claiming it — the two
    can run at the same time and never contend for the same attempt.
    Bounded by batch_size so one sweep tick is always a small, fast,
    short-lived transaction — never "every due attempt at once" — callers
    loop calling this repeatedly when a full batch keeps coming back, to
    drain a mass-deadline moment without any single call growing large."""
    try:
        return execute_returning(
            f"""
            UPDATE exam_attempts
            SET status='finalizing', finalization_claimed_at=(NOW() AT TIME ZONE 'UTC')
            WHERE id IN (
                SELECT id FROM exam_attempts
                WHERE (
                    (status='in_progress' AND effective_deadline IS NOT NULL
                     AND effective_deadline <= (NOW() AT TIME ZONE 'UTC'))
                    OR
                    (status='finalizing' AND finalization_claimed_at IS NOT NULL
                     AND finalization_claimed_at < (NOW() AT TIME ZONE 'UTC') - (%s || ' minutes')::interval)
                )
                ORDER BY effective_deadline ASC NULLS LAST
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING {_FULL_COLS}
            """,
            (stale_minutes, batch_size),
        )
    except Exception as e:
        print(f"[db.attempts] claim_due_attempts_batch error: {e}")
        return []


def complete_attempt(attempt_id: int) -> bool:
    """Final finalizing -> completed transition, once scoring/results/
    responses have already been safely persisted — never called before
    that, so an attempt can never be observed as 'completed' with no
    corresponding result row. Conditioned on status='finalizing' so it's
    a no-op (not an error) if something else already completed it."""
    try:
        return execute(
            "UPDATE exam_attempts SET status='completed', end_time=(NOW() AT TIME ZONE 'UTC') "
            "WHERE id=%s AND status='finalizing'",
            (attempt_id,),
        ) > 0
    except Exception as e:
        print(f"[db.attempts] complete_attempt error: {e}")
        return False
