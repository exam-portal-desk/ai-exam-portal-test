"""
app/services/ai_service.py
Business logic for the AI study assistant:
  - Groq API call
  - Daily limit tracking
  - Chat history helpers
"""

from typing import List, Dict, Optional

import app.config as config
from app.db.ai import (
    get_chat_history as db_get_history,
    save_chat_message as db_save_message,
    get_today_usage,
    increment_usage,
)
from app.utils.helpers import strip_ai_reasoning
from app.utils.datetime_service import today_app_date, format_display
from app.services import ai_provider


# ─────────────────────────────────────────────
# System prompt (single source of truth)
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an expert tutor for physics, chemistry, mathematics, biology, and engineering, \
talking directly to a student in a chat.

CORE BEHAVIOUR — ANSWER, DON'T PERFORM:
- Answer the student's actual question. Nothing more.
- Be as brief as possible while still fully answering. A simple question gets a simple, short answer. \
Only go into more depth when the question genuinely requires it (e.g. a derivation, a multi-step numeric \
problem, or the student explicitly asks for detail).
- Do NOT open with a greeting, "Sure!", "Great question!", or any other filler. Do NOT restate the question \
back to the student. Do NOT close with a generic summary, "I hope this helps", or similar filler.
- Never show your reasoning process, internal analysis, chain-of-thought, self-checks, or any commentary \
about how you interpreted or validated the question. Output ONLY the final answer content a student should \
read — nothing about how you produced it. Never use tags like <think> or similar.

RESPONSE STRUCTURE — USE ONLY WHAT THE QUESTION NEEDS:
- For a quick factual/conceptual question: answer directly in 1 short paragraph or a few bullet points. \
Do NOT force it into the structure below.
- For a numeric/derivation problem (something with given values, a formula, and steps to solve): use plain \
section headers only for the sections that are actually needed:
  [FINAL ANSWER] — the direct answer/result, with LaTeX.
  [GIVEN] — only if the problem actually provides known values.
  [SOLUTION] — only if there are real steps to show; number each step, show only the necessary working.
  [EXPLANATION] — only if a short concept note (1-3 lines) adds real value beyond the solution itself.
- Never include a section that has nothing meaningful in it (e.g. don't add an empty [GIVEN] section for a \
question with no given values).

FORMATTING RULES:
1. NEVER use ** or __ for bold. NEVER use * or _ for italic.
2. NEVER use *** or --- or === as separators.
3. When section headers are used, they must be exactly: [FINAL ANSWER], [GIVEN], [SOLUTION], [EXPLANATION]
4. Use numbered lists (1. 2. 3.) or lettered lists (a. b. c.) for real steps only.
5. For bullet points use a dash: - item

LATEX RULES — MANDATORY:
- Every mathematical expression MUST be in LaTeX. NO plain text math.
- Inline math: $expression$ — for variables, small formulas, values with units
- Display math: $$expression$$ — for main equations and derivations
- Greek: $\\alpha$, $\\beta$, $\\gamma$, $\\theta$, $\\lambda$, $\\mu$, $\\pi$
- Fractions: $\\frac{numerator}{denominator}$
- Powers: $x^{2}$, Subscripts: $v_{0}$, Sqrt: $\\sqrt{x}$
- Units in math: $9.8\\,\\text{m/s}^2$, $5\\,\\text{kg}$

CHEMISTRY RULES:
- Use mhchem: $\\ce{H2O}$, $\\ce{CO2}$
- Reactions: $\\ce{2H2 + O2 -> 2H2O}$
- Ions: $\\ce{Na+}$, $\\ce{SO4^{2-}}$

Keep language simple and clear. Always use LaTeX for any number with a unit."""


# ─────────────────────────────────────────────
# Limit helpers
# ─────────────────────────────────────────────

def get_user_chat_limits(user_id: int) -> Dict:
    usage = get_today_usage(user_id)
    questions_used = int(usage.get("questions_used", 0)) if usage else 0
    return {
        "daily_limit": config.AI_DAILY_LIMIT,
        "questions_used": questions_used,
        "reset_date": today_app_date(),
    }


# ─────────────────────────────────────────────
# Chat history helpers
# ─────────────────────────────────────────────

def get_formatted_history(user_id: int, limit: int = 50) -> List[Dict]:
    """Return history sorted ascending (oldest first) for display."""
    records = db_get_history(user_id, limit=limit)
    records.sort(key=lambda x: x.get("timestamp", ""))
    return [
        {
            "text": r.get("message", ""),
            "isUser": bool(r.get("is_user", False)),
            "timestamp": format_display(r.get("timestamp")),
        }
        for r in records
    ]


def get_history_for_context(user_id: int, last_n: int = 4) -> List[Dict]:
    """Return the last N messages in Groq message format."""
    records = db_get_history(user_id, limit=last_n * 2)
    records.sort(key=lambda x: x.get("timestamp", ""))
    result = []
    for r in records[-last_n:]:
        result.append({
            "role": "user" if r.get("is_user") else "assistant",
            "content": r.get("message", ""),
        })
    return result


def save_user_message(user_id: int, message: str) -> None:
    db_save_message(user_id, message, is_user=True)


def save_ai_message(user_id: int, message: str) -> None:
    db_save_message(user_id, message, is_user=False)


# ─────────────────────────────────────────────
# Groq API call
# ─────────────────────────────────────────────

def get_groq_response(user_message: str, context_history: Optional[List[Dict]] = None) -> str:
    """
    Call the assistant's active text model and return its reply text.
    Returns an error string (never raises) so callers stay clean.
    """
    import requests

    model = ai_provider.get_model("text_models", config.ASSISTANT_TEXT_MODEL)
    if not model["api_key"]:
        return "AI service is currently unavailable. Please contact the administrator."

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if context_history:
        messages.extend(context_history)
    messages.append({"role": "user", "content": user_message})

    base_payload = {
        "model": model["model"],
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 4000,
        "top_p": 0.95,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.3,
    }

    try:
        content = _post_groq_chat(model, base_payload)
        if content is None:
            return "I'm having trouble connecting to my AI service. Please try again."
        return strip_ai_reasoning(content)

    except requests.exceptions.Timeout:
        return "Request timed out. Please try asking your question again."
    except Exception as e:
        print(f"[ai_service] get_groq_response error: {e}")
        return "I encountered an error. Please try again."


def _post_groq_chat(model: Dict, payload: Dict) -> Optional[str]:
    """
    POST to the active text model's chat completions endpoint — exactly ONE
    request per call. Returns the raw message content, or None on error.

    Note: we intentionally do NOT send reasoning_format here. Speculatively
    sending it and retrying without it on a 400 doubles traffic on every
    single message whenever the configured model doesn't support the
    param — that's what caused 429 rate-limit errors on the explanation
    pipeline. strip_ai_reasoning() (applied by the caller) already scrubs
    any leaked chain-of-thought from the response regardless of model, so
    the extra round trip isn't needed.
    """
    import requests

    resp = requests.post(
        model["endpoint"],
        headers={
            "Authorization": f"Bearer {model['api_key']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.AI_REQUEST_TIMEOUT,
    )

    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"]

    print(f"[ai_service] AI provider error {resp.status_code}: {resp.text[:200]}")
    return None