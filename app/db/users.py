"""
app/db/users.py
All Supabase queries related to the `users` table.
Uses selective column fetching instead of SELECT *.
"""

from typing import Optional, List, Dict
from app.db import supabase


# Columns needed for authentication and session
_AUTH_COLS = "id,username,email,password,full_name,role"

# Columns needed for listing / admin views
_LIST_COLS = "id,username,email,full_name,role,created_at,updated_at"

# All columns (used only where every field is genuinely needed)
_ALL_COLS = "*"


def get_user_by_username(username: str) -> Optional[Dict]:
    try:
        res = supabase.table("users").select(_AUTH_COLS).eq("username", username).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.users] get_user_by_username error: {e}")
        return None


def get_user_by_email(email: str) -> Optional[Dict]:
    try:
        res = supabase.table("users").select(_AUTH_COLS).eq("email", email.lower()).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.users] get_user_by_email error: {e}")
        return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    try:
        res = supabase.table("users").select(_ALL_COLS).eq("id", user_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.users] get_user_by_id error: {e}")
        return None


def get_all_users() -> List[Dict]:
    """Returns list-safe columns only — avoids fetching passwords in bulk."""
    try:
        res = supabase.table("users").select(_LIST_COLS).order("username").execute()
        return res.data or []
    except Exception as e:
        print(f"[db.users] get_all_users error: {e}")
        return []


def get_users_by_ids(user_ids: List[int]) -> Dict[str, Dict]:
    """
    Batch fetch users by a list of IDs.
    Returns a dict keyed by str(id) for fast lookup.
    """
    if not user_ids:
        return {}
    try:
        res = (
            supabase.table("users")
            .select("id,username,full_name")
            .in_("id", [str(i) for i in user_ids])
            .execute()
        )
        return {str(u["id"]): u for u in (res.data or [])}
    except Exception as e:
        print(f"[db.users] get_users_by_ids error: {e}")
        return {}


def create_user(user_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("users").insert(user_data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.users] create_user error: {e}")
        return None


def update_user(user_id: int, updates: Dict) -> bool:
    try:
        supabase.table("users").update(updates).eq("id", user_id).execute()
        return True
    except Exception as e:
        print(f"[db.users] update_user error: {e}")
        return False


def delete_user(user_id: int) -> bool:
    try:
        supabase.table("users").delete().eq("id", user_id).execute()
        return True
    except Exception as e:
        print(f"[db.users] delete_user error: {e}")
        return False


def get_user_by_google_id(google_id: str) -> Optional[Dict]:
    try:
        res = supabase.table("users").select(_AUTH_COLS).eq("google_id", google_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.users] get_user_by_google_id error: {e}")
        return None


def get_users_count() -> int:
    """Total user count via COUNT query — no data fetch"""
    try:
        return supabase.table("users").select("id", count="exact").execute().count or 0
    except Exception as e:
        print(f"Error getting users count: {e}")
        return 0

def get_admins_count() -> int:
    """Admin user count via COUNT query"""
    try:
        return supabase.table("users").select("id", count="exact").ilike("role", "%admin%").execute().count or 0
    except Exception as e:
        print(f"Error getting admins count: {e}")
        return 0    