from typing import Optional, List, Dict
from app.db import supabase


def get_all_categories() -> List[Dict]:
    try:
        res = supabase.table("categories").select("*").order("name").execute()
        return res.data or []
    except Exception as e:
        print(f"[db.categories] get_all_categories: {e}")
        return []


def get_category_by_id(cat_id: int) -> Optional[Dict]:
    try:
        res = supabase.table("categories").select("*").eq("id", cat_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.categories] get_category_by_id: {e}")
        return None


def create_category(data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("categories").insert(data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.categories] create_category: {e}")
        return None


def update_category(cat_id: int, updates: Dict) -> bool:
    try:
        supabase.table("categories").update(updates).eq("id", cat_id).execute()
        return True
    except Exception as e:
        print(f"[db.categories] update_category: {e}")
        return False


def delete_category(cat_id: int) -> bool:
    try:
        supabase.table("categories").delete().eq("id", cat_id).execute()
        return True
    except Exception as e:
        print(f"[db.categories] delete_category: {e}")
        return False


def category_has_exams(cat_id: int) -> bool:
    try:
        res = (
            supabase.table("exams")
            .select("id", count="exact")
            .eq("category_id", cat_id)
            .execute()
        )
        return (res.count or 0) > 0
    except Exception as e:
        print(f"[db.categories] category_has_exams: {e}")
        return True