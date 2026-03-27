"""
app/db/ai.py
Supabase queries for ai_chat_history and ai_usage_tracking tables.
"""

from typing import Optional, List, Dict
from datetime import datetime
from app.db import supabase


# ─────────────────────────────────────────────
# Chat History
# ─────────────────────────────────────────────

def get_chat_history(user_id: int, limit: int = 50) -> List[Dict]:
    try:
        res = (
            supabase.table("ai_chat_history")
            .select("id,user_id,message,is_user,timestamp")
            .eq("user_id", user_id)
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.ai] get_chat_history error: {e}")
        return []


def save_chat_message(user_id: int, message: str, is_user: bool) -> bool:
    try:
        supabase.table("ai_chat_history").insert(
            {
                "user_id": user_id,
                "message": message,
                "is_user": is_user,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ).execute()
        return True
    except Exception as e:
        print(f"[db.ai] save_chat_message error: {e}")
        return False


def delete_user_chat_history(user_id: int) -> bool:
    try:
        supabase.table("ai_chat_history").delete().eq("user_id", user_id).execute()
        return True
    except Exception as e:
        print(f"[db.ai] delete_user_chat_history error: {e}")
        return False


# ─────────────────────────────────────────────
# Usage Tracking
# ─────────────────────────────────────────────

def get_today_usage(user_id: int) -> Optional[Dict]:
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        res = (
            supabase.table("ai_usage_tracking")
            .select("id,user_id,date,questions_used")
            .eq("user_id", user_id)
            .eq("date", today)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.ai] get_today_usage error: {e}")
        return None


def increment_usage(user_id: int) -> bool:
    """Upsert today's usage count — single round-trip."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        existing = (
            supabase.table("ai_usage_tracking")
            .select("id,questions_used")
            .eq("user_id", user_id)
            .eq("date", today)
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            supabase.table("ai_usage_tracking").update(
                {"questions_used": int(row.get("questions_used", 0)) + 1}
            ).eq("id", row["id"]).execute()
        else:
            supabase.table("ai_usage_tracking").insert(
                {"user_id": user_id, "date": today, "questions_used": 1}
            ).execute()

        return True
    except Exception as e:
        print(f"[db.ai] increment_usage error: {e}")
        return False