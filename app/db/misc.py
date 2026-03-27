"""
app/db/misc.py
Supabase queries for subjects and requests_raised tables.
"""

from typing import Optional, List, Dict
from datetime import datetime
from app.db import supabase


# ─────────────────────────────────────────────
# Subjects
# ─────────────────────────────────────────────

def get_all_subjects() -> List[Dict]:
    try:
        res = (
            supabase.table("subjects")
            .select("id,subject_name,subject_folder_id,subject_folder_created_at")
            .order("subject_name")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.misc] get_all_subjects error: {e}")
        return []


def get_subject_by_name(name: str) -> Optional[Dict]:
    try:
        res = supabase.table("subjects").select("*").eq("subject_name", name).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.misc] get_subject_by_name error: {e}")
        return None


def get_subject_folder_id_by_name(name: str) -> Optional[str]:
    """Case-insensitive subject name → folder_id lookup. Used for image loading."""
    try:
        res = supabase.table("subjects").select("subject_name,subject_folder_id").execute()
        for row in res.data or []:
            if str(row.get("subject_name", "")).strip().lower() == name.strip().lower():
                return str(row.get("subject_folder_id", "")).strip() or None
        return None
    except Exception as e:
        print(f"[db.misc] get_subject_folder_id_by_name error: {e}")
        return None


def create_subject(subject_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("subjects").insert(subject_data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.misc] create_subject error: {e}")
        return None


def update_subject(subject_id: int, updates: Dict) -> bool:
    try:
        supabase.table("subjects").update(updates).eq("id", subject_id).execute()
        return True
    except Exception as e:
        print(f"[db.misc] update_subject error: {e}")
        return False


def delete_subject(subject_id: int) -> bool:
    try:
        supabase.table("subjects").delete().eq("id", subject_id).execute()
        return True
    except Exception as e:
        print(f"[db.misc] delete_subject error: {e}")
        return False


# ─────────────────────────────────────────────
# Access Requests
# ─────────────────────────────────────────────

def get_pending_requests() -> List[Dict]:
    try:
        res = (
            supabase.table("requests_raised")
            .select("*")
            .eq("request_status", "pending")
            .order("request_date", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.misc] get_pending_requests error: {e}")
        return []


def get_processed_requests() -> List[Dict]:
    try:
        res = (
            supabase.table("requests_raised")
            .select("*")
            .in_("request_status", ["completed", "denied"])
            .order("request_date", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.misc] get_processed_requests error: {e}")
        return []


def get_requests_by_user(username: str, email: str) -> List[Dict]:
    try:
        res = (
            supabase.table("requests_raised")
            .select("*")
            .eq("username", username)
            .eq("email", email)
            .order("request_date", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.misc] get_requests_by_user error: {e}")
        return []


def create_request(request_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("requests_raised").insert(request_data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.misc] create_request error: {e}")
        return None


def update_request(request_id: int, updates: Dict) -> bool:
    try:
        supabase.table("requests_raised").update(updates).eq("request_id", request_id).execute()
        return True
    except Exception as e:
        print(f"[db.misc] update_request error: {e}")
        return False