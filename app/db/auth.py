"""
app/db/auth.py
Supabase queries for login_attempts and pw_tokens tables.
Replaces both login_attempts_cache.py and the duplicate functions in supabase_db.py.
"""

from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from app.db import supabase


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
        res = (
            supabase.table("login_attempts")
            .select("*")
            .eq("identifier", identifier)
            .eq("ip_address", ip_address)
            .execute()
        )

        if not res.data:
            return True, "", _MAX_ATTEMPTS

        attempt = res.data[0]

        # Check active lockout
        blocked_until_raw = attempt.get("blocked_until")
        if blocked_until_raw:
            try:
                blocked_until = datetime.fromisoformat(
                    str(blocked_until_raw).replace("Z", "+00:00").replace("+00:00", "")
                )
            except Exception:
                blocked_until = datetime.strptime(str(blocked_until_raw), "%Y-%m-%d %H:%M:%S.%f")

            if datetime.now() < blocked_until:
                remaining_mins = int((blocked_until - datetime.now()).total_seconds() / 60) + 1
                return False, f"Account locked. Try again in {remaining_mins} minutes.", 0
            else:
                # Lock expired — reset
                supabase.table("login_attempts").update(
                    {"failed_count": 0, "blocked_until": None}
                ).eq("identifier", identifier).eq("ip_address", ip_address).execute()
                return True, "", _MAX_ATTEMPTS

        failed_count = int(attempt.get("failed_count", 0))

        if failed_count >= _MAX_ATTEMPTS:
            blocked_until = (datetime.now() + timedelta(minutes=_LOCKOUT_MINUTES)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )
            supabase.table("login_attempts").update({"blocked_until": blocked_until}).eq(
                "identifier", identifier
            ).eq("ip_address", ip_address).execute()
            return False, f"Too many failed attempts. Account locked for {_LOCKOUT_MINUTES} minutes.", 0

        remaining = _MAX_ATTEMPTS - failed_count
        return True, "", remaining

    except Exception as e:
        print(f"[db.auth] check_login_attempts error: {e}")
        return True, "", _MAX_ATTEMPTS  # fail open


def record_failed_login(identifier: str, ip_address: str) -> None:
    try:
        res = (
            supabase.table("login_attempts")
            .select("id,failed_count")
            .eq("identifier", identifier)
            .eq("ip_address", ip_address)
            .execute()
        )
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

        if res.data:
            row = res.data[0]
            new_count = int(row.get("failed_count", 0)) + 1
            supabase.table("login_attempts").update(
                {"failed_count": new_count, "last_failed_at": now_str}
            ).eq("id", row["id"]).execute()
        else:
            supabase.table("login_attempts").insert(
                {
                    "identifier": identifier,
                    "ip_address": ip_address,
                    "failed_count": 1,
                    "first_failed_at": now_str,
                    "last_failed_at": now_str,
                    "blocked_until": None,
                }
            ).execute()
    except Exception as e:
        print(f"[db.auth] record_failed_login error: {e}")


def clear_login_attempts(identifier: str, ip_address: str) -> None:
    try:
        supabase.table("login_attempts").delete().eq("identifier", identifier).eq(
            "ip_address", ip_address
        ).execute()
    except Exception as e:
        print(f"[db.auth] clear_login_attempts error: {e}")


# ─────────────────────────────────────────────
# Password Tokens (setup + reset)
# ─────────────────────────────────────────────

def create_password_token(email: str, token_type: str, token: str, expires_at: str) -> bool:
    try:
        supabase.table("pw_tokens").insert(
            {
                "token": token,
                "email": email.lower(),
                "type": token_type,
                "expires_at": expires_at,
                "used": False,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ).execute()
        return True
    except Exception as e:
        print(f"[db.auth] create_password_token error: {e}")
        return False


def get_password_token(token: str) -> Optional[Dict]:
    try:
        res = supabase.table("pw_tokens").select("*").eq("token", token).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.auth] get_password_token error: {e}")
        return None


def mark_token_used(token: str) -> bool:
    try:
        supabase.table("pw_tokens").update({"used": True}).eq("token", token).execute()
        return True
    except Exception as e:
        print(f"[db.auth] mark_token_used error: {e}")
        return False