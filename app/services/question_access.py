"""
app/services/question_access.py
The ONE server-side rule for "may this student see and discuss this exam question and its answer?".

Used by every feature that shows or talks about a question's answer:
  - the response page and the response PDF        check_result_access()
  - the AI Explanation endpoints                    check_question_access()
  - the AI Assistant's focused ("Discuss") mode     check_question_access()

The rule, in order (the first that fails decides):
  1. the question exists, and this student has THEIR OWN result for its exam that contains a response to it
     (someone else's result, an exam never taken, or an unknown question: all the same neutral "not available")
  2. the student has NO in-progress attempt on that exam (any attempt, so a re-attempt locks the earlier result's
     answers again until it is submitted; scheduled exams are no different)
  3. the result is visible: the existing can_user_see_result() (instant / manual release / delay)

Nothing here trusts a client-supplied value beyond the ids it asks about, and a denied Access carries no data.
A database error denies. This module adds no business rule of its own: it composes can_user_see_result, the attempt
lookup and the result lookup the response page already used.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

log = logging.getLogger("smartaiexam.access")

MSG_UNAVAILABLE = "This question isn't available for discussion."
MSG_IN_PROGRESS = ("You have an exam attempt in progress. The questions and answers of this exam are locked "
                   "until you submit it.")
MSG_ERROR = "Something went wrong. Please try again."

_QUESTION_KEYS = ("question_text", "option_a", "option_b", "option_c", "option_d", "image_path", "tolerance")


@dataclass(frozen=True)
class Access:
    """The decision, and (only when allowed) the data it lets through."""
    allowed: bool
    reason: str                         # ok | not_found | no_result | attempt_in_progress | result_hidden | error
    message: str = ""                   # safe to show the student
    question: Optional[Dict] = None     # the question row, shaped like a `questions` row plus given_answer
    exam: Optional[Dict] = None
    result: Optional[Dict] = None
    response: Optional[Dict] = None
    number: Optional[int] = None        # "Question 14": its position on the response page
    category: str = ""
    subcategory: str = ""
    explanation: Optional[str] = None   # this student's latest saved AI Explanation, if any
    graded_key: str = ""                # the answer key stored on the response when it was graded

    @property
    def status(self) -> str:
        """correct | incorrect | skipped"""
        resp = self.response or {}
        attempted = resp.get("is_attempted")
        if isinstance(attempted, str):
            attempted = attempted.strip().lower() in ("true", "1", "yes")
        if attempted is False or str(resp.get("given_answer") or "").strip().lower() in ("", "not answered", "none", "nan"):
            return "skipped"
        return "correct" if resp.get("is_correct") in (True, "true", "True", "1", 1) else "incorrect"

    def context(self) -> Dict:
        """What question_context.build_focus_block() / build_card() format."""
        return {"question": self.question, "exam_name": (self.exam or {}).get("name", ""), "exam_id": (self.exam or {}).get("id"),
                "category": self.category, "subcategory": self.subcategory, "number": self.number,
                "response": self.response, "explanation": self.explanation, "graded_key": self.graded_key,
                "result_id": (self.result or {}).get("id"), "status": self.status}


def _denied(reason: str, message: str) -> Access:
    return Access(allowed=False, reason=reason, message=message)


def decide(*, has_result: bool, has_response: bool, has_active_attempt: bool,
           exam: Optional[Dict], result: Optional[Dict]) -> Tuple[str, str]:
    """(reason, message). "ok" only when every rule above passes. Pure: no I/O."""
    from app.services.result_service import can_user_see_result
    if not has_result or not has_response:
        return "no_result", MSG_UNAVAILABLE
    if has_active_attempt:
        return "attempt_in_progress", MSG_IN_PROGRESS
    visible, reason = can_user_see_result(exam or {}, result or {})
    if not visible:
        return "result_hidden", reason
    return "ok", ""


def evaluate_bundle(row: Optional[Dict]) -> Access:
    """Turn one get_access_bundle() row into a decision. Pure."""
    if not row:
        return _denied("not_found", MSG_UNAVAILABLE)
    exam = {"id": row["exam_id"], "name": row.get("exam_name"), "result_mode": row.get("result_mode"),
            "results_released": row.get("results_released"), "result_delay": row.get("result_delay"),
            "scheduled_mode": row.get("scheduled_mode")}
    result = ({"id": row["result_id"], "completed_at": row.get("completed_at"), "attempt_id": row.get("attempt_id")}
              if row.get("result_id") else None)
    reason, message = decide(has_result=result is not None, has_response=row.get("response_id") is not None,
                             has_active_attempt=bool(row.get("has_active_attempt")), exam=exam, result=result)
    if reason != "ok":
        return _denied(reason, message)

    question = {"id": row["q_id"], "exam_id": row["exam_id"], "correct_answer": row.get("q_correct_answer"),
                "question_type": row.get("q_question_type") or "MCQ",
                "positive_marks": row.get("q_positive_marks"), "negative_marks": row.get("q_negative_marks")}
    question.update({k: row.get(k) for k in _QUESTION_KEYS})
    response = {"given_answer": row.get("given_answer"), "correct_answer": row.get("resp_correct_answer"),
                "is_correct": row.get("is_correct"), "is_attempted": row.get("is_attempted"),
                "marks_obtained": row.get("marks_obtained")}
    return Access(allowed=True, reason="ok", question=question, exam=exam, result=result, response=response,
                  number=int(row.get("q_number") or 0) or None, category=row.get("category_name") or "",
                  subcategory=row.get("subcategory_name") or "", explanation=row.get("latest_explanation") or None,
                  graded_key=str(row.get("resp_correct_answer") or ""))


def check_question_access(user_id: int, question_id: int, result_id: Optional[int] = None) -> Access:
    """May `user_id` see and discuss this question's answer? One database query. Any failure denies."""
    from app.db.question_access import get_access_bundle
    try:
        row = get_access_bundle(int(user_id), int(question_id), int(result_id) if result_id else None)
    except Exception as e:                                        # fail CLOSED
        log.warning("access check failed: %s", type(e).__name__)
        return _denied("error", MSG_ERROR)
    return evaluate_bundle(row)


def resolve_result(user_id: int, exam_id: int, result_id: Optional[int], session_hint: Optional[int] = None) -> Optional[Dict]:
    """This student's result for the exam: the requested one (only if it is theirs AND belongs to this exam), else the
    one remembered in their session, else their latest. Moved here from the response page so every feature resolves a
    result the same way. (One tightening: a requested result must also belong to the exam in the URL.)"""
    from app.db.results import get_result_by_id, get_latest_result_by_user_exam
    if result_id:
        r = get_result_by_id(result_id)
        if r and int(r.get("student_id", 0)) == user_id and int(r.get("exam_id", 0)) == exam_id:
            return r
        return None
    if session_hint:
        r = get_result_by_id(session_hint)
        if r and int(r.get("exam_id", 0)) == exam_id and int(r.get("student_id", 0)) == user_id:
            return r
    return get_latest_result_by_user_exam(user_id, exam_id)


def check_result_access(user_id: int, exam: Dict, result: Optional[Dict]) -> Access:
    """The same rule for a whole result (the response page and its PDF). `result` must already be the student's own,
    as returned by resolve_result(). The page shows every answer key, so the in-progress lock applies too."""
    from app.db.attempts import get_active_attempt
    if not result:
        return _denied("no_result", MSG_UNAVAILABLE)
    active = bool(get_active_attempt(int(user_id), int(exam.get("id", 0))))
    reason, message = decide(has_result=True, has_response=True, has_active_attempt=active, exam=exam, result=result)
    return Access(allowed=True, reason="ok", exam=exam, result=result) if reason == "ok" else _denied(reason, message)
