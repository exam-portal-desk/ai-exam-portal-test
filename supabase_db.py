"""
supabase_db.py
Backward-compatibility shim.

All real Supabase queries have moved to app/db/*.py.
This file re-exports every public symbol so existing imports
(chat.py, discussion.py, latex_editor.py, ai_question_generator.py)
continue to work without modification.
"""

# Re-export the client so `from supabase_db import supabase` still works
from app.db import supabase  # noqa: F401

# Users
from app.db.users import (           # noqa: F401
    get_user_by_username,
    get_user_by_email,
    get_user_by_id,
    get_user_by_google_id,
    get_all_users,
    create_user,
    update_user,
    delete_user,
)

# Exams
from app.db.exams import (           # noqa: F401
    get_all_exams,
    get_exam_by_id,
    create_exam,
    update_exam,
    delete_exam,
    release_exam_results,
)

# Questions
from app.db.questions import (       # noqa: F401
    get_questions_by_exam,
    create_question,
    update_question,
    delete_question,
)

# Results & Responses
from app.db.results import (         # noqa: F401
    get_all_results,
    get_result_by_id,
    get_results_by_user,
    get_results_by_exam,
    get_latest_result_by_user_exam,
    get_responses_by_result,
    create_result,
    create_responses_bulk,
)

# Attempts
from app.db.attempts import (        # noqa: F401
    get_active_attempt,
    get_latest_attempt,
    get_completed_attempts_count,
    create_exam_attempt,
    update_exam_attempt,
)

# Sessions
from app.db.sessions import (        # noqa: F401
    create_session,
    get_session_by_token,
    invalidate_session,
    update_session_last_seen,
)

# Auth (login attempts + pw tokens)
from app.db.auth import (            # noqa: F401
    check_login_attempts,
    record_failed_login,
    clear_login_attempts,
    create_password_token as create_password_token_db,
    get_password_token as get_password_token_db,
    mark_token_used as mark_token_used_db,
)

# AI
from app.db.ai import (              # noqa: F401
    get_chat_history,
    save_chat_message as _save_chat_raw,
    delete_user_chat_history,
    get_today_usage,
    increment_usage,
)

# Misc (kept for any direct imports)
from app.db.misc import (            # noqa: F401
    get_all_subjects,
)


# ── Legacy signatures that had different param shapes ──────────────────────

def save_chat_message(message_data: dict) -> bool:
    """Legacy dict-based save; delegates to new db.ai.save_chat_message."""
    return _save_chat_raw(
        user_id=int(message_data.get("user_id", 0)),
        message=str(message_data.get("message", "")),
        is_user=bool(message_data.get("is_user", False)),
    )


def create_password_token(token_data: dict):
    """Legacy dict-based create; not used by new code."""
    from app.db.auth import create_password_token as _cp
    return _cp(
        email=token_data.get("email", ""),
        token_type=token_data.get("type", "reset"),
        token=token_data.get("token", ""),
        expires_at=token_data.get("expires_at", ""),
    )


def get_password_token(token: str):
    from app.db.auth import get_password_token as _gp
    return _gp(token)


def mark_token_used(token: str) -> bool:
    from app.db.auth import mark_token_used as _mu
    return _mu(token)