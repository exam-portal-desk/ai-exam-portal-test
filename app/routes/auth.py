"""
app/routes/auth.py
Authentication routes:
  login, logout, register, portal selection, password setup/reset.

DELETE ACCOUNT: now delegates to app.services.user_deletion_service
  for a complete, safe, ordered deletion of all user data.
"""

import secrets
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, jsonify,
)

from app.db.users import (
    get_user_by_username, get_user_by_email,
    get_all_users, create_user,
    update_user,
)
from app.db.sessions import create_session, invalidate_session, set_exam_active
from app.db.auth import (
    check_login_attempts, record_failed_login, clear_login_attempts,
)
from app.services.auth_service import (
    is_password_hashed, verify_password, hash_password,
    validate_password_strength, create_password_token, validate_and_use_token,
)
from app.services.email_service import send_password_setup_email, send_password_reset_email
from app.utils.helpers import generate_username, is_valid_email

auth_bp = Blueprint("auth", __name__)


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password   = request.form.get("password", "").strip()
        ip         = request.remote_addr

        if not identifier or not password:
            flash("Username/email and password required.", "error")
            return redirect(url_for("auth.login"))

        allowed, err_msg, remaining = check_login_attempts(identifier, ip)
        if not allowed:
            flash(err_msg, "error")
            return redirect(url_for("auth.login"))

        user = get_user_by_username(identifier)
        if not user and "@" in identifier:
            user = get_user_by_email(identifier.lower())

        if not user:
            _handle_bad_password(identifier, ip, user_exists=False)
            return redirect(url_for("auth.login"))

        stored = str(user.get("password", "")).strip()
        if not stored:
            flash("Account setup incomplete. Please check your email for a setup link.", "warning")
            return redirect(url_for("auth.login"))

        ok = verify_password(password, stored) if is_password_hashed(stored) else (stored == password)
        if not ok:
            _handle_bad_password(identifier, ip, user_exists=True)
            return redirect(url_for("auth.login"))

        clear_login_attempts(identifier, ip)

        role = str(user.get("role", "")).lower()
        has_user  = "user"  in role
        has_admin = "admin" in role

        if has_admin and has_user:
            session["pending_user_id"]   = int(user["id"])
            session["pending_username"]  = user.get("username")
            session["pending_full_name"] = user.get("full_name", user.get("username"))
            session["pending_role"]      = role
            return redirect(url_for("auth.select_portal"))

        if has_admin and not has_user:
            flash("Please use the admin login portal.", "error")
            return redirect(url_for("admin.admin_login"))

        # User-only session
        _create_user_session(user, role, admin=False)
        flash(f'Welcome {user.get("full_name")}!', "success")
        return redirect(url_for("dashboard.dashboard"))

    return render_template("login.html")


def _handle_bad_password(identifier, ip, user_exists=True):
    if not user_exists:
        flash("User doesn't exist!", "error")
    else:
        if user_exists:
            record_failed_login(identifier, ip)
        allowed, err_msg, remaining = check_login_attempts(identifier, ip)
        if not allowed:
            flash(err_msg, "error")
        elif remaining > 0:
            flash(f"Invalid credentials! {remaining} attempts remaining.", "error")
        else:
            flash("Invalid credentials!", "error")


def _create_user_session(user, role, admin=False):
    invalidate_session(int(user["id"]))
    token = secrets.token_urlsafe(32)
    create_session({
        "token": token,
        "user_id": int(user["id"]),
        "device_info": request.headers.get("User-Agent", "unknown"),
        "is_exam_active": False,
        "admin_session": admin,
        "active": True,
    })
    session.permanent = True
    session["user_id"]   = int(user["id"])
    session["token"]     = token
    session["username"]  = user.get("username")
    session["full_name"] = user.get("full_name", user.get("username"))
    session["role"]      = role
    if admin:
        session["admin_id"] = int(user["id"])
        session["is_admin"] = True
    session.modified = True


# ─────────────────────────────────────────────
# Portal selection (dual-role users)
# ─────────────────────────────────────────────

@auth_bp.route("/select-portal", methods=["GET", "POST"])
def select_portal():
    if not session.get("pending_user_id"):
        flash("Please login first.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        portal     = request.form.get("portal", "").strip()
        user_id    = session.pop("pending_user_id", None)
        username   = session.pop("pending_username", None)
        full_name  = session.pop("pending_full_name", None)
        role       = session.pop("pending_role", "user")

        if not user_id:
            flash("Session expired. Please login again.", "error")
            return redirect(url_for("auth.login"))

        user = {"id": user_id, "username": username, "full_name": full_name, "role": role}
        is_admin = portal == "admin"
        _create_user_session(user, role, admin=is_admin)

        if is_admin:
            flash(f"Welcome {full_name}! You are in the Admin Portal.", "success")
            return redirect(url_for("admin.dashboard"))

        flash(f"Welcome {full_name}! You are in the User Portal.", "success")
        return redirect(url_for("dashboard.dashboard"))

    return render_template("select_portal.html")


# ─────────────────────────────────────────────
# Logout
# ─────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    import threading
    uid = session.get("user_id")
    tok = session.get("token")

    def _cleanup():
        try:
            if uid and tok:
                invalidate_session(uid, tok)
                set_exam_active(tok, is_active=False)
            from chat import _set_offline
            if uid:
                _set_offline(uid)
        except Exception as e:
            print(f"[auth] logout cleanup error: {e}")

    session.clear()
    threading.Thread(target=_cleanup, daemon=True).start()
    flash("Logged out successfully.", "success")
    return render_template("logout_redirect.html")


# ─────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────

@auth_bp.route("/create_account", methods=["GET", "POST"])
def create_account():
    if request.method == "POST":
        email      = request.form.get("email", "").strip().lower()
        first_name = request.form.get("first_name", "").strip()
        last_name  = request.form.get("last_name", "").strip()

        if not email or not first_name or not last_name:
            flash("All fields are required.", "error")
            return redirect(url_for("auth.create_account"))

        if not is_valid_email(email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("auth.create_account"))

        full_name = f"{first_name} {last_name}".strip()

        all_users = get_all_users()
        if any(str(u.get("email","")).lower() == email for u in all_users):
            # Silent success (prevent email enumeration)
            return redirect(url_for("auth.registration_success"))

        existing_usernames = {str(u.get("username","")).lower() for u in all_users}
        username = generate_username(full_name, existing_usernames)

        admin_exists = any("admin" in str(u.get("role","")).lower() for u in all_users)
        role = "user" if admin_exists else "admin"

        created = create_user({
            "username": username,
            "email": email,
            "full_name": full_name,
            "password": "",
            "role": role,
        })

        if created:
            try:
                token = create_password_token(email, "setup")
                send_password_setup_email(email, full_name, username, token)
            except Exception as e:
                print(f"[auth] setup email error: {e}")
            flash("Account created! Check your email for setup instructions.", "success")
        else:
            flash("Registration failed. Please try again.", "error")

        return redirect(url_for("auth.registration_success"))

    return render_template("create_account.html",
                           email=request.args.get("email",""),
                           first_name=request.args.get("first_name",""),
                           last_name=request.args.get("last_name",""))


@auth_bp.route("/registration-success")
def registration_success():
    return render_template("registration_success.html")


# ─────────────────────────────────────────────
# Password setup (new users)
# ─────────────────────────────────────────────

@auth_bp.route("/setup-password/<token>", methods=["GET", "POST"])
def setup_password(token):
    if request.method == "POST":
        new_pw  = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()

        if not new_pw or new_pw != conf_pw:
            flash("Passwords do not match.", "error")
            return render_template("password_setup_form.html", token=token)

        ok, msg = validate_password_strength(new_pw)
        if not ok:
            flash(msg, "error")
            return render_template("password_setup_form.html", token=token)

        valid, vmsg, token_data = validate_and_use_token(token)
        if not valid:
            flash(vmsg, "error")
            return redirect(url_for("auth.login"))

        if token_data.get("type") != "setup":
            flash("Invalid setup token.", "error")
            return redirect(url_for("auth.login"))

        user = get_user_by_email(token_data["email"])
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("auth.login"))

        if update_user(user["id"], {
            "password": hash_password(new_pw),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }):
            flash(f"Password set succesfully!", "success")
        else:
            flash("Failed to set password. Please try again.", "error")

        return redirect(url_for("auth.login"))

    # GET — validate token without using it
    td = _validate_token_for_display(token, "setup")
    if td is None:
        return redirect(url_for("auth.login"))
    return render_template("password_setup_form.html", token=token, email=td.get("email",""))


# ─────────────────────────────────────────────
# Password reset
# ─────────────────────────────────────────────

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password_page():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if email:
            user = get_user_by_email(email)
            if user:
                try:
                    token = create_password_token(email, "reset")
                    send_password_reset_email(
                        email,
                        user.get("full_name", "User"),
                        user.get("username", ""),
                        token,
                    )
                except Exception as e:
                    print(f"[auth] reset email error: {e}")
        flash(
            "If an account exists with this email, a reset link has been sent. "
            "Please check your inbox and spam folder.",
            "success",
        )
        return redirect(url_for("auth.reset_password_page"))
    return render_template("password_reset.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_with_token(token):
    if request.method == "POST":
        new_pw  = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()

        if not new_pw or new_pw != conf_pw:
            flash("Passwords do not match.", "error")
            return render_template("password_reset_form.html", token=token)

        ok, msg = validate_password_strength(new_pw)
        if not ok:
            flash(msg, "error")
            return render_template("password_reset_form.html", token=token)

        valid, vmsg, token_data = validate_and_use_token(token)
        if not valid:
            flash(vmsg, "error")
            return redirect(url_for("auth.login"))

        if token_data.get("type") != "reset":
            flash("Invalid reset token.", "error")
            return redirect(url_for("auth.login"))

        user = get_user_by_email(token_data["email"])
        if not user or not update_user(user["id"], {
            "password": hash_password(new_pw),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }):
            flash("Failed to update password. Please try again.", "error")
            return render_template("password_reset_form.html", token=token)

        flash("Password updated! You can now login.", "success")
        return redirect(url_for("auth.login"))

    td = _validate_token_for_display(token, "reset")
    if td is None:
        return redirect(url_for("auth.login"))
    return render_template("password_reset_form.html", token=token, email=td.get("email",""))


@auth_bp.route("/api/request-password-reset", methods=["POST"])
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
                print(f"[auth] api reset error: {e}")
    return jsonify({"success": True,
                    "message": "If an account exists with this email, a reset link has been sent."})


# ─────────────────────────────────────────────
# Access request (public — no login needed)
# ─────────────────────────────────────────────

@auth_bp.route("/request-admin-access")
def request_admin_access_page():
    return render_template("request_admin_access.html")


@auth_bp.route("/api/validate-user-for-request", methods=["POST"])
def api_validate_user_for_request():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    email    = data.get("email", "").strip().lower()

    if not username or not email:
        return jsonify({"success": False, "message": "Username and email are required"}), 400

    user = get_user_by_username(username)
    if not user or str(user.get("email","")).lower() != email:
        return jsonify({"success": False, "message": "User not found"}), 404

    from app.db.misc import get_requests_by_user
    current_access = str(user.get("role","user")).strip().lower()
    reqs = get_requests_by_user(username, email)

    formatted = [
        {"request_id": int(r.get("request_id",0)),
         "requested_access": r.get("requested_access",""),
         "request_date": str(r.get("request_date","")),
         "status": r.get("request_status",""),
         "reason": r.get("reason","") or ""}
        for r in reqs
    ]

    available = []
    if current_access == "user":
        available = ["admin","user,admin"]
    elif current_access == "admin":
        available = ["user","user,admin"]

    has_pending = any(r["status"] == "pending" for r in formatted)

    return jsonify({
        "success": True,
        "user": {"username": username, "email": email,
                 "current_access": current_access,
                 "full_name": user.get("full_name", username)},
        "requests": formatted,
        "available_requests": available,
        "has_pending_request": has_pending,
        "can_request": bool(available) and not has_pending,
    })


@auth_bp.route("/api/submit-access-request", methods=["POST"])
def api_submit_access_request():
    data = request.get_json() or {}
    required = ["username","email","current_access","requested_access"]
    for f in required:
        if not data.get(f):
            return jsonify({"success": False, "message": f"{f} is required"}), 400

    username         = data["username"].strip()
    email            = data["email"].strip().lower()
    current_access   = data["current_access"].strip().lower()
    requested_access = data["requested_access"].strip().lower()
    user_reason      = data.get("user_reason","").strip()

    if not user_reason:
        return jsonify({"success": False, "message": "Please provide a reason"}), 400

    from app.db.misc import get_requests_by_user, create_request
    pending = [r for r in get_requests_by_user(username, email) if r.get("request_status") == "pending"]
    if pending:
        return jsonify({"success": False, "message": "You already have a pending request"}), 400

    new_req = {
        "username": username, "email": email,
        "current_access": current_access, "requested_access": requested_access,
        "request_date": datetime.now().isoformat(),
        "request_status": "pending",
        "reason": f"[USER REQUEST] {user_reason}",
    }
    created = create_request(new_req)
    if not created:
        return jsonify({"success": False, "message": "Failed to save request"}), 500

    return jsonify({"success": True,
                    "message": "Request submitted. Please wait for admin approval.",
                    "request_id": int(created.get("request_id",0))})


# ─────────────────────────────────────────────
# Delete account  ← REFACTORED
# ─────────────────────────────────────────────

@auth_bp.route("/api/delete-account", methods=["POST"])
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


# ─────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────

def _validate_token_for_display(token: str, expected_type: str):
    """Validate token for GET display — does NOT mark it used."""
    from app.db.auth import get_password_token
    td = get_password_token(token)
    if not td:
        flash("Invalid link.", "error"); return None
    if td.get("used"):
        flash("This link has already been used.", "error"); return None
    if td.get("type") != expected_type:
        flash("Invalid link type.", "error"); return None
    try:
        exp = datetime.fromisoformat(str(td["expires_at"]))
    except Exception:
        try:
            exp = datetime.strptime(td["expires_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            flash("Link expiry unreadable.", "error"); return None
    if datetime.now() > exp:
        flash("This link has expired.", "error"); return None
    return td