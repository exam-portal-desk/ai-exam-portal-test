"""
app/services/explanation_service.py
Groq-powered AI explanation generator with Chain-of-Thought (CoT) prompting.

Flow:
  1. Check rate limits (daily + per-question).
  2. Build structured CoT prompt from question data.
  3. Image present -> fetch Drive URL -> base64 -> vision model (llama-4-scout).
     Text only    -> fast chat model (llama-3.3-70b-versatile).
  4. Save explanation to ai_explanation_history (persists across reloads).
  5. Increment usage counter only after successful generation.
  6. Return explanation + full history + remaining quota.

Public API:
  check_rate_limits(user_id, question_id)  -> dict
  generate_explanation(question, user_id)  -> dict
  fetch_history(user_id, question_id)      -> list[dict]
  delete_explanation(user_id, question_id, explanation_id) -> dict
"""

import base64
import threading
import requests as _requests

import app.config as config
from app.db.explanation import (
    get_explanation_usage,
    get_daily_total_usage,
    increment_explanation_usage,
    save_explanation,
    get_explanation_history,
    delete_explanation as db_delete_explanation,
    get_reset_time_str,
)
from app.services.image_storage_service import resolve_question_image_url as get_image_url
from app.services import ai_provider
from app.utils.helpers import strip_ai_reasoning


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_TEXT_MODEL   = ai_provider.get_model("text_models", config.EXPLANATION_TEXT_MODEL)
_VISION_MODEL = ai_provider.get_model("vision_models", config.EXPLANATION_VISION_MODEL_NAME)
_DAILY_LIMIT  = config.EXPLANATION_DAILY_LIMIT     # 5 per student per day
_PER_Q_LIMIT  = config.EXPLANATION_PER_QUESTION_LIMIT  # 2 per question per day
_TIMEOUT      = config.AI_REQUEST_TIMEOUT          # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Public: rate-limit checker (read-only, no side effects)
# ─────────────────────────────────────────────────────────────────────────────

def check_rate_limits(user_id: int, question_id: int) -> dict:
    """
    Check both daily-total and per-question limits.

    Returns:
        {
            allowed: bool,
            reason: str | None,
            reset_time: str,          <- e.g. "Resets in 3h 12m at 12:00 AM IST"
            daily_used: int,
            daily_remaining: int,
            question_used: int,
            question_remaining: int,
        }
    """
    daily_used    = get_daily_total_usage(user_id)
    q_row         = get_explanation_usage(user_id, question_id)
    question_used = int(q_row.get("used_count", 0)) if q_row else 0

    daily_remaining    = max(0, _DAILY_LIMIT - daily_used)
    question_remaining = max(0, _PER_Q_LIMIT - question_used)
    reset_time         = get_reset_time_str()

    if daily_used >= _DAILY_LIMIT:
        return {
            "allowed": False,
            "reason":  f"Daily limit of {_DAILY_LIMIT} explanations reached. {reset_time}.",
            "reset_time": reset_time,
            "daily_used": daily_used, "daily_remaining": 0,
            "question_used": question_used, "question_remaining": question_remaining,
        }

    if question_used >= _PER_Q_LIMIT:
        return {
            "allowed": False,
            "reason":  f"You've used all {_PER_Q_LIMIT} explanations for this question today. {reset_time}.",
            "reset_time": reset_time,
            "daily_used": daily_used, "daily_remaining": daily_remaining,
            "question_used": question_used, "question_remaining": 0,
        }

    return {
        "allowed": True, "reason": None,
        "reset_time": reset_time,
        "daily_used": daily_used, "daily_remaining": daily_remaining,
        "question_used": question_used, "question_remaining": question_remaining,
    }


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


def generate_explanation(question: dict, user_id: int) -> dict:
    """
    Generate a CoT explanation via Groq, save it, increment usage.

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

    if has_image:
        ok, img_url = get_image_url(image_path)
        if ok and img_url:
            img_b64 = _fetch_image_as_base64(img_url)
        if not img_b64:
            has_image = False   # fall back to text-only — don't block student

    # ── 3. Build CoT prompt ───────────────────────────────────────────────
    prompt = _build_cot_prompt(question)

    # ── 4. Call Groq ──────────────────────────────────────────────────────
    try:
        raw = _call_groq_vision(prompt, img_b64) if (has_image and img_b64) \
              else _call_groq_text(prompt)
    except _requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 429:
            print(f"[explanation_service] Groq rate limited (429): {e}")
            return {
                "success": False,
                "message": "The AI explanation service is busy right now. Please wait a moment and try again.",
            }
        print(f"[explanation_service] Groq call failed: {e}")
        return {"success": False, "message": "AI service temporarily unavailable. Please try again."}
    except Exception as e:
        print(f"[explanation_service] Groq call failed: {e}")
        return {"success": False, "message": "AI service temporarily unavailable. Please try again."}

    if not raw:
        return {"success": False, "message": "AI returned an empty response. Please try again."}

    # ── 5. Persist explanation ────────────────────────────────────────────
    save_explanation(user_id, question_id, raw)

    # ── 6. Increment usage counter ────────────────────────────────────────
    increment_explanation_usage(user_id, question_id)

    # ── 7. Recalculate remaining after increment ──────────────────────────
    new_daily_used = get_daily_total_usage(user_id)
    q_row          = get_explanation_usage(user_id, question_id)
    new_q_used     = int(q_row.get("used_count", 0)) if q_row else 0

    daily_remaining    = max(0, _DAILY_LIMIT - new_daily_used)
    question_remaining = max(0, _PER_Q_LIMIT - new_q_used)

    # ── 8. Return full history so frontend can render all generations ──────
    history = get_explanation_history(user_id, question_id)

    return {
        "success":            True,
        "explanation":        raw,
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

    qtype       = str(question.get("question_type", "MCQ")).upper()
    qtext       = str(question.get("question_text", "")).strip()
    correct_ans = str(question.get("correct_answer", "")).strip()
    given_ans   = str(question.get("given_answer", "") or "").strip()
    image_path  = str(question.get("image_path") or "").strip()

    has_image = image_path and image_path.lower() not in ("", "nan", "none")

    # Build options block (MCQ/MSQ only)
    options_block = ""

    if qtype in ("MCQ", "MSQ"):

        opts = [
            f"  ({k}) {v}"
            for k in ("A", "B", "C", "D")
            if (
                v := str(
                    question.get(f"option_{k.lower()}", "") or ""
                ).strip()
            )
            and v.lower() not in ("nan", "none", "")
        ]

        if opts:
            options_block = (
                "**Options:**\n"
                + "\n".join(opts)
                + "\n\n"
            )

    wrong_line = (
        f"**Student's Wrong Answer:** {given_ans}\n"
        if given_ans
        and given_ans.lower() not in (
            "not answered",
            "none",
            "nan",
            "",
        )
        else ""
    )

    image_note = (
        "*(A diagram/image is attached — analyse it as part of the question.)*\n\n"
        if has_image
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
6. Keep equations large and readable, with clean spacing, on both desktop and mobile.
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
# Groq callers
# ─────────────────────────────────────────────────────────────────────────────

def _headers(model: dict) -> dict:
    return {
        "Authorization": f"Bearer {model['api_key']}",
        "Content-Type":  "application/json",
    }


# Generous headroom for a full multi-step LaTeX/mhchem solution — the old
# 1500-token cap was the actual root cause of explanations stopping midway
# (see _generate_complete below for how we also detect and recover from
# any case that still runs past this).
_MAX_TOKENS = 3000
_CONTINUATION_MAX_TOKENS = 1200
_MAX_CONTINUATIONS = 1  # hard cap: can never turn into an unbounded retry loop


def _chat_completion(model: dict, messages: list, max_tokens: int) -> tuple[str, str]:
    """Single call to the active model's endpoint. Returns (content, finish_reason)
    — finish_reason is "length" when the model was cut off purely for hitting
    max_tokens, as opposed to "stop" when it actually finished on its own."""
    resp = _requests.post(
        model["endpoint"],
        headers=_headers(model),
        json={"model": model["model"], "messages": messages, "max_tokens": max_tokens, "temperature": 0.3},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    content = choice["message"]["content"].strip()
    finish_reason = choice.get("finish_reason", "stop")
    return strip_ai_reasoning(content), finish_reason


def _generate_complete(model: dict, messages: list) -> str:
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
    """
    content, finish_reason = _chat_completion(model, messages, _MAX_TOKENS)
    continuations = 0
    while finish_reason == "length" and continuations < _MAX_CONTINUATIONS:
        continuations += 1
        follow_up_messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": (
                "Your previous reply was cut off before the solution was finished. "
                "Continue writing EXACTLY where you left off — do not repeat any "
                "earlier text, do not restart, do not add a new heading. Finish the "
                "remaining steps and give the explicit final answer."
            )},
        ]
        more, finish_reason = _chat_completion(model, follow_up_messages, _CONTINUATION_MAX_TOKENS)
        content = f"{content}{more}"
    return content


def _call_groq_text(prompt: str) -> str:
    return _generate_complete(_TEXT_MODEL, [{"role": "user", "content": prompt}])


def _call_groq_vision(prompt: str, img_b64: str) -> str:
    return _generate_complete(_VISION_MODEL, [{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": prompt},
        ],
    }])


# ─────────────────────────────────────────────────────────────────────────────
# Image fetcher
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_image_as_base64(url: str) -> str | None:
    """Download image and return base64 string. Max 4 MB guard."""
    _MAX_BYTES = 4 * 1024 * 1024
    try:
        resp   = _requests.get(url, timeout=15, stream=True)
        resp.raise_for_status()
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=8192):
            total += len(chunk)
            if total > _MAX_BYTES:
                print("[explanation_service] Image >4MB, skipping vision.")
                return None
            chunks.append(chunk)
        return base64.b64encode(b"".join(chunks)).decode("utf-8")
    except Exception as e:
        print(f"[explanation_service] _fetch_image_as_base64 error: {e}")
        return None