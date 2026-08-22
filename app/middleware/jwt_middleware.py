"""
app/middleware/jwt_middleware.py

HYBRID AUTH MIDDLEWARE
======================
Agar request mein "Authorization: Bearer <token>" header hai toh:
  1. JWT verify karo
  2. session['user_id'], session['role'] etc. populate karo
  3. g.jwt_authenticated = True set karo

Existing routes ko kuch pata nahi chalega — unhe lagega normal session hai.
Web UI (cookie session) bilkul untouched rehta hai.

Register in app/__init__.py:
    from app.middleware.jwt_middleware import init_jwt_middleware
    init_jwt_middleware(app)

OPT-IN WEB SESSION BOOTSTRAP
=============================
session_guard.require_user_role()'s JWT branch only accepts requests that
themselves carry the Authorization header — it never touches the
`sessions` DB table, so a page load without that header (i.e. any normal
browser/WebView navigation) still falls through to the cookie branch,
which requires session['token'] to match an active `sessions` row. A
Bearer-only client that never re-sends that header on every request would
therefore authenticate once and then appear logged out on the very next
page. When the caller explicitly asks for it (via X-Bootstrap-Web-Session,
below), this also creates that DB session row and sets session['token'],
mirroring app/routes/web/auth.py's _create_user_session() — so a client
that performs one Bearer-authenticated request can then browse normally
via the resulting cookie, same as any other web login. Gated behind that
header so ordinary stateless Bearer-only API usage (whoever calls
/api/v01/* with a token on every request and never keeps cookies) is
completely unaffected — without the gate, a client like that would create
a new `sessions` row on every single request.
"""

from flask import request, session, g
from app.services.jwt_service import verify_access_token


def init_jwt_middleware(app):
    """Register before_request hook on the Flask app."""

    @app.before_request
    def _jwt_to_session():
        # Sirf tab kaam karo jab Authorization header ho
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return  # Normal cookie session — kuch mat karo

        token   = auth_header[7:].strip()
        payload = verify_access_token(token)

        if not payload:
            # Invalid/expired token — request aage jayegi
            # session_guard decorator 401 return karega kyunki session empty hoga
            # Lekin hum yahan se bhi seedha 401 de sakte hain:
            g.jwt_invalid = True
            return

        # JWT valid hai — session populate karo
        g.jwt_authenticated = True
        session["user_id"]  = int(payload["uid"])
        session["role"]     = str(payload.get("role", "user"))
        # username/full_name optional — session_guard ko sirf user_id chahiye
        # Agar koi route full_name use karta hai toh DB se fetch hoga automatically

        if request.headers.get("X-Bootstrap-Web-Session") == "1":
            _bootstrap_web_session(session["user_id"], session["role"])


def _bootstrap_web_session(user_id: int, role: str) -> None:
    """
    See the OPT-IN WEB SESSION BOOTSTRAP note above.

    Also accepts an optional X-Selected-Portal: user|admin header — for a
    dual-role account, the caller (e.g. a native picker on a mobile client)
    can specify which portal to bind this session to, same as choosing on
    the web's own /select-portal page. Without it, defaults to deriving
    admin-vs-user purely from the role string, same as before.
    """
    import secrets
    from app.db.sessions import create_session, get_session_by_token, invalidate_session
    from app.db.users import get_user_by_id

    selected_portal = request.headers.get("X-Selected-Portal", "").strip().lower()
    if selected_portal not in ("user", "admin"):
        selected_portal = None
    effective_role = selected_portal or role
    is_admin_portal = (
        selected_portal == "admin" if selected_portal
        else ("admin" in role and "user" not in role)
    )

    existing_token = session.get("token")
    if not (existing_token and get_session_by_token(existing_token)):
        invalidate_session(user_id)
        new_token = secrets.token_urlsafe(32)
        create_session({
            "token":         new_token,
            "user_id":       user_id,
            "device_info":   request.headers.get("User-Agent", "unknown"),
            "is_exam_active": False,
            "admin_session": is_admin_portal,
            "active":        True,
        })
        session.permanent = True
        session["token"] = new_token

    # Mirrors the rest of _create_user_session()'s session keys — without
    # these, session_guard's DB-token check now passes, but the navbar
    # (which trusts bare session values, no DB check — see
    # templates/base.html) still had nothing but a bare user_id/role to
    # show, hence a generic name/no avatar even after a working login.
    user = get_user_by_id(user_id)
    if user:
        session["username"]          = user.get("username")
        session["full_name"]         = user.get("full_name", user.get("username"))
        session["role"]              = effective_role
        session["profile_photo_key"] = user.get("profile_photo_key")
        if is_admin_portal:
            session["admin_id"]  = user_id
            session["is_admin"] = True
