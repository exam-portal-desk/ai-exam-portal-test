"""
app/services/jwt_service.py
JWT token creation, verification, and refresh token management.
No external JWT library needed — pure Python HS256.
"""

import secrets
import hmac
import hashlib
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import config

ACCESS_TOKEN_EXPIRY_MINUTES = 60
REFRESH_TOKEN_EXPIRY_DAYS   = 30


# ── Pure Python HS256 ──────────────────────────────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)

def _sign(msg: str) -> str:
    return _b64url_encode(
        hmac.new(config.SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    )

def _create_jwt(payload: dict) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body   = _b64url_encode(json.dumps(payload).encode())
    return f"{header}.{body}.{_sign(f'{header}.{body}')}"

def _verify_jwt(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        if not hmac.compare_digest(sig, _sign(f"{header}.{body}")):
            return None
        payload = json.loads(_b64url_decode(body))
        if datetime.now(timezone.utc).timestamp() > payload.get("exp", 0):
            return None
        return payload
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────────

def create_access_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    return _create_jwt({
        "sub":  str(user["id"]),
        "uid":  int(user["id"]),
        "role": str(user.get("role", "user")),
        "iat":  int(now.timestamp()),
        "exp":  int((now + timedelta(minutes=ACCESS_TOKEN_EXPIRY_MINUTES)).timestamp()),
        "type": "access",
    })


def verify_access_token(token: str) -> Optional[dict]:
    payload = _verify_jwt(token)
    if payload and payload.get("type") == "access":
        return payload
    return None


def create_tokens(user: dict) -> dict:
    """Create access + refresh tokens. Returns full login response dict."""
    access_token              = create_access_token(user)
    refresh_token, _          = _create_refresh_token(int(user["id"]))
    role                      = str(user.get("role", "user"))

    available_portals = []
    if "user"  in role: available_portals.append("user")
    if "admin" in role: available_portals.append("admin")

    return {
        "access_token":      access_token,
        "refresh_token":     refresh_token,
        "token_type":        "Bearer",
        "expires_in":        ACCESS_TOKEN_EXPIRY_MINUTES * 60,
        "available_portals": available_portals,
        "user": {
            "id":        int(user["id"]),
            "username":  user.get("username"),
            "full_name": user.get("full_name"),
            "role":      role,
        },
    }


def refresh_access_token(refresh_token: str) -> tuple:
    """Returns (success, message, data_or_None)."""
    row = _get_refresh_token(refresh_token)
    if not row:
        return False, "refresh_token_invalid", None
    if row.get("revoked"):
        return False, "refresh_token_revoked", None
    try:
        exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "").replace("+00:00", ""))
        if datetime.now() > exp:
            return False, "refresh_token_expired", None
    except Exception:
        return False, "refresh_token_expired", None

    from app.db.users import get_user_by_id
    user = get_user_by_id(int(row["user_id"]))
    if not user:
        return False, "user_not_found", None

    return True, "ok", {
        "access_token": create_access_token(user),
        "token_type":   "Bearer",
        "expires_in":   ACCESS_TOKEN_EXPIRY_MINUTES * 60,
    }


def revoke_refresh_token(token: str) -> None:
    try:
        from app.db import supabase
        supabase.table("jwt_refresh_tokens").update({"revoked": True}).eq("token", token).execute()
    except Exception as e:
        print(f"[jwt_service] revoke error: {e}")


# ── Internal ───────────────────────────────────────────────────────────────

def _create_refresh_token(user_id: int) -> tuple:
    token      = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS)
    try:
        from app.db import supabase
        supabase.table("jwt_refresh_tokens").insert({
            "user_id":    user_id,
            "token":      token,
            "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            "revoked":    False,
        }).execute()
    except Exception as e:
        print(f"[jwt_service] _create_refresh_token error: {e}")
    return token, expires_at


def _get_refresh_token(token: str) -> Optional[dict]:
    try:
        from app.db import supabase
        res = supabase.table("jwt_refresh_tokens").select("*").eq("token", token).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[jwt_service] _get_refresh_token error: {e}")
        return None
