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

def build_notebook_pdf(title: str, pages: list) -> bytes:
    """
    pages: [{"title": str, "image": "data:image/png;base64,..."}], one per notebook page,
    in order. Each image is a client-rendered snapshot of that page's actual canvas content
    (see editor.js exportNotebookPdf) — reusing the browser's own Fabric.js rendering gives
    pixel-accurate layout/position fidelity for free, instead of re-implementing a canvas
    renderer server-side. This function only assembles those images into one PDF (via the
    same reportlab dependency the rest of this service already uses), one page each,
    scaled to fit and centered.
    """
    import base64
    import re
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors

    _ensure_unicode_font()

    buffer = BytesIO()
    margin = 36
    header_h = 26
    # Bounded content area, in points, that each page's size is derived from — matching the
    # page to the actual notebook content's aspect ratio (instead of forcing every page into
    # portrait Letter) is what eliminates the huge blank top/bottom margins on wide pages.
    MAX_DIM, MIN_DIM = 1000, 400
    c = pdf_canvas.Canvas(buffer, pagesize=letter)
    total = len(pages)

    for idx, page in enumerate(pages):
        match = re.match(r"^data:image/\w+;base64,(.+)$", page.get("image", ""), re.S)
        if not match:
            continue
        img = ImageReader(BytesIO(base64.b64decode(match.group(1))))
        img_w, img_h = img.getSize()

        content_w = min(MAX_DIM, max(MIN_DIM, img_w))
        content_h = min(MAX_DIM, max(MIN_DIM, content_w * img_h / img_w)) if img_w else content_w
        page_w = content_w + 2 * margin
        page_h = content_h + 2 * margin + header_h
        c.setPageSize((page_w, page_h))

        c.setFont(_FONT_BOLD, 11)
        c.setFillColor(colors.HexColor("#2c3e50"))
        c.drawString(margin, page_h - margin + 6, str(page.get("title") or f"Page {idx + 1}"))
        c.setStrokeColor(colors.HexColor("#dddddd"))
        c.line(margin, page_h - margin, page_w - margin, page_h - margin)

        avail_w = page_w - 2 * margin
        avail_h = page_h - 2 * margin - header_h
        scale = min(avail_w / img_w, avail_h / img_h) if img_w and img_h else 1
        draw_w, draw_h = img_w * scale, img_h * scale
        x = (page_w - draw_w) / 2
        y = margin + (avail_h - draw_h) / 2
        c.drawImage(img, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")

        c.setFont(_FONT_REGULAR, 8)
        c.setFillColor(colors.HexColor("#999999"))
        c.drawRightString(page_w - margin, margin / 2, f"Page {idx + 1} of {total}")
        c.showPage()

    c.save()
    pdf = buffer.getvalue()
    buffer.close()
    return pdf