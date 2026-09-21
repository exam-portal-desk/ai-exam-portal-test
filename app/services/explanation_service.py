"""
app/services/explanation_service.py
AI explanation generator with Chain-of-Thought (CoT) prompting.

Flow:
  1. Check rate limits (daily + per-question).
  2. Build structured CoT prompt from question data.
  3. Image present -> fetch image -> base64 -> the "explanation_vision" model.
     Text only    -> the "explanation_text" model.
     Which model each feature uses (and its provider) is set in
     Admin > AI Configuration, resolved per request via app/services/ai.
  4. Save explanation to ai_explanation_history (persists across reloads).
  5. Increment usage counter only after successful generation.
  6. Return explanation + full history + remaining quota.

Public API:
  check_rate_limits(user_id, question_id)  -> dict
  generate_explanation(question, user_id)  -> dict
  fetch_history(user_id, question_id)      -> list[dict]
  delete_explanation(user_id, question_id, explanation_id) -> dict
"""

import random
import threading
import time
from datetime import datetime, timezone

import app.config as config
from app.entitlements import limit_for
from app.db.explanation import (
    get_explanation_usage,
    get_daily_total_usage,
    increment_explanation_usage,
    save_explanation,
    get_explanation_history,
    delete_explanation as db_delete_explanation,
    get_reset_time_str,
)
from app.services.image_storage_service import load_question_image
from app.services.ai import (
    AIConfigError, AIProviderError, AIRequest, Message, Part, log_problem, resolve, stream,
)
from app.services.question_context import build_options_block, clean_given_answer, question_has_image
from app.utils.helpers import strip_ai_reasoning


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_TEXT_FEATURE   = "explanation_text"
_VISION_FEATURE = "explanation_vision"
_ENTITLEMENT    = "ai_explanation"


def _remaining(limit, used: int):
    """What is left of a limit; None when there is no limit."""
    return None if limit is None else max(0, limit - used)


# ─────────────────────────────────────────────────────────────────────────────
# Public: rate-limit checker (read-only, no side effects)
# ─────────────────────────────────────────────────────────────────────────────

def check_rate_limits(user_id: int, question_id: int) -> dict:
    """
    Check both daily-total and per-question limits. The limits come from the student's plan
    (config/entitlements.json, feature "ai_explanation"); a limit that is not configured is None and never reached.

    Returns:
        {
            allowed: bool,
            reason: str | None,
            reset_time: str,          <- e.g. "Resets in 3h 12m at 12:00 AM IST"
            daily_limit: int | None,
            daily_used: int,
            daily_remaining: int | None,
            per_question_limit: int | None,
            question_used: int,
            question_remaining: int | None,
        }
    """
    daily_limit        = limit_for(user_id, _ENTITLEMENT, "per_day")
    per_question_limit = limit_for(user_id, _ENTITLEMENT, "per_question")
    daily_used    = get_daily_total_usage(user_id)
    q_row         = get_explanation_usage(user_id, question_id)
    question_used = int(q_row.get("used_count", 0)) if q_row else 0

    daily_remaining    = _remaining(daily_limit, daily_used)
    question_remaining = _remaining(per_question_limit, question_used)
    reset_time         = get_reset_time_str()
    result = {
        "allowed": True, "reason": None, "reset_time": reset_time,
        "daily_limit": daily_limit, "daily_used": daily_used, "daily_remaining": daily_remaining,
        "per_question_limit": per_question_limit, "question_used": question_used, "question_remaining": question_remaining,
    }

    if daily_limit is not None and daily_used >= daily_limit:
        return {**result, "allowed": False, "daily_remaining": 0,
                "reason": f"Daily limit of {daily_limit} explanations reached. {reset_time}."}

    if per_question_limit is not None and question_used >= per_question_limit:
        return {**result, "allowed": False, "question_remaining": 0,
                "reason": f"You've used all {per_question_limit} explanations for this question today. {reset_time}."}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public: fetch saved history (for page load)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_history(user_id: int, question_id: int) -> list:
    """
    Return all previously generated explanations for this (user, question).
    Used on page load so student sees past generations without hitting the API.
    Each item: {id, explanation, generated_at}
    """
    return get_explanation_history(user_id, question_id)


# ─────────────────────────────────────────────────────────────────────────────
# Public: delete one explanation
# ─────────────────────────────────────────────────────────────────────────────

def delete_explanation(user_id: int, question_id: int, explanation_id) -> dict:
    """
    Delete ONE previously-generated explanation belonging to this user.

    question_id is required purely so we can hand back the remaining
    history for that question in one response (avoiding a second round
    trip) — the actual delete is scoped to explanation_id + user_id only
    (see db.explanation.delete_explanation), never to question_id/user_id
    alone, so this can never remove a sibling explanation for the same
    question or touch another student's data.

    Does NOT touch daily/per-question usage counters — the quota was
    already spent generating the explanation; deleting it from view
    doesn't refund that spend, which also prevents a generate/delete
    loop from being used to bypass the daily limit.

    Returns:
        {success: True, history: list[dict]}   on success
        {success: False, message: str}         if not found / not owned
    """
    if not explanation_id:
        return {"success": False, "message": "Missing explanation id."}

    deleted = db_delete_explanation(explanation_id, user_id)
    if not deleted:
        return {"success": False, "message": "Explanation not found."}

    return {
        "success": True,
        "history": get_explanation_history(user_id, question_id),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public: main generation entry point
# ─────────────────────────────────────────────────────────────────────────────

# In-process guard against a duplicate Groq call for the same (user, question)
# explanation request — this is what actually stops a second request for the
# SAME explanation from ever reaching Groq, regardless of what causes the
# duplicate HTTP request (a race before the frontend button disables, a
# double form submit, a network-level retry, etc). Client-side button
# disabling is the first line of defense but can't be trusted alone since
# it's just browser state; this is the authoritative one.
#
# Note: this is a single-process guard (a plain in-memory set), so it only
# protects requests handled by the same worker process. That's the case
# that matters here — it directly prevents the "same user double-clicks /
# same request fires twice" failure mode. It is not a substitute for a
# distributed lock if this app is ever run with multiple worker processes
# sharing one Groq key under heavy concurrent load from many different
# students, which is a separate, genuine account-level quota concern (see
# generate_explanation's docstring).
_inflight_lock = threading.Lock()
_inflight_requests: set = set()


def is_generating(user_id: int, question_id: int) -> bool:
    """True while an explanation for this student and question is being generated (the same in-flight set the duplicate
    guard above uses). Read-only: lets a page that was reloaded, or opened in a second tab, show the generating state
    and pick the result up instead of offering to start another one."""
    with _inflight_lock:
        return (int(user_id), int(question_id)) in _inflight_requests


def generate_explanation(question: dict, user_id: int) -> dict:
    """
    Generate a CoT explanation via the configured AI model, save it, increment usage.

    `question` must contain at minimum:
        id, question_text, correct_answer, question_type
    Optional: option_a/b/c/d, image_path, given_answer

    Returns on success:
        {
            success: True,
            explanation: str,          <- the new explanation (Markdown + LaTeX)
            history: list[dict],       <- ALL explanations for this question (including new)
            daily_remaining: int,
            question_remaining: int,
            reset_time: str,
        }

    Returns on failure:
        {success: False, message: str, limit_reached?: bool}

    NOTE on 429s: a genuine Groq account-level rate limit (too many tokens/
    requests per minute across all students using this app's shared Groq
    key) is NOT something this function can eliminate — it's a provider
    quota, not an application bug. What this function DOES guarantee is
    that it will never itself be the cause of extra Groq traffic: exactly
    one Groq call per explanation (plus, only if a response is genuinely
    truncated, one bounded continuation call — see _generate_complete),
    and a concurrent duplicate request for the same (user, question) is
    rejected before it ever reaches Groq.
    """
    question_id = int(question.get("id", 0))
    key = (int(user_id), question_id)

    with _inflight_lock:
        if key in _inflight_requests:
            return {
                "success": False,
                "message": "An explanation is already being generated for this question. Please wait for it to finish.",
            }
        _inflight_requests.add(key)

    try:
        return _generate_explanation_locked(question, user_id, question_id)
    finally:
        with _inflight_lock:
            _inflight_requests.discard(key)


def _generate_explanation_locked(question: dict, user_id: int, question_id: int) -> dict:
    """Actual generation logic — only ever called while generate_explanation()
    holds this (user_id, question_id) in _inflight_requests. Unchanged from
    before other than being split out of generate_explanation()."""

    # ── 1. Rate-limit gate ────────────────────────────────────────────────
    limits = check_rate_limits(user_id, question_id)
    if not limits["allowed"]:
        return {
            "success": False,
            "message": limits["reason"],
            "limit_reached": True,
            "reset_time": limits["reset_time"],
            "daily_remaining": limits["daily_remaining"],
            "question_remaining": limits["question_remaining"],
        }

    # ── 2. Resolve image (if any) ─────────────────────────────────────────
    image_path = str(question.get("image_path") or "").strip()
    has_image  = image_path and image_path.lower() not in ("", "nan", "none")
    img_b64    = None
    img_mime   = "image/jpeg"

    if has_image:
        # Read straight through the storage abstraction. (This used to download the app's own relative image URL with
        # `requests`, which can never work — "Invalid URL, no scheme" — so image questions were always text-only.)
        loaded = load_question_image(image_path)
        if loaded:
            img_b64, img_mime = loaded
        else:
            has_image = False   # fall back to text-only — don't block student
            # the prompt must not claim a diagram is attached when the model is not getting one
            question = {**question, "image_path": ""}

    # ── 3. Build CoT prompt ───────────────────────────────────────────────
    prompt = _build_cot_prompt(question)

    # ── 4. Call the AI model ──────────────────────────────────────────────
    try:
        raw = _call_ai_vision(prompt, img_b64, img_mime) if (has_image and img_b64) else _call_ai_text(prompt)
    except AIConfigError as e:
        log_problem("explanation model not usable", e)
        return {"success": False, "message": "AI service temporarily unavailable. Please try again."}
    except AIProviderError as e:                              # (already logged by the AI client, once per failed attempt)
        if e.status_code == 429 or e.kind == "rate_limited":
            return {
                "success": False,
                "message": "The AI explanation service is busy right now. Please wait a moment and try again.",
            }
        if e.kind in ("timeout", "connect_timeout"):
            return {"success": False, "message": "The AI is taking longer than usual right now. Please try again in a moment."}
        return {"success": False, "message": "AI service temporarily unavailable. Please try again."}
    except Exception as e:
        log_problem("explanation generation", e)
        return {"success": False, "message": "AI service temporarily unavailable. Please try again."}

    if not raw:
        return {"success": False, "message": "AI returned an empty response. Please try again."}

    # ── 5. Persist explanation ────────────────────────────────────────────
    # The explanation exists now: the student gets it whatever happens to the database. (This result used to be ignored: when the
    # save failed the count was still spent, the history came back without it, and the student saw nothing.)
    saved = save_explanation(user_id, question_id, raw)

    # ── 6. Count it: the AI call was made and its answer is delivered ─────
    if not increment_explanation_usage(user_id, question_id):
        log_problem("explanation usage not recorded", RuntimeError(f"user {user_id}, question {question_id}"))

    # ── 7. Recalculate remaining after increment ──────────────────────────
    new_daily_used = get_daily_total_usage(user_id)
    q_row          = get_explanation_usage(user_id, question_id)
    new_q_used     = int(q_row.get("used_count", 0)) if q_row else 0

    daily_remaining    = _remaining(limits.get("daily_limit"), new_daily_used)
    question_remaining = _remaining(limits.get("per_question_limit"), new_q_used)

    # ── 8. Return full history so frontend can render all generations ──────
    history = get_explanation_history(user_id, question_id)
    if not saved:                                   # shown, but not kept: the page marks it "not saved" (see response.html)
        history = list(history) + [{
            "id": -int(time.time() * 1000),         # never a real id: negative, so it can never be deleted or confused with a saved one
            "explanation": raw,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "unsaved": True,
        }]

    return {
        "success":            True,
        "explanation":        raw,
        "saved":              bool(saved),
        "history":            history,
        "daily_remaining":    daily_remaining,
        "question_remaining": question_remaining,
        "reset_time":         get_reset_time_str(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CoT prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_cot_prompt(question: dict) -> str:
    """
    Build a structured Chain-of-Thought prompt.
    Response format: Markdown with $...$ inline and $$...$$ block LaTeX.
    """

    qtext       = str(question.get("question_text", "")).strip()
    correct_ans = str(question.get("correct_answer", "")).strip()
    given_ans   = clean_given_answer(question.get("given_answer"))

    # The options block and the image test are shared with the Assistant's focused mode (question_context.py).
    options_block = build_options_block(question)
    wrong_line = f"**Student's Wrong Answer:** {given_ans}\n" if given_ans else ""
    image_note = (
        "*(A diagram/image is attached — analyse it as part of the question.)*\n\n"
        if question_has_image(question)
        else ""
    )

    return f"""You are an expert exam tutor. A student answered the question below incorrectly and wants to \
quickly understand why, and what the right answer/approach actually is.

{image_note}**Question:**
{qtext}

{options_block}{wrong_line}**Correct Answer:** {correct_ans}

---

Write an explanation that is only as long as this question genuinely needs — proportional to its \
complexity. A simple MCQ needs a short explanation. A conceptual question needs enough to clarify the \
concept. A multi-step numeric/derivation question needs the working shown, but nothing padded on top of it.

Do NOT pad the response to fill out every section below regardless of whether it's needed. Use this \
structure as a menu, not a checklist — include a section only if it adds real value for this specific \
question; skip or shorten sections that don't:

## Why It's Wrong
Directly explain why the selected/given answer is incorrect and what the correct answer/concept is. \
Be direct but encouraging. For a simple factual MCQ, this alone may be the whole explanation.

## Solution
Only for questions that involve real working (a calculation, derivation, or multi-step reasoning). Number \
each step. Skip this section entirely for questions with no working to show.

## Key Concept
One or two lines reinforcing the core concept or formula, only if it's not already obvious from the \
sections above.

STRICT RULES:

1. Never expose your internal reasoning, analysis process, or how you arrived at the explanation — write \
only the final explanation itself, nothing about how you produced it. Never use tags like <think>.
2. Never add generic filler, restated question text, "let me explain", or a closing pep-talk line.
3. Use Markdown formatting. Use LaTeX for every mathematical expression — never plain-text math.
4. Block equations: $$\\Large ...$$   Important inline equations: $\\Large ...$
5. Multi-step derivations use:
$$\\Large
\\begin{{aligned}}
...
\\end{{aligned}}
$$
6. Keep equations large and readable, with clean spacing, on both desktop and mobile. For every \
fraction, use \\dfrac{{numerator}}{{denominator}} — never \\frac{{...}}{{...}} — since \\frac renders \
visibly compressed/shrunk inline while \\dfrac keeps it at full, readable display size.
7. For chemistry, use mhchem syntax and ALWAYS wrap it in math delimiters — never write \\ce{{...}} \
outside $...$ or $$...$$:
   - Formulas: $\\ce{{H2O}}$, $\\ce{{CO2}}$
   - Reactions: $$\\ce{{2H2 + O2 -> 2H2O}}$$
   - Ions/charges: $\\ce{{Na+}}$, $\\ce{{SO4^{{2-}}}}$
   - Equilibrium: $\\ce{{CH3COOH + H2O <=> CH3COO^- + H3O^+}}$
   - Conditions above/below the arrow: $$\\ce{{A ->[\\text{{condition}}] B}}$$
8. Keep language clear and student-friendly. The student is preparing for a competitive exam.
9. Concise means no filler — it never means stopping early. Always carry every calculation through to \
the explicit final numeric/final answer, and every conceptual explanation through to its actual \
conclusion. An unfinished step, a dangling equation, or a solution that stops before reaching the \
answer is not acceptable at any length."""


# ─────────────────────────────────────────────────────────────────────────────
# Model callers
# ─────────────────────────────────────────────────────────────────────────────

# Generous headroom for a full multi-step LaTeX/mhchem solution — the old
# 1500-token cap was the actual root cause of explanations stopping midway
# (see _generate_complete below for how we also detect and recover from
# any case that still runs past this).
_MAX_TOKENS = 3000
_CONTINUATION_MAX_TOKENS = 1200
_MAX_CONTINUATIONS = 1  # hard cap: can never turn into an unbounded retry loop


# A failure that is the provider's or the network's momentary trouble, so trying again is worthwhile. Never a wrong key, a
# rejected model, a bad request or a configuration problem: repeating those cannot help.
_RETRY_KINDS = frozenset({"timeout", "connect_timeout", "unreachable", "server_error", "rate_limited", "malformed"})


def _backoff_seconds(kind: str, attempt: int) -> float:
    """How long to wait before attempt+1. A rate limit needs longer (the provider's window has to move on)."""
    base = (6.0, 14.0) if kind == "rate_limited" else (2.0, 5.0)
    return base[min(attempt - 1, len(base) - 1)] + random.uniform(0.0, 1.0)


def _stream_once(feature: str, messages: list, max_tokens: int, low_reasoning: bool, total_timeout: float) -> tuple[str, str]:
    """One STREAMED attempt: (text, finish_reason). Streaming is the point: with a plain request the whole reply has to arrive
    inside one read timeout, and a big vision model writes a full solution far slower than that (~10-20 tokens/s). Here only
    silence (EXPLANATION_IDLE_TIMEOUT: before the first word or between two pieces) and a ceiling for the attempt can time out."""
    request = AIRequest(
        messages=messages, max_tokens=max_tokens, temperature=0.3,
        timeout=config.EXPLANATION_IDLE_TIMEOUT, total_timeout=total_timeout,
        reasoning_effort="low" if low_reasoning else None,
    )
    pieces, finish = [], None
    for chunk in stream(feature, request):
        if chunk.text:
            pieces.append(chunk.text)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    return strip_ai_reasoning("".join(pieces).strip()), finish or "stop"


def _chat_completion(feature: str, messages: list, max_tokens: int, low_reasoning: bool = False,
                     deadline: float | None = None) -> tuple[str, str]:
    """Returns (content, finish_reason) — finish_reason is "length" when the model was cut off purely for hitting
    max_tokens, as opposed to "stop" when it actually finished on its own.
    `low_reasoning` asks a model that thinks before answering to think as little as it allows (ignored by any
    model whose configuration does not say how; see AIRequest.reasoning_effort).

    A transient failure (see _RETRY_KINDS) is retried, up to EXPLANATION_MAX_ATTEMPTS attempts, after a short pause, and
    only while `deadline` (a time.monotonic() value: the whole request's budget) leaves room for another attempt. Each failed
    attempt has already been logged, once, by the AI client; nothing is logged for a call that works."""
    attempt = 0
    while True:
        attempt += 1
        remaining = (deadline - time.monotonic()) if deadline is not None else config.EXPLANATION_TOTAL_TIMEOUT
        try:
            return _stream_once(feature, messages, max_tokens, low_reasoning,
                                total_timeout=max(10.0, min(config.EXPLANATION_TOTAL_TIMEOUT, remaining)))
        except AIProviderError as e:
            if e.kind not in _RETRY_KINDS or attempt >= config.EXPLANATION_MAX_ATTEMPTS:
                raise
            wait = _backoff_seconds(e.kind, attempt)
            if deadline is not None and deadline - time.monotonic() < wait + 20:      # no room left for a useful attempt
                raise
            time.sleep(wait)


def _can_reduce_reasoning(feature: str) -> bool:
    """Does this feature's current model have a way to be asked to think less? (options.low_reasoning_effort)"""
    try:
        return bool(resolve(feature).options.get("low_reasoning_effort"))
    except Exception:
        return False


def _generate_complete(feature: str, messages: list) -> str:
    """
    Root-cause fix for incomplete explanations: the previous implementation
    always took whatever the model returned at face value, even when its own
    finish_reason said the response was cut off for hitting max_tokens
    (as opposed to the model actually finishing). That's why explanations
    sometimes stopped mid-derivation.

    This calls the model once with a generous token budget; if — and only if —
    the response was genuinely truncated by the token limit, it makes ONE
    follow-up call asking the model to continue exactly where it left off,
    then stitches the two pieces together. Capped at _MAX_CONTINUATIONS so
    a persistently-truncating response can never trigger unbounded calls or
    reintroduce the doubled-request issue from before — this only fires
    when a response is actually incomplete, not on every request.

    A model that thinks before it answers (gpt-oss, ...) counts that hidden thinking against the same token budget.
    On a hard question it can use ALL of it and write nothing: finish "length" with an empty reply. "Continue where you
    left off" is meaningless then, so instead the question is asked once more with the model's low-reasoning setting
    (when its configuration has one). Ordinary replies never take this path.
    """
    can_think_less = _can_reduce_reasoning(feature)
    deadline = time.monotonic() + config.EXPLANATION_TOTAL_BUDGET      # one budget for everything below, retries included
    content, finish_reason = _chat_completion(feature, messages, _MAX_TOKENS, deadline=deadline)
    if finish_reason == "length" and not content.strip() and can_think_less:
        content, finish_reason = _chat_completion(feature, messages, _MAX_TOKENS, low_reasoning=True, deadline=deadline)
    continuations = 0
    while finish_reason == "length" and content.strip() and continuations < _MAX_CONTINUATIONS:
        continuations += 1
        follow_up_messages = messages + [
            Message.of("assistant", content),
            Message.of("user", (
                "Your previous reply was cut off before the solution was finished. "
                "Continue writing EXACTLY where you left off — do not repeat any "
                "earlier text, do not restart, do not add a new heading. Finish the "
                "remaining steps and give the explicit final answer."
            )),
        ]
        more, finish_reason = _chat_completion(feature, follow_up_messages, _CONTINUATION_MAX_TOKENS,
                                               low_reasoning=can_think_less, deadline=deadline)
        content = f"{content}{more}"
    return content


def _call_ai_text(prompt: str) -> str:
    return _generate_complete(_TEXT_FEATURE, [Message.of("user", prompt)])


def _call_ai_vision(prompt: str, img_b64: str, mime_type: str = "image/jpeg") -> str:
    return _generate_complete(_VISION_FEATURE, [
        Message.of("user", [Part.of_image(img_b64, mime_type), Part.of_text(prompt)]),
    ])