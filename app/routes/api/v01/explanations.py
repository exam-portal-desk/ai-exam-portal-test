"""
app/routes/api/v01/explanations.py
REST API endpoints for the AI Explanation Generator feature (v01).
Relocated from app/routes/explanation.py — logic unchanged, only the URL
prefix moved under /api/v01/explanations.

Endpoints:
  GET    /api/v01/explanations/<question_id>/limits               — rate-limit quota check
  GET    /api/v01/explanations/<question_id>                      — fetch all saved explanations (page load)
  POST   /api/v01/explanations/<question_id>                      — generate new explanation
  DELETE /api/v01/explanations/<question_id>/<explanation_id>     — delete one saved explanation

All endpoints require a valid user session (@require_user_role).
"""

from flask import Blueprint, jsonify, session, request

from app.entitlements.guard import gate_blueprint
from app.middleware.session_guard import require_user_role
from app.services.explanation_service import (
    check_rate_limits,
    generate_explanation,
    fetch_history,
    delete_explanation,
    is_generating,
)
from app.services.question_access import check_question_access
from app.services.question_context import clean_given_answer

explanation_bp = Blueprint("explanation", __name__, url_prefix="/api/v01/explanations")
gate_blueprint(explanation_bp, "ai_explanation")


def _result_id(raw):
    """The result the page was showing (optional). Only ever used to ask the access policy about it."""
    try:
        return int(raw) if raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def _locked(access):
    """Reading or generating an explanation reveals the answer key, so it needs the same access as the response page:
    the student's own, released result for this question, and no attempt in progress on that exam."""
    return jsonify({"success": False, "message": access.message, "locked": True}), 403


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v01/explanations/<question_id>/limits
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/<int:question_id>/limits", methods=["GET"])
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
        "daily_limit":        limits.get("daily_limit"),
        "per_question_limit": limits.get("per_question_limit"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v01/explanations/<question_id>
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/<int:question_id>", methods=["GET"])
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
    access = check_question_access(user_id, question_id, _result_id(request.args.get("result_id")))
    if not access.allowed:
        return _locked(access)

    history = fetch_history(user_id, question_id)
    limits  = check_rate_limits(user_id, question_id)

    return jsonify({
        "success":            True,
        "history":            history,
        "question_remaining": limits["question_remaining"],
        "daily_remaining":    limits["daily_remaining"],
        "reset_time":         limits.get("reset_time", ""),
        "allowed":            limits["allowed"],
        "generating":         is_generating(user_id, question_id),
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v01/explanations/<question_id>
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/<int:question_id>", methods=["POST"])
@require_user_role
def api_generate_explanation(question_id: int):
    """
    Generate a new step-by-step AI explanation, save it, return it + history.

    Request JSON (optional):
        { "result_id": 55 }     <- which of the student's results the page is showing (default: their latest)

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

    body   = request.get_json(silent=True) or {}
    access = check_question_access(user_id, question_id, _result_id(body.get("result_id")))
    if not access.allowed:
        return _locked(access)

    # The question and the student's answer come from the database, through the same check: a browser can no
    # longer ask for the explanation of a question it may not see, nor claim a different given answer.
    question = dict(access.question)
    given_answer = clean_given_answer((access.response or {}).get("given_answer"))
    if given_answer:
        question["given_answer"] = given_answer

    result = generate_explanation(question, user_id)

    if not result["success"]:
        status_code = 429 if result.get("limit_reached") else 500
        return jsonify(result), status_code

    return jsonify(result), 200


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/v01/explanations/<question_id>/<explanation_id>
# ─────────────────────────────────────────────────────────────────────────────

@explanation_bp.route("/<int:question_id>/<int:explanation_id>", methods=["DELETE"])
@require_user_role
def api_delete_explanation(question_id: int, explanation_id: int):
    """
    Delete ONE previously-generated explanation belonging to the current user.

    Deletion is scoped to explanation_id + the session's user_id (see
    explanation_service.delete_explanation / db.explanation.delete_explanation) —
    never to question_id/user_id alone — so this can only ever remove the
    exact explanation the student selected, never another explanation for
    the same question, another question, or another user's data.

    Response 200:
        { "success": true, "history": list }   <- remaining explanations for this question

    Response 404:
        { "success": false, "message": str }   <- not found / not owned by this user
    """
    user_id = session["user_id"]

    result = delete_explanation(user_id, question_id, explanation_id)

    if not result["success"]:
        return jsonify(result), 404

    return jsonify(result), 200
