"""Business rules for Phase 1 of the Notes module."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.db import notes as notes_db
from app.services import notes_storage_service
from app.utils.notes_validation import (
    normalize_notebook_payload,
    validate_notebook_id,
    validate_notebook_import,
    IMPORT_TITLE_PREFIX,
    MAX_TITLE_LENGTH,
)


def _refresh_image_urls(objects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Canvas payloads persist a signed URL captured at upload time
    (fabric.Image serializes its `src`), but Supabase signed URLs expire
    (see notes_storage_service.signed_asset_url — 1 hour). The durable,
    canonical reference is asset_id / notes_assets.storage_path, so on
    every load we resolve a FRESH signed URL keyed off asset_id and swap
    it into the payload, instead of trusting whatever URL happens to be
    stored. Batches the Storage calls (one round trip for every distinct
    asset on the page, not one per image) so this doesn't turn into a
    sequential per-image bottleneck on image-heavy pages.

    Objects without a resolvable asset_id (very old data saved before
    asset_id tracking existed) are left untouched — we can't recover a
    canonical reference for those, so whatever URL they already have is
    the best we can do.
    """
    image_objects = []
    for obj in objects:
        if obj.get("object_type") != "image":
            continue
        fabric = (obj.get("payload") or {}).get("fabric")
        if not isinstance(fabric, dict):
            continue
        # Prefer the row's own asset_id column; fall back to the id fabric
        # serialized onto itself, for legacy rows saved before the top-level
        # asset_id column was populated on every write.
        asset_id = obj.get("asset_id") or fabric.get("assetId")
        if asset_id:
            image_objects.append((obj, fabric, str(asset_id)))

    if not image_objects:
        return objects

    asset_ids = list({asset_id for _, _, asset_id in image_objects})
    assets = notes_db.get_assets_by_ids(asset_ids)
    if not assets:
        return objects  # none of the referenced assets exist anymore — leave as-is, handled as missing downstream

    fresh_urls = notes_storage_service.signed_asset_urls_bulk(assets)
    for _, fabric, asset_id in image_objects:
        url = fresh_urls.get(asset_id)
        if url:
            fabric["src"] = url
        # else: asset genuinely missing or signing failed — leave the old
        # (likely stale) src so the frontend shows a normal broken-image
        # state rather than us silently rewriting it to something wrong.

    return objects


def resolve_asset_for_serving(user_id: int, storage_path: str) -> Optional[Dict[str, Any]]:
    """Authorize a local-storage asset request: owner access, or the asset's
    notebook is currently public. Mirrors the access rules already enforced
    for signed cloud URLs — local storage must not be weaker."""
    asset = notes_db.get_asset_by_storage_path(storage_path)
    if not asset:
        return None
    if asset.get("owner_id") == user_id:
        return asset
    if notes_db.get_public_notebook(asset["notebook_id"]):
        return asset
    return None


def resolve_asset_for_serving_by_id(user_id: int, asset_id: str) -> Optional[Dict[str, Any]]:
    """Same authorization as resolve_asset_for_serving, keyed by asset_id instead of
    storage_path — used by the PDF export same-origin asset proxy (see asset_file_api) so the
    frontend never needs to know the raw storage_path, and so this works identically regardless
    of which storage backend (local disk or an S3-compatible bucket) is configured."""
    asset = notes_db.get_asset(asset_id)
    if not asset:
        return None
    if asset.get("owner_id") == user_id:
        return asset
    if notes_db.get_public_notebook(asset["notebook_id"]):
        return asset
    return None


def get_my_notebooks(user_id: int) -> List[Dict[str, Any]]:
    return notes_db.list_notebooks_for_owner(int(user_id))


def get_my_trash(user_id: int) -> List[Dict[str, Any]]:
    return notes_db.list_trashed_notebooks_for_owner(int(user_id))


def create_notebook(user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    notebook = normalize_notebook_payload(payload)
    created = notes_db.create_notebook(int(user_id), notebook)
    notes_db.create_page(created["id"], "Untitled page", 0)
    return created


def update_notebook(user_id: int, notebook_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    updates = normalize_notebook_payload(payload, partial=True)
    if not updates:
        raise ValueError("Provide at least one notebook field to update.")
    if updates.get("visibility") == "public":
        from datetime import datetime, timezone
        from app.db.users import get_user_by_id
        updates["published_at"] = datetime.now(timezone.utc).isoformat()
        user = get_user_by_id(int(user_id)) or {}
        updates["author_display_name"] = user.get("full_name") or user.get("username") or "Student"
    return notes_db.update_owned_notebook(validate_notebook_id(notebook_id), int(user_id), updates)


def public_library(term: str, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    rows = notes_db.search_public_notebooks(str(term or "").strip()[:100])
    for row in rows:
        row.update(notes_db.engagement_counts(row["id"]))
    flags = notes_db.get_user_engagement_flags(int(user_id), [row["id"] for row in rows]) if user_id is not None else {}
    for row in rows:
        row.update(flags.get(row["id"], {"liked": False, "bookmarked": False}))
    return rows


def public_notebook(notebook_id: str) -> Optional[Dict[str, Any]]:
    return notes_db.get_public_notebook(validate_notebook_id(notebook_id))


def toggle_public_engagement(user_id: int, notebook_id: str, kind: str) -> Optional[Dict[str, Any]]:
    notebook = public_notebook(notebook_id)
    if not notebook: return None
    table = "notes_likes" if kind == "like" else "notes_bookmarks"
    active = notes_db.toggle_engagement(table, notebook["id"], int(user_id))
    return {"active": active, **notes_db.engagement_counts(notebook["id"])}


def get_public_pages(notebook_id: str) -> Optional[List[Dict[str, Any]]]:
    notebook = public_notebook(notebook_id)
    if not notebook:
        return None
    return notes_db.list_pages(notebook["id"])


def get_public_page_objects(notebook_id: str, page_id: str) -> Optional[List[Dict[str, Any]]]:
    notebook = public_notebook(notebook_id)
    if not notebook:
        return None
    if not any(page["id"] == page_id for page in notes_db.list_pages(notebook["id"])):
        return None
    return _refresh_image_urls(notes_db.list_page_objects(page_id))


def export_public_notebook(notebook_id: str) -> Optional[Dict[str, Any]]:
    notebook = public_notebook(notebook_id)
    return notes_db.export_notebook(notebook["id"]) if notebook else None


def import_notebook(user_id: int, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a brand-new, privately-owned Notebook from a validated export
    file. This is the ONLY way content from a Public Notebook (or any other
    notebook) becomes an editable Notebook in a user's collection — there is
    no direct copy path anymore (see Task 1).

    Runs as a best-effort saga: if anything fails partway through, everything
    created so far (notebook row, pages, objects, copied storage assets) is
    torn down and a single clean error is raised. Nothing partial is ever
    left behind for the user to find.
    """
    clean = validate_notebook_import(raw_payload)

    notebook_fields = {
        "title": (IMPORT_TITLE_PREFIX + clean["title"])[:MAX_TITLE_LENGTH],
        "description": clean["description"] or None,
        "visibility": "private",  # imported notebooks are always private, regardless of the source's visibility
    }
    created = notes_db.create_notebook(int(user_id), notebook_fields)
    notebook_id = created["id"]
    copied_assets_by_original_id: Dict[str, Dict[str, Any]] = {}

    try:
        for position, page in enumerate(clean["pages"]):
            new_page = notes_db.create_page(notebook_id, page["title"], position)
            records = []
            for obj in page["objects"]:
                payload = dict(obj["payload"])
                asset_id = None
                if obj["object_type"] == "image":
                    original_asset_id = obj["original_asset_id"]
                    new_asset = copied_assets_by_original_id.get(original_asset_id)
                    if not new_asset:
                        source_meta = clean["assets_by_original_id"][original_asset_id]
                        new_asset = notes_storage_service.copy_asset_for_import(source_meta, int(user_id), notebook_id)
                        copied_assets_by_original_id[original_asset_id] = new_asset
                    asset_id = new_asset["id"]
                    if isinstance(payload.get("fabric"), dict):
                        payload["fabric"]["src"] = notes_storage_service.signed_asset_url(new_asset)
                records.append({"page_id": new_page["id"], "object_type": obj["object_type"], "z_index": 0, "transform": obj["transform"], "payload": payload, "asset_id": asset_id})
            for index, record in enumerate(records):
                record["z_index"] = index
            # Chunked the same way as an editor save (MAX_OBJECTS_PER_PAGE per request/batch) —
            # a page can hold any number of objects; this only bounds a single INSERT's size.
            from app.utils.notes_validation import MAX_OBJECTS_PER_PAGE
            for start in range(0, len(records), MAX_OBJECTS_PER_PAGE):
                batch = records[start:start + MAX_OBJECTS_PER_PAGE]
                if batch:
                    notes_db.create_objects_bulk(batch)
        return notes_db.get_owned_notebook(notebook_id, int(user_id))
    except Exception:
        import traceback
        traceback.print_exc()
        notes_storage_service.delete_assets(list(copied_assets_by_original_id.values()))
        notes_db.delete_notebook_hard(notebook_id)
        raise ValueError("This Notebook file could not be imported. Please try again with a valid export.")


def permanently_delete_notebook(user_id: int, notebook_id: str) -> bool:
    notebook_id = validate_notebook_id(notebook_id)
    notebook = notes_db.get_owned_notebook(notebook_id, int(user_id), include_deleted=True)
    if not notebook or not notebook.get("deleted_at"):
        return False
    notes_storage_service.delete_assets(notes_db.list_notebook_assets(notebook_id))
    return notes_db.permanently_delete_trashed_notebook(notebook_id, int(user_id))


def cleanup_expired_trash() -> int:
    from datetime import datetime, timedelta, timezone
    import app.config as config
    if not config.NOTES_TRASH_CLEANUP_ENABLED:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.NOTES_TRASH_RETENTION_DAYS)).isoformat()
    removed = 0
    for notebook in notes_db.list_expired_trashed_notebooks(cutoff):
        notes_storage_service.delete_assets(notes_db.list_notebook_assets(notebook["id"]))
        if notes_db.permanently_delete_expired_notebook(notebook["id"]):
            removed += 1
    return removed


def export_owned_notebook(user_id: int, notebook_id: str) -> Optional[Dict[str, Any]]:
    notebook = get_editor_notebook(user_id, notebook_id)
    return notes_db.export_notebook(notebook["id"]) if notebook else None


def delete_notebook(user_id: int, notebook_id: str) -> bool:
    return notes_db.soft_delete_owned_notebook(validate_notebook_id(notebook_id), int(user_id))


def restore_notebook(user_id: int, notebook_id: str) -> Optional[Dict[str, Any]]:
    return notes_db.restore_owned_notebook(validate_notebook_id(notebook_id), int(user_id))


def get_editor_notebook(user_id: int, notebook_id: str) -> Optional[Dict[str, Any]]:
    return notes_db.get_owned_notebook(validate_notebook_id(notebook_id), int(user_id))


def get_pages(user_id: int, notebook_id: str) -> List[Dict[str, Any]]:
    notebook = get_editor_notebook(user_id, notebook_id)
    if not notebook:
        return []
    return notes_db.list_pages(notebook["id"])


def _next_untitled_title(notebook_id: str) -> str:
    existing = {page["title"] for page in notes_db.list_pages(notebook_id)}
    if "Untitled" not in existing:
        return "Untitled"
    suffix = 2
    while f"Untitled {suffix}" in existing:
        suffix += 1
    return f"Untitled {suffix}"


def create_page(user_id: int, notebook_id: str, title: Optional[str] = None) -> Optional[Dict[str, Any]]:
    notebook = get_editor_notebook(user_id, notebook_id)
    if not notebook:
        return None
    title = str(title or "").strip()
    if not title:
        # Instant-create flow (the "+" button): no title required up front,
        # a student can rename the page later — see update_page().
        title = _next_untitled_title(notebook["id"])
    elif len(title) > 160:
        raise ValueError("Page titles must contain 1–160 characters.")
    elif notes_db.page_name_exists(notebook["id"], title):
        raise ValueError("A page with this name already exists in this notebook.")
    pages = notes_db.list_pages(notebook["id"])
    next_position = max((int(page.get("position") or 0) for page in pages), default=-1) + 1
    return notes_db.create_page(notebook["id"], title, next_position)


def update_page(user_id: int, notebook_id: str, page_id: str, title: str) -> Optional[Dict[str, Any]]:
    if not get_editor_notebook(user_id, notebook_id):
        return None
    title = str(title or "").strip()
    if not title or len(title) > 160:
        raise ValueError("Page titles must contain 1–160 characters.")
    if notes_db.page_name_exists(validate_notebook_id(notebook_id), title, page_id):
        raise ValueError("A page with this name already exists in this notebook.")
    return notes_db.update_page(page_id, validate_notebook_id(notebook_id), {"title": title})


def delete_page(user_id: int, notebook_id: str, page_id: str) -> bool:
    if not get_editor_notebook(user_id, notebook_id):
        return False
    return notes_db.delete_page(page_id, validate_notebook_id(notebook_id))


def get_page_objects(user_id: int, notebook_id: str, page_id: str) -> Optional[List[Dict[str, Any]]]:
    if not get_editor_notebook(user_id, notebook_id):
        return None
    if not any(page["id"] == page_id for page in notes_db.list_pages(validate_notebook_id(notebook_id))):
        return None
    return _refresh_image_urls(notes_db.list_page_objects(page_id))


def save_page_objects(user_id: int, notebook_id: str, page_id: str, objects: List[Dict[str, Any]], deleted_ids: List[str], start_index: int = 0) -> Optional[List[Dict[str, Any]]]:
    """A page has no cap on total objects — only a single save REQUEST does (see
    MAX_OBJECTS_PER_PAGE / notes_validation.py). A page bigger than that chunk size is saved
    as several sequential calls here (editor.js splits canvas.getObjects() client-side), each
    one this many objects and tagged with its own `start_index` so z_index — and therefore
    draw/stacking order on reload — stays correct and non-colliding across chunks, instead of
    every chunk restarting from 0."""
    current = get_page_objects(user_id, notebook_id, page_id)
    if current is None:
        return None
    if not isinstance(objects, list) or not isinstance(deleted_ids, list):
        raise ValueError("Invalid canvas save data.")
    from app.utils.notes_validation import ALLOWED_OBJECT_TYPES, MAX_OBJECTS_PER_PAGE
    if len(objects) > MAX_OBJECTS_PER_PAGE:
        raise ValueError("Too many objects in a single save request.")
    saved = []
    for index, item in enumerate(objects):
        if not isinstance(item, dict) or item.get("object_type") not in ALLOWED_OBJECT_TYPES:
            raise ValueError("Invalid canvas object.")
        record = {"page_id": page_id, "object_type": item["object_type"], "z_index": start_index + index, "transform": item.get("transform") or {}, "payload": item.get("payload") or {}, "asset_id": item.get("asset_id")}
        if item.get("id"):
            record["id"] = str(item["id"])
        saved.append(record)
    deleted_id_strs = {str(item) for item in deleted_ids}
    result = notes_db.replace_page_objects(page_id, saved, list(deleted_id_strs))
    # Deleting an image object leaves its asset (and file) orphaned unless
    # cleaned up here — works the same for local and cloud storage.
    removed_asset_ids = {str(o["asset_id"]) for o in current if str(o["id"]) in deleted_id_strs and o.get("object_type") == "image" and o.get("asset_id")}
    orphaned = [aid for aid in removed_asset_ids if not notes_db.asset_still_referenced(aid)]
    if orphaned:
        notes_storage_service.delete_assets(notes_db.get_assets_by_ids(orphaned))
        notes_db.delete_assets_by_ids(orphaned)
    return result