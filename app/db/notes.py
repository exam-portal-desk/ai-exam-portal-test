"""PostgreSQL data access for private student notebooks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db import fetch_one, fetch_all, execute, execute_returning, set_clause, insert_returning, insert_many


def list_notebooks_for_owner(owner_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,title,description,visibility,subject,course,tags,created_at,updated_at,published_at "
        "FROM notes_notebooks WHERE owner_id=%s AND deleted_at IS NULL ORDER BY updated_at DESC",
        (owner_id,),
    )


def list_trashed_notebooks_for_owner(owner_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,title,deleted_at,updated_at FROM notes_notebooks "
        "WHERE owner_id=%s AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
        (owner_id,),
    )


def get_owned_notebook(notebook_id: str, owner_id: int, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM notes_notebooks WHERE id=%s AND owner_id=%s"
    if not include_deleted:
        query += " AND deleted_at IS NULL"
    return fetch_one(query + " LIMIT 1", (notebook_id, owner_id))


def create_notebook(owner_id: int, notebook: Dict[str, Any]) -> Dict[str, Any]:
    return insert_returning("notes_notebooks", {"owner_id": owner_id, **notebook})


def update_owned_notebook(notebook_id: str, owner_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sc, params = set_clause(updates)
    rows = execute_returning(
        f"UPDATE notes_notebooks SET {sc} WHERE id=%s AND owner_id=%s AND deleted_at IS NULL RETURNING *",
        params + [notebook_id, owner_id],
    )
    return rows[0] if rows else None


def soft_delete_owned_notebook(notebook_id: str, owner_id: int) -> bool:
    rows = execute_returning(
        "UPDATE notes_notebooks SET deleted_at=%s WHERE id=%s AND owner_id=%s AND deleted_at IS NULL RETURNING id",
        (datetime.now(timezone.utc).isoformat(), notebook_id, owner_id),
    )
    return bool(rows)


def restore_owned_notebook(notebook_id: str, owner_id: int) -> Optional[Dict[str, Any]]:
    rows = execute_returning(
        "UPDATE notes_notebooks SET deleted_at=NULL WHERE id=%s AND owner_id=%s AND deleted_at IS NOT NULL RETURNING *",
        (notebook_id, owner_id),
    )
    return rows[0] if rows else None


def list_pages(notebook_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,title,position,created_at,updated_at FROM notes_pages WHERE notebook_id=%s ORDER BY position",
        (notebook_id,),
    )


def create_page(notebook_id: str, title: str, position: int) -> Dict[str, Any]:
    return insert_returning("notes_pages", {"notebook_id": notebook_id, "title": title, "position": position})


def update_page(page_id: str, notebook_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sc, params = set_clause(updates)
    rows = execute_returning(
        f"UPDATE notes_pages SET {sc} WHERE id=%s AND notebook_id=%s RETURNING *",
        params + [page_id, notebook_id],
    )
    return rows[0] if rows else None


def delete_page(page_id: str, notebook_id: str) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_pages WHERE id=%s AND notebook_id=%s RETURNING id", (page_id, notebook_id)
    )
    return bool(rows)


def page_name_exists(notebook_id: str, title: str, exclude_page_id: str | None = None) -> bool:
    rows = fetch_all(
        "SELECT id FROM notes_pages WHERE notebook_id=%s AND title ILIKE %s", (notebook_id, title)
    )
    return any(row["id"] != exclude_page_id for row in rows)


def replace_page_objects(page_id: str, objects: List[Dict[str, Any]], deleted_ids: List[str]) -> List[Dict[str, Any]]:
    if deleted_ids:
        execute("DELETE FROM notes_objects WHERE page_id=%s AND id = ANY(%s::uuid[])", (page_id, deleted_ids))
    if not objects:
        return []
    cols = list(objects[0].keys())
    row_sql = "(" + ", ".join(["%s"] * len(cols)) + ")"
    values_sql = ", ".join([row_sql] * len(objects))
    update_cols = [c for c in cols if c != "id"]
    set_sql = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
    query = (
        f"INSERT INTO notes_objects ({', '.join(cols)}) VALUES {values_sql} "
        f"ON CONFLICT (id) DO UPDATE SET {set_sql} RETURNING *"
    )
    params = [row[c] for row in objects for c in cols]
    return execute_returning(query, params)


def create_objects_bulk(records: List[Dict[str, Any]]) -> int:
    return insert_many("notes_objects", records)


def list_page_objects(page_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,object_type,z_index,transform,payload,asset_id,updated_at "
        "FROM notes_objects WHERE page_id=%s ORDER BY z_index",
        (page_id,),
    )


def create_asset(asset: Dict[str, Any]) -> Dict[str, Any]:
    return insert_returning("notes_assets", asset)


def get_owned_asset(asset_id: str, owner_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM notes_assets WHERE id=%s AND owner_id=%s LIMIT 1", (asset_id, owner_id))


def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM notes_assets WHERE id=%s LIMIT 1", (asset_id,))


def get_asset_by_storage_path(storage_path: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM notes_assets WHERE storage_path=%s LIMIT 1", (storage_path,))


def get_assets_by_ids(asset_ids: List[str]) -> List[Dict[str, Any]]:
    """Batch asset lookup so a page with many images costs one DB round
    trip instead of one per image. Used to resolve fresh signed URLs on
    page load — see notes_service._refresh_image_urls()."""
    if not asset_ids:
        return []
    return fetch_all(
        "SELECT id,storage_path,content_type,original_filename FROM notes_assets WHERE id = ANY(%s::uuid[])",
        (list(set(asset_ids)),),
    )


def list_notebook_assets(notebook_id: str) -> List[Dict[str, Any]]:
    return fetch_all("SELECT * FROM notes_assets WHERE notebook_id=%s", (notebook_id,))


def asset_still_referenced(asset_id: str) -> bool:
    return bool(fetch_one("SELECT 1 FROM notes_objects WHERE asset_id=%s LIMIT 1", (asset_id,)))


def delete_assets_by_ids(asset_ids: List[str]) -> None:
    if asset_ids:
        execute("DELETE FROM notes_assets WHERE id = ANY(%s::uuid[])", (asset_ids,))


def delete_notebook_hard(notebook_id: str) -> None:
    """Explicit cascading delete used ONLY to roll back a failed import that
    already created a notebook/pages/objects/assets. Deletes children before
    parents regardless of what the FK ON DELETE behavior actually turns out
    to be — if a cascade already handles a table, the matching delete here
    is simply a no-op.
    This is deliberately separate from the trash/soft-delete flow: it is not
    reachable from any route, only from notes_service.import_notebook()'s
    rollback path.
    """
    page_ids = [row["id"] for row in fetch_all("SELECT id FROM notes_pages WHERE notebook_id=%s", (notebook_id,))]
    if page_ids:
        execute("DELETE FROM notes_objects WHERE page_id = ANY(%s::uuid[])", (page_ids,))
    execute("DELETE FROM notes_assets WHERE notebook_id=%s", (notebook_id,))
    if page_ids:
        execute("DELETE FROM notes_pages WHERE notebook_id=%s", (notebook_id,))
    execute("DELETE FROM notes_notebooks WHERE id=%s", (notebook_id,))


def permanently_delete_trashed_notebook(notebook_id: str, owner_id: int) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_notebooks WHERE id=%s AND owner_id=%s AND deleted_at IS NOT NULL RETURNING id",
        (notebook_id, owner_id),
    )
    return bool(rows)


def list_expired_trashed_notebooks(cutoff: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id,owner_id,deleted_at FROM notes_notebooks WHERE deleted_at IS NOT NULL AND deleted_at < %s",
        (cutoff,),
    )


def permanently_delete_expired_notebook(notebook_id: str) -> bool:
    rows = execute_returning(
        "DELETE FROM notes_notebooks WHERE id=%s AND deleted_at IS NOT NULL RETURNING id", (notebook_id,)
    )
    return bool(rows)


def export_notebook(notebook_id: str) -> Dict[str, Any]:
    notebook = fetch_one("SELECT * FROM notes_notebooks WHERE id=%s LIMIT 1", (notebook_id,))
    pages = list_pages(notebook_id)
    for page in pages:
        objects = list_page_objects(page["id"])
        for obj in objects:
            # Legacy rows saved before asset_id was backfilled onto every object
            # only carry the reference inside payload.fabric.assetId. Recover it
            # here so an export always stays importable by its own importer,
            # mirroring the same fallback notes_service._refresh_image_urls()
            # already applies for the live editor's page-load path.
            if obj["object_type"] == "image" and not obj.get("asset_id"):
                fabric = (obj.get("payload") or {}).get("fabric")
                if isinstance(fabric, dict) and fabric.get("assetId"):
                    obj["asset_id"] = fabric["assetId"]
        page["objects"] = objects
    return {
        "format": "smartai-notes-export-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "notebook": notebook,
        "pages": pages,
        "assets": list_notebook_assets(notebook_id),
    }


def get_public_notebook(notebook_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM notes_notebooks WHERE id=%s AND visibility=%s "
        "AND published_at IS NOT NULL AND deleted_at IS NULL LIMIT 1",
        (notebook_id, "public"),
    )


def search_public_notebooks(term: str, limit: int = 30) -> List[Dict[str, Any]]:
    query = (
        "SELECT id,title,description,subject,department,semester,course,topic,tags,"
        "author_display_name,author_deleted,published_at,updated_at FROM notes_notebooks "
        "WHERE visibility=%s AND published_at IS NOT NULL AND deleted_at IS NULL"
    )
    params: List[Any] = ["public"]
    if term:
        pattern = f"%{term}%"
        query += (
            " AND (title ILIKE %s OR description ILIKE %s OR subject ILIKE %s "
            "OR course ILIKE %s OR topic ILIKE %s OR author_display_name ILIKE %s)"
        )
        params += [pattern] * 6
    query += " ORDER BY published_at DESC LIMIT %s"
    params.append(limit)
    return fetch_all(query, params)


def toggle_engagement(table: str, notebook_id: str, user_id: int) -> bool:
    existing = fetch_one(
        f"SELECT notebook_id FROM {table} WHERE notebook_id=%s AND user_id=%s LIMIT 1", (notebook_id, user_id)
    )
    if existing:
        execute(f"DELETE FROM {table} WHERE notebook_id=%s AND user_id=%s", (notebook_id, user_id))
        return False
    insert_returning(table, {"notebook_id": notebook_id, "user_id": user_id})
    return True


def engagement_counts(notebook_id: str) -> Dict[str, int]:
    likes = fetch_one("SELECT COUNT(*) AS count FROM notes_likes WHERE notebook_id=%s", (notebook_id,))
    bookmarks = fetch_one("SELECT COUNT(*) AS count FROM notes_bookmarks WHERE notebook_id=%s", (notebook_id,))
    return {"likes": likes["count"] if likes else 0, "bookmarks": bookmarks["count"] if bookmarks else 0}


def get_user_engagement_flags(user_id: int, notebook_ids: List[str]) -> Dict[str, Dict[str, bool]]:
    """Per-user liked/bookmarked state for a set of public notebooks, so the
    library page can render correct initial icon state instead of just counts."""
    if not notebook_ids:
        return {}
    liked = {
        row["notebook_id"] for row in fetch_all(
            "SELECT notebook_id FROM notes_likes WHERE user_id=%s AND notebook_id = ANY(%s::uuid[])",
            (user_id, notebook_ids),
        )
    }
    bookmarked = {
        row["notebook_id"] for row in fetch_all(
            "SELECT notebook_id FROM notes_bookmarks WHERE user_id=%s AND notebook_id = ANY(%s::uuid[])",
            (user_id, notebook_ids),
        )
    }
    return {nid: {"liked": nid in liked, "bookmarked": nid in bookmarked} for nid in notebook_ids}
