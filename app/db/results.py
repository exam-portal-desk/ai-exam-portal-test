"""
app/db/results.py
All Supabase queries for `results` and `responses` tables.
"""

from typing import Optional, List, Dict
from app.db import supabase


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
        res = (
            supabase.table("results")
            .select(_RESULT_COLS)
            .order("completed_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.results] get_all_results error: {e}")
        return []


def get_result_by_id(result_id: int) -> Optional[Dict]:
    try:
        res = supabase.table("results").select(_RESULT_COLS).eq("id", result_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.results] get_result_by_id error: {e}")
        return None


def get_results_by_user(user_id: int) -> List[Dict]:
    try:
        res = (
            supabase.table("results")
            .select(_RESULT_COLS)
            .eq("student_id", user_id)
            .order("completed_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.results] get_results_by_user error: {e}")
        return []


def get_results_by_exam(exam_id: int) -> List[Dict]:
    try:
        res = (
            supabase.table("results")
            .select(_RESULT_COLS)
            .eq("exam_id", exam_id)
            .order("completed_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.results] get_results_by_exam error: {e}")
        return []


def get_latest_result_by_user_exam(user_id: int, exam_id: int) -> Optional[Dict]:
    try:
        res = (
            supabase.table("results")
            .select(_RESULT_COLS)
            .eq("student_id", user_id)
            .eq("exam_id", exam_id)
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.results] get_latest_result_by_user_exam error: {e}")
        return None


def create_result(result_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("results").insert(result_data).execute()
        return res.data[0] if res.data else None
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
        res = (
            supabase.table("responses")
            .select(_RESPONSE_COLS)
            .eq("result_id", result_id)
            .order("question_id")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.results] get_responses_by_result error: {e}")
        return []


def create_responses_bulk(responses: List[Dict]) -> bool:
    try:
        supabase.table("responses").insert(responses).execute()
        return True
    except Exception as e:
        print(f"[db.results] create_responses_bulk error: {e}")
        return False