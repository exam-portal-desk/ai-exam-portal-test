"""
app/db/results.py
All PostgreSQL queries for `results` and `responses` tables.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, insert_returning, insert_many


# ─────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────

_RESULT_COLS = (
    "id,student_id,exam_id,score,max_score,percentage,grade,"
    "completed_at,time_taken_minutes,correct_answers,incorrect_answers,"
    "unanswered_questions,total_questions"
)


def get_all_results() -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_RESULT_COLS} FROM results ORDER BY completed_at DESC")
    except Exception as e:
        print(f"[db.results] get_all_results error: {e}")
        return []


def get_result_by_id(result_id: int) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_RESULT_COLS} FROM results WHERE id=%s", (result_id,))
    except Exception as e:
        print(f"[db.results] get_result_by_id error: {e}")
        return None


def get_results_by_user(user_id: int) -> List[Dict]:
    try:
        return fetch_all(
            f"SELECT {_RESULT_COLS} FROM results WHERE student_id=%s ORDER BY completed_at DESC", (user_id,)
        )
    except Exception as e:
        print(f"[db.results] get_results_by_user error: {e}")
        return []


def get_results_by_exam(exam_id: int) -> List[Dict]:
    try:
        return fetch_all(
            f"SELECT {_RESULT_COLS} FROM results WHERE exam_id=%s ORDER BY completed_at DESC", (exam_id,)
        )
    except Exception as e:
        print(f"[db.results] get_results_by_exam error: {e}")
        return []


def get_latest_result_by_user_exam(user_id: int, exam_id: int) -> Optional[Dict]:
    try:
        return fetch_one(
            f"SELECT {_RESULT_COLS} FROM results WHERE student_id=%s AND exam_id=%s "
            "ORDER BY completed_at DESC LIMIT 1",
            (user_id, exam_id),
        )
    except Exception as e:
        print(f"[db.results] get_latest_result_by_user_exam error: {e}")
        return None


def create_result(result_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("results", result_data)
    except Exception as e:
        print(f"[db.results] create_result error: {e}")
        return None


# ─────────────────────────────────────────────
# Responses
# ─────────────────────────────────────────────

_RESPONSE_COLS = (
    "id,result_id,exam_id,question_id,given_answer,correct_answer,"
    "is_correct,marks_obtained,question_type,is_attempted"
)


def get_responses_by_result(result_id: int) -> List[Dict]:
    try:
        return fetch_all(
            f"SELECT {_RESPONSE_COLS} FROM responses WHERE result_id=%s ORDER BY question_id", (result_id,)
        )
    except Exception as e:
        print(f"[db.results] get_responses_by_result error: {e}")
        return []


def create_responses_bulk(responses: List[Dict]) -> bool:
    try:
        insert_many("responses", responses)
        return True
    except Exception as e:
        print(f"[db.results] create_responses_bulk error: {e}")
        return False
