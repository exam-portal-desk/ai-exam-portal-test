"""
app/routes/api/v01/admin/ai_centre.py
AI Command Centre JSON API (v01): generate, status.
Relocated from app/routes/admin/ai_centre.py.
Background job model unchanged: POST /generate -> job_id, GET /status/<job_id>
for live progress, backed by the same in-memory job store (still
process-local, not shared across multiple worker processes — a known,
pre-existing constraint noted in the architecture audit and out of scope
for this routing refactor).

  POST /admin/ai-command-centre/generate    -> POST /api/v01/admin/ai/generate
  GET  /admin/ai-command-centre/status/<id> -> GET  /api/v01/admin/ai/status/<id>

job_id also doubles as the safe reference the CSV Editor page uses to pick
up a completed generation job (GET .../status/<id> again) — see
templates/admin/csv_upload.html's _loadAiGeneratedQuestions(). Saving and
CSV export both now go through the same single pipeline manually-uploaded
CSVs use (POST /api/v01/admin/questions/import-csv, and the CSV Editor's
own client-side Export CSV) — the JSON-based /ai/save and /ai/export-csv
endpoints that used to serve the old inline preview on this page have been
removed since nothing calls them anymore.
"""

import uuid
import os
import tempfile
import threading

from flask import request, jsonify

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role

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
    from app.services.ai_question_generator import generate_questions

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


@admin_api_bp.route("/ai/generate", methods=["POST"])
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


@admin_api_bp.route("/ai/status/<job_id>", methods=["GET"])
@require_admin_role
def ai_generation_status(job_id: str):
    with _jobs_lock:
        job = dict(_jobs.get(job_id, {}))
    if not job:
        return jsonify({"success": False, "message": "Job not found"}), 404
    return jsonify(job)
