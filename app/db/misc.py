"""
app/db/misc.py
PostgreSQL queries for subjects and requests_raised tables.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning
from app.utils.pagination import paginate_params, pagination_meta, attach_row_numbers
from app.utils.datetime_service import now_utc_naive


# ─────────────────────────────────────────────
# Subjects
# ─────────────────────────────────────────────

def get_subjects_count() -> int:
    """Total subject count via COUNT query — no data fetch."""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM subjects")
        return row["count"] if row else 0
    except Exception as e:
        print(f"[db.misc] get_subjects_count error: {e}")
        return 0


def get_all_subjects() -> List[Dict]:
    try:
        return fetch_all(
            "SELECT id,subject_name,subject_folder_id,subject_folder_created_at "
            "FROM subjects ORDER BY subject_name"
        )
    except Exception as e:
        print(f"[db.misc] get_all_subjects error: {e}")
        return []


def get_subjects_page(search: str = "", page=1, per_page=20) -> Dict:
    page, per_page, offset = paginate_params(page, per_page)
    try:
        where_sql, params = "", []
        if search:
            where_sql = "WHERE subject_name ILIKE %s"
            params.append(f"%{search}%")
        total = fetch_one(f"SELECT COUNT(*) AS count FROM subjects {where_sql}", params)["count"]
        rows = fetch_all(
            f"SELECT id,subject_name,subject_folder_id,subject_folder_created_at FROM subjects "
            f"{where_sql} ORDER BY subject_name LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        attach_row_numbers(rows, page, per_page)
        return {"subjects": rows, **pagination_meta(total, page, per_page)}
    except Exception as e:
        print(f"[db.misc] get_subjects_page error: {e}")
        return {"subjects": [], **pagination_meta(0, page, per_page)}


def get_subject_by_name(name: str) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM subjects WHERE subject_name=%s", (name,))
    except Exception as e:
        print(f"[db.misc] get_subject_by_name error: {e}")
        return None


def get_subject_by_id(subject_id: int) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM subjects WHERE id=%s", (subject_id,))
    except Exception as e:
        print(f"[db.misc] get_subject_by_id error: {e}")
        return None


def get_subject_by_folder_id(folder_id: str) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM subjects WHERE subject_folder_id=%s", (folder_id,))
    except Exception as e:
        print(f"[db.misc] get_subject_by_folder_id error: {e}")
        return None


def create_subject(subject_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("subjects", subject_data)
    except Exception as e:
        print(f"[db.misc] create_subject error: {e}")
        return None


def update_subject(subject_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE subjects SET {sc} WHERE id=%s", params + [subject_id])
        return True
    except Exception as e:
        print(f"[db.misc] update_subject error: {e}")
        return False


def delete_subject(subject_id: int) -> bool:
    try:
        execute("DELETE FROM subjects WHERE id=%s", (subject_id,))
        return True
    except Exception as e:
        print(f"[db.misc] delete_subject error: {e}")
        return False


# ─────────────────────────────────────────────
# Access Requests
# ─────────────────────────────────────────────

def get_requests_status_counts() -> Dict[str, int]:
    """Access-request count per request_status — one aggregate query, for
    the admin dashboard's Requests chart."""
    try:
        rows = fetch_all("SELECT request_status, COUNT(*) AS count FROM requests_raised GROUP BY request_status")
        return {r["request_status"]: r["count"] for r in rows}
    except Exception as e:
        print(f"[db.misc] get_requests_status_counts error: {e}")
        return {}


def get_pending_requests() -> List[Dict]:
    try:
        return fetch_all(
            "SELECT * FROM requests_raised WHERE request_status=%s ORDER BY request_date DESC", ("pending",)
        )
    except Exception as e:
        print(f"[db.misc] get_pending_requests error: {e}")
        return []


def get_processed_requests() -> List[Dict]:
    try:
        return fetch_all(
            "SELECT * FROM requests_raised WHERE request_status = ANY(%s) ORDER BY request_date DESC",
            (["completed", "denied"],),
        )
    except Exception as e:
        print(f"[db.misc] get_processed_requests error: {e}")
        return []


def get_requests_by_user(username: str, email: str) -> List[Dict]:
    try:
        return fetch_all(
            "SELECT * FROM requests_raised WHERE username=%s AND email=%s ORDER BY request_date DESC",
            (username, email),
        )
    except Exception as e:
        print(f"[db.misc] get_requests_by_user error: {e}")
        return []


def create_request(request_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("requests_raised", request_data)
    except Exception as e:
        print(f"[db.misc] create_request error: {e}")
        return None


def update_request(request_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE requests_raised SET {sc} WHERE request_id=%s", params + [request_id])
        return True
    except Exception as e:
        print(f"[db.misc] update_request error: {e}")
        return False


def soft_delete_request(request_id: int, deleted_by: str) -> bool:
    """Hide a request/history row from every list view while keeping the row
    (and its append-only `reason` audit trail) intact in the DB — never
    touches users.role, so removing a request from the UI can never look
    like an approved access grant being revoked."""
    try:
        execute(
            "UPDATE requests_raised SET is_deleted=TRUE, deleted_at=%s, deleted_by=%s WHERE request_id=%s",
            (now_utc_naive().isoformat(), deleted_by, request_id),
        )
        return True
    except Exception as e:
        print(f"[db.misc] soft_delete_request error: {e}")
        return False
