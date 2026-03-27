"""
app/routes/ai_assistant.py
AI Study Assistant routes.
"""

import threading
from flask import Blueprint, render_template, request, jsonify, session

from app.middleware.session_guard import require_user_role
from app.db.ai import delete_user_chat_history
from app.services.ai_service import (
    get_user_chat_limits, get_formatted_history,
    get_history_for_context, save_user_message, save_ai_message,
    get_groq_response, increment_usage,
)
import config

ai_bp = Blueprint("ai", __name__)

# Simple in-process limits cache to avoid redundant DB reads
_limits_cache: dict = {}


@ai_bp.route("/ai-assistant")
@require_user_role
def ai_assistant():
    return render_template(
        "ai_assistant.html",
        username=session.get("username"),
        full_name=session.get("full_name"),
    )


@ai_bp.route("/api/assistant-init")
@require_user_role
def api_assistant_init():
    """Single endpoint returning limits + history — replaces two separate calls."""
    import time
    user_id = session["user_id"]

    cached = _limits_cache.get(user_id)
    if cached and time.time() - cached["ts"] < config.CACHE_AI_LIMITS_TTL:
        limits = cached["data"]
    else:
        limits = get_user_chat_limits(user_id)
        _limits_cache[user_id] = {"data": limits, "ts": time.time()}

    history = get_formatted_history(user_id, limit=50)
    return jsonify({
        "success":      True,
        "dailyLimit":   limits["daily_limit"],
        "questionsUsed": limits["questions_used"],
        "history":      history,
    })


@ai_bp.route("/api/study-chat", methods=["POST"])
@require_user_role
def api_study_chat():
    data    = request.get_json() or {}
    message = data.get("message","").strip()
    user_id = session["user_id"]

    if not message:
        return jsonify({"success": False, "message": "No message provided."}), 400
    if len(message) > config.AI_MAX_MESSAGE_LENGTH:
        return jsonify({"success": False,
                        "message": f"Message too long. Max {config.AI_MAX_MESSAGE_LENGTH} characters."}), 400
    if len(message) < 3:
        return jsonify({"success": False, "message": "Message too short."}), 400

    limits = get_user_chat_limits(user_id)
    if limits["questions_used"] >= limits["daily_limit"]:
        return jsonify({"success": False, "message": "Daily limit reached. Resets at midnight.",
                        "limit_reached": True}), 429

    # Save user message + load context in parallel
    history_result: list = [None]

    def _load_history():
        history_result[0] = get_history_for_context(user_id, last_n=4)

    t1 = threading.Thread(target=save_user_message, args=(user_id, message), daemon=True)
    t2 = threading.Thread(target=_load_history, daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

    ai_resp = get_groq_response(message, history_result[0] or [])

    # Save AI reply and increment usage in background
    def _post():
        save_ai_message(user_id, ai_resp)
        increment_usage(user_id)
        _limits_cache.pop(user_id, None)  # invalidate limits cache

    threading.Thread(target=_post, daemon=True).start()

    return jsonify({
        "success":            True,
        "response":           ai_resp,
        "questions_remaining": limits["daily_limit"] - limits["questions_used"] - 1,
    })


@ai_bp.route("/api/clear-chat-history", methods=["POST"])
@require_user_role
def api_clear_chat_history():
    user_id = session["user_id"]
    ok = delete_user_chat_history(user_id)
    _limits_cache.pop(user_id, None)
    return jsonify({"success": ok, "message": "Chat history cleared." if ok else "Failed."})