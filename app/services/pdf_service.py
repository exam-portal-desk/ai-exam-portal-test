"""
app/services/pdf_service.py
ReportLab PDF generation for both:
  - Admin: detailed result PDF (questions + answers)
  - Student: response summary PDF
Extracted from admin.py and main.py.
"""

import os
from io import BytesIO
from datetime import datetime
from typing import List, Dict, Optional

from app.utils.latex import strip_latex

# Unicode font (DejaVu Sans) — base-14 Helvetica can't render the Greek/math
# symbols strip_latex() produces, so a Unicode TTF must be registered.
_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "fonts")
_FONT_REGULAR = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"


def _ensure_unicode_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _FONT_REGULAR in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(_FONT_REGULAR, os.path.join(_FONTS_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        _FONT_REGULAR, normal=_FONT_REGULAR, bold=_FONT_BOLD,
        italic=_FONT_REGULAR, boldItalic=_FONT_BOLD,
    )


# ─────────────────────────────────────────────
# Admin + User — detailed result PDF
# ─────────────────────────────────────────────

def build_student_response_pdf(
    result: dict,
    exam: dict,
    responses: list,
    questions_map: dict,
    student_name: str,
    username: str,
) -> bytes:
    """
    Student-facing response PDF.
    Same function used by both user route and admin download.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from io import BytesIO

    _ensure_unicode_font()

    buffer  = BytesIO()
    doc     = SimpleDocTemplate(buffer, pagesize=letter,
                                rightMargin=50, leftMargin=50,
                                topMargin=50, bottomMargin=50)
    styles  = getSampleStyleSheet()
    title_s = ParagraphStyle("T", parent=styles["Title"], fontName=_FONT_BOLD, fontSize=18,
                             textColor=colors.HexColor("#2c3e50"), alignment=TA_CENTER)
    h2_s    = ParagraphStyle("H", parent=styles["Heading2"], fontName=_FONT_BOLD, fontSize=14,
                             textColor=colors.HexColor("#2c3e50"))
    body_s  = ParagraphStyle("B", parent=styles["Normal"], fontName=_FONT_REGULAR, fontSize=10)

    # Serial position of each question within the exam (by ascending question id),
    # not the raw DB id, for display as "Question N".
    q_serial = {qid: idx + 1 for idx, qid in
                enumerate(sorted(questions_map.keys(), key=lambda k: int(k)))}

    story = [Paragraph("Exam Response Analysis", title_s)]

    hdr = Table([
        ["Exam:",    str(exam.get("name",""))],
        ["Student:", student_name],
        ["Score:",   f"{result.get('score')}/{result.get('max_score')} ({float(result.get('percentage',0)):.1f}%)"],
        ["Grade:",   str(result.get("grade","N/A"))],
    ], colWidths=[1.5*inch, 4*inch])
    hdr.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,-1),colors.lightgrey),
        ("FONTNAME",(0,0),(-1,-1),_FONT_REGULAR),
        ("FONTSIZE",(0,0),(-1,-1),12),
        ("PADDING",(0,0),(-1,-1),8),
        ("GRID",(0,0),(-1,-1),1,colors.black),
    ]))
    story += [hdr, Spacer(1,20)]

    for resp in responses:
        qid = int(resp.get("question_id",0))
        q   = questions_map.get(qid, questions_map.get(str(qid), {}))
        if not q: continue

        story.append(Paragraph(f"Question {q_serial.get(qid, qid)}", h2_s))
        story.append(Paragraph(strip_latex(q.get("question_text","")), body_s))
        story.append(Spacer(1,6))

        for lbl, key in [("A","option_a"),("B","option_b"),("C","option_c"),("D","option_d")]:
            val = q.get(key,"")
            if val and str(val).strip() not in ("","nan","None"):
                story.append(Paragraph(f"<b>{lbl}.</b> {strip_latex(val)}", body_s))

        story.append(Spacer(1,8))
        given  = strip_latex(resp.get("given_answer","")) or "Not Answered"
        corr   = strip_latex(resp.get("correct_answer","")) or "N/A"
        marks  = resp.get("marks_obtained", 0)
        is_cor = str(resp.get("is_correct","false")).lower() == "true"
        is_att = resp.get("is_attempted")
        is_att = (str(is_att).lower() == "true") if is_att is not None else bool(str(resp.get("given_answer","")).strip())

        if not is_att:
            status_text, status_color, status_bg = "Not Attempted", colors.HexColor("#6c757d"), colors.HexColor("#e9ecef")
        elif is_cor:
            status_text, status_color, status_bg = "Correct", colors.HexColor("#1e7e34"), colors.white
        else:
            status_text, status_color, status_bg = "Incorrect", colors.HexColor("#c0392b"), colors.HexColor("#f5c6cb")

        ans_t = Table([
            ["Your Answer:", given],
            ["Correct Answer:", corr],
            ["Marks:", str(marks)],
            ["Status:", status_text],
        ], colWidths=[1.5*inch, 4*inch])
        ans_t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,-1),colors.lightblue),
            ("BACKGROUND",(1,3),(1,3),status_bg),
            ("TEXTCOLOR",(1,3),(1,3),status_color),
            ("FONTNAME",(0,0),(-1,-1),_FONT_REGULAR),
            ("FONTNAME",(1,3),(1,3),_FONT_BOLD),
            ("FONTSIZE",(0,0),(-1,-1),10),
            ("PADDING",(0,0),(-1,-1),6),
            ("GRID",(0,0),(-1,-1),1,colors.black),
        ]))
        story += [ans_t, Spacer(1,16)]

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf


# ─────────────────────────────────────────────
# Notes — export whole notebook as multi-page PDF
# ─────────────────────────────────────────────

def _safe_color(value, fallback_hex):
    """Parse a CSS color string ('#rrggbb' or 'rgb(a)(...)', as raw-copied from a live
    getComputedStyle() custom-property read on the client — see editor.js exportNotebookPdf)
    into a reportlab Color, tolerating whatever the client actually sent instead of trusting it.
    """
    from reportlab.lib import colors
    import re as _re
    if isinstance(value, str):
        value = value.strip()
        try:
            return colors.HexColor(value)
        except Exception:
            pass
        # Alpha (the optional 4th rgba() component) must be captured and carried through: dark
        # themes define --border as a low-alpha rgba (e.g. "rgba(255,255,255,0.07)") so the dot
        # grid stays subtle against a dark --bg, same as it renders on the live canvas. Dropping
        # it here made every dot render fully opaque — the theme's intended ~6-14% dot suddenly
        # painted at 100% strength, which is what actually made dark-theme PDFs unreadable (light
        # themes use solid opaque hex for --border, so they never hit this branch and were never
        # affected). The alpha carried on this Color is consumed by _flatten_over() below, not by
        # reportlab's own alpha/ExtGState machinery — see that function for why.
        m = _re.match(r"^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s]+([\d.]+))?\s*\)?", value)
        if m:
            r, g, b = (min(255.0, max(0.0, float(x))) / 255.0 for x in m.groups()[:3])
            a = m.group(4)
            alpha = min(1.0, max(0.0, float(a))) if a is not None else 1.0
            return colors.Color(r, g, b, alpha=alpha)
    return colors.HexColor(fallback_hex)


def _flatten_over(fg, bg):
    """Alpha-composite fg (an rgba Color, alpha possibly <1) over an opaque bg Color, returning
    a fully opaque Color with the same math a browser uses to paint a translucent color over a
    solid one underneath.

    Needed because the dot-grid fill (see beginForm/endForm below) is painted inside a PDF Form
    XObject, and reportlab's PDFFormXObject.format() never copies the ExtGState it was given
    into the form's own Resources dict (unlike a page, which does) — so setFillAlpha()/a Color's
    alpha component has no visible effect on anything drawn inside a form in this reportlab
    version; the alpha is silently dropped and the fill paints fully opaque regardless. Dark
    themes define their dot-grid color (--border) as a low-alpha rgba specifically so the grid
    stays subtle against a dark background, same as it renders on the live canvas — pre-blending
    it into an opaque color here reproduces that exact intended appearance without depending on
    the broken transparency path.
    """
    a = fg.alpha if fg.alpha is not None else 1.0
    if a >= 1.0:
        return fg
    from reportlab.lib import colors
    return colors.Color(
        fg.red * a + bg.red * (1 - a),
        fg.green * a + bg.green * (1 - a),
        fg.blue * a + bg.blue * (1 - a),
    )


# Must mirror PAGE_WIDTH/PAGE_HEIGHT in static/notes/editor.js — that constant is the ONE
# canonical, fixed size for every notebook page's canvas (see PAGE_WIDTH/PAGE_HEIGHT and
# constrainObjectToPage() there). Nothing here derives a page size from content or from an
# individual exported image — this module just reads the same fixed numbers the editor already
# enforces, so a PDF page is always exactly the notebook's real, fixed page geometry.
_CANVAS_PX_W, _CANVAS_PX_H = 2400, 1600
# Canvas coordinates are plain CSS px at 100% zoom; 72/96 = 0.75pt per px is the standard
# CSS-px-to-PDF-pt conversion (same one a browser's own "print to PDF" uses), so content lands
# on the page at exactly the size/position it has in the notebook — no resize, no reflow.
_CANVAS_PX_TO_PT = 0.75
_GRID_SPACING_PT = 20 * _CANVAS_PX_TO_PT  # matches editor.css .canvas-shell's 20px background-size
_GRID_DOT_RADIUS_PT = 1 * _CANVAS_PX_TO_PT  # matches its 1px radial-gradient dot


def build_notebook_pdf(title: str, pages: list, grid_theme: dict = None) -> bytes:
    """
    pages: [{"title": str, "image": "data:image/png;base64,..."}], one per notebook page, in
    order. Each image is a client-rendered snapshot of just that page's Fabric objects — a
    transparent PNG that's always exactly PAGE_WIDTH x PAGE_HEIGHT (see _CANVAS_PX_W/H above),
    the notebook's one fixed page size, enforced by the editor itself — so reusing the browser's
    own Fabric.js rendering gives pixel-accurate content fidelity for free, instead of
    re-implementing a canvas renderer server-side, while every PDF page ends up exactly the same
    fixed size, matching every notebook page.

    The notebook's dot-grid page background is deliberately NOT part of that raster: it's
    rendered here as a single reusable PDF Form XObject (build once, stamp on every page — see
    beginForm/doForm below), which is both the "proper PDF" way to represent a repeating vector
    pattern and the fix for the old approach's dominant file-size cost — a full-page dot pattern
    baked into every page's raster compresses far worse (much higher entropy) than the mostly-
    transparent content-only PNG this now embeds instead.

    grid_theme: optional {"bg": "...", "dot": "..."} — the live --bg/--border CSS custom
    property values read from the notebook at export time, so the PDF's background matches
    whichever theme (light/dark/etc.) the user was actually viewing.
    """
    import base64
    import re
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors

    _ensure_unicode_font()

    buffer = BytesIO()
    margin = 36
    header_h = 26
    content_w = _CANVAS_PX_W * _CANVAS_PX_TO_PT  # fixed for every page
    content_h = _CANVAS_PX_H * _CANVAS_PX_TO_PT  # fixed for every page
    page_w = content_w + 2 * margin
    page_h = content_h + 2 * margin + header_h
    total = len(pages)

    c = pdf_canvas.Canvas(buffer, pagesize=(page_w, page_h))

    # Built once, then stamped on every page via doForm — a genuine shared PDF resource, so its
    # cost doesn't scale with page count.
    grid_theme = grid_theme or {}
    bg_color = _safe_color(grid_theme.get("bg"), "#f4f5f7")
    dot_color = _safe_color(grid_theme.get("dot"), "#e1e4e8")
    c.beginForm("notebookGrid", 0, 0, content_w, content_h)
    c.setFillColor(bg_color)
    c.rect(0, 0, content_w, content_h, fill=1, stroke=0)
    c.setFillColor(_flatten_over(dot_color, bg_color))
    # Squares, not circle() — at this size (~1.5pt across) a filled square and a filled circle
    # are visually indistinguishable, but circle() emits a ~4-curve bezier approximation per dot
    # (reportlab has no native circle primitive) vs. rect()'s single 're' operator, and this form
    # draws thousands of dots — that operator-count difference is what actually determines the
    # compressed size of this one-time shared resource.
    dot_side = _GRID_DOT_RADIUS_PT * 2
    y = _GRID_SPACING_PT / 2
    while y < content_h:
        x = _GRID_SPACING_PT / 2
        while x < content_w:
            c.rect(x - _GRID_DOT_RADIUS_PT, y - _GRID_DOT_RADIUS_PT, dot_side, dot_side, fill=1, stroke=0)
            x += _GRID_SPACING_PT
        y += _GRID_SPACING_PT
    c.endForm()

    for idx, page in enumerate(pages):
        match = re.match(r"^data:image/\w+;base64,(.+)$", page.get("image", ""), re.S)
        if not match:
            continue
        img = ImageReader(BytesIO(base64.b64decode(match.group(1))))

        c.setFont(_FONT_BOLD, 11)
        c.setFillColor(colors.HexColor("#2c3e50"))
        c.drawString(margin, page_h - margin + 6, str(page.get("title") or f"Page {idx + 1}"))
        c.setStrokeColor(colors.HexColor("#dddddd"))
        c.line(margin, page_h - margin, page_w - margin, page_h - margin)

        c.saveState()
        c.translate(margin, margin)
        c.doForm("notebookGrid")
        c.restoreState()
        c.drawImage(img, margin, margin, width=content_w, height=content_h, mask="auto")

        c.setFont(_FONT_REGULAR, 8)
        c.setFillColor(colors.HexColor("#999999"))
        c.drawRightString(page_w - margin, margin / 2, f"Page {idx + 1} of {total}")
        c.showPage()

    c.save()
    pdf = buffer.getvalue()
    buffer.close()
    return pdf