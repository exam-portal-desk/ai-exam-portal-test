"""
app/utils/instructions_formatter.py
Single source of truth for turning an admin-authored exam "instructions"
string into safe, structured HTML — reused everywhere instructions are
shown (normal Exam Instructions page, Scheduled Exam Kiosk, admin
create/edit preview) so none of those can ever drift out of sync with
each other. There is exactly one storage source too: exams.instructions
(plain text) — this module only controls how that one string is rendered.
Nothing about the stored format changed: every instruction saved before
the richer syntax below existed renders as it always did.

Supported author-facing syntax (small and explicit — no WYSIWYG dependency):
  Blocks (a blank line separates blocks)
  - "# Title", "## Section", "### Sub-section"   -> headings
  - "---" on its own line                         -> horizontal rule
  - "- " / "* " item, "1. " item                  -> list items. Consecutive items form ONE list; two or more spaces (or a
                    tab) of indent nest an item under the one above; digits typed are ignored, the browser numbers <ol>
  - A block whose first line starts with "IMPORTANT:", "WARNING:" or "NOTE:" (case-insensitive) -> highlighted callout
  - "[center] text" / "[right] text" / "[left] text" at the start of a line (or right after a heading's "#"s) -> text alignment;
                    an aligned line is its own paragraph
  - anything else -> paragraph (lines of one block joined with <br>)
  Inline
  - **bold**, *italic*, __underline__
  - [link text](https://example.com)   (http, https and mailto only; opens in a new tab with rel="noopener noreferrer nofollow")

Safety: the whole input is HTML-escaped BEFORE any tag is generated and the only tags ever produced are the fixed ones below,
so pasted HTML or scripts are displayed as text, never run. Link targets are restricted to http(s)/mailto and cannot contain
quotes, so an attribute can never be broken out of.
"""

import re
from markupsafe import Markup, escape

_BOLDITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_UNDERLINE_RE = re.compile(r"__(.+?)__")
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s|\*)(.+?)(?<![\s\*])\*(?![\*\w])")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(((?:https?://|mailto:)[^\s()<>\"']+)\)", re.IGNORECASE)
_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)(?:(?P<bullet>[-*])|(?P<num>\d+[.)]))\s+(?P<text>.*\S.*)$")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*\S.*)$")
_RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_ALIGN_RE = re.compile(r"^\[(left|center|right)\]\s*", re.IGNORECASE)
_WARNING_PREFIX_RE = re.compile(r"^(important|warning|note)\s*:\s*", re.IGNORECASE)
_LEAD_MARK_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+")
# Earlier editor versions wrapped a whole line in emphasis markers, burying its list marker / heading hashes inside them
# ("**- item**", "__1. step__", "**## Title**"); read those as what the author meant instead of showing a stray "-" / "#".
_WRAPPED_PREFIX_RE = re.compile(r"^(?P<ind>[ \t]*)(?P<w>\*\*|__)\s*(?P<pre>[-]|\d+[.)]|#{1,3})\s+(?P<text>.*\S.*?)(?P=w)\s*$")
_BAD_URL_CHARS = ("&quot;", "&#34;", "&#39;", "&lt;", "&gt;")


def instructions_is_blank(raw) -> bool:
    return not str(raw or "").strip()


def _safe_link(match) -> str:
    label, url = match.group(1), match.group(2)
    if any(bad in url for bad in _BAD_URL_CHARS):
        return match.group(0)
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer nofollow">{label}</a>'


def _inline(text: str) -> str:
    out = str(escape(text))
    out = _LINK_RE.sub(_safe_link, out)
    out = _BOLDITALIC_RE.sub(r"<strong><em>\1</em></strong>", out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _UNDERLINE_RE.sub(r"<u>\1</u>", out)
    out = _ITALIC_RE.sub(r"<em>\1</em>", out)
    return out


def _indent_width(indent: str) -> int:
    return len(indent.replace("\t", "    "))


def _render_list(items) -> str:
    """items: [(indent_width, 'ul' | 'ol', text)] -> nested lists. A change of marker type at the same level starts a new list."""
    html, stack = [], []          # stack: [(indent, tag)]
    for indent, tag, text in items:
        while stack and indent < stack[-1][0]:
            html.append(f"</li></{stack.pop()[1]}>")
        if stack and indent == stack[-1][0]:
            if tag != stack[-1][1]:
                html.append(f"</li></{stack.pop()[1]}>")
                html.append(f"<{tag} class='instr-list'><li>{text}")
                stack.append((indent, tag))
            else:
                html.append(f"</li><li>{text}")
        else:
            html.append(f"<{tag} class='instr-list'><li>{text}")
            stack.append((indent, tag))
    while stack:
        html.append(f"</li></{stack.pop()[1]}>")
    return "".join(html)


def _normalize_line(ln: str) -> str:
    m = _WRAPPED_PREFIX_RE.match(ln)
    if not m:
        return ln
    w, text = m.group("w"), m.group("text").strip()
    return f"{m.group('ind')}{m.group('pre')} {w}{text}{w}"


def _list_text(text: str) -> str:
    """List-item text: a second leading marker ("1. - x") is a leftover, and lists have no alignment, so neither shows up literally."""
    text = text.strip()
    while True:
        stripped = _ALIGN_RE.sub("", _LEAD_MARK_RE.sub("", text, count=1), count=1).strip()
        if stripped == text or not stripped:
            return text
        text = stripped


def _split_align(line: str):
    m = _ALIGN_RE.match(line)
    if not m:
        return line, ""
    return line[m.end():], f"instr-align-{m.group(1).lower()}"


def render_exam_instructions(raw) -> Markup:
    text = str(raw or "").strip()
    if not text:
        return Markup("")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)

    html_parts = []
    for block in blocks:
        raw_lines = [_normalize_line(ln.rstrip()) for ln in block.split("\n") if ln.strip()]
        if not raw_lines:
            continue

        warning_match = _WARNING_PREFIX_RE.match(raw_lines[0].strip())
        if warning_match:
            lines = [ln.strip() for ln in raw_lines]
            lines[0] = lines[0][warning_match.end():]
            body = " ".join(_inline(ln) for ln in lines)
            html_parts.append(
                f'<div class="instr-warning"><i class="fas fa-triangle-exclamation"></i>'
                f'<span>{body}</span></div>'
            )
            continue

        para, list_items = [], []

        def flush_para():
            nonlocal para
            if para:
                html_parts.append(f"<p>{'<br>'.join(_inline(ln) for ln in para)}</p>")
                para = []

        def flush_list():
            nonlocal list_items
            if list_items:
                html_parts.append(_render_list(list_items))
                list_items = []

        for ln in raw_lines:
            stripped = ln.strip()
            heading = _HEADING_RE.match(stripped)
            list_match = _LIST_RE.match(ln)
            if _RULE_RE.match(stripped):
                flush_para(); flush_list()
                html_parts.append('<hr class="instr-rule">')
            elif heading:
                flush_para(); flush_list()
                content, align = _split_align(heading.group(2))
                level = len(heading.group(1))
                cls = f"instr-h instr-h{level}" + (f" {align}" if align else "")
                html_parts.append(f'<h{level + 2} class="{cls}">{_inline(content)}</h{level + 2}>')
            elif list_match:
                flush_para()
                tag = "ul" if list_match.group("bullet") else "ol"
                list_items.append((_indent_width(list_match.group("indent")), tag, _inline(_list_text(list_match.group("text")))))
            elif _ALIGN_RE.match(stripped):
                # an aligned line is its own paragraph, so aligning one line never drags its neighbours along
                flush_para(); flush_list()
                content, align = _split_align(stripped)
                html_parts.append(f'<p class="{align}">{_inline(content)}</p>')
            else:
                flush_list()
                para.append(stripped)
        flush_para(); flush_list()

    return Markup("".join(html_parts))


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_END_RE = re.compile(r"</(?:p|li|h[1-6]|div|ul|ol)>|<br>|<hr[^>]*>")


def plain_instructions(raw, limit=0) -> str:
    """One-line plain text of the instructions (markers and tags removed) for compact previews — dashboard cards, tooltips —
    so a '# Heading' or '**bold**' never shows up literally outside the real instructions page."""
    import html as _html
    out = _BLOCK_END_RE.sub(" ", str(render_exam_instructions(raw)))
    out = _html.unescape(_TAG_RE.sub("", out))
    out = re.sub(r"\s+", " ", out).strip()
    return out if not limit or len(out) <= limit else out[:limit].rstrip() + "…"
