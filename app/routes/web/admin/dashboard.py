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
from app.db.categories import get_categories_count, get_exams_per_category
from app.db.misc import get_subjects_count, get_requests_status_counts
from app.db.questions import (
    get_questions_count, get_questions_by_type_counts, get_top_exams_by_question_count,
)
from app.db.attempts import get_attempts_status_counts, get_top_attempted_exams
from app.services.dashboard_service import get_greeting

# Canonical registry of every Admin module — this list, plus the sidebar in
# templates/admin_base.html, are the ONLY two places Admin modules are
# listed. This one drives the Dashboard's "Apps" grid; when a module is
# added to the sidebar, add it here too so the two never drift apart.
ADMIN_MODULE_GROUPS = [
    {"name": "Content", "modules": [
        {"icon": "fa-layer-group",     "label": "Manage Categories", "desc": "Organize categories & subcategories", "endpoint": "admin.categories"},
        {"icon": "fa-file-alt",        "label": "Manage Exams",      "desc": "Create, edit and publish exams",       "endpoint": "admin.exams"},
        {"icon": "fa-book",            "label": "Manage Subjects",   "desc": "Add or remove image subjects",         "endpoint": "admin.subjects"},
        {"icon": "fa-question-circle", "label": "Manage Questions",  "desc": "Add, edit and import questions",       "endpoint": "admin.questions_index"},
        {"icon": "fa-images",          "label": "Upload Images",     "desc": "Manage question image files",          "endpoint": "admin.upload_images_page"},
        {"icon": "fa-square-root-alt", "label": "LaTeX Editor",      "desc": "Compose questions with LaTeX",         "endpoint": "admin.latex_editor", "popup": True},
        {"icon": "fa-hdd",             "label": "Object Storage",    "desc": "Browse stored files & images",         "endpoint": "admin.object_storage"},
        {"icon": "fa-robot",           "label": "AI Command Centre", "desc": "Generate questions with AI",           "endpoint": "admin.ai_command_centre"},
    ]},
    {"name": "Management", "modules": [
        {"icon": "fa-user-plus", "label": "Users & Requests", "desc": "Approve or deny access requests", "endpoint": "admin.requests_dashboard"},
        {"icon": "fa-list-ol",   "label": "Manage Attempts",  "desc": "View every exam attempt",         "endpoint": "admin.attempts"},
    ]},
    {"name": "Analytics", "modules": [
        {"icon": "fa-chart-line", "label": "Users Analytics", "desc": "Performance insights per student", "endpoint": "admin.users_analytics"},
    ]},
    {"name": "Account", "modules": [
        {"icon": "fa-book-open",   "label": "Admin Guide",       "desc": "Learn how to run the portal",  "endpoint": "admin.guide"},
        {"icon": "fa-user-circle", "label": "Profile & Account", "desc": "Manage your admin account",    "endpoint": "admin.profile"},
    ]},
]


@admin_bp.route("/dashboard")
@require_admin_role
def dashboard():
    module_groups = [
        {"name": g["name"], "modules": [
            {**m, "url": url_for(m["endpoint"])} for m in g["modules"]
        ]}
        for g in ADMIN_MODULE_GROUPS
    ]

    total_users     = get_users_count()
    total_admins    = get_admins_count()
    total_exams     = get_exams_count()
    total_categories = get_categories_count()
    total_subjects  = get_subjects_count()
    total_questions = get_questions_count()

    def _rows_to_dict(rows):
        return {r["name"]: r["count"] for r in rows}

    return render_template(
        "admin/dashboard.html",
        greeting=get_greeting(),
        # Only what the 4 primary KPI cards need — everything else now
        # lives in the analytics charts below instead of being repeated
        # a second time in a separate stats list.
        stats={
            "total_exams":  total_exams,
            "total_users":  total_users,
            "total_admins": total_admins,
        },
        module_groups=module_groups,
        charts={
            # COUNT/GROUP BY aggregate queries only — never a full row
            # fetch — and every number here is reused as-is from a single
            # query, nothing is fetched twice.
            "content_library": {
                "Categories": total_categories, "Subjects": total_subjects,
                "Exams": total_exams, "Questions": total_questions,
            },
            "user_roles": {"Admins": total_admins, "Students": max(0, total_users - total_admins)},
            "question_types": get_questions_by_type_counts(),
            "attempt_status": get_attempts_status_counts(),
            "request_status": get_requests_status_counts(),
            "exams_per_category": _rows_to_dict(get_exams_per_category()),
            "top_attempted_exams": _rows_to_dict(get_top_attempted_exams()),
            "top_question_exams": _rows_to_dict(get_top_exams_by_question_count()),
        },
    )


@admin_bp.route("/guide")
@require_admin_role
def guide():
    # Pure static documentation — no DB queries, no payload beyond the
    # template itself, and only loaded when an admin actually opens it.
    return render_template("admin/guide.html")


@admin_bp.route("/guide/scheduled-exams")
@require_admin_role
def guide_scheduled_exams():
    # Dedicated deep-dive page for the Scheduled Exam feature, linked from
    # admin/guide.html — kept separate rather than folded into the main
    # guide since it's long enough to need its own sidebar/TOC.
    return render_template("admin/guide_scheduled_exams.html")


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
