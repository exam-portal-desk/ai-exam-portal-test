"""
app/db/attempts.py
All Supabase queries for the `exam_attempts` table.
"""

from typing import Optional, List, Dict
from datetime import datetime
from app.db import supabase


_COLS = "id,student_id,exam_id,attempt_number,status,start_time,end_time"


def get_active_attempt(user_id: int, exam_id: int) -> Optional[Dict]:
    """Return the most recent in_progress attempt for this user+exam."""
    try:
        res = (
            supabase.table("exam_attempts")
            .select(_COLS)
            .eq("student_id", user_id)
            .eq("exam_id", exam_id)
            .eq("status", "in_progress")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.attempts] get_active_attempt error: {e}")
        return None


def get_latest_attempt(user_id: int, exam_id: int) -> Optional[Dict]:
    """Return the most recent attempt regardless of status."""
    try:
        res = (
            supabase.table("exam_attempts")
            .select(_COLS)
            .eq("student_id", user_id)
            .eq("exam_id", exam_id)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.attempts] get_latest_attempt error: {e}")
        return None


def get_completed_attempts_count(user_id: int, exam_id: int) -> int:
    try:
        res = (
            supabase.table("exam_attempts")
            .select("id", count="exact")
            .eq("student_id", user_id)
            .eq("exam_id", exam_id)
            .eq("status", "completed")
            .execute()
        )
        return res.count or 0
    except Exception as e:
        print(f"[db.attempts] get_completed_attempts_count error: {e}")
        return 0


def get_all_attempts_for_exam(user_id: int, exam_id: int) -> List[Dict]:
    """All attempts (any status) for next attempt_number calculation."""
    try:
        res = (
            supabase.table("exam_attempts")
            .select("id,attempt_number")
            .eq("student_id", user_id)
            .eq("exam_id", exam_id)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.attempts] get_all_attempts_for_exam error: {e}")
        return []


def create_exam_attempt(attempt_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("exam_attempts").insert(attempt_data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.attempts] create_exam_attempt error: {e}")
        return None


def update_exam_attempt(attempt_id: int, updates: Dict) -> bool:
    try:
        supabase.table("exam_attempts").update(updates).eq("id", attempt_id).execute()
        return True
    except Exception as e:
        print(f"[db.attempts] update_exam_attempt error: {e}")
        return False


def get_attempts_summary() -> List[Dict]:
    """
    Return all attempts with student+exam IDs for the admin attempts page.
    Replaces the O(users × exams) Python loop in admin.py.
    """
    try:
        res = (
            supabase.table("exam_attempts")
            .select("student_id,exam_id,status")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.attempts] get_attempts_summary error: {e}")
        return []