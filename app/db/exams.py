"""
app/db/exams.py
All PostgreSQL queries related to the `exams` table.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning
from app.utils.pagination import paginate_params, pagination_meta, attach_row_numbers


_ALL_COLS = (
    "id,name,date,start_time,duration,total_questions,status,"
    "instructions,positive_marks,negative_marks,max_attempts,"
    "result_mode,result_delay,results_released,category_id,"
    "passing_percentage,created_at,subcategory_id,"
    "scheduled_mode,prep_window_minutes,completion_buffer_minutes,"
    "allow_manual_submission,available_after_datetime"
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


def get_exams_for_selector() -> List[Dict]:
    """Everything the Questions Management exam picker needs to show a
    real "which exam am I about to touch" card (name, category/subcategory,
    date/time, mode/status, question count) — in exactly ONE round trip
    regardless of how many exams exist, never one query per exam and never
    a separate round trip for the count.

    PERFORMANCE: the database is a remote Supabase instance, so each round
    trip costs real, measurable network latency (~100-150ms observed) —
    the dominant cost for admin pages at this app's actual data scale is
    round-trip COUNT, not query complexity. This used to be two separate
    fetch_all() calls (a JOIN, then a standalone GROUP BY merged in
    Python); the count is now a LEFT JOIN to a pre-aggregated subquery in
    the same statement, so Postgres does the aggregation server-side in
    the same trip instead of the app paying for a second one. Deliberately
    NOT exams.total_questions (that's the admin's originally configured
    target, which can drift from what's actually been added).

    effective_status is attached the same way get_exams_page() does, so the
    picker's badge (Upcoming/Ongoing/Completed/Scheduled) matches Manage
    Exams exactly."""
    from app.services.exam_service import get_effective_status

    try:
        exams = fetch_all(
            "SELECT e.id, e.name, e.date, e.start_time, e.scheduled_mode, e.status, "
            "c.name AS category_name, s.name AS subcategory_name, "
            "COALESCE(qc.question_count, 0) AS question_count "
            "FROM exams e "
            "LEFT JOIN categories c ON c.id = e.category_id "
            "LEFT JOIN subcategories s ON s.id = e.subcategory_id "
            "LEFT JOIN (SELECT exam_id, COUNT(*) AS question_count FROM questions GROUP BY exam_id) qc "
            "  ON qc.exam_id = e.id "
            "ORDER BY e.id DESC"
        )
        for e in exams:
            e["effective_status"] = get_effective_status(e)
        return exams
    except Exception as ex:
        print(f"[db.exams] get_exams_for_selector error: {ex}")
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


def set_scheduled_exam_cancelled(exam_id: int, cancelled: bool = True) -> bool:
    """Explicit admin override for a Scheduled Exam only — the ONLY writer
    of 'cancelled' into the status column. Deliberately narrow (a single
    UPDATE, not a general status setter) so it can never be reached from
    the normal edit form's status field, which the admin UI hides entirely
    for scheduled exams. Reverting (cancelled=False) restores 'scheduled',
    the normal marker meaning "effective status is computed, not stored".
    Callers must verify scheduled_mode=true before calling this — it does
    not itself check, mirroring release_exam_results()'s existing shape."""
    try:
        execute(
            "UPDATE exams SET status=%s WHERE id=%s",
            ("cancelled" if cancelled else "scheduled", exam_id),
        )
        return True
    except Exception as e:
        print(f"[db.exams] set_scheduled_exam_cancelled error: {e}")
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


def _filter_paginate_by_effective_status(rows: List[Dict], status: str, page: int, per_page: int,
                                          reverse: bool = False) -> Dict:
    """Shared tail end of get_exams_by_subcategory_page()/get_exams_page():
    given an already status-unfiltered row set (small — bounded by
    subcategory/category/search at the SQL level first), attach each row's
    effective_status, optionally filter by it, then paginate in Python.

    WHY NOT FILTER status IN SQL: a Scheduled Exam's bucket depends on
    date+start_time+duration+prep/buffer in config.APP_TIMEZONE — the same
    computation get_effective_status() already owns as the single source
    of truth. Duplicating that as a timezone-aware SQL CASE expression
    would mean two implementations of the same time math that could drift
    out of sync, and would hardcode APP_TIMEZONE into SQL despite it being
    a configurable env var. Filtering/paginating this small, already-
    bounded set in Python keeps exactly one implementation of "what status
    is this exam right now" for both manual and scheduled exams alike.
    """
    from app.services.exam_service import get_effective_status

    for r in rows:
        r["effective_status"] = get_effective_status(r)

    if status:
        rows = [r for r in rows if r["effective_status"] == status]

    rows.sort(key=lambda r: r["id"], reverse=reverse)

    total = len(rows)
    page, per_page, offset = paginate_params(page, per_page)
    page_rows = rows[offset:offset + per_page]
    return {"exams": page_rows, **pagination_meta(total, page, per_page)}


def get_exams_by_subcategory_page(subcategory_id: int, status: str = "", search: str = "",
                                   page=1, per_page=12) -> Dict:
    """Bounded/searchable variant of get_exams_by_subcategory, used by the
    student dashboard so opening it never loads every exam in the
    subcategory at once. subcategory/search filter in SQL (cheap, indexed);
    status bucketing (Live/Upcoming/Completed) uses each exam's EFFECTIVE
    status — see _filter_paginate_by_effective_status()."""
    try:
        where = ["subcategory_id=%s"]
        params = [subcategory_id]
        if search:
            where.append("name ILIKE %s")
            params.append(f"%{search}%")
        where_sql = "WHERE " + " AND ".join(where)

        rows = fetch_all(f"SELECT {_ALL_COLS} FROM exams {where_sql} ORDER BY id", params)
        return _filter_paginate_by_effective_status(rows, status, page, per_page)
    except Exception as e:
        print(f"[db.exams] get_exams_by_subcategory_page error: {e}")
        page, per_page, _ = paginate_params(page, per_page)
        return {"exams": [], **pagination_meta(0, page, per_page)}


_ADMIN_LIST_COLS = ",".join(f"e.{c}" for c in _ALL_COLS.split(","))


def get_exams_page(search: str = "", category_id=None, subcategory_id=None, status: str = "",
                    page=1, per_page=20) -> Dict:
    """Admin exam list: search/category/subcategory filter in SQL (cheap,
    indexed); the status filter/badge uses each exam's EFFECTIVE status —
    see _filter_paginate_by_effective_status() — so a Scheduled Exam shows
    up under the right status filter without its `status` column ever
    being written automatically."""
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
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        rows = fetch_all(
            f"SELECT {_ADMIN_LIST_COLS}, c.name AS category_name, s.name AS subcategory_name "
            f"FROM exams e "
            f"LEFT JOIN categories c ON c.id = e.category_id "
            f"LEFT JOIN subcategories s ON s.id = e.subcategory_id "
            f"{where_sql} ORDER BY e.id",
            params,
        )
        result = _filter_paginate_by_effective_status(rows, status, page, per_page, reverse=True)
        # UI-facing S.No., not the DB id — see attach_row_numbers(). Admin-
        # list-only: get_exams_by_subcategory_page() (student dashboard)
        # never shows a row number, so it doesn't call this.
        attach_row_numbers(result["exams"], result["page"], result["per_page"])
        return result
    except Exception as e:
        print(f"[db.exams] get_exams_page error: {e}")
        page, per_page, _ = paginate_params(page, per_page)
        return {"exams": [], **pagination_meta(0, page, per_page)}
