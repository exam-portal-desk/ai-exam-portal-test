"""
app/routes/web/admin/dashboard.py
Admin dashboard and publish (cache-clear) routes.
"""

import time
from flask import render_template, redirect, url_for, flash, session, request

from app import entitlements
from app.entitlements import has_admin_permission
from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role, require_admin_permission
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
        {"icon": "fa-layer-group",     "label": "Manage Categories", "desc": "Organize categories & subcategories", "endpoint": "admin.categories", "feature": "category_management"},
        {"icon": "fa-file-alt",        "label": "Manage Exams",      "desc": "Create, edit and publish exams",       "endpoint": "admin.exams", "feature": "exam_management"},
        {"icon": "fa-book",            "label": "Manage Subjects",   "desc": "Add or remove image subjects",         "endpoint": "admin.subjects", "feature": "subject_management"},
        {"icon": "fa-question-circle", "label": "Manage Questions",  "desc": "Add, edit and import questions",       "endpoint": "admin.questions_index", "feature": "question_management"},
        {"icon": "fa-images",          "label": "Upload Images",     "desc": "Manage question image files",          "endpoint": "admin.upload_images_page", "feature": "question_management"},
        {"icon": "fa-square-root-alt", "label": "LaTeX Editor",      "desc": "Compose questions with LaTeX",         "endpoint": "admin.latex_editor", "feature": "question_management", "popup": True},
        {"icon": "fa-hdd",             "label": "Object Storage",    "desc": "Browse stored files & images",         "endpoint": "admin.object_storage", "feature": "storage_management"},
        {"icon": "fa-robot",           "label": "AI Command Centre", "desc": "Generate questions with AI",           "endpoint": "admin.ai_command_centre", "feature": "question_generation"},
    ]},
    {"name": "Management", "modules": [
        {"icon": "fa-user-plus", "label": "Users & Requests", "desc": "Approve or deny access requests", "endpoint": "admin.requests_dashboard", "feature": "user_management"},
        {"icon": "fa-list-ol",   "label": "Manage Attempts",  "desc": "View every exam attempt",         "endpoint": "admin.attempts", "feature": "attempt_management"},
    ]},
    {"name": "Analytics", "modules": [
        {"icon": "fa-chart-line", "label": "Users Analytics", "desc": "Performance insights per student", "endpoint": "admin.users_analytics", "feature": "user_analytics"},
    ]},
    {"name": "Account", "modules": [
        {"icon": "fa-book-open",   "label": "Admin Guide",       "desc": "Learn how to run the portal",  "endpoint": "admin.guide"},
        {"icon": "fa-user-circle", "label": "Profile & Account", "desc": "Manage your admin account",    "endpoint": "admin.profile"},
    ]},
]


@admin_bp.route("/dashboard")
@require_admin_role
def dashboard():
    # A module is listed only if the admin has its feature (modules with no feature, like the guide, are for every admin).
    module_groups = [g for g in (
        {"name": g["name"], "modules": [
            {**m, "url": url_for(m["endpoint"])} for m in g["modules"]
            if "feature" not in m or has_admin_permission(session.get("user_id"), m["feature"])
        ]}
        for g in ADMIN_MODULE_GROUPS
    ) if g["modules"]]

    # Every figure on this page belongs to an admin feature: an administrator sees (and the server queries) only the figures
    # their granted features cover, never portal-wide numbers for something they have not been given.
    uid = session.get("user_id")
    can = {name: has_admin_permission(uid, name) for name in (
        "category_management", "exam_management", "subject_management", "question_management",
        "attempt_management", "user_management", "user_analytics")}
    users_visible = can["user_management"] or can["user_analytics"]

    total_users = get_users_count() if users_visible else 0
    total_admins = get_admins_count() if users_visible else 0
    total_exams = get_exams_count() if can["exam_management"] else 0
    library = {}
    if can["category_management"]:
        library["Categories"] = get_categories_count()
    if can["subject_management"]:
        library["Subjects"] = get_subjects_count()
    if can["exam_management"]:
        library["Exams"] = total_exams
    if can["question_management"]:
        library["Questions"] = get_questions_count()

    def _rows_to_dict(rows):
        return {r["name"]: r["count"] for r in rows}

    charts = {
        # COUNT/GROUP BY aggregate queries only, never a full row fetch, and every number is reused from a single query.
        "content_library": library,
        "user_roles": {"Admins": total_admins, "Students": max(0, total_users - total_admins)} if users_visible else {},
        "question_types": get_questions_by_type_counts() if can["question_management"] else {},
        "attempt_status": get_attempts_status_counts() if can["attempt_management"] else {},
        "request_status": get_requests_status_counts() if can["user_management"] else {},
        "exams_per_category": _rows_to_dict(get_exams_per_category()) if can["exam_management"] else {},
        "top_attempted_exams": _rows_to_dict(get_top_attempted_exams()) if can["attempt_management"] else {},
        "top_question_exams": _rows_to_dict(get_top_exams_by_question_count()) if can["exam_management"] else {},
    }

    return render_template(
        "admin/dashboard.html",
        greeting=get_greeting(),
        stats={"total_exams": total_exams, "total_users": total_users, "total_admins": total_admins},
        show={"exams": can["exam_management"], "users": users_visible, "library": bool(library),
              "question_types": can["question_management"], "attempts": can["attempt_management"],
              "requests": can["user_management"]},
        module_groups=module_groups,
        no_admin_features=not any("feature" in m for g in module_groups for m in g["modules"]),
        charts=charts,
    )


@admin_bp.route("/guide")
@require_admin_role
def guide():
    # Static documentation; the Plans & Access section is read from the entitlement configuration so it always
    # matches what is in force. No DB queries.
    try:
        plans = entitlements.overview()
    except Exception as e:
        print(f"[admin.guide] plans overview unavailable: {e}")
        plans = None
    return render_template("admin/guide.html", plans=plans)


@admin_bp.route("/guide/scheduled-exams")
@require_admin_role
def guide_scheduled_exams():
    # Dedicated deep-dive page for the Scheduled Exam feature, linked from
    # admin/guide.html — kept separate rather than folded into the main
    # guide since it's long enough to need its own sidebar/TOC.
    return render_template("admin/guide_scheduled_exams.html")


@admin_bp.route("/publish", methods=["GET", "POST"])
@require_admin_permission("exam_management")
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
