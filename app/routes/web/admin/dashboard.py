"""
app/routes/web/admin/dashboard.py
Admin dashboard and publish (cache-clear) routes.
"""

import time
from flask import render_template, redirect, url_for, flash, session, request

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exams_count
from app.utils.cache import set_force_refresh, clear_all as clear_app_cache
from app.db.users import get_users_count, get_admins_count

@admin_bp.route("/dashboard")
@require_admin_role
def dashboard():
    return render_template("admin/dashboard.html", stats={
        # COUNT queries instead of fetching every exam/user row just to
        # take len() of the result (flagged in the architecture audit).
        "total_exams":  get_exams_count(),
        "total_users":  get_users_count(),
        "total_admins": get_admins_count(),
    })


@admin_bp.route("/publish", methods=["GET", "POST"])
@require_admin_role
def publish():
    if request.method == "POST":
        try:
            clear_app_cache()
            set_force_refresh(True)

            try:
                from flask import current_app
                current_app.config["FORCE_REFRESH_TIMESTAMP"] = time.time()
            except Exception:
                pass

            session["force_refresh"] = True
            session.modified = True

            flash("✅ All caches cleared! Fresh data and images will load now.", "success")
        except Exception as e:
            print(f"[admin.publish] error: {e}")
            flash("⚠️ Cache clear completed with some errors.", "warning")
        return redirect(url_for("admin.dashboard"))

    return render_template("admin/publish.html")
