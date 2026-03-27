"""
app/db/exams.py
All Supabase queries related to the `exams` table.
"""

from typing import Optional, List, Dict
from app.db import supabase


_ALL_COLS = (
    "id,name,date,start_time,duration,total_questions,status,"
    "instructions,positive_marks,negative_marks,max_attempts,"
    "result_mode,result_delay,results_released,category_id"
)

# Lighter projection for dropdowns / listings
_LIST_COLS = "id,name,status,date,start_time,duration,total_questions,max_attempts,result_mode,result_delay,results_released"


def get_all_exams() -> List[Dict]:
    try:
        res = supabase.table("exams").select(_ALL_COLS).order("id").execute()
        return res.data or []
    except Exception as e:
        print(f"[db.exams] get_all_exams error: {e}")
        return []


def get_exams_for_dropdown() -> List[Dict]:
    """Minimal projection for exam select elements."""
    try:
        res = supabase.table("exams").select("id,name").order("name").execute()
        return res.data or []
    except Exception as e:
        print(f"[db.exams] get_exams_for_dropdown error: {e}")
        return []


def get_exam_by_id(exam_id: int) -> Optional[Dict]:
    try:
        res = supabase.table("exams").select(_ALL_COLS).eq("id", exam_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.exams] get_exam_by_id error: {e}")
        return None


def get_exams_by_ids(exam_ids: List[int]) -> Dict[str, Dict]:
    """Batch fetch exams; returns dict keyed by str(id)."""
    if not exam_ids:
        return {}
    try:
        res = (
            supabase.table("exams")
            .select("id,name")
            .in_("id", [str(i) for i in exam_ids])
            .execute()
        )
        return {str(e["id"]): e for e in (res.data or [])}
    except Exception as e:
        print(f"[db.exams] get_exams_by_ids error: {e}")
        return {}


def create_exam(exam_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("exams").insert(exam_data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.exams] create_exam error: {e}")
        return None


def update_exam(exam_id: int, updates: Dict) -> bool:
    try:
        supabase.table("exams").update(updates).eq("id", exam_id).execute()
        return True
    except Exception as e:
        print(f"[db.exams] update_exam error: {e}")
        return False


def delete_exam(exam_id: int) -> bool:
    try:
        supabase.table("exams").delete().eq("id", exam_id).execute()
        return True
    except Exception as e:
        print(f"[db.exams] delete_exam error: {e}")
        return False


def release_exam_results(exam_id: int, release: bool = True) -> bool:
    try:
        supabase.table("exams").update({"results_released": release}).eq("id", exam_id).execute()
        return True
    except Exception as e:
        print(f"[db.exams] release_exam_results error: {e}")
        return False


def get_exams_by_category(category_id: int) -> List[Dict]:
    try:
        res = (
            supabase.table("exams")
            .select(_ALL_COLS)
            .eq("category_id", category_id)
            .order("id")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.exams] get_exams_by_category error: {e}")
        return []