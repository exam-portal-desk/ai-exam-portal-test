"""
app/routes/api/v01/admin/ai_centre.py
AI Command Centre JSON API (v01): generate, status, retry.

Background job model: POST /generate -> job_id, GET /status/<job_id> for
live progress, POST /retry/<job_id> to re-run only the batches that failed.

Two-layer job store:
  - `_jobs` (in-memory dict, process-local) is the fast path for active
    polling during a live admin session — unchanged in spirit from before.
  - `ai_generation_jobs` (Postgres, app/db/ai.py) is a write-through durability
    layer underneath it: every batch checkpoint is persisted there as it
    happens, not just at the end. This is what makes a browser refresh, a
    server restart, or a crash mid-generation non-destructive — the admin's
    already-paid-for successful questions and the job's status survive all
    three. GET /status/<job_id> checks memory first and falls back to the
    DB (re-seeding memory) so a job started before a restart is still
    reachable. See migrations/20260831_ai_generation_jobs.sql for the full
    rationale.

Each batch is tracked individually in job["batches"] (list of
{n, status, count, error, error_type, error_friendly}) instead of only the
single last-overwritten progress event — this is what lets a job that had
some batches fail and others succeed report itself accurately as
"partially_completed" (with the successful questions still fully
downloadable) instead of a plain, misleading "done".

job_id also doubles as the safe reference the CSV Editor page uses to pick
up a completed generation job (GET .../status/<id> again) — see
templates/admin/csv_upload.html's _loadAiGeneratedQuestions(). Saving and
CSV export both go through the same single pipeline manually-uploaded CSVs
use (POST /api/v01/admin/questions/import-csv, and the CSV Editor's own
client-side Export CSV).

  POST /admin/ai-command-centre/generate    -> POST /api/v01/admin/ai/generate
  GET  /admin/ai-command-centre/status/<id> -> GET  /api/v01/admin/ai/status/<id>
"""

import uuid
import os
import tempfile
import threading

from flask import request, jsonify, session

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role

# ── In-memory job store (fast path) ─────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()


def _job_update(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def _job_snapshot(job_id: str) -> dict:
    with _jobs_lock:
        return dict(_jobs.get(job_id, {})) if job_id in _jobs else {}


def _persist(job_id: str, **fields):
    """Write-through to the durable table. Never raises — a DB hiccup here
    must not crash generation; the in-memory dict is the source of truth
    for the currently-running thread either way, this is best-effort
    durability on top of it."""
    from app.db.ai import update_generation_job
    try:
        update_generation_job(job_id, **fields)
    except Exception as e:
        print(f"[ai_centre] persist failed for job {job_id}: {e}")


def _ensure_batches_len(job: dict, total: int):
    batches = job.setdefault("batches", [])
    while len(batches) < total:
        batches.append({"n": len(batches) + 1, "status": "pending",
                         "count": 0, "error": None, "error_type": None, "error_friendly": None})


def _set_batch(job: dict, n: int, **fields):
    batches = job.get("batches", [])
    if 1 <= n <= len(batches):
        batches[n - 1].update(fields)


def _compute_final_status(job: dict) -> str:
    batches = job.get("batches", [])
    questions = job.get("questions", [])
    if not questions:
        return "failed"
    if any(b.get("status") == "failed" for b in batches):
        return "partially_completed"
    return "completed"


def _make_progress_handler(job_id: str, batch_offset: int = 0):
    """batch_offset lets a retry run's own internal batch numbering (which
    always restarts at 1) append as NEW slots after the job's existing
    batches, instead of overwriting the original attempt's history."""
    def on_progress(event: dict):
        ev_type = event.get("type", "")
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return

            if ev_type == "context_ready":
                job["pdf_is_vision"] = event.get("is_vision")
                job["pdf_file_uri"] = event.get("file_uri")
                job["pdf_context"] = event.get("context")
                _persist(job_id, pdf_is_vision=job["pdf_is_vision"],
                         pdf_file_uri=job["pdf_file_uri"], pdf_context=job["pdf_context"])
                return

            total_this_run = event.get("total_batches") or 1
            total = batch_offset + total_this_run
            job["total_batches"] = max(job.get("total_batches", 0), total)
            _ensure_batches_len(job, total)

            n = batch_offset + event.get("batch", 0) if event.get("batch") else None

            if ev_type == "batches_ready":
                job["status"] = "processing"
                job["message"] = event.get("message", "")
                job["last_event"] = ev_type
                # total_batches/completed_batches/questions_so_far are kept
                # in-memory only — they're cheap to re-derive from
                # batch_configs/batches/questions on the DB-fallback path
                # (see ai_generation_status()) rather than needing their own
                # columns.
                _persist(job_id, status="processing", batches=job["batches"], message=job["message"])
                return

            if ev_type == "batch_start" and n:
                _set_batch(job, n, status="running")
            elif ev_type == "batch_done" and n:
                new_qs = event.get("questions", [])
                _set_batch(job, n, status="done", count=event.get("batch_count", len(new_qs)),
                           error=None, error_type=None, error_friendly=None)
                job.setdefault("questions", [])
                job["questions"].extend(new_qs)
                job["completed_batches"] = job.get("completed_batches", 0) + 1
            elif ev_type == "batch_error" and n:
                _set_batch(job, n, status="failed", count=0,
                           error=event.get("message", ""),
                           error_type=event.get("error_type"),
                           error_friendly=event.get("error_friendly"))
                job["completed_batches"] = job.get("completed_batches", 0) + 1

            job["message"] = event.get("message", job.get("message", ""))
            job["last_event"] = ev_type
            job["questions_so_far"] = len(job.get("questions", []))

            done = job.get("completed_batches", 0)
            pct = 5
            if ev_type == "vision_detected": pct = 8
            elif ev_type == "uploading":     pct = 10
            elif ev_type == "uploaded":      pct = 15
            elif ev_type == "batch_start":   pct = 20 + int((max(done - 1, 0) / max(total, 1)) * 75)
            elif ev_type in ("batch_done", "batch_error"):
                pct = 20 + int((done / max(total, 1)) * 75)
            job["percent"] = min(max(pct, job.get("percent", 0)), 95)

            if ev_type in ("batch_done", "batch_error"):
                _persist(job_id, batches=job["batches"], questions=job["questions"],
                         percent=job["percent"], message=job["message"])
    return on_progress


def _finalize(job_id: str, exc: Exception | None):
    from app.services.ai_question_generator import classify_error
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if exc is not None and not job.get("questions"):
            # Total failure — nothing usable came out of ANY batch (including
            # the case where the exception happened before any batch even
            # started, e.g. PDF upload/parsing failed outright).
            error_type, friendly, raw = classify_error(exc)
            job["status"] = "failed"
            job["error"] = raw
            job["error_type"] = error_type
            job["error_friendly"] = friendly
            job["message"] = f"Failed: {friendly}"
        else:
            # Either full success, or a partial success where enough batches
            # succeeded that generate_questions() returned normally instead
            # of raising — status reflects whether ALL batches actually made
            # it, not just whether the call itself didn't throw.
            status = _compute_final_status(job)
            job["status"] = status
            n_q = len(job.get("questions", []))
            n_failed = sum(1 for b in job.get("batches", []) if b.get("status") == "failed")
            if status == "completed":
                job["message"] = f"Complete — {n_q} questions generated."
            else:
                job["message"] = f"Partially completed — {n_q} questions generated, {n_failed} batch(es) failed."
        job["percent"] = 100
        _persist(job_id, status=job["status"], error=job.get("error"),
                 error_type=job.get("error_type"), message=job["message"],
                 percent=100, questions=job.get("questions", []), batches=job.get("batches", []))


def _run_generation(job_id: str, mode: str, config_data: dict, pdf_path: str | None, topic: str | None,
                     preloaded_context=None, preloaded_file_uri=None, preloaded_is_vision=None,
                     batch_offset: int = 0):
    """Background thread: runs generation, updates the job store (memory +
    write-through DB), cleans up the temp PDF. Shared by both the initial
    /generate call and /retry (retry passes preloaded_* to skip re-parsing
    the PDF, and batch_offset so its own batch numbers append after the
    original attempt's history instead of overwriting it)."""
    from app.services.ai_question_generator import generate_questions

    on_progress = _make_progress_handler(job_id, batch_offset=batch_offset)
    exc = None
    try:
        generate_questions(
            mode=mode, config=config_data,
            pdf_path=pdf_path, topic=topic,
            progress_callback=on_progress,
            preloaded_context=preloaded_context,
            preloaded_file_uri=preloaded_file_uri,
            preloaded_is_vision=preloaded_is_vision,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        exc = e
    finally:
        _finalize(job_id, exc)
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.unlink(pdf_path)
            except Exception:
                pass


@admin_api_bp.route("/ai/generate", methods=["POST"])
@require_admin_role
def ai_generate_questions():
    try:
        from app.db.ai import create_generation_job, delete_old_generation_jobs
        try:
            delete_old_generation_jobs()
        except Exception:
            pass

        mode    = request.form.get("mode")
        exam_id = int(request.form.get("exam_id") or 0)

        from app.db.exams import get_exam_by_id
        target_exam = get_exam_by_id(exam_id) if exam_id else None
        if not target_exam:
            return jsonify({"success": False, "message": "Select a valid exam first."}), 400

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
            # Direct-Extraction-only — see app/services/ai_question_generator.py's
            # extract_from_pdf() for where this is actually enforced. Gated on
            # mode=='extract' here too so a crafted request can't turn this on
            # for Concept Mining/Pure Generation, which never read this key anyway.
            "keep_duplicates":    mode == "extract" and request.form.get("dedup_mode") == "keep",
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

        from app.services.ai_question_generator import split_batches_public
        batch_configs = split_batches_public(config_data)

        job_id = uuid.uuid4().hex[:12]
        admin_id = session.get("user_id")
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "queued",
                "message": "Starting AI Engine...",
                "last_event": "start",
                "total_batches": len(batch_configs),
                "completed_batches": 0,
                "questions_so_far": 0,
                "percent": 0,
                "questions": [],
                "batches": [],
                "error": None,
                "error_type": None,
                # The admin's OWN selection at generation time — not the
                # exam_id each generated question echoes back in its own
                # JSON (that value comes from the LLM's output, which the
                # model is only asked, not guaranteed, to get right — see
                # QuestionModel's docstring in app/services/ai_question_generator.py).
                # The CSV Editor page reads this field, not the per-question
                # one, as the authoritative target exam for Save to DB.
                "exam_id":   exam_id,
                "exam_name": target_exam.get("name", ""),
                "mode": mode,
                "config": {**config_data, "topic": topic},
                "batch_configs": batch_configs,
                "pdf_context": None, "pdf_file_uri": None, "pdf_is_vision": None,
            }

        create_generation_job(job_id, admin_id, exam_id, target_exam.get("name", ""),
                               mode, {**config_data, "topic": topic}, batch_configs)

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
    job = _job_snapshot(job_id)
    if not job:
        # Not in this process's memory — either a different worker process
        # picked it up, or this process restarted. Fall back to the durable
        # row and re-seed memory so subsequent polls are fast again.
        from app.db.ai import get_generation_job
        row = get_generation_job(job_id)
        if not row:
            return jsonify({"success": False, "message": "Job not found"}), 404
        job = {
            "status": row.get("status"), "message": row.get("message") or "",
            "last_event": row.get("status"),
            "total_batches": len(row.get("batch_configs") or [1]),
            "completed_batches": sum(1 for b in (row.get("batches") or []) if b.get("status") in ("done", "failed")),
            "questions_so_far": len(row.get("questions") or []),
            "percent": row.get("percent") or 0,
            "questions": row.get("questions") or [],
            "batches": row.get("batches") or [],
            "error": row.get("error"), "error_type": row.get("error_type"),
            "exam_id": row.get("exam_id"), "exam_name": row.get("exam_name"),
            "mode": row.get("mode"), "config": row.get("config"),
            "batch_configs": row.get("batch_configs"),
            "pdf_context": row.get("pdf_context"), "pdf_file_uri": row.get("pdf_file_uri"),
            "pdf_is_vision": row.get("pdf_is_vision"),
        }
        with _jobs_lock:
            _jobs[job_id] = job

    # Don't ship the (potentially large) cached PDF text/URI to the browser —
    # it's only needed server-side for retry.
    public_job = {k: v for k, v in job.items() if k not in ("pdf_context", "pdf_file_uri")}
    return jsonify(public_job)


@admin_api_bp.route("/ai/retry/<job_id>", methods=["POST"])
@require_admin_role
def ai_retry_failed_batches(job_id: str):
    """Re-run ONLY the batches that failed, reusing the original run's
    already-extracted PDF text / already-uploaded file URI (never re-parses
    or re-uploads the PDF, never re-calls the AI for batches that already
    succeeded) — see the module docstring and
    app/services/ai_question_generator.py's classify_error/preloaded_*
    docstrings for the full rationale."""
    job = _job_snapshot(job_id)
    if not job:
        from app.db.ai import get_generation_job
        row = get_generation_job(job_id)
        if not row:
            return jsonify({"success": False, "message": "Job not found"}), 404
        with _jobs_lock:
            _jobs[job_id] = job = {
                "status": row.get("status"), "message": row.get("message") or "",
                "last_event": row.get("status"),
                "total_batches": len(row.get("batch_configs") or [1]),
                "completed_batches": sum(1 for b in (row.get("batches") or []) if b.get("status") in ("done", "failed")),
                "questions_so_far": len(row.get("questions") or []),
                "percent": row.get("percent") or 0,
                "questions": row.get("questions") or [],
                "batches": row.get("batches") or [],
                "error": row.get("error"), "error_type": row.get("error_type"),
                "exam_id": row.get("exam_id"), "exam_name": row.get("exam_name"),
                "mode": row.get("mode"), "config": row.get("config") or {},
                "batch_configs": row.get("batch_configs") or [],
                "pdf_context": row.get("pdf_context"), "pdf_file_uri": row.get("pdf_file_uri"),
                "pdf_is_vision": row.get("pdf_is_vision"),
            }

    failed = [b for b in job.get("batches", []) if b.get("status") == "failed"]
    if not failed:
        return jsonify({"success": False, "message": "No failed batches to retry."}), 400

    mode = job.get("mode")
    batch_configs = job.get("batch_configs") or []
    failed_ns = [b["n"] for b in failed]

    retry_counts = {"mcq_count": 0, "msq_count": 0, "numeric_count": 0}
    base_config = dict(job.get("config") or {})
    for n in failed_ns:
        if 1 <= n <= len(batch_configs):
            bc = batch_configs[n - 1]
            retry_counts["mcq_count"] += bc.get("mcq_count", 0)
            retry_counts["msq_count"] += bc.get("msq_count", 0)
            retry_counts["numeric_count"] += bc.get("numeric_count", 0)

    if sum(retry_counts.values()) == 0:
        return jsonify({"success": False, "message": "Could not determine the failed batches' question counts — please re-run generation from scratch."}), 400

    already_generated = [q.get("question_text", "")[:160] for q in job.get("questions", [])]
    retry_config = {**base_config, **retry_counts,
                     "excluded_texts": list(base_config.get("excluded_texts", [])) + already_generated}
    topic = base_config.get("topic")

    if mode in ("extract", "mine") and job.get("pdf_is_vision") is None:
        return jsonify({"success": False, "message":
                         "The original PDF's extracted content is no longer available for this job "
                         "(it predates this durability feature, or was never captured). "
                         "Please re-run generation from scratch for the missing questions."}), 400
    if mode in ("extract", "mine") and job.get("pdf_is_vision") and not job.get("pdf_file_uri"):
        # Inline-vision small PDFs never got a durable file_uri (no Gemini File
        # API round trip was needed) and the original temp upload is deleted
        # right after the run finishes — nothing to safely replay from.
        return jsonify({"success": False, "message":
                         "This PDF was processed inline and its temporary copy has already been "
                         "cleaned up — please re-upload the PDF and generate again for the missing questions."}), 400

    # Mark the retried slots so they stop counting as "still failing" — the
    # retry's own outcome (success or failure) lands in freshly appended
    # batch slots instead of overwriting this history.
    with _jobs_lock:
        for n in failed_ns:
            _set_batch(job, n, status="retried")
        _jobs[job_id]["batches"] = job["batches"]
        _jobs[job_id]["status"] = "processing"
    _persist(job_id, batches=job["batches"], status="processing")

    batch_offset = len(job.get("batches", []))
    thread = threading.Thread(
        target=_run_generation,
        args=(job_id, mode, retry_config, None, topic,
              job.get("pdf_context"), job.get("pdf_file_uri"), job.get("pdf_is_vision")),
        kwargs={"batch_offset": batch_offset},
        daemon=True,
    )
    thread.start()
    return jsonify({"success": True, "job_id": job_id, "retrying_batches": failed_ns})
