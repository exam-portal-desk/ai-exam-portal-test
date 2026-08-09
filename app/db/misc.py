"""
app/db/misc.py
PostgreSQL queries for subjects and requests_raised tables.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning


# ─────────────────────────────────────────────
# Subjects
# ─────────────────────────────────────────────

def get_all_subjects() -> List[Dict]:
    try:
        return fetch_all(
            "SELECT id,subject_name,subject_folder_id,subject_folder_created_at "
            "FROM subjects ORDER BY subject_name"
        )
    except Exception as e:
        print(f"[db.misc] get_all_subjects error: {e}")
        return []


def get_subject_by_name(name: str) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM subjects WHERE subject_name=%s", (name,))
    except Exception as e:
        print(f"[db.misc] get_subject_by_name error: {e}")
        return None


def get_subject_folder_id_by_name(name: str) -> Optional[str]:
    """Case-insensitive subject name → folder_id lookup. Used for image loading."""
    try:
        rows = fetch_all("SELECT subject_name,subject_folder_id FROM subjects")
        for row in rows:
            if str(row.get("subject_name", "")).strip().lower() == name.strip().lower():
                return str(row.get("subject_folder_id", "")).strip() or None
        return None
    except Exception as e:
        print(f"[db.misc] get_subject_folder_id_by_name error: {e}")
        return None


def create_subject(subject_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("subjects", subject_data)
    except Exception as e:
        print(f"[db.misc] create_subject error: {e}")
        return None


def update_subject(subject_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE subjects SET {sc} WHERE id=%s", params + [subject_id])
        return True
    except Exception as e:
        print(f"[db.misc] update_subject error: {e}")
        return False


def delete_subject(subject_id: int) -> bool:
    try:
        execute("DELETE FROM subjects WHERE id=%s", (subject_id,))
        return True
    except Exception as e:
        print(f"[db.misc] delete_subject error: {e}")
        return False


# ─────────────────────────────────────────────
# Access Requests
# ─────────────────────────────────────────────

def get_pending_requests() -> List[Dict]:
    try:
        return fetch_all(
            "SELECT * FROM requests_raised WHERE request_status=%s ORDER BY request_date DESC", ("pending",)
        )
    except Exception as e:
        print(f"[db.misc] get_pending_requests error: {e}")
        return []


def get_processed_requests() -> List[Dict]:
    try:
        return fetch_all(
            "SELECT * FROM requests_raised WHERE request_status = ANY(%s) ORDER BY request_date DESC",
            (["completed", "denied"],),
        )
    except Exception as e:
        print(f"[db.misc] get_processed_requests error: {e}")
        return []


def get_requests_by_user(username: str, email: str) -> List[Dict]:
    try:
        return fetch_all(
            "SELECT * FROM requests_raised WHERE username=%s AND email=%s ORDER BY request_date DESC",
            (username, email),
        )
    except Exception as e:
        print(f"[db.misc] get_requests_by_user error: {e}")
        return []


def create_request(request_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("requests_raised", request_data)
    except Exception as e:
        print(f"[db.misc] create_request error: {e}")
        return None


def update_request(request_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE requests_raised SET {sc} WHERE request_id=%s", params + [request_id])
        return True
    except Exception as e:
        print(f"[db.misc] update_request error: {e}")
        return False
