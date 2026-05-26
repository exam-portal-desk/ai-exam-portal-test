"""
app/db/explanation.py
Supabase queries for AI Explanation feature.

Tables:
  ai_explanation_usage   — tracks daily rate limits (user+question+date)
  ai_explanation_history — persists all generated explanations permanently

Public API:
  get_explanation_usage(user_id, question_id)        -> dict | None
  get_daily_total_usage(user_id)                     -> int
  increment_explanation_usage(user_id, question_id)  -> bool
  save_explanation(user_id, question_id, text)        -> dict | None
  get_explanation_history(user_id, question_id)      -> list[dict]
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

from app.db import supabase


# ─────────────────────────────────────────────────────────────────────────────
# IST timezone helper  (UTC+5:30)
# ─────────────────────────────────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> str:
    """Return today's date in IST as 'YYYY-MM-DD'."""
    return datetime.now(_IST).strftime("%Y-%m-%d")


def get_reset_time_str() -> str:
    """
    Return a human-readable string for when the daily limit resets.
    The limit resets at midnight IST (00:00 IST = 18:30 UTC previous day).

    Example outputs:
        "resets today at 12:00 AM IST"
        "resets tomorrow at 12:00 AM IST"
    """
    now_ist      = datetime.now(_IST)
    midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    delta        = midnight_ist - now_ist

    hours   = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)

    if hours >= 20:
        when = "tomorrow"
    elif hours == 0:
        when = f"in {minutes} min"
    else:
        when = f"in {hours}h {minutes}m"

    return f"Resets {when} at 12:00 AM IST"


# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit queries  (ai_explanation_usage)
# ─────────────────────────────────────────────────────────────────────────────

def get_explanation_usage(user_id: int, question_id: int) -> Optional[Dict]:
    """
    Return today's (IST) usage row for (user_id, question_id), or None.
    Shape: {id, user_id, question_id, date, used_count}
    """
    try:
        res = (
            supabase.table("ai_explanation_usage")
            .select("id, user_id, question_id, date, used_count")
            .eq("user_id", user_id)
            .eq("question_id", question_id)
            .eq("date", _today_ist())
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.explanation] get_explanation_usage error: {e}")
        return None


def get_daily_total_usage(user_id: int) -> int:
    """Sum of used_count for this user across all questions today (IST)."""
    try:
        res = (
            supabase.table("ai_explanation_usage")
            .select("used_count")
            .eq("user_id", user_id)
            .eq("date", _today_ist())
            .execute()
        )
        if not res.data:
            return 0
        return sum(int(row.get("used_count", 0)) for row in res.data)
    except Exception as e:
        print(f"[db.explanation] get_daily_total_usage error: {e}")
        return 0


def increment_explanation_usage(user_id: int, question_id: int) -> bool:
    """
    Upsert today's (IST) usage row and increment used_count by 1.
    Two round-trips: fetch existing row, then insert or update.
    UNIQUE constraint prevents duplicates under concurrent requests.
    """
    try:
        today    = _today_ist()
        existing = (
            supabase.table("ai_explanation_usage")
            .select("id, used_count")
            .eq("user_id", user_id)
            .eq("question_id", question_id)
            .eq("date", today)
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            supabase.table("ai_explanation_usage").update(
                {"used_count": int(row.get("used_count", 0)) + 1}
            ).eq("id", row["id"]).execute()
        else:
            supabase.table("ai_explanation_usage").insert(
                {"user_id": user_id, "question_id": question_id,
                 "date": today, "used_count": 1}
            ).execute()

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
        res = (
            supabase.table("ai_explanation_history")
            .insert({
                "user_id":     user_id,
                "question_id": question_id,
                "explanation": explanation_text,
            })
            .execute()
        )
        return res.data[0] if res.data else None
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
        res = (
            supabase.table("ai_explanation_history")
            .select("id, explanation, generated_at")
            .eq("user_id", user_id)
            .eq("question_id", question_id)
            .order("generated_at", desc=False)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.explanation] get_explanation_history error: {e}")
        return []
