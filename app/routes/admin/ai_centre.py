"""
app/routes/admin/ai_centre.py
AI Command Centre — generate, save, and export questions via Gemini.
"""

import os
import io
import csv
import tempfile

from flask import render_template, request, jsonify, Response

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams
from app.db import supabase


@admin_bp.route("/ai-command-centre", methods=["GET"])
@require_admin_role
def ai_command_centre():
    return render_template("admin/ai_command_centre.html", exams=get_all_exams())


@admin_bp.route("/ai-command-centre/generate", methods=["POST"])
@require_admin_role
def ai_generate_questions():
    try:
        mode    = request.form.get("mode")
        exam_id = int(request.form.get("exam_id", 0))

        config_data = {
            "exam_id":              exam_id,
            "difficulty":           request.form.get("difficulty", "Medium"),
            "mcq_count":            int(request.form.get("mcq_count", 0)),
            "msq_count":            int(request.form.get("msq_count", 0)),
            "numeric_count":        int(request.form.get("numeric_count", 0)),
            "mcq_plus":             float(request.form.get("mcq_plus", 4)),
            "mcq_minus":            float(request.form.get("mcq_minus", 1)),
            "msq_plus":             float(request.form.get("msq_plus", 4)),
            "msq_minus":            float(request.form.get("msq_minus", 2)),
            "numeric_plus":         float(request.form.get("numeric_plus", 3)),
            "numeric_tolerance":    float(request.form.get("numeric_tolerance", 0.01)),
            "custom_instructions":  request.form.get("custom_instructions", ""),
        }

        from ai_question_generator import generate_questions

        pdf_path = None
        if mode in ("extract", "mine"):
            if "pdf_file" not in request.files:
                return jsonify({"success": False, "message": "PDF file required"}), 400
            f = request.files["pdf_file"]
            if not f.filename:
                return jsonify({"success": False, "message": "No file selected"}), 400
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                f.save(tmp.name)
                pdf_path = tmp.name

        topic = None
        if mode == "pure":
            topic = request.form.get("topic", "")
            if not topic:
                return jsonify({"success": False, "message": "Topic required"}), 400

        try:
            questions = generate_questions(mode=mode, config=config_data,
                                           pdf_path=pdf_path, topic=topic)
            return jsonify({"success": True, "questions": questions, "count": len(questions)})
        finally:
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.unlink(pdf_path)
                except Exception:
                    pass

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Generation failed: {e}"}), 500


@admin_bp.route("/ai-command-centre/save", methods=["POST"])
@require_admin_role
def ai_save_questions():
    data      = request.get_json() or {}
    questions = data.get("questions", [])
    if not questions:
        return jsonify({"success": False, "message": "No questions to save"}), 400

    rows = [
        {
            "exam_id":        q["exam_id"],
            "question_text":  q["question_text"],
            "option_a":       q.get("option_a", ""),
            "option_b":       q.get("option_b", ""),
            "option_c":       q.get("option_c", ""),
            "option_d":       q.get("option_d", ""),
            "correct_answer": q["correct_answer"],
            "question_type":  q.get("question_type", "MCQ"),
            "image_path":     None,
            "positive_marks": int(q.get("positive_marks", 4)),
            "negative_marks": float(q.get("negative_marks", 1)),
            "tolerance":      float(q.get("tolerance", 0)),
        }
        for q in questions
    ]

    try:
        supabase.table("questions").insert(rows).execute()
        return jsonify({"success": True,
                        "message": f"Saved {len(rows)} questions.",
                        "count":   len(rows)})
    except Exception as e:
        return jsonify({"success": False, "message": f"Save failed: {e}"}), 500


@admin_bp.route("/ai-command-centre/export-csv", methods=["POST"])
@require_admin_role
def ai_export_csv():
    data      = request.get_json() or {}
    questions = data.get("questions", [])
    if not questions:
        return jsonify({"success": False, "message": "No questions to export"}), 400

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["exam_id","question_text","option_a","option_b","option_c","option_d",
                     "correct_answer","question_type","image_path","positive_marks",
                     "negative_marks","tolerance"])
    for q in questions:
        writer.writerow([q["exam_id"], q["question_text"],
                         q.get("option_a",""), q.get("option_b",""),
                         q.get("option_c",""), q.get("option_d",""),
                         q["correct_answer"], q.get("question_type","MCQ"), "",
                         q.get("positive_marks",4), q.get("negative_marks",1),
                         q.get("tolerance",0)])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ai_generated_questions.csv"},
    )