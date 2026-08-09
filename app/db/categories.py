from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning


def get_all_categories() -> List[Dict]:
    try:
        return fetch_all("SELECT * FROM categories ORDER BY name")
    except Exception as e:
        print(f"[db.categories] get_all_categories: {e}")
        return []


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
