"""
app/routes/api/v01/assistant.py
AI Study Assistant JSON API (v01) — multi-conversation chat.
"""

import threading
import time
from flask import Blueprint, request, jsonify, session

from app.middleware.session_guard import require_user_role
from app.db.ai import (
    list_conversations, get_owned_conversation, create_conversation,
    rename_conversation, delete_conversation, touch_and_count_conversation,
    increment_usage,
)
from app.services.ai_service import (
    get_user_chat_limits, get_formatted_messages, get_history_for_context,
    save_user_message, save_ai_message, get_groq_response,
    validate_domain, derive_title_heuristic, generate_title_via_model,
)
import app.config as config

assistant_api_bp = Blueprint("assistant_api", __name__, url_prefix="/api/v01/assistant")

# Simple in-process limits cache to avoid redundant DB reads
_limits_cache: dict = {}

# Per-user in-flight send guard — closes the double-submit race without
# needing distributed locking (this app runs single-DB, and duplicate sends
# only ever come from the same user's own browser tab retrying too fast).
_sending: set = set()
_sending_guard = threading.Lock()


def _conversation_summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "messageCount": row.get("message_count", 0),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@assistant_api_bp.route("/init")
@require_user_role
def api_assistant_init():
    """Single endpoint returning limits + first page of conversations — sidebar's first paint."""
    user_id = session["user_id"]

    cached = _limits_cache.get(user_id)
    if cached and time.time() - cached["ts"] < config.CACHE_AI_LIMITS_TTL:
        limits = cached["data"]
    else:
        limits = get_user_chat_limits(user_id)
        _limits_cache[user_id] = {"data": limits, "ts": time.time()}

    rows = list_conversations(user_id, limit=20, offset=0)
    has_more = len(rows) > 20
    rows = rows[:20]

    return jsonify({
        "success":       True,
        "dailyLimit":    limits["daily_limit"],
        "questionsUsed": limits["questions_used"],
        "conversations": [_conversation_summary(c) for c in rows],
        "hasMoreConversations": has_more,
    })


@assistant_api_bp.route("/conversations")
@require_user_role
def api_list_conversations():
    user_id = session["user_id"]
    try:
        limit = min(max(int(request.args.get("limit", 20)), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0
    search = (request.args.get("search") or "").strip()[:200] or None

    rows = list_conversations(user_id, limit=limit, offset=offset, search=search)
    has_more = len(rows) > limit
    rows = rows[:limit]

    return jsonify({
        "success":       True,
        "conversations": [_conversation_summary(c) for c in rows],
        "hasMore":       has_more,
    })


@assistant_api_bp.route("/conversations/<int:conversation_id>/messages")
@require_user_role
def api_get_conversation_messages(conversation_id):
    user_id = session["user_id"]
    convo = get_owned_conversation(conversation_id, user_id)
    if not convo:
        return jsonify({"success": False, "message": "Conversation not found."}), 404

    try:
        limit = min(max(int(request.args.get("limit", 30)), 1), 100)
    except (TypeError, ValueError):
        limit = 30
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    result = get_formatted_messages(conversation_id, user_id, limit=limit, offset=offset)
    return jsonify({
        "success":      True,
        "messages":     result["messages"],
        "hasMore":      result["has_more"],
        "conversation": _conversation_summary(convo),
    })


@assistant_api_bp.route("/conversations/<int:conversation_id>", methods=["PATCH"])
@require_user_role
def api_rename_conversation(conversation_id):
    user_id = session["user_id"]
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "message": "Title is required."}), 400
    title = title[:70]

    row = rename_conversation(conversation_id, user_id, title)
    if not row:
        return jsonify({"success": False, "message": "Conversation not found."}), 404
    return jsonify({"success": True, "conversation": _conversation_summary(row)})


@assistant_api_bp.route("/conversations/<int:conversation_id>", methods=["DELETE"])
@require_user_role
def api_delete_conversation(conversation_id):
    user_id = session["user_id"]
    ok = delete_conversation(conversation_id, user_id)
    if not ok:
        return jsonify({"success": False, "message": "Conversation not found."}), 404
    return jsonify({"success": True, "message": "Conversation deleted."})


@assistant_api_bp.route("/messages", methods=["POST"])
@require_user_role
def api_study_chat():
    data    = request.get_json() or {}
    message = data.get("message", "").strip()
    raw_conversation_id = data.get("conversation_id")
    user_id = session["user_id"]

    if not message:
        return jsonify({"success": False, "message": "No message provided."}), 400
    if len(message) > config.AI_MAX_MESSAGE_LENGTH:
        return jsonify({"success": False,
                        "message": f"Message too long. Max {config.AI_MAX_MESSAGE_LENGTH} characters."}), 400
    if len(message) < 3:
        return jsonify({"success": False, "message": "Message too short."}), 400

    is_new_conversation = raw_conversation_id in (None, "", 0)
    conversation_id = None
    convo = None

    if not is_new_conversation:
        try:
            conversation_id = int(raw_conversation_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid conversation."}), 400
        convo = get_owned_conversation(conversation_id, user_id)
        if not convo:
            return jsonify({"success": False, "message": "Conversation not found."}), 404
        if int(convo.get("message_count", 0)) >= config.MAX_MESSAGES_PER_CONVERSATION:
            return jsonify({
                "success": False,
                "message": "This conversation has reached its message limit. Start a new chat to continue.",
                "limit_reached": "conversation",
            }), 400

    with _sending_guard:
        if user_id in _sending:
            return jsonify({"success": False,
                            "message": "Please wait for your previous message to finish."}), 429
        _sending.add(user_id)

    try:
        limits = get_user_chat_limits(user_id)
        if limits["questions_used"] >= limits["daily_limit"]:
            return jsonify({"success": False, "message": "Daily limit reached. Resets at midnight.",
                            "limit_reached": True}), 429

        # Only now are we committed to actually persisting this exchange —
        # create the conversation row here so a rejected/blocked request never
        # leaves an empty conversation behind.
        if is_new_conversation:
            convo = create_conversation(user_id, title=derive_title_heuristic(message))
            if not convo:
                return jsonify({"success": False, "message": "Could not start a new conversation."}), 500
            conversation_id = convo["id"]

        # Domain guardrail — deterministic, zero-cost, runs before any model call.
        refusal = validate_domain(message)
        if refusal is not None:
            save_user_message(user_id, conversation_id, message)
            save_ai_message(user_id, conversation_id, refusal)
            touch_and_count_conversation(conversation_id)
            touch_and_count_conversation(conversation_id)
            return jsonify({
                "success":            True,
                "response":           refusal,
                "conversation_id":    conversation_id,
                "title":              convo["title"],
                "questions_remaining": limits["daily_limit"] - limits["questions_used"],
                "refused":            True,
            })

        # Save user message + load per-conversation context in parallel
        history_result: list = [None]

        def _load_history():
            history_result[0] = get_history_for_context(conversation_id)

        t1 = threading.Thread(target=save_user_message, args=(user_id, conversation_id, message), daemon=True)
        t2 = threading.Thread(target=_load_history, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

        ai_resp = get_groq_response(message, history_result[0] or [])
        title = convo["title"]

        # Save AI reply, increment usage/counters, and (for a brand new chat)
        # refine the title — all in the background after the response is sent.
        def _post():
            save_ai_message(user_id, conversation_id, ai_resp)
            increment_usage(user_id)
            touch_and_count_conversation(conversation_id)  # user message
            touch_and_count_conversation(conversation_id)  # ai message
            _limits_cache.pop(user_id, None)
            if is_new_conversation:
                refined = generate_title_via_model(message)
                if refined:
                    rename_conversation(conversation_id, user_id, refined)

        threading.Thread(target=_post, daemon=True).start()

        return jsonify({
            "success":            True,
            "response":           ai_resp,
            "conversation_id":    conversation_id,
            "title":              title,
            "questions_remaining": limits["daily_limit"] - limits["questions_used"] - 1,
        })
    finally:
        with _sending_guard:
            _sending.discard(user_id)
