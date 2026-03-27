"""
app/routes/admin/dashboard.py
Admin dashboard and publish (cache-clear) routes.
"""

import time
from flask import render_template, redirect, url_for, flash, session, request

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams
from app.utils.cache import set_force_refresh
from app.services.drive_service import clear_all_caches
from app.db.users import get_users_count, get_admins_count
from app.db.exams import get_all_exams

@admin_bp.route("/dashboard")
@require_admin_role
def dashboard():
    exams        = get_all_exams()
    total_users  = get_users_count()
    total_admins = get_admins_count()

    return render_template("admin/dashboard.html", stats={
        "total_exams":  len(exams),
        "total_users":  total_users,
        "total_admins": total_admins,
    })


@admin_bp.route("/publish", methods=["GET", "POST"])
@require_admin_role
def publish():
    if request.method == "POST":
        try:
            clear_all_caches()
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