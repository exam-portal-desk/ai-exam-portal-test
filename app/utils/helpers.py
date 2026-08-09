"""
app/utils/helpers.py
Small, pure utility functions with no dependencies on Flask or DB.
"""

import re
import string
import secrets

# ─────────────────────────────────────────────
# AI output sanitisation
# ─────────────────────────────────────────────
# Some LLMs (esp. "reasoning" models such as DeepSeek-R1 / Qwen3 / GPT-OSS
# variants served on Groq) emit their internal chain-of-thought inline in the
# response content, wrapped in structural tags. This must never reach the
# student. We only strip known, structurally-delimited reasoning blocks -
# never individual words - so legitimate educational content that happens to
# mention "reasoning" or "analysis" is left untouched.
_REASONING_TAG_RE = re.compile(
    r"<\s*(think|thinking|reason|reasoning|analysis|scratchpad|internal)\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Handles the case where a closing tag never arrived (e.g. truncated stream
# or a model that only emits the opening tag before the real answer) - strip
# from the opening tag onward only if it appears at the very start of the
# response, so we never eat content the model intentionally wrote later.
_LEADING_OPEN_TAG_RE = re.compile(
    r"^\s*<\s*(think|thinking|reason|reasoning|analysis|scratchpad|internal)\s*>.*",
    re.IGNORECASE | re.DOTALL,
)


def strip_ai_reasoning(text: str) -> str:
    """
    Remove structurally-delimited chain-of-thought / internal-reasoning
    blocks from a raw LLM response, regardless of which model produced it.
    Safe to call on any text - if no such tags are present it is a no-op.
    """
    if not text:
        return text

    cleaned = _REASONING_TAG_RE.sub("", text)

    # If an unclosed reasoning tag survived (streaming edge case) and it's
    # the very first thing in the response, drop the leading fragment up to
    # the next blank line / double newline, which usually separates the
    # leaked preamble from the real answer. If we can't find a clean split,
    # leave the text as-is rather than risk deleting a real answer.
    if _LEADING_OPEN_TAG_RE.match(cleaned):
        parts = re.split(r"\n\s*\n", cleaned, maxsplit=1)
        if len(parts) == 2:
            cleaned = parts[1]

    return cleaned.strip()


def safe_float(value, default: float = 0.0) -> float:
    if value is None or str(value).strip() in ("", "None", "null", "nan"):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default: int = 0) -> int:
    if value is None or str(value).strip() in ("", "None", "null", "nan"):
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def generate_username(full_name: str, existing_usernames: set) -> str:
    """
    Generate a unique username in FirstName.LastName format.
    Appends a counter if the base name is already taken.
    """
    base = full_name.strip().lower().replace(" ", ".")
    if base not in existing_usernames:
        return base
    counter = 1
    while f"{base}{counter}" in existing_usernames:
        counter += 1
    return f"{base}{counter}"


def generate_password(length: int = 8) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def is_valid_email(email: str) -> bool:
    if "@" not in email or len(email) < 6:
        return False
    domain = email.split("@")[1].lower()
    return "." in domain and len(domain) > 3


def parse_max_attempts(raw) -> int | None:
    """
    Parse max_attempts field: returns None (unlimited) or a non-negative int.
    Raises ValueError on invalid input.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if not s.isdigit():
        raise ValueError("max_attempts must be a non-negative integer")
    val = int(s)
    if val < 0:
        raise ValueError("max_attempts must be non-negative")
    return val