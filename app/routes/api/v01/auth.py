"""
app/routes/api/v01/auth.py
Authentication JSON API (v01). Merges the previous app/routes/api_auth.py
(JWT endpoints for external clients) with the JSON endpoints that used to
live inline inside app/routes/auth.py (password-reset request, delete
account). Logic is unchanged from both sources — only relocated and
re-versioned under /api/v01/auth.

Endpoints:
  POST   /api/v01/auth/login           — username/password -> JWT
  POST   /api/v01/auth/google          — Google ID token -> JWT
  POST   /api/v01/auth/refresh         — refresh token -> new access token
  POST   /api/v01/auth/logout          — revoke refresh token
  GET    /api/v01/auth/me              — current user info (JWT required)
  DELETE /api/v01/auth/me              — delete the current (session) account
  POST   /api/v01/auth/password-reset  — request a password-reset email
"""

from flask import Blueprint, request, jsonify, g, session

from app.db.users import (
    get_user_by_username, get_user_by_email,
    get_user_by_google_id, get_all_users,
    create_user, update_user,
)
from app.utils.datetime_service import now_utc_naive
from app.db.auth import check_login_attempts, record_failed_login, clear_login_attempts
from app.services.auth_service import is_password_hashed, verify_password, create_password_token
from app.services.email_service import send_password_reset_email
from app.services.jwt_service import create_tokens, refresh_access_token, revoke_refresh_token
from app.utils.helpers import generate_username

api_auth_bp = Blueprint("api_auth", __name__, url_prefix="/api/v01/auth")


# ── POST /api/v01/auth/login ──────────────────────────────────────────────

@api_auth_bp.route("/login", methods=["POST"])
def api_login():
    data       = request.get_json(silent=True) or {}
    identifier = str(data.get("username") or data.get("email") or "").strip()
    password   = str(data.get("password") or "").strip()
    ip         = request.remote_addr

    if not identifier or not password:
        return jsonify({"status": "error", "message": "username/email and password required"}), 400

    allowed, err_msg, _ = check_login_attempts(identifier, ip)
    if not allowed:
        return jsonify({"status": "error", "message": err_msg}), 429

    user = get_user_by_username(identifier)
    if not user and "@" in identifier:
        user = get_user_by_email(identifier.lower())

    if not user:
        record_failed_login(identifier, ip)
        return jsonify({"status": "error", "message": "Invalid credentials"}), 401

    stored = str(user.get("password", "")).strip()
    if not stored:
        return jsonify({
            "status": "error",
            "message": "Password not set. Please use the web portal to set your password first."
        }), 401

    ok = verify_password(password, stored) if is_password_hashed(stored) else (stored == password)
    if not ok:
        record_failed_login(identifier, ip)
        _, _, remaining = check_login_attempts(identifier, ip)
        msg = f"Invalid credentials. {remaining} attempts remaining." if remaining > 0 else "Account locked."
        return jsonify({"status": "error", "message": msg}), 401

    clear_login_attempts(identifier, ip)
    return jsonify({"status": "success", "data": create_tokens(user)})


# ── POST /api/v01/auth/google ─────────────────────────────────────────────

@api_auth_bp.route("/google", methods=["POST"])
def api_google_login():
    data     = request.get_json(silent=True) or {}
    id_token = str(data.get("id_token") or "").strip()

    if not id_token:
        return jsonify({"status": "error", "message": "id_token required"}), 400

    user_info = _verify_google_token(id_token)
    if not user_info:
        return jsonify({"status": "error", "message": "Invalid or expired Google token"}), 401

    google_id = str(user_info.get("sub", ""))
    email     = str(user_info.get("email", "")).strip().lower()
    full_name = str(user_info.get("name", "")).strip()

    if not email or not google_id:
        return jsonify({"status": "error", "message": "Google did not provide required info"}), 400

    # Find or create user
    user   = get_user_by_google_id(google_id)
    is_new = False

    if not user:
        user = get_user_by_email(email)
        if user:
            # Link Google ID to existing account
            update_user(int(user["id"]), {
                "google_id":     google_id,
                "auth_provider": "google",
                "updated_at":    now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
            })

    if not user:
        all_users    = get_all_users()
        existing     = {str(u.get("username", "")).lower() for u in all_users}
        username     = generate_username(full_name, existing)
        admin_exists = any("admin" in str(u.get("role", "")).lower() for u in all_users)
        user = create_user({
            "username":      username,
            "email":         email,
            "full_name":     full_name,
            "password":      "",
            "role":          "user" if admin_exists else "admin",
            "google_id":     google_id,
            "auth_provider": "google",
        })
        is_new = True
        if not user:
            return jsonify({"status": "error", "message": "Failed to create account"}), 500

    tokens         = create_tokens(user)
    tokens["is_new_user"] = is_new
    return jsonify({"status": "success", "data": tokens})


# ── POST /api/v01/auth/refresh ────────────────────────────────────────────

@api_auth_bp.route("/refresh", methods=["POST"])
def api_refresh():
    data          = request.get_json(silent=True) or {}
    refresh_token = str(data.get("refresh_token") or "").strip()

    if not refresh_token:
        return jsonify({"status": "error", "message": "refresh_token required"}), 400

    success, message, token_data = refresh_access_token(refresh_token)
    if not success:
        return jsonify({"status": "error", "message": message}), 401

    return jsonify({"status": "success", "data": token_data})


# ── POST /api/v01/auth/logout ─────────────────────────────────────────────

@api_auth_bp.route("/logout", methods=["POST"])
def api_logout():
    data          = request.get_json(silent=True) or {}
    refresh_token = str(data.get("refresh_token") or "").strip()
    if refresh_token:
        revoke_refresh_token(refresh_token)
    return jsonify({"status": "success", "message": "Logged out"})


# ── GET /api/v01/auth/me ──────────────────────────────────────────────────

@api_auth_bp.route("/me", methods=["GET"])
def api_me():
    # Must be called with JWT — middleware populates session
    user_id = session.get("user_id")
    if not user_id or not getattr(g, "jwt_authenticated", False):
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    from app.db.users import get_user_by_id
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    role = str(user.get("role", "user"))
    available_portals = []
    if "user"  in role: available_portals.append("user")
    if "admin" in role: available_portals.append("admin")

    return jsonify({
        "status": "success",
        "data": {
            "id":                int(user["id"]),
            "username":          user.get("username"),
            "email":             user.get("email"),
            "full_name":         user.get("full_name"),
            "role":              role,
            "available_portals": available_portals,
            "created_at":        str(user.get("created_at", "")),
        }
    })


# ── DELETE /api/v01/auth/me ───────────────────────────────────────────────
# Relocated from POST /api/delete-account in app/routes/auth.py.

@api_auth_bp.route("/me", methods=["DELETE"])
def delete_account():
    """
    Complete account deletion via centralized user_deletion_service.
    All related data is removed in the correct dependency order.
    """
    user_id = session.get("user_id")
    if not user_id or session.get("admin_id"):
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.get_json() or {}
    if data.get("confirm") != "DELETE":
        return jsonify({"success": False, "message": "Invalid confirmation"}), 400

    # Clear session BEFORE deletion so the user is logged out immediately
    # even if a later step fails (they can't log in again anyway — user is gone)
    session.clear()

    from app.services.user_deletion_service import delete_user_completely
    success, message = delete_user_completely(int(user_id))

    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": message}), 500


# ── POST /api/v01/auth/password-reset ─────────────────────────────────────
# Relocated from POST /api/request-password-reset in app/routes/auth.py.

@api_auth_bp.route("/password-reset", methods=["POST"])
def api_request_password_reset():
    data  = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    if email:
        user = get_user_by_email(email)
        if user:
            try:
                token = create_password_token(email, "reset")
                send_password_reset_email(
                    email, user.get("full_name","User"), user.get("username",""), token
                )
            except Exception as e:
                print(f"[api.v01.auth] password-reset error: {e}")
    return jsonify({"success": True,
                    "message": "If an account exists with this email, a reset link has been sent."})


# ── Internal: Google token verification ───────────────────────────────────

def _verify_google_token(id_token: str) -> dict | None:
    try:
        import requests as _req
        import app.config as _cfg
        resp = _req.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        info = resp.json()
        # Security: verify audience matches our client ID
        if _cfg.GOOGLE_OAUTH_CLIENT_ID and info.get("aud") != _cfg.GOOGLE_OAUTH_CLIENT_ID:
            print(f"[api.v01.auth] Google token audience mismatch: {info.get('aud')}")
            return None
        return info
    except Exception as e:
        print(f"[api.v01.auth] _verify_google_token error: {e}")
        return None
