"""
app/db/explanation.py
PostgreSQL queries for AI Explanation feature.

Tables:
  ai_explanation_usage   — tracks daily rate limits (user+question+date)
  ai_explanation_history — persists all generated explanations permanently

Public API:
  get_explanation_usage(user_id, question_id)        -> dict | None
  get_daily_total_usage(user_id)                     -> int
  increment_explanation_usage(user_id, question_id)  -> bool
  save_explanation(user_id, question_id, text)        -> dict | None   (any length; retried; heals the old index)
  get_explanation_history(user_id, question_id)      -> list[dict]
  delete_explanation(explanation_id, user_id)        -> bool
"""

import logging
import threading
import time
from typing import Optional, Dict, List

from app.db import fetch_one, fetch_all, execute, insert_returning
from app.utils.datetime_service import today_app_date, daily_reset_message as get_reset_time_str


# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit queries  (ai_explanation_usage)
# ─────────────────────────────────────────────────────────────────────────────

def get_explanation_usage(user_id: int, question_id: int) -> Optional[Dict]:
    """
    Return today's usage row for (user_id, question_id), or None.
    Shape: {id, user_id, question_id, date, used_count}
    """
    try:
        return fetch_one(
            "SELECT id, user_id, question_id, date, used_count FROM ai_explanation_usage "
            "WHERE user_id=%s AND question_id=%s AND date=%s",
            (user_id, question_id, today_app_date()),
        )
    except Exception as e:
        print(f"[db.explanation] get_explanation_usage error: {e}")
        return None


def get_daily_total_usage(user_id: int) -> int:
    """Sum of used_count for this user across all questions today."""
    try:
        rows = fetch_all(
            "SELECT used_count FROM ai_explanation_usage WHERE user_id=%s AND date=%s",
            (user_id, today_app_date()),
        )
        return sum(int(row.get("used_count", 0)) for row in rows)
    except Exception as e:
        print(f"[db.explanation] get_daily_total_usage error: {e}")
        return 0


def increment_explanation_usage(user_id: int, question_id: int) -> bool:
    """
    Upsert today's usage row and increment used_count by 1.
    Two round-trips: fetch existing row, then insert or update.
    UNIQUE constraint prevents duplicates under concurrent requests.
    """
    try:
        today    = today_app_date()
        existing = fetch_one(
            "SELECT id, used_count FROM ai_explanation_usage WHERE user_id=%s AND question_id=%s AND date=%s",
            (user_id, question_id, today),
        )

        if existing:
            execute(
                "UPDATE ai_explanation_usage SET used_count=%s WHERE id=%s",
                (int(existing.get("used_count", 0)) + 1, existing["id"]),
            )
        else:
            insert_returning("ai_explanation_usage", {
                "user_id": user_id, "question_id": question_id, "date": today, "used_count": 1,
            })

        return True
    except Exception as e:
        print(f"[db.explanation] increment_explanation_usage error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# History queries  (ai_explanation_history)
# ─────────────────────────────────────────────────────────────────────────────

# The explanation text is written by an AI model, so its length is not ours to limit. An index that copied the text into
# itself (the old idx_expl_hist_covering ... INCLUDE (explanation)) makes PostgreSQL refuse any explanation above ~2.7 KB
# ("index row size N exceeds btree version 4 maximum 2704"; SQLSTATE 54000). migrations/20260922_explanation_history_index.sql
# replaces it with an index on the lookup columns only; if the database has not had that applied yet, the first save that
# hits the error applies it here and goes on, so a student never loses an explanation to it.
_log = logging.getLogger("smartaiexam.db")
_OLD_INDEX = "idx_expl_hist_covering"
_NEW_INDEX = "idx_expl_hist_user_question"
_TABLE = "ai_explanation_history"
_repair_lock = threading.Lock()


def _index_row_too_large(exc: BaseException) -> bool:
    return getattr(exc, "pgcode", None) == "54000" or "index row size" in str(exc)


def repair_history_index() -> bool:
    """Replace the old covering index by the lean one (the same two statements as the migration; idempotent).
    True when the database is in the wanted state afterwards."""
    with _repair_lock:
        try:
            execute(f"DROP INDEX IF EXISTS public.{_OLD_INDEX}")
            execute(f"CREATE INDEX IF NOT EXISTS {_NEW_INDEX} ON public.{_TABLE} USING btree (user_id, question_id, generated_at DESC)")
            _log.warning("ai_explanation_history: replaced %s by %s (it copied the whole explanation into the index)", _OLD_INDEX, _NEW_INDEX)
            return True
        except Exception as e:
            _log.warning("ai_explanation_history: could not replace %s: %s", _OLD_INDEX, " ".join(str(e).split())[:160])
            return False


def save_explanation(user_id: int, question_id: int, explanation_text: str) -> Optional[Dict]:
    """
    Persist a generated explanation permanently, whatever its length.
    Returns the inserted row dict, or None if it could not be saved (the caller still shows it to the student).
    A failed insert is tried once more; the index-size error additionally repairs the index first.
    """
    healed = False
    for attempt in (1, 2):
        try:
            return insert_returning("ai_explanation_history", {
                "user_id":     user_id,
                "question_id": question_id,
                "explanation": explanation_text,
            })
        except Exception as e:
            if _index_row_too_large(e) and not healed:
                healed = True
                if repair_history_index():
                    continue                                   # the very next attempt goes through
            if attempt == 1:
                time.sleep(0.3)                                # a momentary database hiccup: once more
                continue
            _log.warning("save_explanation failed (%d chars): %s", len(explanation_text or ""), " ".join(str(e).split())[:200])
    return None


def get_explanation_history(user_id: int, question_id: int) -> List[Dict]:
    """
    Return all saved explanations for (user_id, question_id),
    ordered oldest-first so the UI can show them in generation order.

    Each item: {id, explanation, generated_at}
    """
    try:
        return fetch_all(
            "SELECT id, explanation, generated_at FROM ai_explanation_history "
            "WHERE user_id=%s AND question_id=%s ORDER BY generated_at ASC",
            (user_id, question_id),
        )
    except Exception as e:
        print(f"[db.explanation] get_explanation_history error: {e}")
        return []


def delete_explanation(explanation_id, user_id: int) -> bool:
    """
    Permanently delete ONE explanation row, scoped to its primary key
    (`id`) AND the owning user_id — never by question_id/user_id alone,
    since a user can have multiple explanations for the same question
    (a per-question daily limit applies) and question_id is not
    unique per row. The user_id filter is what prevents one student from
    being able to delete another student's explanation by guessing/
    tampering with an id — the WHERE clause only deletes rows matching
    BOTH conditions, so a mismatched id/user_id pair deletes nothing.

    Returns True only if a row was actually deleted.
    """
    try:
        return execute(
            "DELETE FROM ai_explanation_history WHERE id=%s AND user_id=%s",
            (explanation_id, user_id),
        ) > 0
    except Exception as e:
        print(f"[db.explanation] delete_explanation error: {e}")
        return False
