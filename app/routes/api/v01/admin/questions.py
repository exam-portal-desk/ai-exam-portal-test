"""
app/routes/api/v01/admin/questions.py
Admin question-management JSON API (v01). Relocated from
app/routes/admin/questions.py.

  POST /admin/questions/add-ajax        -> POST /api/v01/admin/exams/<exam_id>/questions  (add)
  GET  /admin/questions/get/<id>        -> GET  /api/v01/admin/questions/<id>
  POST /admin/questions/edit-ajax/<id>  -> PATCH /api/v01/admin/questions/<id>
  POST /admin/questions/delete-multiple -> POST /api/v01/admin/questions/delete-multiple
  POST /admin/questions/batch-add       -> POST /api/v01/admin/questions/batch-add
  POST /admin/questions/bulk-update     -> POST /api/v01/admin/questions/bulk-update
  POST /admin/questions/import-csv      -> POST /api/v01/admin/questions/import-csv

Two fixes applied while relocating (both flagged in the architecture audit):
  - questions_bulk_update now issues one set-based UPDATE (see
    app.db.questions.update_questions_by_type) instead of fetching every
    matching question and calling update_question() in a loop.
  - import_questions_csv now bulk-inserts all valid rows in one call to
    create_questions_bulk() (the same helper batch-add already used)
    instead of one create_question() call per CSV row. Per-row validation
    (empty question_text) is unchanged and still reported row-by-row; only
    the DB write itself is now batched.

SECURITY FIX (wrong-exam import): import_questions_csv() used to read
exam_id PER ROW from the CSV and trust it outright — the only check was
"does this id exist anywhere", never "does it match whatever exam the
admin has selected in the UI". A stale/edited/copy-pasted CSV could
silently import questions into the wrong exam with no indication anything
was wrong. The endpoint now requires an explicit `exam_id` FORM FIELD —
sent by the caller's own UI selection, never read out of the file — as
the single authority for where every row in the batch lands. A CSV's own
`exam_id` column (still accepted for old files) is now purely a
consistency check: blank/absent is fine, a value matching the selected
exam is fine, a value that conflicts with it aborts the ENTIRE import
before anything is written, rather than silently skipping just that row
or (worse) writing it under the wrong exam.
"""

import pandas as pd
from flask import request, jsonify, render_template_string

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_exam_by_id
from app.db.questions import (
    get_question_by_id, get_questions_by_exam, get_questions_by_exam_page,
    QUESTIONS_SHOW_ALL_PER_PAGE,
    create_question, create_questions_bulk,
    update_question, update_questions_by_type, delete_questions_bulk,
    build_question_metadata, merge_question_metadata,
)
from app.utils.helpers import safe_float, safe_int
from app.utils.sanitize import sanitize_html

_QUESTION_ROWS_TPL = (
    '{% from "admin/_question_rows.html" import render_question_row %}'
    '{% for q in questions %}{{ render_question_row(q, q.row_no) }}{% endfor %}'
)


@admin_api_bp.route("/questions", methods=["GET"])
@require_admin_role
def api_questions_list():
    """PERFORMANCE: backs Manage Questions' search box, Type/Image filters,
    "Show N entries" and pagination controls — every one of those now
    re-fetches just the matching page from the database instead of
    filtering an already-fully-loaded in-DOM row set. Row markup is
    rendered server-side from the exact same macro the initial page load
    uses (admin/_question_rows.html), so the two can never drift apart —
    same pattern as api_exams_list()/api_subjects_list()."""
    exam_id = request.args.get("exam_id", type=int)
    if not exam_id:
        return jsonify({"success": False, "message": "exam_id is required"}), 400

    per_page_raw = request.args.get("per_page", "10")
    per_page = QUESTIONS_SHOW_ALL_PER_PAGE if per_page_raw == "all" else per_page_raw

    result = get_questions_by_exam_page(
        exam_id,
        search=request.args.get("q", "").strip(),
        question_type=request.args.get("type", "").strip(),
        has_image=request.args.get("image", "").strip(),
        page=request.args.get("page", 1),
        per_page=per_page,
    )
    for q in result["questions"]:
        q["question_text"] = sanitize_html(q.get("question_text", ""))
        q["option_a"] = sanitize_html(q.get("option_a", ""))
        q["option_b"] = sanitize_html(q.get("option_b", ""))
        q["option_c"] = sanitize_html(q.get("option_c", ""))
        q["option_d"] = sanitize_html(q.get("option_d", ""))
        q["source_tag"] = (q.get("metadata") or {}).get("source_tag", "")

    result["rows_html"] = render_template_string(_QUESTION_ROWS_TPL, questions=result["questions"])
    del result["questions"]
    return jsonify(result)


@admin_api_bp.route("/questions", methods=["POST"])
@require_admin_role
def add_question_ajax():
    d = request.form.to_dict()
    result = create_question({
        "exam_id":        int(d.get("exam_id") or 0),
        "question_text":  d.get("question_text","").strip(),
        "option_a":       d.get("option_a","").strip(),
        "option_b":       d.get("option_b","").strip(),
        "option_c":       d.get("option_c","").strip(),
        "option_d":       d.get("option_d","").strip(),
        "correct_answer": d.get("correct_answer","").strip(),
        "question_type":  d.get("question_type","MCQ").strip(),
        "image_path":     d.get("image_path","").strip(),
        "tolerance":      safe_float(d.get("tolerance"), 0),
        "positive_marks": safe_int(d.get("positive_marks"), 4),
        "negative_marks": safe_float(d.get("negative_marks"), 1),
        "metadata":       build_question_metadata(d.get("source_tag")),
    })
    if result:
        return jsonify({"success": True, "message": "Question added."})
    return jsonify({"success": False, "message": "Failed to add question."}), 500


@admin_api_bp.route("/questions/<int:question_id>", methods=["GET"])
@require_admin_role
def get_question_ajax(question_id):
    q = get_question_by_id(question_id)
    if not q:
        return jsonify({"success": False, "message": "Not found."}), 404
    return jsonify({"success": True, "question": q})


@admin_api_bp.route("/questions/<int:question_id>", methods=["PATCH"])
@require_admin_role
def edit_question_ajax(question_id):
    q = get_question_by_id(question_id)
    if not q:
        return jsonify({"success": False, "message": "Not found."}), 404
    d = request.form.to_dict()
    ok = update_question(question_id, {
        "exam_id":        int(d.get("exam_id") or q["exam_id"]),
        "question_text":  d.get("question_text","").strip(),
        "option_a":       d.get("option_a","").strip(),
        "option_b":       d.get("option_b","").strip(),
        "option_c":       d.get("option_c","").strip(),
        "option_d":       d.get("option_d","").strip(),
        "correct_answer": d.get("correct_answer","").strip(),
        "question_type":  d.get("question_type","MCQ").strip(),
        "image_path":     d.get("image_path","").strip(),
        "tolerance":      safe_float(d.get("tolerance"), 0),
        "positive_marks": safe_int(d.get("positive_marks"), 4),
        "negative_marks": safe_float(d.get("negative_marks"), 1),
    })
    # Merge-only: only ever touches the 'source_tag' key, leaving any other
    # metadata this question already carries (e.g. a future 'difficulty'
    # key) untouched. Only runs when the form actually sent the field, so
    # an older/other client that omits it entirely can't accidentally wipe
    # an existing tag.
    if "source_tag" in d:
        merge_question_metadata(question_id, {"source_tag": d.get("source_tag","").strip() or None})
    return jsonify({"success": ok, "message": "Updated." if ok else "Failed."})


@admin_api_bp.route("/questions/delete-multiple", methods=["POST"])
@require_admin_role
def delete_multiple_questions():
    payload = request.get_json(force=True) or {}
    ids = [int(i) for i in (payload.get("ids") or []) if str(i).strip()]
    if not ids:
        return jsonify({"success": False, "message": "No IDs provided"}), 400
    deleted = delete_questions_bulk(ids)
    return jsonify({"success": True, "deleted": deleted})


@admin_api_bp.route("/questions/batch-add", methods=["POST"])
@require_admin_role
def questions_batch_add():
    payload = request.get_json(force=True) or {}
    exam_id = int(payload.get("exam_id",0))
    items   = payload.get("questions",[])
    rows = [
        {
            "exam_id":        exam_id,
            "question_text":  (it.get("question_text") or "").strip(),
            "option_a":       (it.get("option_a") or "").strip(),
            "option_b":       (it.get("option_b") or "").strip(),
            "option_c":       (it.get("option_c") or "").strip(),
            "option_d":       (it.get("option_d") or "").strip(),
            "correct_answer": (it.get("correct_answer") or "").strip(),
            "question_type":  (it.get("question_type") or "MCQ").strip(),
            "image_path":     (it.get("image_path") or "").strip(),
            "positive_marks": safe_int(it.get("positive_marks"),4),
            "negative_marks": safe_float(it.get("negative_marks"),1),
            "tolerance":      safe_float(it.get("tolerance"),0),
            # A caller that already has a structured metadata object (e.g. a
            # future JSON importer) wins as-is; otherwise build the
            # canonical shape from a flat source_tag field. Always present
            # (even as None) — insert_many() derives its column list from
            # the first row, so every row dict must share the same keys.
            "metadata":       it.get("metadata") if isinstance(it.get("metadata"), dict) else build_question_metadata(it.get("source_tag")),
        }
        for it in items if (it.get("question_text") or "").strip()
    ]
    if not rows:
        return jsonify({"success": False, "message": "No valid rows"}), 400
    ok = create_questions_bulk(rows)
    return jsonify({"success": ok, "added": len(rows) if ok else 0})


@admin_api_bp.route("/questions/bulk-update", methods=["POST"])
@require_admin_role
def questions_bulk_update():
    payload  = request.get_json(force=True) or {}
    exam_id  = payload.get("exam_id")
    qtype    = str(payload.get("question_type") or "").strip()
    pos      = payload.get("positive_marks")
    neg      = payload.get("negative_marks")
    tol      = payload.get("tolerance")

    if not exam_id or not qtype:
        return jsonify({"success": False, "message": "exam_id and question_type required"}), 400

    upd = {}
    if pos is not None and str(pos).strip(): upd["positive_marks"] = int(pos)
    if neg is not None and str(neg).strip(): upd["negative_marks"] = float(neg)
    if tol is not None:                      upd["tolerance"]      = float(tol)

    if not upd:
        return jsonify({"success": True, "updated": 0})

    updated = update_questions_by_type(exam_id, qtype, upd)
    return jsonify({"success": True, "updated": updated})


@admin_api_bp.route("/questions/import-csv", methods=["POST"])
@require_admin_role
def import_questions_csv():
    if "csv_file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    f = request.files["csv_file"]
    if not f.filename or not f.filename.endswith(".csv"):
        return jsonify({"success": False, "message": "File must be a CSV"}), 400

    # SECURITY: the target exam is whatever the caller's UI has selected —
    # never read out of the file itself. See the module docstring.
    target_exam_id = request.form.get("exam_id", type=int)
    if not target_exam_id:
        return jsonify({"success": False, "message": "No target exam selected."}), 400
    target_exam = get_exam_by_id(target_exam_id)
    if not target_exam:
        return jsonify({"success": False, "message": "Selected exam not found."}), 400

    try:
        df = pd.read_csv(f)
    except Exception as e:
        return jsonify({"success": False, "message": f"Cannot read CSV: {e}"}), 400

    required = ["question_text","option_a","option_b","option_c","option_d",
                "correct_answer","question_type","image_path","positive_marks","negative_marks","tolerance"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return jsonify({"success": False, "message": f"Missing columns: {', '.join(missing)}"}), 400

    # exam_id is optional/legacy in the CSV now — accepted for old files,
    # but only ever as a consistency check against target_exam_id, never as
    # the row's actual destination. A present-and-different value means
    # this file (or a row in it) was built for a different exam — reject
    # the WHOLE import before writing anything, rather than silently
    # skipping that row or (worse) honoring it.
    if "exam_id" in df.columns:
        for idx, row in df.iterrows():
            raw = row.get("exam_id")
            if not pd.notna(raw) or str(raw).strip() in ("", "nan"):
                continue
            try:
                row_eid = int(float(raw))
            except (TypeError, ValueError):
                continue
            if row_eid != target_exam_id:
                return jsonify({
                    "success": False,
                    "message": (
                        f"Row {idx + 2} has exam_id {row_eid}, which does not match the "
                        f"selected exam '{target_exam.get('name', '')}' (ID {target_exam_id}). "
                        f"Import cancelled — no questions were added. Remove the exam_id column "
                        f"or make sure every row matches the selected exam."
                    ),
                }), 400

    skipped = 0
    errors = []
    valid_rows = []

    for idx, row in df.iterrows():
        qt = str(row.get("question_text","")).strip() if pd.notna(row.get("question_text")) else ""
        if not qt:
            skipped += 1
            errors.append(f"Row {idx+2}: skipped (empty question_text)")
            continue
        valid_rows.append({
            "exam_id":        target_exam_id,
            "question_text":  qt,
            "option_a":       str(row.get("option_a","")).strip() if pd.notna(row.get("option_a")) else "",
            "option_b":       str(row.get("option_b","")).strip() if pd.notna(row.get("option_b")) else "",
            "option_c":       str(row.get("option_c","")).strip() if pd.notna(row.get("option_c")) else "",
            "option_d":       str(row.get("option_d","")).strip() if pd.notna(row.get("option_d")) else "",
            "correct_answer": str(row.get("correct_answer","")).strip() if pd.notna(row.get("correct_answer")) else "",
            "question_type":  str(row.get("question_type","MCQ")).strip(),
            "image_path":     str(row.get("image_path","")).strip() if pd.notna(row.get("image_path")) else "",
            "positive_marks": int(float(row.get("positive_marks",4))) if pd.notna(row.get("positive_marks")) else 4,
            "negative_marks": float(row.get("negative_marks", 1) or 1) if pd.notna(row.get("negative_marks")) else 1.0,
            "tolerance":      float(row.get("tolerance", 0) or 0) if pd.notna(row.get("tolerance")) else 0.0,
            # Optional column — absent entirely in old CSVs/templates (df.get
            # then returns None, same as any other missing pandas column),
            # blank cells, and files that never heard of this feature all
            # import exactly as before.
            "metadata":       build_question_metadata(str(row.get("source_tag","")).strip() if pd.notna(row.get("source_tag")) else ""),
        })

    inserted = 0
    if valid_rows:
        if create_questions_bulk(valid_rows):
            inserted = len(valid_rows)
        else:
            skipped += len(valid_rows)
            errors.append("Bulk insert failed for all valid rows")

    if inserted:
        msg = f"Imported {inserted} question(s) into '{target_exam.get('name', '')}'."
        if skipped: msg += f" Skipped {skipped}."
        return jsonify({"success": True, "message": msg, "inserted": inserted,
                        "skipped": skipped, "errors": errors[:10] or None})
    return jsonify({"success": False, "message": f"No questions imported. {skipped} errors.",
                    "errors": errors[:10] or None}), 400
