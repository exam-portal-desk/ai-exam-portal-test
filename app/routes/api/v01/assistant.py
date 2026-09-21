"""
app/routes/api/v01/assistant.py
AI Study Assistant JSON API (v01) — multi-conversation chat.

POST /messages answers in one of two ways, chosen by the request body:
  {"message", "conversation_id"}                  one JSON reply once the model has finished
  {"message", "conversation_id", "stream": true}  Server-Sent Events: {"type":"delta"} pieces as the model
                                                  produces them, then one {"type":"done"} (or {"type":"error"})

Either way a failed AI call is reported as a failure (with a message that is safe to show): nothing is saved,
the daily limit is not charged, and a chat that was created only for this message is removed again.
"""

import json
import logging
import threading
import time
from flask import Blueprint, Response, g, request, jsonify, session, stream_with_context

from app.middleware.session_guard import require_user_role
from app.db.ai import (
    list_conversations, get_owned_conversation, create_conversation,
    rename_conversation, delete_conversation, touch_and_count_conversation,
    increment_usage, get_turn_context, find_focus_conversation, clear_conversation_focus, focus_supported,
)
from app.services.ai_service import (
    get_assistant_limits, get_user_chat_limits, get_formatted_messages, history_from_rows,
    save_user_message, save_ai_message, save_exchange, build_assistant_request, ask_assistant, stream_assistant,
    validate_domain, derive_title_heuristic, generate_title_via_model,
)
from app.services.question_access import check_question_access
from app.services.question_context import build_card, build_focus_block, question_image_paths, starter_chips
from app.services.image_storage_service import fetch_question_image, QuestionImageError
from app.services.ai import AIError, AIProviderError, public_error, resolve as resolve_model
from app.utils.helpers import strip_ai_reasoning
import app.config as config
from app.entitlements.guard import check_request, gate_blueprint, require_feature

assistant_api_bp = Blueprint("assistant_api", __name__, url_prefix="/api/v01/assistant")
gate_blueprint(assistant_api_bp, "ai_assistant")

log = logging.getLogger("smartaiexam.ai.assistant")      # shares the AI layer's handler (see services/ai/client.py)

# Simple in-process limits cache to avoid redundant DB reads
_limits_cache: dict = {}

# Per-user in-flight send guard — closes the double-submit race without
# needing distributed locking (this app runs single-DB, and duplicate sends
# only ever come from the same user's own browser tab retrying too fast).
_sending: set = set()
_sending_guard = threading.Lock()


_FOCUS_NOT_READY = "Discussing a question with the Assistant isn't available yet. Please try again later."


def _focus_payload(access, question_id) -> dict:
    """What the browser is told about a focus. Allowed: the compact card and the starter actions (labels and references
    only, never the question). Denied: only the reason the student may be shown."""
    if not access.allowed:
        return {"allowed": False, "reason": access.reason, "message": access.message, "question_id": question_id}
    ctx = access.context()
    return {"allowed": True, "card": build_card(ctx), "chips": starter_chips(ctx)}


def _stored_focus_payload(user_id: int, focus: dict) -> dict:
    """The same, for the focus stored on a conversation. Re-checked now: a stored focus grants nothing by itself."""
    try:
        question_id, result_id = int(focus["question_id"]), int(focus["result_id"])
    except (KeyError, TypeError, ValueError):
        return {"allowed": False, "reason": "not_found", "message": "This question isn't available for discussion."}
    return _focus_payload(check_question_access(user_id, question_id, result_id), question_id)


_IMAGE_CACHE_SECONDS = 300
_MSG_NO_VISION = ("This question has a diagram, but the AI model the Assistant currently uses can't view images, "
                  "so it can't discuss this question yet. Please try again later.")
_MSG_IMAGE_UNAVAILABLE = "The diagram for this question couldn't be loaded right now, so the Assistant can't discuss it. Please try again in a moment."


class _ImageProblem(Exception):
    """The question has a diagram that cannot be given to the model. Carries what may be shown to the student."""

    def __init__(self, message: str, status: int, kind: str, retryable: bool):
        super().__init__(message)
        self.message, self.status, self.kind, self.retryable = message, status, kind, retryable


def _load_focus_images(question: dict) -> list:
    """The question's diagram(s) as [(base64, mime)], in order, ready to attach to the request; [] for a text-only question.

    The paths come from the question row the access policy just allowed, never from the browser, and the bytes are read
    through the storage abstraction: nothing is fetched by URL and no storage detail leaves the server. The model is the
    assistant's normal one from the AI registry; if it cannot take images this says so, and a diagram that cannot be
    loaded is an error too: the model is never sent a text-only request as if it had seen the diagram."""
    paths = question_image_paths(question)
    if not paths:
        return []
    if "vision" not in resolve_model("assistant_chat").capabilities:
        log.warning("question image not sent: the assistant_chat model has no vision capability (question_id=%s)", question.get("id"))
        raise _ImageProblem(_MSG_NO_VISION, 422, "vision_unsupported", retryable=False)
    images = []
    for path in paths:
        try:
            images.append(fetch_question_image(path, cache_seconds=_IMAGE_CACHE_SECONDS))
        except QuestionImageError as e:
            log.warning("question image unavailable question_id=%s reason=%s detail=%s", question.get("id"), e.reason, e.detail)
            raise _ImageProblem(_MSG_IMAGE_UNAVAILABLE, 503, "image_unavailable", retryable=True)
    return images


def _parse_new_focus(raw):
    """A focus sent with the FIRST message of a new conversation: {question_id, result_id}, both integers, or None."""
    if raw in (None, False):
        return None
    try:
        return {"question_id": int(raw["question_id"]), "result_id": int(raw["result_id"])}
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid question focus.")


def _questions_remaining(limits: dict, after_this: bool = False):
    """Questions left today, or None when the student has no daily limit."""
    if limits["daily_limit"] is None:
        return None
    return limits["daily_limit"] - limits["questions_used"] - (1 if after_this else 0)


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
        "maxMessages":   limits["conversation_limit"],
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

    # A focused conversation also reports its question card (freshly authorization-checked). That check runs
    # alongside the message read, so opening a focused chat costs no extra round trip.
    focus_holder, focus_thread = {}, None
    if convo.get("focus") and offset == 0:
        def _load_focus():
            focus_holder["payload"] = _stored_focus_payload(user_id, convo["focus"])
        focus_thread = threading.Thread(target=_load_focus, daemon=True)
        focus_thread.start()

    result = get_formatted_messages(conversation_id, user_id, limit=limit, offset=offset)
    if focus_thread:
        focus_thread.join()
    return jsonify({
        "success":      True,
        "messages":     result["messages"],
        "hasMore":      result["has_more"],
        "conversation": _conversation_summary(convo),
        "focus":        focus_holder.get("payload"),
    })


@assistant_api_bp.route("/focus")
@require_user_role
@require_feature("response_ai_discussion")
def api_question_focus():
    """Resolve "Discuss with AI" for one question: is it allowed, what does the context bar show, and does this student
    already have a conversation about exactly this question and result? Read-only: nothing is created here (the
    conversation is created by the first message, as for every other chat)."""
    user_id = session["user_id"]
    try:
        question_id = int(request.args.get("question_id", ""))
        result_id = int(request.args["result_id"]) if request.args.get("result_id") else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid question."}), 400
    if not focus_supported():
        return jsonify({"success": False, "message": _FOCUS_NOT_READY}), 503

    access = check_question_access(user_id, question_id, result_id)
    payload = _focus_payload(access, question_id)
    if not access.allowed:
        return jsonify({"success": False, **payload}), 403
    existing = find_focus_conversation(user_id, question_id, access.result["id"])
    return jsonify({"success": True, **payload, "conversation_id": existing["id"] if existing else None})


@assistant_api_bp.route("/conversations/<int:conversation_id>/focus", methods=["DELETE"])
@require_user_role
@require_feature("response_ai_discussion")
def api_clear_conversation_focus(conversation_id):
    """Stop discussing the question: the conversation goes on as a general chat. Its messages are kept."""
    if not clear_conversation_focus(conversation_id, session["user_id"]):
        return jsonify({"success": False, "message": "Conversation not found."}), 404
    return jsonify({"success": True})


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


@assistant_api_bp.before_request
def _start_clock():
    """Start of the request, so the time spent in authentication/session checks is measured too."""
    g._assistant_t0 = time.perf_counter()


class _Stages:
    """Wall-clock per stage of one request, written as a single log line at the end (never sent to the user)."""

    def __init__(self, t0: float):
        self.t0 = self.last = t0
        self.ms: dict = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.ms[name] = self.ms.get(name, 0) + int((now - self.last) * 1000)
        self.last = now

    def set(self, name: str, ms: int) -> None:
        self.ms[name] = ms

    def log(self, outcome: str, **extra) -> None:
        total = int((time.perf_counter() - self.t0) * 1000)
        parts = " ".join(f"{k}={v}ms" for k, v in self.ms.items())
        more = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
        log.info("ASSISTANT TIMING outcome=%s %s total=%dms %s", outcome, parts, total, more)


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _drop_empty_new_chat(user_id: int, conversation_id, is_new_conversation: bool) -> None:
    """A chat created just for a message that then failed must not linger as an empty conversation."""
    if is_new_conversation and conversation_id:
        try:
            delete_conversation(conversation_id, user_id)
        except Exception as e:                                    # cosmetic clean-up: never mask the real error
            log.warning("could not remove the empty conversation after a failed request: %s", type(e).__name__)


def _failure(exc: BaseException):
    """(message safe to show, HTTP status, machine-readable kind) for any exception from the AI call."""
    message, status = public_error(exc)
    kind = getattr(exc, "kind", None) or ("config" if isinstance(exc, AIError) else "error")
    log.warning("assistant request failed kind=%s status=%s type=%s", kind, status, type(exc).__name__)
    return message, status, kind


def _save_exchange(user_id: int, conversation_id: int, message: str, reply: str) -> int:
    """Persist one successful exchange (the student's message, then the reply) in a single database round trip."""
    t = time.perf_counter()
    save_exchange(user_id, conversation_id, message, reply)
    return int((time.perf_counter() - t) * 1000)


def _start_conversation(user_id: int, message: str, focus, access):
    """Create the row for a new chat. A chat about a question is titled after it ("Q14 · Physics Mock 3") and keeps only
    the two references; any other chat is titled from its first message, as always."""
    if focus and access:
        card = build_card(access.context())
        return create_conversation(user_id, title=(f"{card['label']} · {card['exam']}" if card["exam"] else card["label"])[:70],
                                   focus=focus)
    return create_conversation(user_id, title=derive_title_heuristic(message))


def _after_reply(user_id: int, conversation_id: int, message: str, is_new_conversation: bool) -> None:
    """Bookkeeping that nothing waits for: usage, message counters and (new general chats) a better title.
    `is_new_conversation` is False for a chat about a question: its title already names the question."""
    t = time.perf_counter()
    try:
        increment_usage(user_id)
        touch_and_count_conversation(conversation_id)     # student's message
        touch_and_count_conversation(conversation_id)     # reply
        _limits_cache.pop(user_id, None)
        if is_new_conversation:
            refined = generate_title_via_model(message)
            if refined:
                rename_conversation(conversation_id, user_id, refined)
    except Exception as e:
        log.warning("assistant bookkeeping failed: %s", type(e).__name__)
    log.info("ASSISTANT BOOKKEEPING ms=%d new_chat=%s", int((time.perf_counter() - t) * 1000), is_new_conversation)


@assistant_api_bp.route("/messages", methods=["POST"])
@require_user_role
def api_study_chat():
    stages = _Stages(getattr(g, "_assistant_t0", time.perf_counter()))
    stages.mark("auth_session")

    data    = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    raw_conversation_id = data.get("conversation_id")
    want_stream = data.get("stream") is True
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
    turn = None                    # existing chat: its conversation row, today's usage and recent history, from ONE query
    new_focus = None               # a question focus is only honoured when it STARTS a conversation

    if is_new_conversation:
        try:
            new_focus = _parse_new_focus(data.get("focus"))
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        if new_focus and not focus_supported():
            return jsonify({"success": False, "message": _FOCUS_NOT_READY}), 503
        denied = check_request("response_ai_discussion") if new_focus else None
        if denied is not None:
            return denied
    else:
        try:
            conversation_id = int(raw_conversation_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid conversation."}), 400
        turn = get_turn_context(user_id, conversation_id, config.AI_CONTEXT_RECENT_MESSAGES)
        convo = turn
        stages.mark("conversation_context")
        if not convo:
            return jsonify({"success": False, "message": "Conversation not found."}), 404
        denied = check_request("response_ai_discussion") if convo.get("focus") else None
        if denied is not None:
            return denied
        conversation_limit = get_assistant_limits(user_id)["conversation_limit"]
        if conversation_limit is not None and int(convo.get("message_count", 0)) >= conversation_limit:
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

    handed_off = False        # True once a streaming response owns releasing the in-flight guard
    try:
        limits = ({"daily_limit": get_assistant_limits(user_id)["daily_limit"], "questions_used": int(turn.get("questions_used") or 0)}
                  if turn else get_user_chat_limits(user_id))
        stages.mark("daily_limit")
        if limits["daily_limit"] is not None and limits["questions_used"] >= limits["daily_limit"]:
            return jsonify({"success": False, "message": "Daily limit reached. Resets at midnight.",
                            "limit_reached": True}), 429

        # A conversation about an exam question. Its focus is only a pair of references; what the student may see is
        # decided NOW, from the current data, on every message (never from the stored focus, never from the browser).
        focus = new_focus if is_new_conversation else (convo.get("focus") or None)
        access = None
        if focus:
            try:
                access = check_question_access(user_id, int(focus["question_id"]), int(focus["result_id"]))
            except (KeyError, TypeError, ValueError):
                access = check_question_access(user_id, 0)                      # a malformed focus is simply denied
            stages.mark("focus_access")
            if not access.allowed:
                return jsonify({"success": False, "message": access.message, "focus_locked": True,
                                "reason": access.reason}), 403
        # Domain guardrail — deterministic, zero-cost, runs before any model call.
        refusal = validate_domain(message)
        if refusal is not None:
            if is_new_conversation:
                convo = _start_conversation(user_id, message, focus, access)
                if not convo:
                    return jsonify({"success": False, "message": "Could not start a new conversation."}), 500
                conversation_id = convo["id"]
            save_user_message(user_id, conversation_id, message)
            save_ai_message(user_id, conversation_id, refusal)
            touch_and_count_conversation(conversation_id)
            touch_and_count_conversation(conversation_id)
            stages.mark("refusal_saved")
            stages.log("refused")
            return jsonify({
                "success":            True,
                "response":           refusal,
                "conversation_id":    conversation_id,
                "title":              convo["title"],
                "questions_remaining": _questions_remaining(limits),
                "refused":            True,
            })

        # Conversation context first, from what is already saved. (The new message is saved together with its
        # reply once there is one: a failed call therefore leaves no half-finished exchange behind, and loading
        # the context can no longer race with saving the message that is being answered.)
        history = history_from_rows(turn["history"]) if turn else []
        images, focus_block = [], None
        if access:
            # Before any conversation is created or anything is charged: a question whose diagram cannot be given to
            # the model is reported, not answered blind.
            try:
                images = _load_focus_images(access.question)
            except _ImageProblem as e:
                stages.log("image_problem", kind=e.kind)
                return jsonify({"success": False, "message": e.message, "error": e.kind, "retryable": e.retryable}), e.status
            except AIError as e:                                            # e.g. the assistant model is not configured
                text, status, kind = _failure(e)
                stages.log("failed", kind=kind)
                return jsonify({"success": False, "message": text, "error": kind}), status
            stages.mark("question_image")
            focus_block = build_focus_block(access.context(), image_count=len(images))
        ai_request = build_assistant_request(message, history, focus_block=focus_block, images=images)
        stages.mark("prompt")

        # A new chat gets its row now so the reply can be attached to it; if the call fails it is removed again.
        if is_new_conversation:
            convo = _start_conversation(user_id, message, focus, access)
            stages.mark("create_conversation")
            if not convo:
                return jsonify({"success": False, "message": "Could not start a new conversation."}), 500
            conversation_id = convo["id"]
        title = convo["title"]
        remaining = _questions_remaining(limits, after_this=True)

        # ── Streaming reply ────────────────────────────────────────────────────────────────────────────────
        if want_stream:
            handed_off = True

            def _events():
                t_call = time.perf_counter()
                first_ms, pieces, saved = None, [], False
                try:
                    for chunk in stream_assistant(message, history, request=ai_request):
                        if chunk.text:
                            if first_ms is None:
                                first_ms = int((time.perf_counter() - t_call) * 1000)
                            pieces.append(chunk.text)
                            yield _sse({"type": "delta", "text": chunk.text})
                    reply = strip_ai_reasoning("".join(pieces))
                    if not reply.strip():
                        raise AIProviderError("The AI provider returned an empty reply.", kind="malformed")
                    stages.set("provider_first_token", first_ms or 0)
                    stages.set("provider_total", int((time.perf_counter() - t_call) * 1000))
                    stages.last = time.perf_counter()
                    stages.set("save_messages", _save_exchange(user_id, conversation_id, message, reply))
                    saved = True
                    threading.Thread(target=_after_reply, args=(user_id, conversation_id, message, is_new_conversation and not focus),
                                     daemon=True).start()
                    yield _sse({"type": "done", "success": True, "response": reply, "conversation_id": conversation_id,
                                "title": title, "questions_remaining": remaining})
                    stages.log("ok")
                except GeneratorExit:                       # the browser went away
                    if not saved:                           # ...before the reply was complete and stored
                        _drop_empty_new_chat(user_id, conversation_id, is_new_conversation)
                    stages.log("ok" if saved else "client_disconnected")
                    raise
                except Exception as e:
                    text, status, kind = _failure(e)
                    if not saved:
                        _drop_empty_new_chat(user_id, conversation_id, is_new_conversation)
                    stages.log("failed", kind=kind)
                    yield _sse({"type": "error", "success": False, "message": text, "error": kind, "status": status})
                finally:
                    with _sending_guard:
                        _sending.discard(user_id)

            return Response(stream_with_context(_events()), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

        # ── Normal reply ───────────────────────────────────────────────────────────────────────────────────
        t_call = time.perf_counter()
        try:
            ai_resp = ask_assistant(message, history, request=ai_request)
            if not ai_resp.text.strip():
                raise AIProviderError("The AI provider returned an empty reply.", kind="malformed")
        except Exception as e:
            text, status, kind = _failure(e)
            _drop_empty_new_chat(user_id, conversation_id, is_new_conversation)
            stages.set("provider_total", int((time.perf_counter() - t_call) * 1000))
            stages.log("failed", kind=kind)
            return jsonify({"success": False, "message": text, "error": kind}), status
        stages.set("provider_total", int((time.perf_counter() - t_call) * 1000))
        stages.last = time.perf_counter()

        # Save the exchange and do the bookkeeping after the answer has been sent.
        def _post():
            try:
                _save_exchange(user_id, conversation_id, message, ai_resp.text)
            except Exception as e:
                log.warning("could not save the exchange: %s", type(e).__name__)
            _after_reply(user_id, conversation_id, message, is_new_conversation and not focus)

        threading.Thread(target=_post, daemon=True).start()
        stages.log("ok", model_ms=ai_resp.elapsed_ms, tokens_out=ai_resp.output_tokens,
                   tokens_reasoning=ai_resp.reasoning_tokens)

        return jsonify({
            "success":            True,
            "response":           ai_resp.text,
            "conversation_id":    conversation_id,
            "title":              title,
            "questions_remaining": remaining,
        })
    finally:
        if not handed_off:
            with _sending_guard:
                _sending.discard(user_id)
