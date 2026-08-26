from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning
from app.utils.pagination import paginate_params, pagination_meta


def get_all_categories() -> List[Dict]:
    try:
        return fetch_all("SELECT * FROM categories ORDER BY name")
    except Exception as e:
        print(f"[db.categories] get_all_categories: {e}")
        return []


def get_categories_sample(limit: int = 2) -> List[Dict]:
    """Cheap 'is there just one (or none)?' probe — LIMIT'd, never a full
    table scan. Two rows is enough to know "more than one" without a
    separate COUNT query."""
    try:
        return fetch_all("SELECT * FROM categories ORDER BY name LIMIT %s", (limit,))
    except Exception as e:
        print(f"[db.categories] get_categories_sample: {e}")
        return []


def get_categories_page(search: str = "", page=1, per_page=20) -> Dict:
    page, per_page, offset = paginate_params(page, per_page)
    try:
        where_sql, params = "", []
        if search:
            where_sql = "WHERE name ILIKE %s"
            params.append(f"%{search}%")
        total = fetch_one(f"SELECT COUNT(*) AS count FROM categories {where_sql}", params)["count"]
        rows = fetch_all(
            f"SELECT * FROM categories {where_sql} ORDER BY name LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return {"categories": rows, **pagination_meta(total, page, per_page)}
    except Exception as e:
        print(f"[db.categories] get_categories_page: {e}")
        return {"categories": [], **pagination_meta(0, page, per_page)}


def get_category_by_id(cat_id: int) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM categories WHERE id=%s", (cat_id,))
    except Exception as e:
        print(f"[db.categories] get_category_by_id: {e}")
        return None


def create_category(data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("categories", data)
    except Exception as e:
        print(f"[db.categories] create_category: {e}")
        return None


def update_category(cat_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE categories SET {sc} WHERE id=%s", params + [cat_id])
        return True
    except Exception as e:
        print(f"[db.categories] update_category: {e}")
        return False


def delete_category(cat_id: int) -> bool:
    try:
        execute("DELETE FROM categories WHERE id=%s", (cat_id,))
        return True
    except Exception as e:
        print(f"[db.categories] delete_category: {e}")
        return False


def category_has_exams(cat_id: int) -> bool:
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM exams WHERE category_id=%s", (cat_id,))
        return (row["count"] if row else 0) > 0
    except Exception as e:
        print(f"[db.categories] category_has_exams: {e}")
        return True


def category_has_subcategories(cat_id: int) -> bool:
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM subcategories WHERE category_id=%s", (cat_id,))
        return (row["count"] if row else 0) > 0
    except Exception as e:
        print(f"[db.categories] category_has_subcategories: {e}")
        return True
