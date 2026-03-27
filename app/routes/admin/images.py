"""
app/routes/admin/images.py
Admin image upload route.
"""

import os
import mimetypes

from flask import render_template, request, jsonify
from werkzeug.utils import secure_filename
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError
import io

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import get_all_subjects
from app.services.drive_service import get_drive_service_for_upload
from app.utils.cache import clear_image, set_force_refresh
import config


@admin_bp.route("/upload-images", methods=["GET", "POST"])
@require_admin_role
def upload_images_page():
    if request.method == "POST":
        folder_id = request.form.get("subject_folder_id", "").strip()
        files     = request.files.getlist("images")

        if not folder_id:
            return jsonify({"success": False, "message": "No folder selected."}), 400
        if not files:
            return jsonify({"success": False, "message": "No files received."}), 400

        try:
            drive = get_drive_service_for_upload()
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

        uploaded = 0
        failed   = []

        for f in files:
            if not f or not f.filename:
                continue

            safe_name = secure_filename(f.filename)
            ext       = os.path.splitext(safe_name)[1].lower()

            if ext not in config.ALLOWED_IMAGE_EXTS:
                failed.append({"filename": safe_name, "error": f"Not allowed ({ext})"})
                continue

            f.seek(0, os.SEEK_END)
            size_mb = f.tell() / (1024 * 1024)
            f.seek(0)

            if size_mb > config.MAX_FILE_SIZE_MB:
                failed.append({"filename": safe_name, "error": f"Exceeds {config.MAX_FILE_SIZE_MB} MB"})
                continue

            tmp_path = os.path.join(config.UPLOAD_TMP_DIR, safe_name)
            f.save(tmp_path)
            fh = None

            try:
                from google_drive_service import find_file_by_name
                existing_id = find_file_by_name(drive, safe_name, folder_id)
                mime, _     = mimetypes.guess_type(safe_name)
                fh          = open(tmp_path, "rb")
                media       = MediaIoBaseUpload(fh, mimetype=mime or "application/octet-stream", resumable=True)

                if existing_id:
                    res = drive.files().update(fileId=existing_id, media_body=media, fields="id").execute()
                    new_id = res.get("id", existing_id) if isinstance(res, dict) else existing_id
                else:
                    res    = drive.files().create(
                        body={"name": safe_name, "parents": [folder_id]},
                        media_body=media, fields="id",
                    ).execute()
                    new_id = res.get("id") if isinstance(res, dict) else None

                uploaded += 1

                # Clear caches for this file
                if new_id:
                    clear_image(new_id)
                set_force_refresh(True)

            except HttpError as e:
                failed.append({"filename": safe_name, "error": str(e)})
            except Exception as e:
                failed.append({"filename": safe_name, "error": str(e)})
            finally:
                try:
                    if fh and not fh.closed:
                        fh.close()
                except Exception:
                    pass
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

        return jsonify({"success": True, "uploaded": uploaded, "failed": failed}), 200

    # GET
    subjects_list = [
        {
            "id":        int(s.get("id", 0)),
            "name":      str(s.get("subject_name", "")).strip(),
            "folder_id": str(s.get("subject_folder_id", "")).strip(),
        }
        for s in get_all_subjects()
        if str(s.get("subject_folder_id", "")).strip()
    ]

    return render_template(
        "admin/upload_images.html",
        subjects=subjects_list,
        load_error=None if subjects_list else "No subjects found.",
    )