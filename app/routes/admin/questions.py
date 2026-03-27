"""
app/routes/admin/questions.py
Admin question management: CRUD, bulk ops, CSV import/export.
"""

import io
import csv
import pandas as pd
from flask import render_template, request, redirect, url_for, flash, jsonify, Response

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams, get_exam_by_id
from app.db.questions import (
    get_questions_by_exam, get_question_by_id,
    create_question, create_questions_bulk,
    update_question, delete_question, delete_questions_bulk,
)
from app.db import supabase
from app.utils.helpers import safe_float, safe_int
from app.utils.sanitize import sanitize_html


def _exam_list():
    return [{"id": int(e["id"]), "name": e.get("name", f"Exam {e['id']}")} for e in get_all_exams()]


@admin_bp.route("/questions", methods=["GET"])
@require_admin_role
def questions_index():
    exams_list = _exam_list()
    selected   = request.args.get("exam_id", type=int)
    if not selected and exams_list:
        selected = exams_list[0]["id"]

    questions_list = []
    if selected:
        for q in get_questions_by_exam(selected):
            questions_list.append({
                "id":            int(q["id"]),
                "exam_id":       int(q["exam_id"]),
                "question_text": sanitize_html(q.get("question_text","")),
                "option_a":      sanitize_html(q.get("option_a","")),
                "option_b":      sanitize_html(q.get("option_b","")),
                "option_c":      sanitize_html(q.get("option_c","")),
                "option_d":      sanitize_html(q.get("option_d","")),
                "correct_answer":q.get("correct_answer",""),
                "question_type": q.get("question_type","MCQ"),
                "image_path":    q.get("image_path",""),
                "positive_marks":q.get("positive_marks","4"),
                "negative_marks":q.get("negative_marks","1"),
                "tolerance":     q.get("tolerance",""),
            })

    return render_template("admin/questions.html", exams=exams_list,
                           selected_exam_id=selected, questions=questions_list)


@admin_bp.route("/questions/add-ajax", methods=["POST"])
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
    })
    if result:
        return jsonify({"success": True, "message": "Question added."})
    return jsonify({"success": False, "message": "Failed to add question."}), 500


@admin_bp.route("/questions/get/<int:question_id>", methods=["GET"])
@require_admin_role
def get_question_ajax(question_id):
    q = get_question_by_id(question_id)
    if not q:
        return jsonify({"success": False, "message": "Not found."}), 404
    return jsonify({"success": True, "question": q})


@admin_bp.route("/questions/edit-ajax/<int:question_id>", methods=["POST"])
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
    return jsonify({"success": ok, "message": "Updated." if ok else "Failed."})


@admin_bp.route("/questions/delete/<int:question_id>", methods=["POST"])
@require_admin_role
def delete_question_route(question_id):
    q = get_question_by_id(question_id)
    exam_id = int(q["exam_id"]) if q else None
    ok = delete_question(question_id)
    flash("Question deleted." if ok else "Failed to delete.", "info" if ok else "danger")
    return redirect(url_for("admin.questions_index", exam_id=exam_id) if exam_id
                    else url_for("admin.questions_index"))


@admin_bp.route("/questions/delete-multiple", methods=["POST"])
@require_admin_role
def delete_multiple_questions():
    payload = request.get_json(force=True) or {}
    ids = [int(i) for i in (payload.get("ids") or []) if str(i).strip()]
    if not ids:
        return jsonify({"success": False, "message": "No IDs provided"}), 400
    deleted = delete_questions_bulk(ids)
    return jsonify({"success": True, "deleted": deleted})


@admin_bp.route("/questions/batch-add", methods=["POST"])
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
        }
        for it in items if (it.get("question_text") or "").strip()
    ]
    if not rows:
        return jsonify({"success": False, "message": "No valid rows"}), 400
    ok = create_questions_bulk(rows)
    return jsonify({"success": ok, "added": len(rows) if ok else 0})


@admin_bp.route("/questions/bulk-update", methods=["POST"])
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

    questions = [q for q in get_questions_by_exam(exam_id)
                 if str(q.get("question_type","")).strip().upper() == qtype.upper()]
    if not questions:
        return jsonify({"success": True, "updated": 0})

    upd = {}
    if pos is not None and str(pos).strip(): upd["positive_marks"] = int(pos)
    if neg is not None and str(neg).strip(): upd["negative_marks"] = float(neg)
    if tol is not None:                      upd["tolerance"]      = float(tol)

    updated = 0
    for q in questions:
        if update_question(int(q["id"]), upd):
            updated += 1
    return jsonify({"success": True, "updated": updated})


@admin_bp.route("/questions/export-csv/<int:exam_id>")
@require_admin_role
def export_questions_csv(exam_id):
    exam = get_exam_by_id(exam_id)
    if not exam:
        flash("Exam not found.", "error")
        return redirect(url_for("admin.questions_index"))

    qs = get_questions_by_exam(exam_id)
    if not qs:
        flash("No questions found.", "warning")
        return redirect(url_for("admin.questions_index", exam_id=exam_id))

    cols = ["exam_id","question_text","option_a","option_b","option_c","option_d",
            "correct_answer","question_type","image_path","positive_marks","negative_marks","tolerance"]
    rows = [{c: q.get(c,"") for c in cols} for q in qs]
    df   = pd.DataFrame(rows)[cols]

    out  = io.StringIO()
    df.to_csv(out, index=False, encoding="utf-8")
    fname = f"questions_{exam.get('name','exam').replace(' ','_')}_{exam_id}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@admin_bp.route("/questions/import-csv", methods=["POST"])
@require_admin_role
def import_questions_csv():
    if "csv_file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    f = request.files["csv_file"]
    if not f.filename or not f.filename.endswith(".csv"):
        return jsonify({"success": False, "message": "File must be a CSV"}), 400

    try:
        df = pd.read_csv(f)
    except Exception as e:
        return jsonify({"success": False, "message": f"Cannot read CSV: {e}"}), 400

    required = ["exam_id","question_text","option_a","option_b","option_c","option_d",
                "correct_answer","question_type","image_path","positive_marks","negative_marks","tolerance"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return jsonify({"success": False, "message": f"Missing columns: {', '.join(missing)}"}), 400

    valid_eids = {int(e["id"]) for e in get_all_exams()}
    inserted = skipped = 0
    errors = []

    for idx, row in df.iterrows():
        eid = int(row.get("exam_id",0)) if pd.notna(row.get("exam_id")) else 0
        qt  = str(row.get("question_text","")).strip() if pd.notna(row.get("question_text")) else ""
        if eid not in valid_eids or not qt:
            skipped += 1
            errors.append(f"Row {idx+2}: skipped")
            continue
        try:
            supabase.table("questions").insert({
                "exam_id":        eid,
                "question_text":  qt,
                "option_a":       str(row.get("option_a","")).strip() if pd.notna(row.get("option_a")) else "",
                "option_b":       str(row.get("option_b","")).strip() if pd.notna(row.get("option_b")) else "",
                "option_c":       str(row.get("option_c","")).strip() if pd.notna(row.get("option_c")) else "",
                "option_d":       str(row.get("option_d","")).strip() if pd.notna(row.get("option_d")) else "",
                "correct_answer": str(row.get("correct_answer","")).strip() if pd.notna(row.get("correct_answer")) else "",
                "question_type":  str(row.get("question_type","MCQ")).strip(),
                "image_path":     str(row.get("image_path","")).strip() if pd.notna(row.get("image_path")) else "",
                "positive_marks": int(row.get("positive_marks",4)) if pd.notna(row.get("positive_marks")) else 4,
                "negative_marks": float(row.get("negative_marks",1)) if pd.notna(row.get("negative_marks")) else 1,
                "tolerance":      float(row.get("tolerance",0)) if pd.notna(row.get("tolerance")) else 0,
            }).execute()
            inserted += 1
        except Exception as e:
            skipped += 1; errors.append(f"Row {idx+2}: {e}")

    if inserted:
        msg = f"Imported {inserted} question(s)."
        if skipped: msg += f" Skipped {skipped}."
        return jsonify({"success": True, "message": msg, "inserted": inserted,
                        "skipped": skipped, "errors": errors[:10] or None})
    return jsonify({"success": False, "message": f"No questions imported. {skipped} errors.",
                    "errors": errors[:10] or None}), 400