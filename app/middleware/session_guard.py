"""
app/middleware/session_guard.py
Route decorators for session validation and role enforcement.

CHANGES vs original:
  - require_user_role  → agar g.jwt_authenticated hai toh DB session check skip
  - require_admin_role → same
  - g.jwt_invalid check → 401 return immediately
"""

import time
from functools import wraps
from flask import session, redirect, url_for, flash, g, jsonify, request

from app.db.sessions import get_session_by_token, update_session_last_seen


def _is_api_request() -> bool:
    """Check if request is an API call (expects JSON, not HTML redirect)."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("Accept", "").startswith("application/json")
        or request.headers.get("Content-Type", "").startswith("application/json")
        or request.headers.get("Authorization", "").startswith("Bearer ")
    )


def require_user_role(f):
    """Block unauthenticated access. JWT requests skip DB session check."""
    @wraps(f)
    def wrapped(*args, **kwargs):

        # ── JWT invalid token provided ─────────────────────────────────
        if getattr(g, "jwt_invalid", False):
            return jsonify({"status": "error", "message": "token_expired_or_invalid"}), 401

        # ── JWT authenticated — skip DB session check ──────────────────
        if getattr(g, "jwt_authenticated", False):
            role = session.get("role", "")
            if "admin" in role and "user" not in role:
                # Admin-only account trying user routes via JWT
                return jsonify({"status": "error", "message": "Use admin portal endpoints"}), 403
            return f(*args, **kwargs)

        # ── Normal cookie session ──────────────────────────────────────
        uid = session.get("user_id")
        tok = session.get("token")

        if not uid or not tok:
            if _is_api_request():
                return jsonify({"status": "error", "message": "Authentication required"}), 401
            flash("Please login to access this page.", "warning")
            return redirect(url_for("auth.login"))

        sess = get_session_by_token(tok)
        if not sess:
            session.clear()
            if _is_api_request():
                return jsonify({"status": "error", "message": "Session expired"}), 401
            flash("Your session has expired.", "warning")
            return redirect(url_for("auth.login"))

        if sess.get("admin_session"):
            if _is_api_request():
                return jsonify({"status": "error", "message": "Use admin portal"}), 403
            flash("You are logged in as Admin. Please logout to access the User portal.", "warning")
            return redirect(url_for("admin.dashboard"))

        update_session_last_seen(tok)
        return f(*args, **kwargs)

    return wrapped


def require_admin_role(f):
    """Block non-admin access. JWT requests skip DB session check."""
    @wraps(f)
    def wrapped(*args, **kwargs):

        # ── JWT invalid token provided ─────────────────────────────────
        if getattr(g, "jwt_invalid", False):
            return jsonify({"status": "error", "message": "token_expired_or_invalid"}), 401

        # ── JWT authenticated — skip DB session check ──────────────────
        if getattr(g, "jwt_authenticated", False):
            role = session.get("role", "")
            if "admin" not in role:
                return jsonify({"status": "error", "message": "Admin access required"}), 403
            return f(*args, **kwargs)

        # ── Normal cookie session ──────────────────────────────────────
        uid = session.get("user_id")
        tok = session.get("token")

        if not uid or not tok:
            if _is_api_request():
                return jsonify({"status": "error", "message": "Authentication required"}), 401
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
            if _is_api_request():
                return jsonify({"status": "error", "message": "Session expired"}), 401
            flash("Session expired. Please login again.", "warning")
            return redirect(url_for("admin.admin_login"))

        if not sess.get("admin_session"):
            session.clear()
            if _is_api_request():
                return jsonify({"status": "error", "message": "Admin access required"}), 403
            flash("Invalid admin session. Please login as admin.", "warning")
            return redirect(url_for("admin.admin_login"))

        update_session_last_seen(tok)
        return f(*args, **kwargs)

    return wrapped
