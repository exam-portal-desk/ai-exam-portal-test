"""
app/routes/api/v01/admin/image_mapping.py
JSON API for the "Question Image Mapping" admin feature — lets an admin
bulk-map already-uploaded Subject images onto Exam questions without ever
typing a "SubjectName/filename.ext" string by hand.

Deliberately reuses existing building blocks wherever possible instead of
introducing a parallel image/question listing system:
  - GET /subjects  wraps app.db.misc.get_subjects_page() (the same
    searchable/paginated subject list "Manage Subjects" already uses) and
    attaches a bounded image count per subject via the storage backend —
    the same bounded-scan approach app/routes/api/v01/admin/storage.py's
    _scan_prefix_stats() already uses for the Object Storage dashboard.
  - GET /questions wraps app.db.questions.get_questions_by_exam_page()
    (the same server-paginated/searched/filtered list Manage Questions
    uses) and additionally resolves image existence for the current page
    only, via the existing resolve_question_image_urls_bulk().
  - Browsing a subject's image library needs NO new endpoint at all — the
    frontend calls the existing GET /api/v01/admin/storage/objects
    directly (already returns paginated objects with size/preview_url).
  - POST /save is the one genuinely new piece: no existing endpoint can
    set a DIFFERENT image_path per question in one call (the single-PATCH
    endpoint is one row at a time; bulk-update broadcasts one scalar value
    to a whole type). It validates everything BEFORE writing anything,
    then commits via app.db.questions.bulk_set_image_paths() — a single
    atomic UPDATE ... FROM (VALUES ...), never a partial save.
"""

from flask import request, jsonify

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exam_by_id
from app.db.misc import get_subjects_page, get_subject_by_name
from app.db.questions import (
    get_questions_by_exam_page, get_questions_by_ids, get_question_ids_for_exam, bulk_set_image_paths,
)
from app.utils.sanitize import sanitize_html
from app.storage import get_storage
from app.services.image_storage_service import resolve_question_image_urls_bulk

# Sanity bound on one bulk-save request — generous for realistic exams
# (hundreds of questions) while still ruling out an unbounded/malformed
# payload turning one request into an enormous transaction.
_MAX_MAPPINGS_PER_SAVE = 300

# Bounded per-subject image-count scan — mirrors storage.py's
# _FOLDER_SCAN_MAX_OBJECTS cap for the same reason: a folder with an
# unusually large number of objects still gets a real (partial) count
# rather than an unbounded scan, flagged via `truncated`.
_SUBJECT_COUNT_MAX_OBJECTS = 2000
_SUBJECT_COUNT_PAGE_LIMIT = 1000

_QUESTION_TEXT_PREVIEW_LEN = 140


def _count_subject_images(storage, subject_name: str) -> dict:
    """Bounded object count for one subject's storage folder — same
    approach as app/routes/api/v01/admin/storage.py's _scan_prefix_stats(),
    kept as a small local copy rather than a cross-module import so this
    admin API module doesn't depend on the Object Storage one."""
    prefix = f"{subject_name}/"
    count, cursor, truncated = 0, None, False
    while count < _SUBJECT_COUNT_MAX_OBJECTS:
        page = storage.list_objects(prefix=prefix, cursor=cursor, limit=_SUBJECT_COUNT_PAGE_LIMIT)
        count += len(page["objects"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    if cursor:
        truncated = True
    return {"image_count": count, "truncated": truncated}


@admin_api_bp.route("/image-mapping/subjects", methods=["GET"])
@require_admin_role
def image_mapping_subjects():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = min(max(request.args.get("per_page", 20, type=int), 1), 50)

    page_data = get_subjects_page(search=q, page=page, per_page=per_page)
    storage = get_storage()
    subjects = []
    for s in page_data["subjects"]:
        stats = _count_subject_images(storage, s["subject_name"])
        subjects.append({
            "id": s["id"],
            "subject_name": s["subject_name"],
            "image_count": stats["image_count"],
            "truncated": stats["truncated"],
        })

    return jsonify({
        "success": True,
        "subjects": subjects,
        "total": page_data["total"],
        "page": page_data["page"],
        "per_page": page_data["per_page"],
        "total_pages": page_data["total_pages"],
    })


def _serialize_questions(rows, image_status_filter=""):
    """rows -> the JSON shape the frontend renders, resolving image
    existence for exactly these rows (never more) so "Image Attached" vs
    "Missing/Broken" can be told apart — image_path alone can't do that."""
    paths = [r["image_path"] for r in rows if r.get("image_path")]
    resolved = resolve_question_image_urls_bulk(paths) if paths else {}

    out = []
    for r in rows:
        path = r.get("image_path") or ""
        if not path:
            status = "none"
        else:
            has_image, _url = resolved.get(path, (False, None))
            status = "attached" if has_image else "broken"
        if image_status_filter in ("attached", "broken") and status != image_status_filter:
            continue
        text = sanitize_html(r.get("question_text", ""))
        if len(text) > _QUESTION_TEXT_PREVIEW_LEN:
            text = text[:_QUESTION_TEXT_PREVIEW_LEN].rstrip() + "…"
        out.append({
            "id": r["id"],
            "row_no": r["row_no"],
            "question_type": r.get("question_type") or "MCQ",
            "question_text": text,
            "image_path": path or None,
            "image_status": status,
            "image_url": resolved.get(path, (False, None))[1] if path else None,
        })
    return out


@admin_api_bp.route("/image-mapping/questions", methods=["GET"])
@require_admin_role
def image_mapping_questions():
    exam_id = request.args.get("exam_id", type=int)
    if not exam_id:
        return jsonify({"success": False, "message": "exam_id is required."}), 400

    # "ids" mode: full rows for an arbitrary, already-known set of question
    # ids (hydrating the mapping workspace after "select all matching", or
    # any selection that spans multiple list pages) — bypasses
    # search/pagination entirely, bounded the same as one bulk save.
    ids_param = (request.args.get("ids") or "").strip()
    if ids_param:
        try:
            ids = [int(x) for x in ids_param.split(",") if x.strip()][:_MAX_MAPPINGS_PER_SAVE]
        except ValueError:
            return jsonify({"success": False, "message": "Invalid ids parameter."}), 400
        rows = get_questions_by_ids(exam_id, ids)
        return jsonify({"success": True, "questions": _serialize_questions(rows)})

    q = (request.args.get("q") or "").strip()
    qtype = (request.args.get("type") or "").strip()
    image_status = (request.args.get("image_status") or "").strip()  # "" | "none" | "attached" | "broken"
    page = request.args.get("page", 1, type=int)
    per_page = min(max(request.args.get("per_page", 20, type=int), 1), 100)

    # "attached"/"broken" both need a real existence check (image_path
    # alone can't tell them apart) — narrow the DB query to "has an
    # image_path at all" and split the two apart below, on this page only.
    db_has_image = "without" if image_status == "none" else ("with" if image_status in ("attached", "broken") else "")

    page_data = get_questions_by_exam_page(
        exam_id, search=q, question_type=qtype, has_image=db_has_image, page=page, per_page=per_page,
    )
    questions = _serialize_questions(page_data["questions"], image_status)

    return jsonify({
        "success": True,
        "questions": questions,
        "total": page_data["total"],
        "page": page_data["page"],
        "per_page": page_data["per_page"],
        "total_pages": page_data["total_pages"],
    })


@admin_api_bp.route("/image-mapping/questions/select-all", methods=["GET"])
@require_admin_role
def image_mapping_select_all_ids():
    """Every question id matching the current search/type/image filters
    (not just the visible page) — backs "Select all matching". Capped at
    _MAX_MAPPINGS_PER_SAVE, the same bound one bulk save already enforces,
    so this can never hand back more ids than a single save could apply."""
    exam_id = request.args.get("exam_id", type=int)
    if not exam_id:
        return jsonify({"success": False, "message": "exam_id is required."}), 400
    q = (request.args.get("q") or "").strip()
    qtype = (request.args.get("type") or "").strip()
    image_status = (request.args.get("image_status") or "").strip()
    db_has_image = "without" if image_status == "none" else ("with" if image_status in ("attached", "broken") else "")

    page_data = get_questions_by_exam_page(
        exam_id, search=q, question_type=qtype, has_image=db_has_image,
        page=1, per_page=_MAX_MAPPINGS_PER_SAVE,
    )
    rows = page_data["questions"]
    # Whether the fetch itself hit the per-request cap — independent of
    # the attached/broken post-filter below, which reduces the id list for
    # an unrelated reason and must not be confused with "there were more
    # matching rows than we scanned".
    scan_truncated = len(rows) >= _MAX_MAPPINGS_PER_SAVE and page_data["total"] > len(rows)
    if image_status in ("attached", "broken"):
        serialized = _serialize_questions(rows, image_status)
        ids = [r["id"] for r in serialized]
    else:
        ids = [r["id"] for r in rows]

    return jsonify({"success": True, "ids": ids, "total_matching": page_data["total"], "truncated": scan_truncated})


@admin_api_bp.route("/image-mapping/save", methods=["POST"])
@require_admin_role
def image_mapping_save():
    payload = request.get_json(force=True, silent=True) or {}
    exam_id = payload.get("exam_id")
    subject_name = (payload.get("subject_name") or "").strip()
    mappings = payload.get("mappings")

    if not exam_id or not isinstance(mappings, list) or not mappings:
        return jsonify({"success": False, "message": "exam_id and a non-empty mappings list are required."}), 400
    if len(mappings) > _MAX_MAPPINGS_PER_SAVE:
        return jsonify({"success": False, "message": f"Save at most {_MAX_MAPPINGS_PER_SAVE} mappings at a time."}), 400

    exam = get_exam_by_id(exam_id)
    if not exam:
        return jsonify({"success": False, "message": "Exam not found."}), 404

    # Normalize + basic shape validation. A question_id repeated in the
    # payload keeps only its LAST occurrence — matches the "latest pending
    # choice wins" rule the frontend's Map-keyed state already guarantees,
    # enforced here too so a duplicate can never make the post-save row
    # count come out short and misreport as a failure.
    clean_by_id = {}
    for m in mappings:
        qid = m.get("question_id")
        key = m.get("image_key")
        if not isinstance(qid, int):
            return jsonify({"success": False, "message": "Every mapping needs a valid question_id."}), 400
        key = key.strip() if isinstance(key, str) and key.strip() else None
        clean_by_id[qid] = {"question_id": qid, "image_key": key}
    clean = list(clean_by_id.values())

    # Every question must belong to the selected exam — reject the whole
    # save if ANY doesn't, rather than silently skipping it.
    requested_ids = [m["question_id"] for m in clean]
    owned_ids = get_question_ids_for_exam(exam_id, requested_ids)
    bad_ids = [qid for qid in requested_ids if qid not in owned_ids]
    if bad_ids:
        return jsonify({
            "success": False,
            "message": f"Question id {bad_ids[0]} does not belong to the selected exam.",
        }), 403

    non_null_keys = sorted({m["image_key"] for m in clean if m["image_key"]})
    if non_null_keys:
        if not subject_name or not get_subject_by_name(subject_name):
            return jsonify({"success": False, "message": "The selected Subject could not be found."}), 404

        prefix = f"{subject_name}/"
        outside_subject = [k for k in non_null_keys if not k.startswith(prefix)]
        if outside_subject:
            return jsonify({"success": False, "message": "The selected image is not part of the selected Subject."}), 403

        resolved = resolve_question_image_urls_bulk(non_null_keys)
        missing = [k for k in non_null_keys if not resolved.get(k, (False, None))[0]]
        if missing:
            noun = "image" if len(missing) == 1 else "images"
            return jsonify({
                "success": False,
                "message": f"{len(missing)} selected {noun} could not be found in storage.",
            }), 404

    saved = bulk_set_image_paths(exam_id, [{"id": m["question_id"], "image_path": m["image_key"]} for m in clean])
    if len(saved) != len(clean):
        return jsonify({
            "success": False,
            "message": "Unable to save mappings. No changes were applied. Please try again.",
        }), 500

    saved_paths = [row["image_path"] for row in saved if row.get("image_path")]
    resolved_final = resolve_question_image_urls_bulk(saved_paths) if saved_paths else {}
    result_mappings = []
    for row in saved:
        path = row.get("image_path")
        url = resolved_final.get(path, (False, None))[1] if path else None
        result_mappings.append({"question_id": row["id"], "image_path": path, "image_url": url})

    return jsonify({"success": True, "updated": len(saved), "mappings": result_mappings})
