"""
sessions.py
Backward-compatibility shim.

All real logic has moved to:
  - app/db/sessions.py       (DB queries)
  - app/middleware/session_guard.py  (decorators)

This file re-exports everything so any existing code that imports
from `sessions` continues to work without changes.
"""

# Re-export DB functions
from app.db.sessions import (          # noqa: F401
    create_session,
    get_session_by_token,
    invalidate_session,
    update_session_last_seen,
    set_exam_active,
)

# Re-export decorators
from app.middleware.session_guard import (  # noqa: F401
    require_user_role,
    require_admin_role,
)


def generate_session_token() -> str:
    import secrets
    return secrets.token_urlsafe(32)


def save_session_record(session_data: dict) -> bool:
    """Legacy wrapper — calls the new create_session."""
    from app.db.sessions import invalidate_session as _inv
    uid = session_data.get("user_id")
    if uid:
        _inv(int(uid))
    return create_session(session_data)