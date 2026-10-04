"""
app/services/exam_export_service.py
Bulk exam export/import — a .zip containing one manifest.json (every selected exam's metadata
and questions, inline) plus an assets/ folder holding each question's image, referenced by
relative path instead of a database-specific storage key (that key won't mean anything once the
file is imported into a different environment/storage backend).

Both directions run as a background job (see the in-memory job-dict + threading.Thread + polling
pattern already used by app/routes/api/v01/notebooks.py's notebook import, and by AI question
generation in app/routes/api/v01/admin/ai_centre.py — this reuses that same convention rather
than introducing Celery/Redis, which this app has no other use for and which a single Render
worker doesn't need for the data volumes involved here). Progress is real, not a timer: every
tick below is fired by a question actually processed, so "Exporting exam 2 of 5: 150/400
questions" always reflects work genuinely done at that instant.

Import is a best-effort saga PER EXAM, not one all-or-nothing transaction across the whole
file — deliberately, since the exams in one export are independent of each other: one corrupt
exam's questions failing to insert should never undo four other exams that imported cleanly.
Each exam's own rows (the exam row + every one of its questions) DO go in one DB transaction
(app.db.transaction), so a single exam is still all-or-nothing internally.

Known trade-off, not hidden: a question's image is uploaded to storage as part of the same loop
that inserts its DB row, but that storage write is NOT covered by the exam's DB transaction (no
storage backend here participates in Postgres transactions) — so if a LATER question in the same
exam fails and the transaction rolls back, an image already uploaded for an EARLIER question in
that same exam is not automatically deleted. That leaves an occasional orphaned file in storage
on a partial-exam failure, never a wrong/missing image on anything that successfully imported,
and never a security or correctness issue — just minor storage cost, cleanable later same as any
other orphaned asset.
"""

import json
import os
import tempfile
import time
import uuid
import zipfile
from typing import Callable, Dict, List, Optional

from app.db import transaction
from app.db.categories import get_category_by_id, get_or_create_category
from app.db.exams import get_exams_by_ids_full
from app.db.questions import get_questions_by_exam
from app.db.subcategories import get_subcategory_by_id, get_or_create_subcategory
from app.services.image_storage_service import upload_question_image
from app.storage import get_storage
from app.utils.datetime_service import now_utc_naive

EXPORT_FORMAT = "smartai-exams-export-v1"

# category_id/subcategory_id/id/created_at deliberately excluded — the numeric ids are
# environment-specific (won't mean anything on import into a different database) and are
# replaced by category_name/subcategory_name, resolved (and auto-created if missing) at import
# time instead; created_at is set fresh by the INSERT itself.
_EXAM_FIELDS = [
    "name", "date", "start_time", "duration", "total_questions", "status",
    "instructions", "positive_marks", "negative_marks", "max_attempts",
    "result_mode", "result_delay", "results_released", "passing_percentage",
    "scheduled_mode", "prep_window_minutes", "completion_buffer_minutes",
    "allow_manual_submission", "available_after_datetime",
]

# image_path deliberately excluded — replaced by image_asset (a relative path INSIDE this zip),
# since the original storage key means nothing outside the environment that produced it.
_QUESTION_FIELDS = [
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_answer", "question_type", "positive_marks", "negative_marks",
    "tolerance", "metadata",
]

_EXPORT_DIR = os.path.join(tempfile.gettempdir(), "smartai_exam_exports")
_EXPORT_TTL_SECONDS = 3600  # 1 hour — matches _import_jobs' own effectively-short lifetime; a
                            # finished export is downloaded within minutes in practice.

ProgressFn = Optional[Callable[[str, str, int], None]]


def export_out_path(job_id: str) -> str:
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return os.path.join(_EXPORT_DIR, f"{job_id}.zip")


def import_in_path(job_id: str) -> str:
    """Where an uploaded import .zip is saved before its background job reads it — same
    directory as export_out_path's output, just a distinct filename shape so cleanup_expired_
    exports() (and a human glancing at the temp dir) can tell the two apart at a glance."""
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return os.path.join(_EXPORT_DIR, f"import-{job_id}.zip")


def cleanup_expired_exports() -> None:
    """Called from the app's existing 5-minute cleanup loop (see _start_periodic_cleanup in
    app/__init__.py) — deletes any export .zip older than _EXPORT_TTL_SECONDS. A job's own
    in-memory status entry already vanishes on its own (process-local dict, same as the
    notebook-import job store); this only clears the actual file it pointed at."""
    if not os.path.isdir(_EXPORT_DIR):
        return
    cutoff = time.time() - _EXPORT_TTL_SECONDS
    for fname in os.listdir(_EXPORT_DIR):
        path = os.path.join(_EXPORT_DIR, fname)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def run_export(exam_ids: List[int], out_path: str, progress: ProgressFn = None) -> Dict:
    """Streams the export directly to out_path — each question's image is read from storage and
    written into the archive one at a time (writestr), so memory held at any moment is
    proportional to one image, never the whole export. Returns a small summary dict."""
    def _report(phase, message, percent):
        if progress:
            progress(phase, message, percent)

    _report("preparing", "Loading selected exams...", 0)
    exams_by_id = get_exams_by_ids_full(exam_ids)
    ordered = [exams_by_id[str(eid)] for eid in exam_ids if str(eid) in exams_by_id]
    if not ordered:
        raise ValueError("None of the selected exams could be found.")

    per_exam_questions = {e["id"]: get_questions_by_exam(e["id"]) for e in ordered}
    total_units = sum(len(qs) for qs in per_exam_questions.values()) or 1

    storage = get_storage()
    manifest_exams = []
    done = 0

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, exam in enumerate(ordered):
            questions = per_exam_questions[exam["id"]]
            category = get_category_by_id(exam["category_id"]) if exam.get("category_id") else None
            subcategory = get_subcategory_by_id(exam["subcategory_id"]) if exam.get("subcategory_id") else None

            exam_out = {f: exam.get(f) for f in _EXAM_FIELDS}
            exam_out["category_name"] = category["name"] if category else None
            exam_out["subcategory_name"] = subcategory["name"] if subcategory else None
            exam_out["questions"] = []

            total_q = len(questions) or 1
            for qi, q in enumerate(questions):
                q_out = {f: q.get(f) for f in _QUESTION_FIELDS}
                q_out["image_asset"] = None
                image_path = q.get("image_path")
                if image_path:
                    try:
                        if storage.exists(image_path):
                            content = storage.download(image_path)
                            asset_name = f"{uuid.uuid4().hex[:12]}_{os.path.basename(image_path)}"
                            zf.writestr(f"assets/{asset_name}", content)
                            q_out["image_asset"] = f"assets/{asset_name}"
                    except Exception as e:
                        print(f"[exam_export_service] skipping unreadable image {image_path!r}: {e}")
                exam_out["questions"].append(q_out)
                done += 1
                _report(
                    "exporting",
                    f"Exporting exam {idx + 1} of {len(ordered)}: {exam.get('name', '')} — {qi + 1}/{total_q} questions",
                    min(99, round(done / total_units * 99)),
                )

            manifest_exams.append(exam_out)

        manifest = {
            "format": EXPORT_FORMAT,
            "exported_at": now_utc_naive().isoformat(),
            "exams": manifest_exams,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, default=str))

    _report("complete", "Export complete", 100)
    return {"exam_count": len(ordered), "question_count": sum(len(qs) for qs in per_exam_questions.values())}


def _guess_mime(filename: str) -> str:
    import mimetypes
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def run_import(zip_path: str, progress: ProgressFn = None) -> Dict:
    """Best-effort per-exam saga — see module docstring. Returns
    {"imported": [names...], "failed": [{"name":.., "error":..}, ...]}."""
    def _report(phase, message, percent):
        if progress:
            progress(phase, message, percent)

    _report("validating", "Reading export file...", 0)
    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile:
        raise ValueError("This file isn't a valid .zip archive.")

    with zf:
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except KeyError:
            raise ValueError("This file doesn't look like an exam export (no manifest.json inside it).")
        except json.JSONDecodeError:
            raise ValueError("This file's manifest.json is corrupt and can't be read.")

        exams = manifest.get("exams") or []
        if not exams:
            raise ValueError("This file has no exams to import.")

        total_units = sum(len(e.get("questions") or []) for e in exams) or 1
        done = 0
        imported: List[str] = []
        failed: List[Dict] = []
        stamp = now_utc_naive().strftime("%Y%m%d%H%M%S")

        for idx, exam_data in enumerate(exams):
            raw_name = str(exam_data.get("name") or f"Imported exam {idx + 1}").strip()
            questions = exam_data.get("questions") or []
            total_q = len(questions) or 1
            processed_in_exam = 0
            try:
                with transaction() as cur:
                    category = get_or_create_category(exam_data["category_name"]) if exam_data.get("category_name") else None
                    subcategory = (
                        get_or_create_subcategory(category["id"], exam_data["subcategory_name"])
                        if category and exam_data.get("subcategory_name") else None
                    )

                    cur.execute("SELECT COUNT(*) AS count FROM exams WHERE lower(trim(name)) = lower(trim(%s))", (raw_name,))
                    name_taken = cur.fetchone()["count"] > 0
                    exam_name = f"{raw_name} (Imported {stamp})" if name_taken else raw_name

                    exam_row = {f: exam_data.get(f) for f in _EXAM_FIELDS if f in exam_data}
                    exam_row["name"] = exam_name
                    exam_row["category_id"] = category["id"] if category else None
                    exam_row["subcategory_id"] = subcategory["id"] if subcategory else None

                    cols = list(exam_row.keys())
                    cur.execute(
                        f"INSERT INTO exams ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) RETURNING id",
                        [exam_row[c] for c in cols],
                    )
                    new_exam_id = cur.fetchone()["id"]

                    for qi, q in enumerate(questions):
                        q_row = {f: q.get(f) for f in _QUESTION_FIELDS if f in q}
                        q_row["exam_id"] = new_exam_id
                        q_row["image_path"] = None

                        image_asset = q.get("image_asset")
                        if image_asset:
                            try:
                                content = zf.read(image_asset)
                                folder = f"Imported/{new_exam_id}"
                                filename = f"{uuid.uuid4().hex[:10]}_{os.path.basename(image_asset)}"
                                q_row["image_path"] = upload_question_image(folder, filename, content, _guess_mime(filename))
                            except KeyError:
                                # Referenced in the manifest but missing from the zip — the
                                # question still imports, just without its image, rather than
                                # failing the whole exam over one missing file.
                                pass

                        q_cols = list(q_row.keys())
                        cur.execute(
                            f"INSERT INTO questions ({','.join(q_cols)}) VALUES ({','.join(['%s'] * len(q_cols))})",
                            [q_row[c] for c in q_cols],
                        )
                        done += 1
                        processed_in_exam += 1
                        _report(
                            "importing",
                            f"Importing exam {idx + 1} of {len(exams)}: {exam_name} — {qi + 1}/{total_q} questions",
                            min(99, round(done / total_units * 99)),
                        )

                imported.append(exam_name)
            except Exception as e:
                # This exam's transaction rolled back (including whatever questions above already
                # ticked done), but the overall bar still needs to reach 100 by the end of the
                # batch — count this exam's remaining, never-attempted questions as done too.
                done += total_q - processed_in_exam
                failed.append({"name": raw_name, "error": str(e)})

        _report("complete", "Import complete", 100)
        return {"imported": imported, "failed": failed}
