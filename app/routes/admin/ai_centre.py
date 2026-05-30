"""
app/routes/admin/ai_centre.py
AI Command Centre — generate, save, and export questions via Gemini.
Background job model: POST /generate → job_id, GET /status/<job_id> for live progress.
"""

import os
import io
import csv
import uuid
import tempfile
import threading

from flask import render_template, request, jsonify, Response

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams
from app.db import supabase

# ── In-memory job store ──────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()


def _job_update(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def _run_generation(job_id: str, mode: str, config_data: dict,
                    pdf_path: str | None, topic: str | None):
    """Background thread: runs generation, updates job store, cleans up PDF."""
    from ai_question_generator import generate_questions

    def on_progress(event: dict):
        ev_type = event.get("type", "")
        total = event.get("total_batches") or _jobs[job_id].get("total_batches", 1)
        done  = event.get("batch", 0) if "done" in ev_type else _jobs[job_id].get("completed_batches", 0)

        pct = 5
        if ev_type in ("uploading",):            pct = 10
        elif ev_type in ("uploaded",):           pct = 15
        elif ev_type in ("batches_ready",):      pct = 20
        elif ev_type == "batch_start":
            pct = 20 + int(((done - 1) / total) * 75)
        elif ev_type == "batch_done":
            pct = 20 + int((done / total) * 75)
        elif ev_type == "batch_error":
            pct = 20 + int((done / total) * 75)

        _job_update(job_id,
                    last_event=ev_type,
                    message=event.get("message", ""),
                    total_batches=total,
                    completed_batches=done if "done" in ev_type or "error" in ev_type else max(done - 1, 0),
                    questions_so_far=event.get("questions_so_far", _jobs[job_id].get("questions_so_far", 0)),
                    percent=min(pct, 95))

    try:
        questions = generate_questions(
            mode=mode, config=config_data,
            pdf_path=pdf_path, topic=topic,
            progress_callback=on_progress,
        )
        _job_update(job_id, status="done", questions=questions,
                    percent=100, message=f"Complete — {len(questions)} questions generated.",
                    questions_so_far=len(questions))
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Return partial results if any were collected before the failure
        partial = _jobs[job_id].get("questions", [])
        _job_update(job_id, status="failed",
                    error=str(e), percent=100,
                    message=f"Failed: {str(e)[:120]}",
                    questions=partial)
    finally:
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.unlink(pdf_path)
            except Exception:
                pass


# ── Routes ───────────────────────────────────────────────────────────────────

@admin_bp.route("/ai-command-centre", methods=["GET"])
@require_admin_role
def ai_command_centre():
    return render_template("admin/ai_command_centre.html", exams=get_all_exams())


@admin_bp.route("/ai-command-centre/generate", methods=["POST"])
@require_admin_role
def ai_generate_questions():
    try:
        mode    = request.form.get("mode")
        exam_id = int(request.form.get("exam_id") or 0)

        def _int(key, default):
            return int(request.form.get(key) or default)

        def _float(key, default):
            return float(request.form.get(key) or default)

        import json as _json
        _excl_raw = request.form.get("excluded_texts", "[]")
        try:
            _excluded_texts = _json.loads(_excl_raw) if _excl_raw.strip() else []
        except Exception:
            _excluded_texts = []

        config_data = {
            "exam_id":             exam_id,
            "difficulty":          request.form.get("difficulty", "Medium"),
            "mcq_count":           _int("mcq_count", 0),
            "msq_count":           _int("msq_count", 0),
            "numeric_count":       _int("numeric_count", 0),
            "mcq_plus":            _float("mcq_plus", 4),
            "mcq_minus":           _float("mcq_minus", 1),
            "msq_plus":            _float("msq_plus", 4),
            "msq_minus":           _float("msq_minus", 2),
            "numeric_plus":        _float("numeric_plus", 3),
            "numeric_tolerance":   _float("numeric_tolerance", 0.01),
            "custom_instructions": request.form.get("custom_instructions", ""),
            "excluded_texts":      _excluded_texts,
        }

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

        job_id = uuid.uuid4().hex[:12]
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "running",
                "message": "Starting AI Engine...",
                "last_event": "start",
                "total_batches": 1,
                "completed_batches": 0,
                "questions_so_far": 0,
                "percent": 0,
                "questions": [],
                "error": None,
            }

        thread = threading.Thread(
            target=_run_generation,
            args=(job_id, mode, config_data, pdf_path, topic),
            daemon=True,
        )
        thread.start()
        return jsonify({"success": True, "job_id": job_id})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Failed to start job: {e}"}), 500


@admin_bp.route("/ai-command-centre/status/<job_id>", methods=["GET"])
@require_admin_role
def ai_generation_status(job_id: str):
    with _jobs_lock:
        job = dict(_jobs.get(job_id, {}))
    if not job:
        return jsonify({"success": False, "message": "Job not found"}), 404
    return jsonify(job)


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