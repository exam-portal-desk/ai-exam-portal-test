"""
login_attempts_cache.py
Backward-compatibility shim.
Real logic is in app/db/auth.py.
"""

from app.db.auth import (  # noqa: F401
    check_login_attempts,
    record_failed_login,
    clear_login_attempts,
)