"""
app/routes/explanation.py
REST API endpoints for the AI Explanation Generator feature.

Endpoints:
  GET  /api/explain/limits/<question_id>     — rate-limit quota check
  GET  /api/explain/history/<question_id>    — fetch all saved explanations (page load)
  POST /api/explain/<question_id>            — generate new explanation

All endpoints require a valid user session (@require_user_role).
"""

import config
from flask import Blueprint, jsonify, session, request

from app.middleware.session_guard import require_user_role
from app.db.questions import get_question_by_id
from app.services.explanation_service import (
    check_rate_limits,
    generate_explanation,
    fetch_history,
)

explanation_bp = Blueprint("explanation", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/explain/limits/<question_id>
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/api/explain/limits/<int:question_id>", methods=["GET"])
@require_user_role
def api_explain_limits(question_id: int):
    """
    Return current rate-limit status for (current_user, question_id).
    Used by the frontend to decide button state on page load.
    """
    user_id = session["user_id"]
    limits  = check_rate_limits(user_id, question_id)

    return jsonify({
        "success":            True,
        "allowed":            limits["allowed"],
        "reason":             limits.get("reason"),
        "reset_time":         limits.get("reset_time", ""),
        "daily_used":         limits["daily_used"],
        "daily_remaining":    limits["daily_remaining"],
        "question_used":      limits["question_used"],
        "question_remaining": limits["question_remaining"],
        "daily_limit":        config.EXPLANATION_DAILY_LIMIT,
        "per_question_limit": config.EXPLANATION_PER_QUESTION_LIMIT,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/explain/history/<question_id>
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/api/explain/history/<int:question_id>", methods=["GET"])
@require_user_role
def api_explain_history(question_id: int):
    """
    Return all previously generated explanations for (current_user, question_id).
    Called on page load — no API hit, just DB read.

    Response 200:
        {
            "success": true,
            "history": [
                {"id": 1, "explanation": "...", "generated_at": "2025-01-01T12:00:00"},
                ...
            ],
            "question_remaining": int,
            "daily_remaining": int,
            "reset_time": str,
        }
    """
    user_id = session["user_id"]
    history = fetch_history(user_id, question_id)
    limits  = check_rate_limits(user_id, question_id)

    return jsonify({
        "success":            True,
        "history":            history,
        "question_remaining": limits["question_remaining"],
        "daily_remaining":    limits["daily_remaining"],
        "reset_time":         limits.get("reset_time", ""),
        "allowed":            limits["allowed"],
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/explain/<question_id>
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/api/explain/<int:question_id>", methods=["POST"])
@require_user_role
def api_generate_explanation(question_id: int):
    """
    Generate a new step-by-step AI explanation, save it, return it + history.

    Request JSON (optional):
        { "given_answer": "B" }

    Response 200:
        {
            "success":            true,
            "explanation":        str,       <- new explanation (Markdown + LaTeX)
            "history":            list,      <- all explanations including new one
            "daily_remaining":    int,
            "question_remaining": int,
            "reset_time":         str,
        }

    Response 404 / 429 / 500:
        { "success": false, "message": str, "limit_reached"?: bool, "reset_time"?: str }
    """
    user_id  = session["user_id"]

    question = get_question_by_id(question_id)
    if not question:
        return jsonify({"success": False, "message": "Question not found."}), 404

    body         = request.get_json(silent=True) or {}
    given_answer = str(body.get("given_answer", "") or "").strip()
    if given_answer:
        question["given_answer"] = given_answer

    result = generate_explanation(question, user_id)

    if not result["success"]:
        status_code = 429 if result.get("limit_reached") else 500
        return jsonify(result), status_code

    return jsonify(result), 200
