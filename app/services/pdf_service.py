"""
app/services/pdf_service.py
ReportLab PDF generation for both:
  - Admin: detailed result PDF (questions + answers)
  - Student: response summary PDF
Extracted from admin.py and main.py.
"""

from io import BytesIO
from datetime import datetime
from typing import List, Dict, Optional

from app.utils.latex import strip_latex


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

    buffer  = BytesIO()
    doc     = SimpleDocTemplate(buffer, pagesize=letter,
                                rightMargin=50, leftMargin=50,
                                topMargin=50, bottomMargin=50)
    styles  = getSampleStyleSheet()
    title_s = ParagraphStyle("T", parent=styles["Title"], fontSize=18,
                             textColor=colors.HexColor("#2c3e50"), alignment=TA_CENTER)
    h2_s    = ParagraphStyle("H", parent=styles["Heading2"], fontSize=14,
                             textColor=colors.HexColor("#2c3e50"))

    story = [Paragraph("Exam Response Analysis", title_s)]

    hdr = Table([
        ["Exam:",    str(exam.get("name",""))],
        ["Student:", student_name],
        ["Score:",   f"{result.get('score')}/{result.get('max_score')} ({float(result.get('percentage',0)):.1f}%)"],
        ["Grade:",   str(result.get("grade","N/A"))],
    ], colWidths=[1.5*inch, 4*inch])
    hdr.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,-1),colors.lightgrey),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
        ("FONTSIZE",(0,0),(-1,-1),12),
        ("PADDING",(0,0),(-1,-1),8),
        ("GRID",(0,0),(-1,-1),1,colors.black),
    ]))
    story += [hdr, Spacer(1,20)]

    for resp in responses:
        qid = int(resp.get("question_id",0))
        q   = questions_map.get(qid, questions_map.get(str(qid), {}))
        if not q: continue

        story.append(Paragraph(f"Question {qid}", h2_s))
        story.append(Paragraph(str(q.get("question_text","")), styles["Normal"]))
        story.append(Spacer(1,6))

        for lbl, key in [("A","option_a"),("B","option_b"),("C","option_c"),("D","option_d")]:
            val = q.get(key,"")
            if val and str(val).strip() not in ("","nan","None"):
                story.append(Paragraph(f"<b>{lbl}.</b> {val}", styles["Normal"]))

        story.append(Spacer(1,8))
        given  = str(resp.get("given_answer","") or "Not Answered")
        corr   = str(resp.get("correct_answer","") or "N/A")
        marks  = resp.get("marks_obtained", 0)
        is_cor = str(resp.get("is_correct","false")).lower() == "true"

        ans_t = Table([
            ["Your Answer:", given],
            ["Correct Answer:", corr],
            ["Marks:", str(marks)],
            ["Status:", "Correct" if is_cor else "Incorrect"],
        ], colWidths=[1.5*inch, 4*inch])
        ans_t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,-1),colors.lightblue),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),10),
            ("PADDING",(0,0),(-1,-1),6),
            ("GRID",(0,0),(-1,-1),1,colors.black),
        ]))
        story += [ans_t, Spacer(1,16)]

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf