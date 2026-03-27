"""
app/routes/admin/auth.py
Admin login and logout routes.
"""

import secrets
from flask import render_template, request, redirect, url_for, session, flash

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.users import get_user_by_username, get_user_by_email
from app.db.sessions import create_session, invalidate_session
from app.db.auth import check_login_attempts, record_failed_login, clear_login_attempts
from app.services.auth_service import is_password_hashed, verify_password


@admin_bp.route("/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin/admin_login.html")

    identifier = request.form.get("username", "").strip()
    password   = request.form.get("password", "").strip()
    ip         = request.remote_addr

    if not identifier or not password:
        flash("Username and password required.", "error")
        return redirect(url_for("admin.admin_login"))

    allowed, err_msg, remaining = check_login_attempts(identifier, ip)
    if not allowed:
        flash(err_msg, "error")
        return redirect(url_for("admin.admin_login"))

    user = get_user_by_username(identifier)
    if not user and "@" in identifier:
        user = get_user_by_email(identifier.lower())

    if not user:
        _fail(identifier, ip, remaining)
        return redirect(url_for("admin.admin_login"))

    stored = str(user.get("password","")).strip()
    ok = verify_password(password, stored) if is_password_hashed(stored) else (stored == password)
    if not ok:
        _fail(identifier, ip, remaining)
        return redirect(url_for("admin.admin_login"))

    role = str(user.get("role","")).lower()
    if "admin" not in role:
        flash("You do not have admin access.", "error")
        return redirect(url_for("admin.admin_login"))

    clear_login_attempts(identifier, ip)

    # Dual-role → portal selection
    if "user" in role and "admin" in role:
        session["pending_user_id"]   = int(user["id"])
        session["pending_username"]  = user.get("username")
        session["pending_full_name"] = user.get("full_name", user.get("username"))
        session["pending_role"]      = role
        return redirect(url_for("auth.select_portal"))

    # Admin-only session
    invalidate_session(int(user["id"]))
    token = secrets.token_urlsafe(32)
    create_session({
        "token":        token,
        "user_id":      int(user["id"]),
        "device_info":  request.headers.get("User-Agent","admin"),
        "is_exam_active": False,
        "admin_session": True,
        "active":       True,
    })
    session.permanent   = True
    session["user_id"]  = int(user["id"])
    session["admin_id"] = int(user["id"])
    session["token"]    = token
    session["username"] = user.get("username")
    session["full_name"]= user.get("full_name", user.get("username"))
    session["is_admin"] = True
    session.modified    = True

    flash("Admin login successful!", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/logout")
def logout():
    import threading
    uid = session.get("user_id")
    tok = session.get("token")
    session.clear()

    def _cleanup():
        try:
            if uid and tok:
                invalidate_session(uid, tok)
        except Exception as e:
            print(f"[admin.logout] cleanup error: {e}")

    threading.Thread(target=_cleanup, daemon=True).start()
    flash("Admin logout successful.", "success")
    return render_template("logout_redirect.html", is_admin=True)


def _fail(identifier, ip, remaining):
    record_failed_login(identifier, ip)
    allowed, err_msg, rem = check_login_attempts(identifier, ip)
    if not allowed:
        flash(err_msg, "error")
    elif rem > 0:
        flash(f"Invalid credentials! {rem} attempts remaining.", "error")
    else:
        flash("Invalid credentials!", "error")