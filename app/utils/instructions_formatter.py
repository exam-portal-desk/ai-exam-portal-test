"""
app/utils/instructions_formatter.py
Single source of truth for turning an admin-authored exam "instructions"
string into safe, structured HTML — reused everywhere instructions are
shown (normal Exam Instructions page, Scheduled Exam Kiosk, admin
create/edit preview) so none of those can ever drift out of sync with
each other. There is exactly one storage source too: exams.instructions
(plain text) — this module only controls how that one string is rendered.

Supported author-facing syntax (deliberately small — no external
rich-text/WYSIWYG dependency):
  - Blank line   -> paragraph break
  - "- " / "* "  -> bullet list item (every line in the block)
  - "1. " "2. "  -> numbered list item (every line in the block; the
                    actual digits typed are ignored, the browser numbers
                    the <ol> itself, so admins never need to keep them in
                    sync when reordering)
  - A block whose first line starts with "IMPORTANT:", "WARNING:", or
    "NOTE:" (case-insensitive) -> a highlighted warning callout
  - **bold text** -> <strong>bold text</strong>

All input is HTML-escaped before any tag is generated, so this is safe
to render even though it's admin-authored content.
"""

import re
from markupsafe import Markup, escape

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BULLET_RE = re.compile(r"^[-*]\s+")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")
_WARNING_PREFIX_RE = re.compile(r"^(important|warning|note)\s*:\s*", re.IGNORECASE)


def instructions_is_blank(raw) -> bool:
    return not str(raw or "").strip()


def _inline(text: str) -> str:
    escaped = str(escape(text))
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def render_exam_instructions(raw) -> Markup:
    text = str(raw or "").strip()
    if not text:
        return Markup("")

    # Normalize line endings, then split into blank-line-separated blocks.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)

    html_parts = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        warning_match = _WARNING_PREFIX_RE.match(lines[0])
        if warning_match:
            lines[0] = lines[0][warning_match.end():]
            body = " ".join(_inline(ln) for ln in lines)
            html_parts.append(
                f'<div class="instr-warning"><i class="fas fa-triangle-exclamation"></i>'
                f'<span>{body}</span></div>'
            )
            continue

        if all(_BULLET_RE.match(ln) for ln in lines):
            items = "".join(f"<li>{_inline(_BULLET_RE.sub('', ln))}</li>" for ln in lines)
            html_parts.append(f"<ul class='instr-list'>{items}</ul>")
            continue

        if all(_NUMBERED_RE.match(ln) for ln in lines):
            items = "".join(f"<li>{_inline(_NUMBERED_RE.sub('', ln))}</li>" for ln in lines)
            html_parts.append(f"<ol class='instr-list'>{items}</ol>")
            continue

        html_parts.append(f"<p>{'<br>'.join(_inline(ln) for ln in lines)}</p>")

    return Markup("".join(html_parts))
