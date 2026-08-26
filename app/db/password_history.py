"""
app/db/password_history.py
Tracks each user's most recent superseded password hashes, so
auth_service.is_password_reused() can block reuse of the last 3
passwords (current + 2 prior). Only bcrypt hashes are ever stored.
"""

from typing import List
from app.db import fetch_all, execute


def get_recent_password_hashes(user_id: int, limit: int = 2) -> List[str]:
    try:
        rows = fetch_all(
            "SELECT password_hash FROM password_history WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
        return [r["password_hash"] for r in rows]
    except Exception as e:
        print(f"[db.password_history] get_recent_password_hashes error: {e}")
        return []


def record_password_history(user_id: int, password_hash: str) -> None:
    """Push the password being replaced into history, then prune to the
    2 most recent rows — combined with the live `users.password` value,
    this keeps a rolling window of exactly the last 3 real passwords."""
    try:
        execute("INSERT INTO password_history (user_id, password_hash) VALUES (%s, %s)", (user_id, password_hash))
        execute(
            "DELETE FROM password_history WHERE user_id=%s AND id NOT IN "
            "(SELECT id FROM password_history WHERE user_id=%s ORDER BY created_at DESC LIMIT 2)",
            (user_id, user_id),
        )
    except Exception as e:
        print(f"[db.password_history] record_password_history error: {e}")
