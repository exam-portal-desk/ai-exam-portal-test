"""
app/db/exams.py
All PostgreSQL queries related to the `exams` table.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning


_ALL_COLS = (
    "id,name,date,start_time,duration,total_questions,status,"
    "instructions,positive_marks,negative_marks,max_attempts,"
    "result_mode,result_delay,results_released,category_id"
)


def get_all_exams() -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_ALL_COLS} FROM exams ORDER BY id")
    except Exception as e:
        print(f"[db.exams] get_all_exams error: {e}")
        return []


def get_exams_for_dropdown() -> List[Dict]:
    """Minimal projection for exam select elements."""
    try:
        return fetch_all("SELECT id,name FROM exams ORDER BY name")
    except Exception as e:
        print(f"[db.exams] get_exams_for_dropdown error: {e}")
        return []


def get_exam_by_id(exam_id: int) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_ALL_COLS} FROM exams WHERE id=%s", (exam_id,))
    except Exception as e:
        print(f"[db.exams] get_exam_by_id error: {e}")
        return None


def get_exams_by_ids(exam_ids: List[int]) -> Dict[str, Dict]:
    """Batch fetch exams; returns dict keyed by str(id)."""
    if not exam_ids:
        return {}
    try:
        rows = fetch_all("SELECT id,name FROM exams WHERE id = ANY(%s)", (list(exam_ids),))
        return {str(e["id"]): e for e in rows}
    except Exception as e:
        print(f"[db.exams] get_exams_by_ids error: {e}")
        return {}


def create_exam(exam_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("exams", exam_data)
    except Exception as e:
        print(f"[db.exams] create_exam error: {e}")
        return None


def update_exam(exam_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE exams SET {sc} WHERE id=%s", params + [exam_id])
        return True
    except Exception as e:
        print(f"[db.exams] update_exam error: {e}")
        return False


def delete_exam(exam_id: int) -> bool:
    try:
        execute("DELETE FROM exams WHERE id=%s", (exam_id,))
        return True
    except Exception as e:
        print(f"[db.exams] delete_exam error: {e}")
        return False


def release_exam_results(exam_id: int, release: bool = True) -> bool:
    try:
        execute("UPDATE exams SET results_released=%s WHERE id=%s", (release, exam_id))
        return True
    except Exception as e:
        print(f"[db.exams] release_exam_results error: {e}")
        return False


def get_exams_by_category(category_id: int) -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_ALL_COLS} FROM exams WHERE category_id=%s ORDER BY id", (category_id,))
    except Exception as e:
        print(f"[db.exams] get_exams_by_category error: {e}")
        return []
