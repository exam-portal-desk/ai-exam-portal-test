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
  save_explanation(user_id, question_id, text)        -> dict | None
  get_explanation_history(user_id, question_id)      -> list[dict]
  delete_explanation(explanation_id, user_id)        -> bool
"""

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

def save_explanation(user_id: int, question_id: int, explanation_text: str) -> Optional[Dict]:
    """
    Persist a generated explanation permanently.
    Returns the inserted row dict, or None on failure.
    """
    try:
        return insert_returning("ai_explanation_history", {
            "user_id":     user_id,
            "question_id": question_id,
            "explanation": explanation_text,
        })
    except Exception as e:
        print(f"[db.explanation] save_explanation error: {e}")
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
    (up to EXPLANATION_PER_QUESTION_LIMIT/day) and question_id is not
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
