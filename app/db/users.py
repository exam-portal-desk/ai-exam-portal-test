"""
app/db/users.py
All PostgreSQL queries related to the `users` table.
Uses selective column fetching instead of SELECT *.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning


_AUTH_COLS = "id,username,email,password,full_name,role,profile_photo_key"
_LIST_COLS = "id,username,email,full_name,role,created_at,updated_at"
_PROFILE_COLS = "id,username,email,full_name,role,created_at,last_login,auth_provider,profile_photo_key"


def get_user_by_username(username: str) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_AUTH_COLS} FROM users WHERE username=%s", (username,))
    except Exception as e:
        print(f"[db.users] get_user_by_username error: {e}")
        return None


def get_user_by_email(email: str) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_AUTH_COLS} FROM users WHERE email=%s", (email.lower(),))
    except Exception as e:
        print(f"[db.users] get_user_by_email error: {e}")
        return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM users WHERE id=%s", (user_id,))
    except Exception as e:
        print(f"[db.users] get_user_by_id error: {e}")
        return None


def get_user_profile_by_id(user_id: int) -> Optional[Dict]:
    """Safe, non-sensitive field set for the Profile page — no password,
    no google_id, no other internal identifiers."""
    try:
        return fetch_one(f"SELECT {_PROFILE_COLS} FROM users WHERE id=%s", (user_id,))
    except Exception as e:
        print(f"[db.users] get_user_profile_by_id error: {e}")
        return None


def get_all_users() -> List[Dict]:
    """Returns list-safe columns only — avoids fetching passwords in bulk."""
    try:
        return fetch_all(f"SELECT {_LIST_COLS} FROM users ORDER BY username")
    except Exception as e:
        print(f"[db.users] get_all_users error: {e}")
        return []


def get_users_by_ids(user_ids: List[int]) -> Dict[str, Dict]:
    """
    Batch fetch users by a list of IDs.
    Returns a dict keyed by str(id) for fast lookup. Includes
    profile_photo_key so callers (chat, discussions) can resolve avatars
    without a second round of per-user queries.
    """
    if not user_ids:
        return {}
    try:
        rows = fetch_all("SELECT id,username,full_name,profile_photo_key FROM users WHERE id = ANY(%s)", (list(user_ids),))
        return {str(u["id"]): u for u in rows}
    except Exception as e:
        print(f"[db.users] get_users_by_ids error: {e}")
        return {}


def create_user(user_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("users", user_data)
    except Exception as e:
        print(f"[db.users] create_user error: {e}")
        return None


def update_user(user_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE users SET {sc} WHERE id=%s", params + [user_id])
        return True
    except Exception as e:
        print(f"[db.users] update_user error: {e}")
        return False


def delete_user(user_id: int) -> bool:
    try:
        execute("DELETE FROM users WHERE id=%s", (user_id,))
        return True
    except Exception as e:
        print(f"[db.users] delete_user error: {e}")
        return False


def get_user_by_google_id(google_id: str) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_AUTH_COLS} FROM users WHERE google_id=%s", (google_id,))
    except Exception as e:
        print(f"[db.users] get_user_by_google_id error: {e}")
        return None


def get_users_count() -> int:
    """Total user count via COUNT query — no data fetch"""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM users")
        return row["count"] if row else 0
    except Exception as e:
        print(f"Error getting users count: {e}")
        return 0


def get_admins_count() -> int:
    """Admin user count via COUNT query"""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM users WHERE role ILIKE %s", ("%admin%",))
        return row["count"] if row else 0
    except Exception as e:
        print(f"Error getting admins count: {e}")
        return 0
