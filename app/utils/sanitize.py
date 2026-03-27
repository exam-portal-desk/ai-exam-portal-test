"""
app/utils/sanitize.py
HTML sanitization helpers used in templates and admin views.
"""

import html
import re
from markupsafe import Markup, escape


def sanitize_html(text) -> str:
    """
    Escape HTML special characters.
    Safe to use without |safe filter in templates.
    """
    if text is None:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return str(escape(s))


def sanitize_for_display(text) -> Markup:
    """
    Escape HTML then convert newlines to <br> tags.
    Returns a Markup object — use with |safe in templates.
    """
    if text is None:
        return Markup("")
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return Markup(escape(s).replace("\n", Markup("<br>")))