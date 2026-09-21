"""
app/db/question_access.py
ONE query that gathers everything the access policy (app/services/question_access.py) and the AI features need to
decide about, and then talk about, one exam question for one student:

  the question · its exam and category · this student's result for that exam (the requested one, else the latest) ·
  this student's response to the question · whether this student has ANY in-progress attempt on that exam ·
  this student's latest saved AI Explanation for the question

Everything is filtered by the student's own id inside the query, so another student's result, response or explanation
can never come back. The row is raw data: it says nothing about whether the student may SEE it. The policy decides.
"""

from typing import Dict, Optional

from app.db import fetch_one

# Same status value db.attempts.get_active_attempt() looks for.
ACTIVE_ATTEMPT_STATUS = "in_progress"

_BUNDLE_SQL = """
SELECT
    q.id AS q_id, q.exam_id, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
    q.correct_answer AS q_correct_answer, q.question_type AS q_question_type, q.image_path,
    q.positive_marks AS q_positive_marks, q.negative_marks AS q_negative_marks, q.tolerance,
    e.name AS exam_name, e.result_mode, e.results_released, e.result_delay, e.scheduled_mode,
    c.name AS category_name, sc.name AS subcategory_name,
    r.id AS result_id, r.completed_at, r.attempt_id,
    resp.id AS response_id, resp.given_answer, resp.correct_answer AS resp_correct_answer,
    resp.is_correct, resp.is_attempted, resp.marks_obtained,
    (SELECT count(*) FROM responses r2 WHERE r2.result_id = r.id AND r2.question_id <= q.id) AS q_number,
    EXISTS (SELECT 1 FROM exam_attempts a
             WHERE a.student_id = %(uid)s AND a.exam_id = q.exam_id AND a.status = %(active)s) AS has_active_attempt,
    (SELECT h.explanation FROM ai_explanation_history h
      WHERE h.user_id = %(uid)s AND h.question_id = q.id
      ORDER BY h.generated_at DESC, h.id DESC LIMIT 1) AS latest_explanation
FROM questions q
JOIN exams e ON e.id = q.exam_id
LEFT JOIN categories c ON c.id = e.category_id
LEFT JOIN subcategories sc ON sc.id = e.subcategory_id
LEFT JOIN LATERAL (
    SELECT rs.id, rs.completed_at, rs.attempt_id
      FROM results rs
     WHERE rs.student_id = %(uid)s AND rs.exam_id = q.exam_id
       AND (%(rid)s::int IS NULL OR rs.id = %(rid)s::int)
     ORDER BY rs.completed_at DESC, rs.id DESC
     LIMIT 1
) r ON TRUE
LEFT JOIN responses resp ON resp.result_id = r.id AND resp.question_id = q.id
WHERE q.id = %(qid)s
"""


def get_access_bundle(user_id: int, question_id: int, result_id: Optional[int] = None) -> Optional[Dict]:
    """The row described above, or None when the question does not exist.

    With `result_id`, only that result is considered (and only if it is this student's own; otherwise the result
    columns come back empty). Without it, the student's latest result for the exam is used. Raises on a database
    error: the caller must treat that as "not allowed", never as "allowed"."""
    return fetch_one(_BUNDLE_SQL, {
        "uid": int(user_id), "qid": int(question_id),
        "rid": int(result_id) if result_id else None, "active": ACTIVE_ATTEMPT_STATUS,
    })
