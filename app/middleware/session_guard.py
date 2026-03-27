"""
app/middleware/session_guard.py
Route decorators for session validation and role enforcement.
Replaces sessions.py require_user_role / require_admin_role.
"""

import time
from functools import wraps
from flask import session, redirect, url_for, flash

from app.db.sessions import get_session_by_token, update_session_last_seen


def require_user_role(f):
    """Block unauthenticated access and admin sessions from user routes."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        tok = session.get("token")

        if not uid or not tok:
            flash("Please login to access this page.", "warning")
            return redirect(url_for("auth.login"))

        sess = get_session_by_token(tok)
        if not sess:
            session.clear()
            flash("Your session has expired.", "warning")
            return redirect(url_for("auth.login"))

        if sess.get("admin_session"):
            flash("You are logged in as Admin. Please logout to access the User portal.", "warning")
            return redirect(url_for("admin.dashboard"))

        update_session_last_seen(tok)
        return f(*args, **kwargs)

    return wrapped


def require_admin_role(f):
    """Block unauthenticated access and non-admin sessions from admin routes."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        tok = session.get("token")

        if not uid or not tok:
            return redirect(url_for("admin.admin_login"))

        sess = None
        for attempt in range(3):
            try:
                sess = get_session_by_token(tok)
                if sess:
                    break
            except Exception as e:
                print(f"[session_guard] admin retry {attempt+1}: {e}")
                time.sleep(0.3 * (attempt + 1))

        if not sess:
            session.clear()
            flash("Session expired. Please login again.", "warning")
            return redirect(url_for("admin.admin_login"))

        if not sess.get("admin_session"):
            session.clear()
            flash("Invalid admin session. Please login as admin.", "warning")
            return redirect(url_for("admin.admin_login"))

        update_session_last_seen(tok)
        return f(*args, **kwargs)

    return wrapped