"""
app/routes/web/auth.py
Authentication web pages:
  login, logout, register, portal selection, password setup/reset pages,
  Google OAuth (Sign in with Google).

JSON endpoints that used to live in this file (password-reset API,
access-request validation/submission, delete-account) now live in
app/routes/api/v01/auth.py and app/routes/api/v01/access_requests.py.

DELETE ACCOUNT: delegates to app.services.user_deletion_service
  for a complete, safe, ordered deletion of all user data.
"""

import secrets
from datetime import datetime, timedelta
from app.utils.datetime_service import now_utc_naive

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash,
)

import app.config as config
from app.db.users import (
    get_user_by_username, get_user_by_email,
    get_user_by_google_id, get_all_users, create_user,
    update_user, get_user_by_id, update_last_login,
)
from app.db.password_history import get_recent_password_hashes, record_password_history
from app.db.sessions import create_session, invalidate_session, set_exam_active, has_active_session
from app.db.auth import (
    check_login_attempts, record_failed_login, clear_login_attempts, mark_token_used,
    count_recent_otp_challenges, create_otp_challenge, get_otp_challenge,
    increment_otp_attempts, delete_otp_challenge,
)
from app.services.auth_service import (
    is_password_hashed, verify_password, hash_password,
    validate_password_strength, create_password_token,
    is_password_reused, generate_otp_code,
)
from app.services.email_service import (
    send_password_setup_email, send_password_reset_email, send_otp_verification_email,
)
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

        # User-only session — if this account already has another active
        # session, don't silently kick it out: show a confirmation prompt
        # first. OTP is only generated once the user explicitly opts in
        # (see verify_session_start) — never on this detection alone.
        if has_active_session(int(user["id"])):
            return _pending_session_conflict(user, role, admin=False)

        _create_user_session(user, role, admin=False)
        flash(f'Welcome back, {user.get("full_name")}!', "success")
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
    previous_login = update_last_login(int(user["id"]))
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
    session["profile_photo_key"] = user.get("profile_photo_key")
    session["last_login_display"] = previous_login
    if admin:
        session["admin_id"] = int(user["id"])
        session["is_admin"] = True
    session.modified = True


# ─────────────────────────────────────────────
# Existing Active Session — email OTP verification
# ─────────────────────────────────────────────

def _pending_session_conflict(user, role, admin):
    """A login/portal choice found another active session already on file.
    Stash the pending identity and show the confirmation prompt — NO OTP is
    generated here. Falls back to the old (auto-invalidate) behaviour only
    if the account has no email on file to verify with."""
    if not user.get("email"):
        _create_user_session(user, role, admin=admin)
        flash(f'Welcome back, {user.get("full_name")}!', "success")
        return redirect(url_for("admin.dashboard" if admin else "dashboard.dashboard"))

    session.pop("otp_challenge_id", None)
    session["otp_user_id"] = int(user["id"])
    session["otp_role"] = role
    session["otp_admin"] = admin
    session["otp_email"] = user["email"]
    session.modified = True
    return redirect(url_for("auth.verify_session"))


def _issue_otp_challenge(user_id: int, email: str, full_name: str) -> bool:
    """Generate, store, and email a fresh OTP challenge for a pending
    conflict the user has explicitly opted to continue past. Only called
    from verify_session_start / verify_session_resend — never on session-
    conflict detection alone. Returns False (with a flash already set) if
    the rate limit is hit or persistence fails."""
    if count_recent_otp_challenges(user_id, config.OTP_RATE_LIMIT_WINDOW_SECONDS) >= config.OTP_MAX_REQUESTS:
        flash("Too many verification attempts. Please try again later.", "error")
        return False

    otp = generate_otp_code(config.OTP_LENGTH)
    expires_at = (now_utc_naive() + timedelta(seconds=config.OTP_EXPIRY_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    challenge = create_otp_challenge(user_id, hash_password(otp), expires_at, config.OTP_RATE_LIMIT_WINDOW_SECONDS)
    if not challenge:
        flash("Could not send verification code. Please try again.", "error")
        return False

    try:
        send_otp_verification_email(email, full_name or "there", otp, max(1, config.OTP_EXPIRY_SECONDS // 60))
    except Exception as e:
        print(f"[auth] otp email error: {e}")

    session["otp_challenge_id"] = challenge["id"]
    session.modified = True
    return True


def _otp_expired(challenge: dict) -> bool:
    return now_utc_naive() > _parse_dt(challenge["expires_at"])


def _parse_dt(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")


def _clear_otp_session():
    for key in ("otp_user_id", "otp_role", "otp_admin", "otp_challenge_id", "otp_email"):
        session.pop(key, None)


@auth_bp.route("/verify-session", methods=["GET", "POST"])
def verify_session():
    user_id = session.get("otp_user_id")
    if not user_id:
        flash("Please login first.", "error")
        return redirect(url_for("auth.login"))

    challenge_id = session.get("otp_challenge_id")

    if request.method == "POST":
        if not challenge_id:
            # Confirmation stage has no OTP field to submit — the "Continue
            # on this device" button posts to verify_session_start instead.
            return redirect(url_for("auth.verify_session"))

        code = "".join(ch for ch in request.form.get("otp", "") if ch.isdigit())[:config.OTP_LENGTH]
        challenge = get_otp_challenge(challenge_id, user_id)

        if not challenge or _otp_expired(challenge):
            flash("Your verification code has expired. Please request a new one.", "error")
            return redirect(url_for("auth.verify_session"))

        if len(code) != config.OTP_LENGTH or not verify_password(code, challenge["otp_hash"]):
            attempts = increment_otp_attempts(challenge_id, config.OTP_MAX_VERIFY_ATTEMPTS)
            if attempts >= config.OTP_MAX_VERIFY_ATTEMPTS:
                # Leave the row in place (still counts toward the request
                # rate limit) but this challenge is dead — force a fresh
                # login rather than allowing further guesses against it.
                _clear_otp_session()
                flash("Too many incorrect attempts. Please login again.", "error")
                return redirect(url_for("auth.login"))
            flash("Incorrect code. Please try again.", "error")
            return redirect(url_for("auth.verify_session"))

        # Correct code — only now do we touch the old device's session.
        delete_otp_challenge(challenge_id)
        user = get_user_by_id(user_id)
        if not user:
            _clear_otp_session()
            flash("Account not found.", "error")
            return redirect(url_for("auth.login"))

        role = session.get("otp_role") or str(user.get("role", "")).lower()
        admin = bool(session.get("otp_admin"))
        _clear_otp_session()
        _create_user_session(user, role, admin=admin)
        flash(f'Welcome back, {user.get("full_name")}!', "success")
        return redirect(url_for("admin.dashboard" if admin else "dashboard.dashboard"))

    # GET — confirmation stage (no challenge issued yet) or OTP-entry stage.
    if not challenge_id:
        return render_template("verify_session.html", stage="confirm", email=session.get("otp_email", ""))

    challenge = get_otp_challenge(challenge_id, user_id)
    expiry_remaining, resend_remaining = 0, 0
    if not challenge or _otp_expired(challenge):
        flash("Your verification code has expired. Please request a new one.", "warning")
    else:
        expiry_remaining = max(0, int((_parse_dt(challenge["expires_at"]) - now_utc_naive()).total_seconds()))
        elapsed = int((now_utc_naive() - _parse_dt(challenge["created_at"])).total_seconds())
        resend_remaining = max(0, config.OTP_RESEND_COOLDOWN_SECONDS - elapsed)

    return render_template(
        "verify_session.html", stage="otp",
        email=session.get("otp_email", ""),
        otp_length=config.OTP_LENGTH,
        expiry_seconds=expiry_remaining,
        resend_cooldown=resend_remaining,
    )


@auth_bp.route("/verify-session/start", methods=["POST"])
def verify_session_start():
    """The user's explicit "Continue on this device / Reset session"
    choice — the ONLY point where an OTP is generated and emailed."""
    user_id = session.get("otp_user_id")
    email = session.get("otp_email")
    if not user_id or not email:
        flash("Please login first.", "error")
        return redirect(url_for("auth.login"))

    if session.get("otp_challenge_id"):
        return redirect(url_for("auth.verify_session"))

    user = get_user_by_id(user_id)
    if not user:
        _clear_otp_session()
        flash("Account not found.", "error")
        return redirect(url_for("auth.login"))

    if _issue_otp_challenge(user_id, email, user.get("full_name")):
        flash("A verification code has been sent to your email.", "success")
    return redirect(url_for("auth.verify_session"))


@auth_bp.route("/verify-session/resend", methods=["POST"])
def verify_session_resend():
    user_id = session.get("otp_user_id")
    email = session.get("otp_email")
    challenge_id = session.get("otp_challenge_id")
    if not user_id or not email or not challenge_id:
        flash("Please login first.", "error")
        return redirect(url_for("auth.login"))

    current = get_otp_challenge(challenge_id, user_id)
    if current:
        elapsed = (now_utc_naive() - _parse_dt(current["created_at"])).total_seconds()
        if elapsed < config.OTP_RESEND_COOLDOWN_SECONDS:
            flash("Please wait before requesting another code.", "warning")
            return redirect(url_for("auth.verify_session"))

    user = get_user_by_id(user_id)
    if not user:
        _clear_otp_session()
        flash("Account not found.", "error")
        return redirect(url_for("auth.login"))

    if _issue_otp_challenge(user_id, email, user.get("full_name")):
        flash("A new verification code has been sent.", "success")
    return redirect(url_for("auth.verify_session"))


@auth_bp.route("/verify-session/cancel")
def verify_session_cancel():
    """Nothing changes: no session is touched, no OTP is left dangling."""
    _clear_otp_session()
    return redirect(url_for("auth.login"))


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

        # Re-fetch the full row instead of trusting the partial pending_*
        # session fields (id/username/full_name/role only) — those silently
        # dropped every other column, including profile_photo_key, so the
        # new session always started with no avatar regardless of what was
        # actually saved in the DB.
        user = get_user_by_id(user_id) or \
            {"id": user_id, "username": username, "full_name": full_name, "role": role}
        is_admin = portal == "admin"

        # Same "existing active session" gate as login() — a dual-role
        # account choosing a portal must not silently kick out another
        # device either.
        if has_active_session(int(user_id)):
            return _pending_session_conflict(user, role, admin=is_admin)

        _create_user_session(user, role, admin=is_admin)

        if is_admin:
            flash(f"Welcome back, {full_name}!", "success")
            return redirect(url_for("admin.dashboard"))

        flash(f"Welcome back, {full_name}!", "success")
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
            from app.services.chat_service import set_offline
            if uid:
                set_offline(uid)
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

        # Validate the token WITHOUT consuming it — a rejected password
        # (mismatch/weak/reused) must not burn the token, so the same link
        # stays usable for a corrected resubmission. Only mark it used once
        # the password has actually been changed, below.
        token_data = _validate_token_for_display(token, "setup")
        if token_data is None:
            return redirect(url_for("auth.login"))

        user = get_user_by_email(token_data["email"])
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("auth.login"))

        old_hash = str(user.get("password", "")).strip()
        history_hashes = get_recent_password_hashes(user["id"])
        if is_password_reused(new_pw, old_hash, history_hashes):
            flash("You can't reuse any of your last 3 passwords. Please choose a different one.", "error")
            return render_template("password_setup_form.html", token=token)

        if old_hash:
            record_password_history(user["id"], old_hash)

        if update_user(user["id"], {
            "password": hash_password(new_pw),
            "updated_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        }):
            mark_token_used(token)
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

        # Validate the token WITHOUT consuming it — see the matching comment
        # in setup_password() above for why.
        token_data = _validate_token_for_display(token, "reset")
        if token_data is None:
            return redirect(url_for("auth.login"))

        user = get_user_by_email(token_data["email"])
        if not user:
            flash("Failed to update password. Please try again.", "error")
            return render_template("password_reset_form.html", token=token)

        old_hash = str(user.get("password", "")).strip()
        history_hashes = get_recent_password_hashes(user["id"])
        if is_password_reused(new_pw, old_hash, history_hashes):
            flash("You can't reuse any of your last 3 passwords. Please choose a different one.", "error")
            return render_template("password_reset_form.html", token=token)

        if old_hash:
            record_password_history(user["id"], old_hash)

        if not update_user(user["id"], {
            "password": hash_password(new_pw),
            "updated_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        }):
            flash("Failed to update password. Please try again.", "error")
            return render_template("password_reset_form.html", token=token)

        mark_token_used(token)
        flash("Password updated! You can now login.", "success")
        return redirect(url_for("auth.login"))

    td = _validate_token_for_display(token, "reset")
    if td is None:
        return redirect(url_for("auth.login"))
    return render_template("password_reset_form.html", token=token, email=td.get("email",""))



# ─────────────────────────────────────────────
# Google OAuth — Sign in with Google
# ─────────────────────────────────────────────

def _get_google_oauth():
    """Return the registered Authlib Google client, or None if not configured."""
    try:
        from authlib.integrations.flask_client import OAuth
        from flask import current_app
        # Authlib stores the OAuth instance under this extension key
        oauth = current_app.extensions.get("authlib.integrations.flask_client")
        if oauth is None:
            return None
        client = oauth.google
        return client
    except Exception as e:
        print(f"[auth] _get_google_oauth error: {e}")
        return None


@auth_bp.route("/auth/google")
def google_login():
    """Initiate Google OAuth flow."""
    google = _get_google_oauth()
    if google is None:
        flash("Google login is not configured. Please use email/password.", "warning")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("auth.google_callback", _external=True, _scheme="https")
    return google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def google_callback():
    """Handle Google OAuth callback."""
    google = _get_google_oauth()
    if google is None:
        flash("Google login unavailable. Please use email/password.", "error")
        return redirect(url_for("auth.login"))

    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")
        if not user_info:
            user_info = google.userinfo()
    except Exception as e:
        print(f"[auth] Google OAuth callback error: {e}")
        flash("Google login failed. Please try again or use email/password.", "error")
        return redirect(url_for("auth.login"))

    if not user_info:
        flash("Could not retrieve your Google profile. Please try again.", "error")
        return redirect(url_for("auth.login"))

    google_id = str(user_info.get("sub", ""))
    email = str(user_info.get("email", "")).strip().lower()
    given_name = str(user_info.get("given_name", "")).strip()
    family_name = str(user_info.get("family_name", "")).strip()
    full_name = f"{given_name} {family_name}".strip() or str(user_info.get("name", "")).strip()

    if not email or not google_id:
        flash("Google did not provide required account information.", "error")
        return redirect(url_for("auth.login"))

    # ── Try to find existing user ──────────────────────────────────────────
    user = get_user_by_google_id(google_id)

    if not user:
        # Fallback: email match (handles users who registered via email first)
        user = get_user_by_email(email)
        if user:
            # Link Google ID to existing account
            update_user(int(user["id"]), {
                "google_id": google_id,
                "auth_provider": "google",
                "updated_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
            })

    if not user:
        # ── New user — create account ──────────────────────────────────────
        all_users = get_all_users()
        existing_usernames = {str(u.get("username", "")).lower() for u in all_users}
        username = generate_username(full_name, existing_usernames)

        # First-admin logic: same as create_account()
        admin_exists = any("admin" in str(u.get("role", "")).lower() for u in all_users)
        role = "user" if admin_exists else "admin"

        user = create_user({
            "username": username,
            "email": email,
            "full_name": full_name,
            "password": "",               # No password for OAuth users
            "role": role,
            "google_id": google_id,
            "auth_provider": "google",
        })

        if not user:
            flash("Failed to create your account. Please try again.", "error")
            return redirect(url_for("auth.login"))

        print(f"[auth] New Google user created: {username} ({email})")

    # ── Session handling — mirrors _create_user_session() ─────────────────
    role = str(user.get("role", "")).lower()
    has_user  = "user"  in role
    has_admin = "admin" in role

    if has_admin and has_user:
        # Dual-role — show portal selection
        session["pending_user_id"]   = int(user["id"])
        session["pending_username"]  = user.get("username")
        session["pending_full_name"] = user.get("full_name", user.get("username"))
        session["pending_role"]      = role
        return redirect(url_for("auth.select_portal"))

    if has_admin and not has_user:
        # Admin-only account — send to admin portal
        _create_user_session(user, role, admin=True)
        flash(f'Welcome back, {user.get("full_name")}!', "success")
        return redirect(url_for("admin.dashboard"))

    # Regular user session — Google authentication succeeding is NOT the
    # same as this app's session being switched: same existing-active-
    # session gate as the password login path, so an account already
    # logged in elsewhere gets the confirm-then-OTP flow here too instead
    # of silently kicking out the other device.
    if has_active_session(int(user["id"])):
        return _pending_session_conflict(user, role, admin=False)

    _create_user_session(user, role, admin=False)
    flash(f'Welcome back, {user.get("full_name")}!', "success")
    return redirect(url_for("dashboard.dashboard"))


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
    if now_utc_naive() > exp:
        flash("This link has expired.", "error"); return None
    return td
