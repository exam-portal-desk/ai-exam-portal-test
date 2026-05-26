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
"""

import base64
import requests as _requests
from datetime import datetime, timezone, timedelta

import config
from app.db.explanation import (
    get_explanation_usage,
    get_daily_total_usage,
    increment_explanation_usage,
    save_explanation,
    get_explanation_history,
    get_reset_time_str,
)
from app.services.drive_service import get_image_url


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_TEXT_MODEL   = config.EXPLANATION_MODEL           # llama-3.3-70b-versatile
_VISION_MODEL = config.EXPLANATION_VISION_MODEL    # llama-4-scout-17b-16e-instruct
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
# Public: main generation entry point
# ─────────────────────────────────────────────────────────────────────────────

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
    """
    question_id = int(question.get("id", 0))

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

    return f"""You are an expert exam tutor. A student answered a question incorrectly and needs a clear, detailed explanation.

{image_note}**Question:**
{qtext}

{options_block}{wrong_line}**Correct Answer:** {correct_ans}

---

Provide a complete step-by-step solution in exactly this structure.

STRICT FORMATTING RULES:

1. Use Markdown formatting.

2. Use LaTeX for ALL mathematical expressions.

3. NEVER write equations in plain text.

4. ALL block equations MUST use:
$$\Large ...$$

5. ALL important inline equations MUST use:
$\Large ...$

6. Always prefer larger readable equations instead of default LaTeX size.

7. Multi-step derivations MUST use:
$$\Large
\\begin{{aligned}}
...
\\end{{aligned}}
$$

8. Use large readable fractions, roots, matrices, vectors, integrals, limits, summations, and derivations.

9. Do NOT generate tiny or normal-sized equations.

10. Ensure every formula is highly readable on both desktop and mobile devices.

11. Keep spacing clean between equations and explanations.

12. Avoid compressed one-line derivations when solving long calculations.

13. Every major mathematical step should be visually separated.

14. Prefer display equations over inline equations whenever possible.

15. Final answers should also be written in large LaTeX format.

## ◈ Understanding the Question

What concept is this question testing? (1-2 sentences)

## ⊘ Why the Answer Was Wrong

Explain specifically why the selected option/answer is incorrect.
Be direct but encouraging.

## ◉ Step-by-Step Solution

Complete solution with all working shown.

Number each step clearly.

Use large readable LaTeX equations everywhere.

## ✦ Key Concept to Remember

• 2-3 bullet points summarising the core concept or formula.

## ➤ Quick Tip

One sentence to avoid this mistake in future.

---

Keep language clear and student-friendly.

The student is preparing for a competitive exam."""


# ─────────────────────────────────────────────────────────────────────────────
# Groq callers
# ─────────────────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GROQ_EXPLANATION_API_KEY}",
        "Content-Type":  "application/json",
    }


def _call_groq_text(prompt: str) -> str:
    resp = _requests.post(
        _GROQ_API_URL,
        headers=_headers(),
        json={
            "model":       _TEXT_MODEL,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  1500,
            "temperature": 0.3,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_groq_vision(prompt: str, img_b64: str) -> str:
    resp = _requests.post(
        _GROQ_API_URL,
        headers=_headers(),
        json={
            "model":   _VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens":  1500,
            "temperature": 0.3,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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
