"""
app/utils/helpers.py
Small, pure utility functions with no dependencies on Flask or DB.
"""

import string
import secrets


def safe_float(value, default: float = 0.0) -> float:
    if value is None or str(value).strip() in ("", "None", "null", "nan"):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default: int = 0) -> int:
    if value is None or str(value).strip() in ("", "None", "null", "nan"):
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def generate_username(full_name: str, existing_usernames: set) -> str:
    """
    Generate a unique username in FirstName.LastName format.
    Appends a counter if the base name is already taken.
    """
    base = full_name.strip().lower().replace(" ", ".")
    if base not in existing_usernames:
        return base
    counter = 1
    while f"{base}{counter}" in existing_usernames:
        counter += 1
    return f"{base}{counter}"


def generate_password(length: int = 8) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def is_valid_email(email: str) -> bool:
    if "@" not in email or len(email) < 6:
        return False
    domain = email.split("@")[1].lower()
    return "." in domain and len(domain) > 3


def parse_max_attempts(raw) -> int | None:
    """
    Parse max_attempts field: returns None (unlimited) or a non-negative int.
    Raises ValueError on invalid input.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if not s.isdigit():
        raise ValueError("max_attempts must be a non-negative integer")
    val = int(s)
    if val < 0:
        raise ValueError("max_attempts must be non-negative")
    return val