"""
app/services/response_view_service.py
The one place that turns a result's stored responses + the exam's questions into the rows templates/response.html draws.
Used by the student's Response page and by the admin User Analytics view of a student's attempt, so the two can never
show different answers, options, marks, status or images. Callers decide WHO may see the result; this only builds rows.
"""

import json
from typing import Dict, List

from app.db.questions import get_questions_by_exam
from app.db.results import get_responses_by_result


def _parse_answer(raw, qtype):
    if not raw or raw in ("None", "nan", ""):
        return None
    try:
        if qtype == "MSQ":
            return json.loads(raw) if raw.startswith("[") else [x.strip() for x in raw.split(",")]
    except Exception:
        pass
    return raw


def _truthy(value, default=True):
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def build_response_rows(result_id: int, exam_id: int) -> List[Dict]:
    from app.services.image_storage_service import resolve_question_image_urls_bulk

    responses = get_responses_by_result(result_id)
    q_map = {int(q["id"]): q for q in get_questions_by_exam(exam_id)}

    # Bulk-resolve unique image paths first (concurrently — see resolve_question_image_urls_bulk).
    unique_paths = set()
    for resp in responses:
        q = q_map.get(int(resp.get("question_id", 0)), {})
        ip = q.get("image_path", "") if q else ""
        if ip and str(ip).strip() not in ("", "nan", "None"):
            unique_paths.add(str(ip).strip())
    image_url_map = resolve_question_image_urls_bulk(unique_paths)

    rows = []
    for resp in responses:
        q = q_map.get(int(resp.get("question_id", 0)), {})
        if not q:
            continue
        image_path = q.get("image_path", "")
        if image_path and str(image_path).strip() not in ("", "nan", "None"):
            has_img, img_url = image_url_map.get(str(image_path).strip(), (False, None))
            q["has_image"], q["image_url"] = has_img, img_url
        else:
            q["has_image"], q["image_url"] = False, None
        qtype = str(resp.get("question_type", "MCQ"))
        rows.append({
            "question": q,
            "given_answer": _parse_answer(str(resp.get("given_answer", "") or ""), qtype) or "Not Answered",
            "correct_answer": _parse_answer(str(resp.get("correct_answer", "") or ""), qtype) or "N/A",
            "is_correct": _truthy(resp.get("is_correct", False), False),
            "is_attempted": _truthy(resp.get("is_attempted", True), True),
            "marks_obtained": float(resp.get("marks_obtained", 0) or 0),
            "question_type": qtype,
        })
    return rows
