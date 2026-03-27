"""
app/services/auth_service.py
Business logic for authentication:
  - bcrypt password hashing / verification
  - Secure token creation and validation
  - Password strength validation
"""

import re
import secrets
from datetime import datetime, timedelta
from typing import Tuple, Dict

import bcrypt

from app.db.auth import (
    create_password_token as db_create_token,
    get_password_token as db_get_token,
    mark_token_used as db_mark_used,
)


# ─────────────────────────────────────────────
# Password hashing
# ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        print(f"[auth_service] verify_password error: {e}")
        return False


def is_password_hashed(password: str) -> bool:
    """Return True if the stored password is already a bcrypt hash."""
    return bool(password and password.startswith(("$2a$", "$2b$", "$2y$")) and len(password) == 60)


# ─────────────────────────────────────────────
# Password strength validation
# ─────────────────────────────────────────────

def validate_password_strength(password: str) -> Tuple[bool, str]:
    if len(password) < 10:
        return False, "Password must be at least 10 characters long"
    if len(password) > 128:
        return False, "Password must be less than 128 characters"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least one digit"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least one special character"

    weak = {"password123", "123456789", "qwerty123", "admin123", "welcome123"}
    if password.lower() in weak:
        return False, "This password is too common. Please choose a stronger password"

    return True, "Password is strong"


# ─────────────────────────────────────────────
# Secure tokens (setup / reset)
# ─────────────────────────────────────────────

def create_password_token(email: str, token_type: str) -> str:
    """
    Generate a secure token, persist it, and return the token string.
    Raises Exception if persistence fails.
    """
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    if not db_create_token(email, token_type, token, expires_at):
        raise Exception(f"Failed to save {token_type} token for {email}")

    return token


def validate_and_use_token(token: str) -> Tuple[bool, str, Dict]:
    """
    Validate a token and mark it used in one call.
    Returns (is_valid, message, token_data).
    """
    token_data = db_get_token(token)

    if not token_data:
        return False, "Invalid token", {}

    if token_data.get("used"):
        return False, "Token has already been used", {}

    try:
        expires_at = datetime.fromisoformat(str(token_data["expires_at"]))
    except Exception:
        try:
            expires_at = datetime.strptime(token_data["expires_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False, "Token expiry format unreadable", {}

    if datetime.now() > expires_at:
        return False, "Token has expired", {}

    if not db_mark_used(token):
        return False, "Failed to mark token as used", {}

    return True, "Token valid", token_data