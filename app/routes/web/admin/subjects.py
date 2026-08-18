"""
app/routes/web/admin/subjects.py
Admin subject management. Pure HTML/redirect flows — no JSON API for this
domain, no API split needed.

subject_folder_id is a stable per-subject key used to route bulk image
uploads into the right storage prefix ("SubjectName/file.ext" — object
storage has no real folders, so there is nothing to create/rename/delete
server-side; the subject's own name IS the key). Kept as a separate column
(rather than switching every caller to subject_name directly) to avoid
touching the DB schema or the several places that already key off
subject_folder_id (see app/db/misc.py, the upload-images page, and the
bulk image upload API).
"""

from flask import render_template, request, redirect, url_for, flash

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import get_all_subjects, get_subject_by_id, create_subject, update_subject, delete_subject
from app.utils.datetime_service import now_utc_naive


@admin_bp.route("/subjects", methods=["GET", "POST"])
@require_admin_role
def subjects():
    if request.method == "POST":
        name = request.form.get("subject_name","").strip()
        if not name:
            flash("Subject name required.", "danger")
            return redirect(url_for("admin.subjects"))

        from app.db.misc import get_subject_by_name
        if get_subject_by_name(name):
            flash("Subject already exists.", "warning")
            return redirect(url_for("admin.subjects"))

        create_subject({
            "subject_name": name,
            "subject_folder_id": name,
            "subject_folder_created_at": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        })
        flash(f"Subject '{name}' created.", "success")
        return redirect(url_for("admin.subjects"))

    return render_template("admin/subjects.html", subjects=get_all_subjects())


@admin_bp.route("/subjects/edit/<int:subject_id>", methods=["POST"])
@require_admin_role
def edit_subject(subject_id):
    # Single-row lookup instead of fetching every subject and scanning in
    # Python for the one we need (flagged in the architecture audit).
    subject = get_subject_by_id(subject_id)
    if not subject:
        flash("Subject not found.", "danger")
        return redirect(url_for("admin.subjects"))

    new_name = request.form.get("subject_name","").strip()
    if not new_name:
        flash("Name required.", "danger")
        return redirect(url_for("admin.subjects"))

    # Existing question image_path values ("OldName/file.png") are static
    # strings set at upload time and are not retroactively updated by a
    # rename — same limitation the previous Drive-folder-based scheme had
    # (folder ids were resolved by *current* subject name too), so this
    # isn't a new behavior change.
    update_subject(subject_id, {"subject_name": new_name, "subject_folder_id": new_name})
    flash("Subject updated.", "success")
    return redirect(url_for("admin.subjects"))


@admin_bp.route("/subjects/delete/<int:subject_id>", methods=["POST"])
@require_admin_role
def delete_subject_route(subject_id):
    # Single-row lookup instead of fetching every subject and scanning in
    # Python for the one we need (flagged in the architecture audit).
    subject = get_subject_by_id(subject_id)
    if not subject:
        flash("Subject not found.", "warning")
        return redirect(url_for("admin.subjects"))

    delete_subject(subject_id)
    flash("Subject deleted.", "info")
    return redirect(url_for("admin.subjects"))
