"""
app/db/sessions.py
All PostgreSQL queries for the `sessions` table.
"""

import time
from typing import Optional, Dict, Tuple
from app.db import fetch_one, execute, set_clause, insert_returning
from app.utils.datetime_service import now_utc_naive


# Throttle last_seen updates to once per 60s per token
_last_seen_cache: Dict[str, float] = {}

# PERFORMANCE: the DB is a remote Supabase instance — every query pays a
# real network round trip (measured ~100-150ms), and get_session_by_token()
# is the one query EVERY single admin/user request pays via
# require_admin_role/require_user_role, regardless of what that request
# actually does. A short in-process cache removes that round trip for
# rapid-fire requests (tab switches, search-as-you-type, a page's own
# handful of near-simultaneous AJAX calls) without meaningfully weakening
# auth: the cached fields (id/token/user_id/admin_session/active/
# is_exam_active/exam_id) are read ONLY for existence + admin_session by
# session_guard.py — nothing exam-related is ever decided from this cached
# snapshot (exam state is always queried fresh, separately, by the exam
# flow itself), so this cache cannot introduce stale Live/Scheduled-exam
# behavior. invalidate_session() below clears it immediately on explicit
# logout, so a deliberate sign-out is never delayed by it.
_SESSION_CACHE_TTL = 3.0
_session_cache: Dict[str, Tuple[Optional[Dict], float]] = {}


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
    """Fetch active session; retries up to 3 times on transient error.
    Served from a short-lived cache when possible — see the module-level
    performance note above."""
    cached = _session_cache.get(token)
    if cached is not None and cached[1] > time.time():
        return cached[0]

    for attempt in range(3):
        try:
            row = fetch_one(
                "SELECT id,token,user_id,admin_session,active,is_exam_active,exam_id "
                "FROM sessions WHERE token=%s AND active=%s",
                (token, True),
            )
            _session_cache[token] = (row, time.time() + _SESSION_CACHE_TTL)
            return row
        except Exception as e:
            print(f"[db.sessions] get_session_by_token attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1))
    return None


def has_active_session(user_id: int) -> bool:
    """True if this user has any active session row — used to detect an
    "already logged in on another device" situation at login time."""
    try:
        row = fetch_one("SELECT id FROM sessions WHERE user_id=%s AND active=%s LIMIT 1", (user_id, True))
        return row is not None
    except Exception as e:
        print(f"[db.sessions] has_active_session error: {e}")
        return False


def invalidate_session(user_id: int, token: Optional[str] = None) -> bool:
    try:
        if token:
            execute("UPDATE sessions SET active=%s WHERE token=%s", (False, token))
            # A deliberate logout must never wait out the read cache's TTL —
            # drop the now-stale cached row immediately.
            _session_cache.pop(token, None)
        else:
            execute("UPDATE sessions SET active=%s WHERE user_id=%s", (False, user_id))
            # No single token to target ("log out everywhere") — this path
            # is rare (not a hot request), so clearing the whole cache is
            # simpler and just as correct as tracking user_id per entry.
            _session_cache.clear()
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
