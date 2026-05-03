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
