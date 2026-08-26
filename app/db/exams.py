"""
app/db/exams.py
All PostgreSQL queries related to the `exams` table.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning
from app.utils.pagination import paginate_params, pagination_meta


_ALL_COLS = (
    "id,name,date,start_time,duration,total_questions,status,"
    "instructions,positive_marks,negative_marks,max_attempts,"
    "result_mode,result_delay,results_released,category_id,"
    "passing_percentage,created_at,subcategory_id"
)


def get_all_exams() -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_ALL_COLS} FROM exams ORDER BY id")
    except Exception as e:
        print(f"[db.exams] get_all_exams error: {e}")
        return []


def get_exams_count() -> int:
    """Total exam count via COUNT query — no data fetch."""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM exams")
        return row["count"] if row else 0
    except Exception as e:
        print(f"[db.exams] get_exams_count error: {e}")
        return 0


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


def get_exams_by_ids_full(exam_ids: List[int]) -> Dict[str, Dict]:
    """Batch fetch full exam rows (all columns, needed for result-visibility
    gating via can_user_see_result — unlike get_exams_by_ids, which only
    returns id,name). Returns dict keyed by str(id)."""
    if not exam_ids:
        return {}
    try:
        rows = fetch_all(f"SELECT {_ALL_COLS} FROM exams WHERE id = ANY(%s)", (list(exam_ids),))
        return {str(e["id"]): e for e in rows}
    except Exception as e:
        print(f"[db.exams] get_exams_by_ids_full error: {e}")
        return {}


def _sync_category_from_subcategory(data: Dict) -> Dict:
    """exams.category_id is kept in sync with subcategories.category_id
    whenever a subcategory_id is supplied, so any older code path reading
    category_id directly (dashboard/session-based browsing, existing
    reports) keeps working without modification. subcategory_id is the
    source of truth going forward."""
    if data.get("subcategory_id"):
        from app.db.subcategories import get_subcategory_by_id
        subcat = get_subcategory_by_id(data["subcategory_id"])
        if subcat:
            data = {**data, "category_id": subcat["category_id"]}
    return data


def create_exam(exam_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("exams", _sync_category_from_subcategory(exam_data))
    except Exception as e:
        print(f"[db.exams] create_exam error: {e}")
        return None


def update_exam(exam_id: int, updates: Dict) -> bool:
    try:
        updates = _sync_category_from_subcategory(updates)
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


def get_exams_by_subcategory(subcategory_id: int) -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_ALL_COLS} FROM exams WHERE subcategory_id=%s ORDER BY id", (subcategory_id,))
    except Exception as e:
        print(f"[db.exams] get_exams_by_subcategory error: {e}")
        return []


def get_exams_by_subcategory_page(subcategory_id: int, status: str = "", search: str = "",
                                   page=1, per_page=12) -> Dict:
    """Bounded/searchable variant of get_exams_by_subcategory, used by the
    student dashboard so opening it never loads every exam in the
    subcategory at once (search/status filtering happens server-side)."""
    page, per_page, offset = paginate_params(page, per_page)
    try:
        where = ["subcategory_id=%s"]
        params = [subcategory_id]
        if status:
            where.append("status=%s")
            params.append(status)
        if search:
            where.append("name ILIKE %s")
            params.append(f"%{search}%")
        where_sql = "WHERE " + " AND ".join(where)

        total = fetch_one(f"SELECT COUNT(*) AS count FROM exams {where_sql}", params)["count"]
        rows = fetch_all(
            f"SELECT {_ALL_COLS} FROM exams {where_sql} ORDER BY id LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return {"exams": rows, **pagination_meta(total, page, per_page)}
    except Exception as e:
        print(f"[db.exams] get_exams_by_subcategory_page error: {e}")
        return {"exams": [], **pagination_meta(0, page, per_page)}


_ADMIN_LIST_COLS = ",".join(f"e.{c}" for c in _ALL_COLS.split(","))


def get_exams_page(search: str = "", category_id=None, subcategory_id=None, status: str = "",
                    page=1, per_page=20) -> Dict:
    """Admin exam list: bounded, server-side filtered, category/subcategory
    names resolved via JOIN (no per-row lookup)."""
    page, per_page, offset = paginate_params(page, per_page)
    try:
        where, params = [], []
        if search:
            where.append("e.name ILIKE %s")
            params.append(f"%{search}%")
        if category_id:
            where.append("e.category_id=%s")
            params.append(category_id)
        if subcategory_id:
            where.append("e.subcategory_id=%s")
            params.append(subcategory_id)
        if status:
            where.append("e.status=%s")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        total = fetch_one(f"SELECT COUNT(*) AS count FROM exams e {where_sql}", params)["count"]
        rows = fetch_all(
            f"SELECT {_ADMIN_LIST_COLS}, c.name AS category_name, s.name AS subcategory_name "
            f"FROM exams e "
            f"LEFT JOIN categories c ON c.id = e.category_id "
            f"LEFT JOIN subcategories s ON s.id = e.subcategory_id "
            f"{where_sql} ORDER BY e.id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return {"exams": rows, **pagination_meta(total, page, per_page)}
    except Exception as e:
        print(f"[db.exams] get_exams_page error: {e}")
        return {"exams": [], **pagination_meta(0, page, per_page)}
