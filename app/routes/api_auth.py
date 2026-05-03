"""
app/routes/api_auth.py
JWT Authentication endpoints for external clients.

Endpoints:
  POST /api/auth/login    — username/password → JWT
  POST /api/auth/google   — Google ID token  → JWT
  POST /api/auth/refresh  — refresh token    → new access token
  POST /api/auth/logout   — revoke refresh token
  GET  /api/auth/me       — current user info (JWT required)
"""

from datetime import datetime
from flask import Blueprint, request, jsonify, g, session

from app.db.users import (
    get_user_by_username, get_user_by_email,
    get_user_by_google_id, get_all_users,
    create_user, update_user,
)
from app.db.auth import check_login_attempts, record_failed_login, clear_login_attempts
from app.services.auth_service import is_password_hashed, verify_password
from app.services.jwt_service import create_tokens, refresh_access_token, revoke_refresh_token
from app.utils.helpers import generate_username

api_auth_bp = Blueprint("api_auth", __name__, url_prefix="/api/auth")


# ── POST /api/auth/login ──────────────────────────────────────────────────

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


# ── POST /api/auth/google ─────────────────────────────────────────────────

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
                "updated_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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


# ── POST /api/auth/refresh ────────────────────────────────────────────────

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


# ── POST /api/auth/logout ─────────────────────────────────────────────────

@api_auth_bp.route("/logout", methods=["POST"])
def api_logout():
    data          = request.get_json(silent=True) or {}
    refresh_token = str(data.get("refresh_token") or "").strip()
    if refresh_token:
        revoke_refresh_token(refresh_token)
    return jsonify({"status": "success", "message": "Logged out"})


# ── GET /api/auth/me ──────────────────────────────────────────────────────

@api_auth_bp.route("/me")
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


# ── Internal: Google token verification ───────────────────────────────────

def _verify_google_token(id_token: str) -> dict | None:
    try:
        import requests as _req
        import config as _cfg
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
            print(f"[api_auth] Google token audience mismatch: {info.get('aud')}")
            return None
        return info
    except Exception as e:
        print(f"[api_auth] _verify_google_token error: {e}")
        return None
