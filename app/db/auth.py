"""
app/db/auth.py
PostgreSQL queries for login_attempts and pw_tokens tables.
"""

from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from app.db import fetch_one, fetch_all, execute, insert_returning
from app.utils.datetime_service import now_utc_naive


# ─────────────────────────────────────────────
# Login Attempts
# ─────────────────────────────────────────────

_MAX_ATTEMPTS = 3
_LOCKOUT_MINUTES = 15


def check_login_attempts(identifier: str, ip_address: str) -> Tuple[bool, str, int]:
    """
    Returns (allowed, error_message, remaining_attempts).
    allowed=False means the login should be blocked.
    """
    try:
        attempt = fetch_one(
            "SELECT * FROM login_attempts WHERE identifier=%s AND ip_address=%s",
            (identifier, ip_address),
        )

        if not attempt:
            return True, "", _MAX_ATTEMPTS

        # Check active lockout
        blocked_until_raw = attempt.get("blocked_until")
        if blocked_until_raw:
            try:
                blocked_until = datetime.fromisoformat(
                    str(blocked_until_raw).replace("Z", "+00:00").replace("+00:00", "")
                )
            except Exception:
                blocked_until = datetime.strptime(str(blocked_until_raw), "%Y-%m-%d %H:%M:%S.%f")

            if now_utc_naive() < blocked_until:
                remaining_mins = int((blocked_until - now_utc_naive()).total_seconds() / 60) + 1
                return False, f"Account locked. Try again in {remaining_mins} minutes.", 0
            else:
                # Lock expired — reset
                execute(
                    "UPDATE login_attempts SET failed_count=%s, blocked_until=%s WHERE identifier=%s AND ip_address=%s",
                    (0, None, identifier, ip_address),
                )
                return True, "", _MAX_ATTEMPTS

        failed_count = int(attempt.get("failed_count", 0))

        if failed_count >= _MAX_ATTEMPTS:
            blocked_until = (now_utc_naive() + timedelta(minutes=_LOCKOUT_MINUTES)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )
            execute(
                "UPDATE login_attempts SET blocked_until=%s WHERE identifier=%s AND ip_address=%s",
                (blocked_until, identifier, ip_address),
            )
            return False, f"Too many failed attempts. Account locked for {_LOCKOUT_MINUTES} minutes.", 0

        remaining = _MAX_ATTEMPTS - failed_count
        return True, "", remaining

    except Exception as e:
        print(f"[db.auth] check_login_attempts error: {e}")
        return True, "", _MAX_ATTEMPTS  # fail open


def record_failed_login(identifier: str, ip_address: str) -> None:
    try:
        row = fetch_one(
            "SELECT id,failed_count FROM login_attempts WHERE identifier=%s AND ip_address=%s",
            (identifier, ip_address),
        )
        now_str = now_utc_naive().strftime("%Y-%m-%d %H:%M:%S.%f")

        if row:
            new_count = int(row.get("failed_count", 0)) + 1
            execute(
                "UPDATE login_attempts SET failed_count=%s, last_failed_at=%s WHERE id=%s",
                (new_count, now_str, row["id"]),
            )
        else:
            insert_returning("login_attempts", {
                "identifier": identifier,
                "ip_address": ip_address,
                "failed_count": 1,
                "first_failed_at": now_str,
                "last_failed_at": now_str,
                "blocked_until": None,
            })
    except Exception as e:
        print(f"[db.auth] record_failed_login error: {e}")


def clear_login_attempts(identifier: str, ip_address: str) -> None:
    try:
        execute("DELETE FROM login_attempts WHERE identifier=%s AND ip_address=%s", (identifier, ip_address))
    except Exception as e:
        print(f"[db.auth] clear_login_attempts error: {e}")


# ─────────────────────────────────────────────
# Password Tokens (setup + reset)
# ─────────────────────────────────────────────

def create_password_token(email: str, token_type: str, token: str, expires_at: str) -> bool:
    try:
        insert_returning("pw_tokens", {
            "token": token,
            "email": email.lower(),
            "type": token_type,
            "expires_at": expires_at,
            "used": False,
            "created_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return True
    except Exception as e:
        print(f"[db.auth] create_password_token error: {e}")
        return False


def get_password_token(token: str) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM pw_tokens WHERE token=%s", (token,))
    except Exception as e:
        print(f"[db.auth] get_password_token error: {e}")
        return None


def mark_token_used(token: str) -> bool:
    try:
        execute("UPDATE pw_tokens SET used=%s WHERE token=%s", (True, token))
        return True
    except Exception as e:
        print(f"[db.auth] mark_token_used error: {e}")
        return False
