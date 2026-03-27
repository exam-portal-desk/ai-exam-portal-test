"""
app/routes/admin/subjects.py
Admin subject management (Supabase + Google Drive folders).
"""

from flask import render_template, request, redirect, url_for, flash

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import get_all_subjects, create_subject, update_subject, delete_subject


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

        try:
            from app.services.drive_service import get_drive_service_for_upload
            from google_drive_service import create_subject_folder
            svc = get_drive_service_for_upload()
            folder_id, created_at = create_subject_folder(svc, name)
        except Exception as e:
            flash(f"Cannot create folder: {e}", "danger")
            return redirect(url_for("admin.subjects"))

        create_subject({"subject_name": name, "subject_folder_id": folder_id,
                        "subject_folder_created_at": created_at})
        flash(f"Subject '{name}' created.", "success")
        return redirect(url_for("admin.subjects"))

    return render_template("admin/subjects.html", subjects=get_all_subjects())


@admin_bp.route("/subjects/edit/<int:subject_id>", methods=["POST"])
@require_admin_role
def edit_subject(subject_id):
    from app.db.misc import get_all_subjects
    subjects = get_all_subjects()
    subject  = next((s for s in subjects if int(s["id"]) == subject_id), None)
    if not subject:
        flash("Subject not found.", "danger")
        return redirect(url_for("admin.subjects"))

    new_name = request.form.get("subject_name","").strip()
    if not new_name:
        flash("Name required.", "danger")
        return redirect(url_for("admin.subjects"))

    folder_id = subject.get("subject_folder_id","")
    try:
        from app.services.drive_service import get_drive_service_for_upload
        svc = get_drive_service_for_upload()
        svc.files().update(fileId=folder_id, body={"name": new_name}).execute()
    except Exception as e:
        flash("Drive folder rename failed; database updated.", "warning")

    update_subject(subject_id, {"subject_name": new_name})
    flash("Subject updated.", "success")
    return redirect(url_for("admin.subjects"))


@admin_bp.route("/subjects/delete/<int:subject_id>")
@require_admin_role
def delete_subject_route(subject_id):
    from app.db.misc import get_all_subjects
    subjects = get_all_subjects()
    subject  = next((s for s in subjects if int(s["id"]) == subject_id), None)
    if not subject:
        flash("Subject not found.", "warning")
        return redirect(url_for("admin.subjects"))

    folder_id = str(subject.get("subject_folder_id","")).strip()
    if folder_id:
        try:
            from app.services.drive_service import get_drive_service_for_upload
            svc = get_drive_service_for_upload()
            try:
                svc.files().delete(fileId=folder_id, supportsAllDrives=True).execute()
            except Exception:
                svc.files().update(fileId=folder_id, body={"trashed": True},
                                   supportsAllDrives=True).execute()
        except Exception as e:
            print(f"[admin.subjects] Drive delete error: {e}")

    delete_subject(subject_id)
    flash("Subject deleted.", "info")
    return redirect(url_for("admin.subjects"))