"""
app/db/ai.py
PostgreSQL queries for ai_chat_history and ai_usage_tracking tables.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, insert_returning
from app.utils.datetime_service import now_utc_naive, today_app_date


# ─────────────────────────────────────────────
# Chat History
# ─────────────────────────────────────────────

def get_chat_history(user_id: int, limit: int = 50) -> List[Dict]:
    try:
        return fetch_all(
            "SELECT id,user_id,message,is_user,timestamp FROM ai_chat_history "
            "WHERE user_id=%s ORDER BY timestamp DESC LIMIT %s",
            (user_id, limit),
        )
    except Exception as e:
        print(f"[db.ai] get_chat_history error: {e}")
        return []


def save_chat_message(user_id: int, message: str, is_user: bool) -> bool:
    try:
        insert_returning("ai_chat_history", {
            "user_id": user_id,
            "message": message,
            "is_user": is_user,
            "timestamp": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return True
    except Exception as e:
        print(f"[db.ai] save_chat_message error: {e}")
        return False


def delete_user_chat_history(user_id: int) -> bool:
    try:
        execute("DELETE FROM ai_chat_history WHERE user_id=%s", (user_id,))
        return True
    except Exception as e:
        print(f"[db.ai] delete_user_chat_history error: {e}")
        return False


# ─────────────────────────────────────────────
# Usage Tracking
# ─────────────────────────────────────────────

def get_today_usage(user_id: int) -> Optional[Dict]:
    try:
        today = today_app_date()
        return fetch_one(
            "SELECT id,user_id,date,questions_used FROM ai_usage_tracking WHERE user_id=%s AND date=%s",
            (user_id, today),
        )
    except Exception as e:
        print(f"[db.ai] get_today_usage error: {e}")
        return None


def increment_usage(user_id: int) -> bool:
    """Upsert today's usage count — single round-trip."""
    try:
        today = today_app_date()
        existing = fetch_one(
            "SELECT id,questions_used FROM ai_usage_tracking WHERE user_id=%s AND date=%s",
            (user_id, today),
        )

        if existing:
            execute(
                "UPDATE ai_usage_tracking SET questions_used=%s WHERE id=%s",
                (int(existing.get("questions_used", 0)) + 1, existing["id"]),
            )
        else:
            insert_returning("ai_usage_tracking", {"user_id": user_id, "date": today, "questions_used": 1})

        return True
    except Exception as e:
        print(f"[db.ai] increment_usage error: {e}")
        return False
