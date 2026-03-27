"""
app/services/ai_service.py
Business logic for the AI study assistant:
  - Groq API call
  - Daily limit tracking
  - Chat history helpers
"""

from typing import List, Dict, Optional
from datetime import datetime

import config
from app.db.ai import (
    get_chat_history as db_get_history,
    save_chat_message as db_save_message,
    get_today_usage,
    increment_usage,
)


# ─────────────────────────────────────────────
# System prompt (single source of truth)
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an expert tutor for physics, chemistry, mathematics, biology, and engineering.

FORMATTING RULES — FOLLOW STRICTLY:
1. NEVER use ** or __ for bold. NEVER use * or _ for italic.
2. NEVER use *** or --- or === as separators.
3. Use plain section headers like: [FINAL ANSWER], [GIVEN], [SOLUTION], [EXPLANATION]
4. Use numbered lists (1. 2. 3.) or lettered lists (a. b. c.) for steps.
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

RESPONSE STRUCTURE:
[FINAL ANSWER]
State the direct answer here with LaTeX.

[GIVEN]
List known values: $m = 5\\,\\text{kg}$, $v = 10\\,\\text{m/s}$

[SOLUTION]
Step 1. Description
$$equation$$

[EXPLANATION]
Brief concept summary in 2-3 lines.

RULES:
- Show EVERY step explicitly
- Keep language simple and clear
- Always use LaTeX for ANY number with a unit"""


# ─────────────────────────────────────────────
# Limit helpers
# ─────────────────────────────────────────────

def get_user_chat_limits(user_id: int) -> Dict:
    usage = get_today_usage(user_id)
    questions_used = int(usage.get("questions_used", 0)) if usage else 0
    return {
        "daily_limit": config.AI_DAILY_LIMIT,
        "questions_used": questions_used,
        "reset_date": datetime.now().strftime("%Y-%m-%d"),
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
            "timestamp": r.get("timestamp", ""),
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
    Call the Groq API and return the assistant's reply text.
    Returns an error string (never raises) so callers stay clean.
    """
    import requests

    if not config.GROQ_API_KEY:
        return "AI service is currently unavailable. Please contact the administrator."

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if context_history:
        messages.extend(context_history)
    messages.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.AI_MODEL_NAME,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 4000,
                "top_p": 0.95,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.3,
            },
            timeout=config.AI_REQUEST_TIMEOUT,
        )

        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]

        print(f"[ai_service] Groq error {resp.status_code}: {resp.text[:200]}")
        return "I'm having trouble connecting to my AI service. Please try again."

    except requests.exceptions.Timeout:
        return "Request timed out. Please try asking your question again."
    except Exception as e:
        print(f"[ai_service] get_groq_response error: {e}")
        return "I encountered an error. Please try again."