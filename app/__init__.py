"""
app/__init__.py
Flask application factory.
Registers all blueprints, extensions, and socket events.
"""

import os
import gc
import threading
import tempfile
from datetime import timedelta

from flask import Flask, request
from flask_session import Session
from flask_socketio import SocketIO

import config

# ─── Single global SocketIO instance ───────────────────────────────────────
socketio = SocketIO()


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )

    # ── Core config ────────────────────────────────────────────────────────
    app.secret_key = config.SECRET_KEY

    # ── Server-side session ────────────────────────────────────────────────
    os.makedirs(config.SESSION_FILE_DIR, exist_ok=True)
    app.config["SESSION_TYPE"] = config.SESSION_TYPE
    app.config["SESSION_FILE_DIR"] = config.SESSION_FILE_DIR
    app.config["SESSION_PERMANENT"] = False
    app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
    app.config["SESSION_COOKIE_HTTPONLY"] = config.SESSION_COOKIE_HTTPONLY
    app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE
    Session(app)

    # ── SocketIO ───────────────────────────────────────────────────────────
    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode="gevent",
        manage_session=False,
    )

    # ── Google OAuth (Authlib) ─────────────────────────────────────────────
    _init_google_oauth(app)

    # ── GC tuning ──────────────────────────────────────────────────────────
    gc.set_threshold(700, 10, 10)

    # ── Register blueprints ────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Register socket events ─────────────────────────────────────────────
    _register_socket_events()

    # ── After-request: disable browser caching ─────────────────────────────
    @app.after_request
    def add_cache_control(response):
        if not app.config.get("TESTING") and not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, "
                "post-check=0, pre-check=0, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # ── Before-request: portal conflict guard ──────────────────────────────
    @app.before_request
    def portal_guard():
        from flask import session, redirect, url_for, flash

        skip_prefixes = (
            "/static/", "/login", "/admin/login",
            "/", "/home", "/forgot-password", "/reset-password",
            "/request-admin-access", "/favicon.ico", "/api/", "/dashboard",
        )
        if any(request.path.startswith(p) for p in skip_prefixes):
            return

        if (
            request.path.startswith("/admin/")
            and session.get("user_id")
            and not session.get("admin_id")
        ):
            flash("Please login as Admin to access Admin portal.", "warning")
            return redirect(url_for("auth.login"))

    # ── Context processor ──────────────────────────────────────────────────
    from datetime import datetime

    @app.context_processor
    def inject_globals():
        return {"CURRENT_YEAR": datetime.now().year}

    # ── Error handlers ─────────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── Periodic background cache cleanup ──────────────────────────────────
    _start_periodic_cleanup()

    # ── Initialize Drive service once at startup ───────────────────────────
    _init_drive_at_startup()

    return app


# ───────────────────────────────────────────────────────────────────────────
# Private helpers
# ───────────────────────────────────────────────────────────────────────────

def _register_blueprints(app: Flask) -> None:
    """Import and register every blueprint."""
    from flask import request  # needed inside after_request / before_request

    # User-facing routes
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.exam import exam_bp
    from app.routes.result import result_bp
    from app.routes.ai_assistant import ai_bp
    from app.routes.misc import misc_bp

    # Chat & discussion (kept as standalone blueprints)
    from chat import chat_bp
    from discussion import discussion_bp

    # Admin routes
    from app.routes.admin import admin_bp as new_admin_bp

    # LaTeX editor (kept simple)
    from latex_editor import latex_bp

    from app.routes.categories import categories_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(exam_bp)
    app.register_blueprint(result_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(misc_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(discussion_bp)
    app.register_blueprint(new_admin_bp, url_prefix="/admin")
    app.register_blueprint(latex_bp)


def _register_socket_events() -> None:
    """Wire up SocketIO event handlers for chat and discussion."""
    from chat import init_chat_socketio, register_chat_socketio_events
    from discussion import init_socketio, register_socketio_events

    init_socketio(socketio)
    register_socketio_events(socketio)
    init_chat_socketio(socketio)
    register_chat_socketio_events(socketio)


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template, request, jsonify, session, redirect, url_for, flash
    from datetime import datetime

    @app.errorhandler(404)
    def not_found(e):
        try:
            return render_template("error.html", error_code=404, error_message="Page not found"), 404
        except Exception:
            return "404 - Page not found", 404

    @app.errorhandler(500)
    def server_error(e):
        try:
            return render_template("error.html", error_code=500, error_message="Internal server error"), 500
        except Exception:
            return "500 - Internal server error", 500

    @app.errorhandler(Exception)
    def handle_global(e):
        import traceback
        print(f"GLOBAL ERROR: {e}")
        traceback.print_exc()

        if request.is_json or "/api/" in request.path:
            return {"error": "Server error occurred"}, 500

        flash("A system error occurred. Please try again.", "error")
        if "/admin/" in request.path:
            return redirect(url_for("admin.admin_login"))
        return redirect(url_for("auth.login"))


def _start_periodic_cleanup() -> None:
    """Run cache cleanup every 5 minutes in a background daemon thread."""
    from app.utils.cache import cleanup_app_cache

    def _loop():
        import time
        while True:
            try:
                time.sleep(300)
                cleanup_app_cache()
            except Exception as e:
                print(f"[CLEANUP] Error: {e}")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def _init_google_oauth(app: Flask) -> None:
    """Register Google as an Authlib OAuth provider."""
    try:
        if not config.GOOGLE_OAUTH_CLIENT_ID or not config.GOOGLE_OAUTH_CLIENT_SECRET:
            print("ℹ️  Google OAuth: GOOGLE_OAUTH_CLIENT_ID/SECRET not set — Sign in with Google disabled")
            return

        from authlib.integrations.flask_client import OAuth
        oauth = OAuth()
        oauth.init_app(app)
        oauth.register(
            name="google",
            client_id=config.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid email profile",
                "prompt": "select_account",
            },
        )
        print("✅ Google OAuth: ACTIVE")
    except Exception as e:
        print(f"❌ Google OAuth init error: {e}")


def _init_drive_at_startup() -> None:
    """Initialize Google Drive service once when the process starts."""
    try:
        from app.services.drive_service import init_drive_service
        if init_drive_service():
            print("✅ Google Drive integration: ACTIVE")
        else:
            print("❌ Google Drive integration: INACTIVE — app runs in limited mode")
    except Exception as e:
        print(f"❌ Drive init error at startup: {e}")