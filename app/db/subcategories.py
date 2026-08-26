"""
app/db/subcategories.py
All PostgreSQL queries related to the `subcategories` table — the second
level of the Category -> Subcategory -> Exam hierarchy.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning
from app.utils.pagination import paginate_params, pagination_meta


def get_subcategories_by_category(category_id: int) -> List[Dict]:
    try:
        return fetch_all("SELECT * FROM subcategories WHERE category_id=%s ORDER BY name", (category_id,))
    except Exception as e:
        print(f"[db.subcategories] get_subcategories_by_category: {e}")
        return []


def get_subcategories_sample(category_id: int, limit: int = 2) -> List[Dict]:
    """Cheap 'is there just one (or none)?' probe for one category —
    mirrors get_categories_sample()."""
    try:
        return fetch_all("SELECT * FROM subcategories WHERE category_id=%s ORDER BY name LIMIT %s", (category_id, limit))
    except Exception as e:
        print(f"[db.subcategories] get_subcategories_sample: {e}")
        return []


def get_subcategories_page(category_id: int, search: str = "", page=1, per_page=30) -> Dict:
    """Bounded/searchable variant of get_subcategories_by_category — same
    data, but never returns more than one page even if a category
    eventually accumulates a very large number of subcategories."""
    page, per_page, offset = paginate_params(page, per_page)
    try:
        where_sql, params = "WHERE category_id=%s", [category_id]
        if search:
            where_sql += " AND name ILIKE %s"
            params.append(f"%{search}%")
        total = fetch_one(f"SELECT COUNT(*) AS count FROM subcategories {where_sql}", params)["count"]
        rows = fetch_all(
            f"SELECT * FROM subcategories {where_sql} ORDER BY name LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return {"subcategories": rows, **pagination_meta(total, page, per_page)}
    except Exception as e:
        print(f"[db.subcategories] get_subcategories_page: {e}")
        return {"subcategories": [], **pagination_meta(0, page, per_page)}


def get_subcategory_by_id(subcat_id: int) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM subcategories WHERE id=%s", (subcat_id,))
    except Exception as e:
        print(f"[db.subcategories] get_subcategory_by_id: {e}")
        return None


def create_subcategory(data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("subcategories", data)
    except Exception as e:
        print(f"[db.subcategories] create_subcategory: {e}")
        return None


def update_subcategory(subcat_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE subcategories SET {sc} WHERE id=%s", params + [subcat_id])
        return True
    except Exception as e:
        print(f"[db.subcategories] update_subcategory: {e}")
        return False


def delete_subcategory(subcat_id: int) -> bool:
    try:
        execute("DELETE FROM subcategories WHERE id=%s", (subcat_id,))
        return True
    except Exception as e:
        print(f"[db.subcategories] delete_subcategory: {e}")
        return False


def subcategory_has_exams(subcat_id: int) -> bool:
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM exams WHERE subcategory_id=%s", (subcat_id,))
        return (row["count"] if row else 0) > 0
    except Exception as e:
        print(f"[db.subcategories] subcategory_has_exams: {e}")
        return True
