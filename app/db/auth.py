"""
app/db/auth.py
PostgreSQL queries for login_attempts and pw_tokens tables.
"""

from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from app.db import fetch_one, fetch_all, execute, execute_returning, insert_returning
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


# ─────────────────────────────────────────────
# OTP Challenges ("Existing Active Session" login verification)
# ─────────────────────────────────────────────

def count_recent_otp_challenges(user_id: int, window_seconds: int) -> int:
    """How many OTP challenges have been requested for this user within the
    rate-limit window — used to enforce OTP_MAX_REQUESTS."""
    try:
        row = fetch_one(
            "SELECT COUNT(*) AS count FROM otp_challenges WHERE user_id=%s AND created_at > %s",
            (user_id, now_utc_naive() - timedelta(seconds=window_seconds)),
        )
        return row["count"] if row else 0
    except Exception as e:
        print(f"[db.auth] count_recent_otp_challenges error: {e}")
        return 0


def create_otp_challenge(user_id: int, otp_hash: str, expires_at: str, window_seconds: int) -> Optional[Dict]:
    """Insert a new OTP challenge. Opportunistically deletes this user's
    rows older than the rate-limit window first — they no longer count
    toward the limit, so nothing is lost by removing them."""
    try:
        execute(
            "DELETE FROM otp_challenges WHERE user_id=%s AND created_at <= %s",
            (user_id, now_utc_naive() - timedelta(seconds=window_seconds)),
        )
        return insert_returning("otp_challenges", {
            "user_id": user_id,
            "otp_hash": otp_hash,
            "expires_at": expires_at,
            "attempts": 0,
            "created_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        print(f"[db.auth] create_otp_challenge error: {e}")
        return None


def get_otp_challenge(challenge_id: int, user_id: int) -> Optional[Dict]:
    try:
        return fetch_one(
            "SELECT * FROM otp_challenges WHERE id=%s AND user_id=%s",
            (challenge_id, user_id),
        )
    except Exception as e:
        print(f"[db.auth] get_otp_challenge error: {e}")
        return None


def increment_otp_attempts(challenge_id: int, fail_safe_max: int) -> int:
    """Atomically bump the attempt counter, returning the new count. On
    error, fails safe by returning fail_safe_max so the caller treats it as
    exhausted rather than allowing unlimited guesses."""
    try:
        rows = execute_returning(
            "UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s RETURNING attempts",
            (challenge_id,),
        )
        return rows[0]["attempts"] if rows else fail_safe_max
    except Exception as e:
        print(f"[db.auth] increment_otp_attempts error: {e}")
        return fail_safe_max


def delete_otp_challenge(challenge_id: int) -> None:
    try:
        execute("DELETE FROM otp_challenges WHERE id=%s", (challenge_id,))
    except Exception as e:
        print(f"[db.auth] delete_otp_challenge error: {e}")
