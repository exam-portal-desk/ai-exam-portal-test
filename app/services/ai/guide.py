"""
app/services/ai/guide.py
The AI Configuration guide is ONE human-readable document, config/AI_MODELS.md.
The Admin page's Guide button shows exactly that file, so there is a single source
of truth and nothing instructional lives in ai_models.json.
"""

import os
from typing import Optional

import app.config as config

# Fixed location (never built from user input): <project root>/config/AI_MODELS.md
_GUIDE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(config.__file__))), "config", "AI_MODELS.md"
)
_MAX_BYTES = 400 * 1024


def read_guide() -> Optional[str]:
    """The guide's markdown text, or None if the file is missing/unreadable."""
    try:
        with open(_GUIDE_PATH, "r", encoding="utf-8") as f:
            return f.read(_MAX_BYTES)
    except OSError as e:
        print(f"[ai.guide] guide not available: {e}")
        return None
