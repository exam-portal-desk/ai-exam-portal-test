"""
app/db/sessions.py
All PostgreSQL queries for the `sessions` table.
"""

import time
from typing import Optional, Dict
from app.db import fetch_one, execute, set_clause, insert_returning
from app.utils.datetime_service import now_utc_naive


# Throttle last_seen updates to once per 60s per token
_last_seen_cache: Dict[str, float] = {}


def create_session(session_data: Dict) -> bool:
    try:
        now = now_utc_naive().isoformat()
        session_data["created_at"] = now
        session_data["last_seen"] = now
        insert_returning("sessions", session_data)
        return True
    except Exception as e:
        print(f"[db.sessions] create_session error: {e}")
        return False


def get_session_by_token(token: str) -> Optional[Dict]:
    """Fetch active session; retries up to 3 times on transient error."""
    for attempt in range(3):
        try:
            return fetch_one(
                "SELECT id,token,user_id,admin_session,active,is_exam_active,exam_id "
                "FROM sessions WHERE token=%s AND active=%s",
                (token, True),
            )
        except Exception as e:
            print(f"[db.sessions] get_session_by_token attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1))
    return None


def invalidate_session(user_id: int, token: Optional[str] = None) -> bool:
    try:
        if token:
            execute("UPDATE sessions SET active=%s WHERE token=%s", (False, token))
        else:
            execute("UPDATE sessions SET active=%s WHERE user_id=%s", (False, user_id))
        return True
    except Exception as e:
        print(f"[db.sessions] invalidate_session error: {e}")
        return False


def update_session_last_seen(token: str) -> bool:
    """Throttled: only hits DB once per 60 seconds per token."""
    now = time.time()
    if _last_seen_cache.get(token, 0) > now - 60:
        return True
    _last_seen_cache[token] = now
    try:
        execute("UPDATE sessions SET last_seen=%s WHERE token=%s", (now_utc_naive().isoformat(), token))
        return True
    except Exception as e:
        print(f"[db.sessions] update_session_last_seen error: {e}")
        return False


def set_exam_active(token: str, exam_id: Optional[int] = None,
                    result_id: Optional[int] = None, is_active: bool = True) -> bool:
    try:
        updates: Dict = {"is_exam_active": is_active}
        if exam_id is not None:
            updates["exam_id"] = exam_id
        if result_id is not None:
            updates["result_id"] = result_id
        sc, params = set_clause(updates)
        execute(f"UPDATE sessions SET {sc} WHERE token=%s", params + [token])
        return True
    except Exception as e:
        print(f"[db.sessions] set_exam_active error: {e}")
        return False
