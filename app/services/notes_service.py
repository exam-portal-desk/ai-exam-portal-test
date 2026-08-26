"""Business rules for Phase 1 of the Notes module."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.db import notes as notes_db
from app.services import notes_storage_service
from app.utils.notes_validation import (
    normalize_notebook_payload,
    validate_notebook_id,
    validate_notebook_import,
    validate_share_permission,
    IMPORT_TITLE_PREFIX,
    MAX_TITLE_LENGTH,
    NotesPermissionError,
)


def assert_can_edit(notebook: Dict[str, Any]) -> None:
    """Server-side write gate — a Viewer's requests must be rejected here
    regardless of what the frontend shows or hides. Called by every
    notebook-content write path (pages, objects, asset upload)."""
    if notebook.get("access") == "viewer":
        raise NotesPermissionError("You have view-only access to this notebook.")


def _refresh_image_urls(objects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Canvas payloads persist whatever `src` was captured at upload time
    (fabric.Image serializes its `src`), which for very old rows may still
    be a raw signed storage URL. The durable, canonical reference is
    asset_id / notes_assets.storage_path, so on every load we normalize
    `src` to the canonical same-origin asset proxy URL
    (notes_storage_service.asset_proxy_url — ownership-checked, never
    expires) keyed off asset_id, instead of trusting whatever URL happens
    to be stored. Pure string formatting, no storage round-trip.

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
    existing_ids = {str(a["id"]) for a in notes_db.get_assets_by_ids(asset_ids)}
    for _, fabric, asset_id in image_objects:
        if asset_id in existing_ids:
            fabric["src"] = notes_storage_service.asset_proxy_url(asset_id)
        # else: asset genuinely missing — leave the old (likely stale) src
        # so the frontend shows a normal broken-image state rather than us
        # silently rewriting it to something wrong.

    return objects


def _asset_accessible(asset: Dict[str, Any], user_id: int) -> bool:
    """Uploader access, notebook owner/share access (whoever uploaded an
    asset, anyone who can access the notebook should be able to see it), or
    the notebook is currently public."""
    if asset.get("owner_id") == user_id:
        return True
    if notes_db.get_accessible_notebook(asset["notebook_id"], user_id) is not None:
        return True
    if notes_db.get_public_notebook(asset["notebook_id"]):
        return True
    return False


def resolve_asset_for_serving(user_id: int, storage_path: str) -> Optional[Dict[str, Any]]:
    """Authorize a local-storage asset request: uploader, notebook owner/share
    access, or the asset's notebook is currently public. Mirrors the access
    rules already enforced for signed cloud URLs — local storage must not be
    weaker."""
    asset = notes_db.get_asset_by_storage_path(storage_path)
    if not asset:
        return None
    return asset if _asset_accessible(asset, user_id) else None


def resolve_asset_for_serving_by_id(user_id: int, asset_id: str) -> Optional[Dict[str, Any]]:
    """Same authorization as resolve_asset_for_serving, keyed by asset_id instead of
    storage_path — used by the PDF export same-origin asset proxy (see asset_file_api) so the
    frontend never needs to know the raw storage_path, and so this works identically regardless
    of which storage backend (local disk or an S3-compatible bucket) is configured."""
    asset = notes_db.get_asset(asset_id)
    if not asset:
        return None
    return asset if _asset_accessible(asset, user_id) else None


NOTEBOOK_PAGE_SIZE = 24


def get_my_notebooks(user_id: int, limit: int = NOTEBOOK_PAGE_SIZE, offset: int = 0) -> tuple[List[Dict[str, Any]], bool]:
    rows = notes_db.list_notebooks_for_owner(int(user_id), limit=limit, offset=offset)
    has_more = len(rows) > limit
    return rows[:limit], has_more


SECTION_PAGE_SIZE = 20

# Ordered for tab display.
NOTEBOOK_SECTIONS = ("created", "imported", "public", "shared_with_me", "shared_by_me")

SECTION_LABELS = {
    "created": "My Notes",
    "imported": "Imported",
    "public": "Public",
    "shared_with_me": "Shared with Me",
    "shared_by_me": "Shared by Me",
}


def get_notebook_section(user_id: int, section: str, offset: int = 0, limit: int = SECTION_PAGE_SIZE, search: str = None) -> tuple[List[Dict[str, Any]], bool]:
    """One independently-paginated My Notebooks section — only the requested
    section is ever queried, never all of them together."""
    if section not in NOTEBOOK_SECTIONS:
        raise ValueError(f"Unknown notebook section: {section}")
    if section == "shared_with_me":
        rows = notes_db.list_notebooks_shared_with_user(int(user_id), limit=limit, offset=offset, search=search)
    elif section == "shared_by_me":
        rows = notes_db.list_notebooks_shared_by_owner(int(user_id), limit=limit, offset=offset, search=search)
    else:
        rows = notes_db.list_notebooks_for_owner(int(user_id), limit=limit, offset=offset, section=section, search=search)
    has_more = len(rows) > limit
    return rows[:limit], has_more


def share_notebook(owner_id: int, notebook_id: str, shares: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bulk-share a notebook the caller owns with one or more users in a
    single operation. `shares` is [{"user_id": int, "permission": str}, ...]."""
    notebook_id = validate_notebook_id(notebook_id)
    owner_id = int(owner_id)
    if not notes_db.get_owned_notebook(notebook_id, owner_id):
        return None
    if not isinstance(shares, list) or not shares:
        raise ValueError("Select at least one user to share with.")
    if len(shares) > 50:
        raise ValueError("Share with at most 50 users at a time.")
    results = []
    for item in shares:
        if not isinstance(item, dict) or not item.get("user_id"):
            raise ValueError("Invalid share entry.")
        try:
            target_user_id = int(item["user_id"])
        except (TypeError, ValueError):
            raise ValueError("Invalid user to share with.")
        if target_user_id == owner_id:
            raise ValueError("You cannot share a notebook with yourself.")
        permission = validate_share_permission(item.get("permission"))
        results.append(notes_db.upsert_share(notebook_id, target_user_id, permission, owner_id))
    return results


def list_notebook_shares(owner_id: int, notebook_id: str) -> Optional[List[Dict[str, Any]]]:
    notebook_id = validate_notebook_id(notebook_id)
    if not notes_db.get_owned_notebook(notebook_id, int(owner_id)):
        return None
    return notes_db.list_shares_for_notebook(notebook_id)


def update_notebook_share(owner_id: int, notebook_id: str, target_user_id: int, permission: str) -> Optional[Dict[str, Any]]:
    notebook_id = validate_notebook_id(notebook_id)
    if not notes_db.get_owned_notebook(notebook_id, int(owner_id)):
        return None
    permission = validate_share_permission(permission)
    return notes_db.update_share_permission(notebook_id, int(target_user_id), permission)


def remove_notebook_share(acting_user_id: int, notebook_id: str, target_user_id: int) -> bool:
    """The owner can remove anyone's access; a recipient can remove their
    own ("leave a shared notebook") — nobody else."""
    notebook_id = validate_notebook_id(notebook_id)
    acting_user_id = int(acting_user_id)
    target_user_id = int(target_user_id)
    notebook = notes_db.get_owned_notebook(notebook_id, acting_user_id)
    is_owner = notebook is not None
    if not is_owner and acting_user_id != target_user_id:
        raise NotesPermissionError("You cannot change sharing on a notebook you don't own.")
    if not is_owner and not notes_db.get_share(notebook_id, acting_user_id):
        return False
    return notes_db.delete_share(notebook_id, target_user_id)


def search_shareable_users(owner_id: int, notebook_id: str, term: str) -> Optional[List[Dict[str, Any]]]:
    notebook_id = validate_notebook_id(notebook_id)
    owner_id = int(owner_id)
    if not notes_db.get_owned_notebook(notebook_id, owner_id):
        return None
    return notes_db.search_shareable_users(str(term or "").strip()[:100], notebook_id, owner_id)


def get_my_trash(user_id: int) -> List[Dict[str, Any]]:
    return notes_db.list_trashed_notebooks_for_owner(int(user_id))


def create_notebook(user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    notebook = normalize_notebook_payload(payload)
    created = notes_db.create_notebook(int(user_id), notebook)
    notes_db.create_page(created["id"], "Untitled page", 0)
    return created


def update_notebook(user_id: int, notebook_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Owner may change any field. An Editor may only rename the title or
    change the description — content-adjacent edits, not access control
    (visibility/delete/sharing stay strictly owner-only, per the sharing
    permission model). A Viewer, or anyone with no access, is rejected."""
    notebook_id = validate_notebook_id(notebook_id)
    updates = normalize_notebook_payload(payload, partial=True)
    if not updates:
        raise ValueError("Provide at least one notebook field to update.")

    notebook = notes_db.get_accessible_notebook(notebook_id, int(user_id))
    if not notebook:
        return None
    access = notebook["access"]

    if access == "owner":
        if updates.get("visibility") == "public":
            from datetime import datetime, timezone
            from app.db.users import get_user_by_id
            updates["published_at"] = datetime.now(timezone.utc).isoformat()
            user = get_user_by_id(int(user_id)) or {}
            updates["author_display_name"] = user.get("full_name") or user.get("username") or "Student"
        return notes_db.update_owned_notebook(notebook_id, int(user_id), updates)

    if access == "editor":
        disallowed = set(updates) - {"title", "description"}
        if disallowed:
            raise NotesPermissionError("As an Editor you can only rename the title or change the description.")
        return notes_db.update_shared_notebook_fields(notebook_id, int(user_id), updates)

    raise NotesPermissionError("You have view-only access to this notebook.")


def public_library(term: str, user_id: Optional[int] = None, limit: int = NOTEBOOK_PAGE_SIZE, offset: int = 0) -> tuple[List[Dict[str, Any]], bool]:
    fetched = notes_db.search_public_notebooks(str(term or "").strip()[:100], limit=limit, offset=offset)
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    for row in rows:
        row.update(notes_db.engagement_counts(row["id"]))
    flags = notes_db.get_user_engagement_flags(int(user_id), [row["id"] for row in rows]) if user_id is not None else {}
    for row in rows:
        row.update(flags.get(row["id"], {"liked": False, "bookmarked": False}))
    return rows, has_more


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


# Chunk size for a single import INSERT round trip. Deliberately smaller than
# MAX_OBJECTS_PER_PAGE (500, the editor-autosave request cap that create_objects_bulk's
# multi-VALUES INSERT is also bounded by) purely so a big page produces several real progress
# checkpoints instead of one — it does not change what MAX_OBJECTS_PER_PAGE means anywhere else.
_IMPORT_INSERT_CHUNK = 150


def import_notebook(user_id: int, raw_payload: Dict[str, Any], progress=None) -> Dict[str, Any]:
    """Create a brand-new, privately-owned Notebook from a validated export
    file. This is the ONLY way content from a Public Notebook (or any other
    notebook) becomes an editable Notebook in a user's collection — there is
    no direct copy path anymore (see Task 1).

    Runs as a best-effort saga: if anything fails partway through, everything
    created so far (notebook row, pages, objects, copied storage assets) is
    torn down and a single clean error is raised. Nothing partial is ever
    left behind for the user to find.

    `progress`, if given, is called as progress(phase: str, message: str, percent: int) as real
    work completes — never on a timer. percent is capped below 100 for every call except the
    very last one, which only fires after the finished notebook has been re-fetched and confirmed
    readable, so 100 always means "actually done," not "division worked out."
    """
    def _report(phase: str, message: str, percent: int) -> None:
        if progress:
            progress(phase, message, percent)

    _report("validating", "Validating notebook...", 0)
    clean = validate_notebook_import(raw_payload)

    pages = clean["pages"]
    total_objects = sum(len(page["objects"]) for page in pages)
    needed_asset_ids = {
        obj["original_asset_id"] for page in pages for obj in page["objects"]
        if obj["object_type"] == "image"
    }
    # Measurable units of real work: notebook row + one per page + one per object actually
    # persisted + one per asset actually copied + final verification. Each unit is ticked only
    # when that exact piece of work has genuinely completed — see the ticks below.
    total_units = 1 + len(pages) + total_objects + len(needed_asset_ids) + 1
    completed_units = 0

    def _tick(phase: str, message: str) -> None:
        nonlocal completed_units
        completed_units += 1
        _report(phase, message, min(99, (completed_units * 100) // total_units))

    _report("creating_notebook", "Preparing notebook...", 0)
    notebook_fields = {
        "title": (IMPORT_TITLE_PREFIX + clean["title"])[:MAX_TITLE_LENGTH],
        "description": clean["description"] or None,
        "visibility": "private",  # imported notebooks are always private, regardless of the source's visibility
        "is_imported": True,
    }
    created = notes_db.create_notebook(int(user_id), notebook_fields)
    notebook_id = created["id"]
    _tick("creating_notebook", "Notebook created")
    copied_assets_by_original_id: Dict[str, Dict[str, Any]] = {}

    try:
        # Assets are copied ONCE, up front, for the whole notebook — copy_assets_for_import_bulk
        # only ever needs notebook_id/user_id (never page_id), so nothing requires doing this
        # interleaved with the per-page loop below. Copying up front also lets it run
        # concurrently (see that function) instead of serially per object.
        if needed_asset_ids:
            _report("processing_images", "Processing images...", (completed_units * 100) // total_units)
            source_metas = {oid: clean["assets_by_original_id"][oid] for oid in needed_asset_ids}
            copied_assets_by_original_id, copy_error = notes_storage_service.copy_assets_for_import_bulk(
                source_metas, int(user_id), notebook_id,
                on_asset_done=lambda: _tick("processing_images", "Processing images..."),
            )
            if copy_error is not None:
                raise copy_error

        total_pages = len(pages)
        for position, page in enumerate(pages):
            page_label = f"Processing page {position + 1} of {total_pages}..."
            _report("processing_page", page_label, (completed_units * 100) // total_units)
            new_page = notes_db.create_page(notebook_id, page["title"], position)
            records = []
            for obj in page["objects"]:
                payload = dict(obj["payload"])
                asset_id = None
                if obj["object_type"] == "image":
                    new_asset = copied_assets_by_original_id[obj["original_asset_id"]]
                    asset_id = new_asset["id"]
                    if isinstance(payload.get("fabric"), dict):
                        payload["fabric"]["src"] = notes_storage_service.asset_proxy_url(new_asset["id"])
                records.append({"page_id": new_page["id"], "object_type": obj["object_type"], "z_index": 0, "transform": obj["transform"], "payload": payload, "asset_id": asset_id})
            for index, record in enumerate(records):
                record["z_index"] = index
            for start in range(0, len(records), _IMPORT_INSERT_CHUNK):
                batch = records[start:start + _IMPORT_INSERT_CHUNK]
                if batch:
                    notes_db.create_objects_bulk(batch)
                    for _ in batch:
                        _tick("processing_page", page_label)
            _tick("processing_page", f"Page {position + 1} of {total_pages} complete")

        _report("finalizing", "Finalizing import...", (completed_units * 100) // total_units)
        result = notes_db.get_owned_notebook(notebook_id, int(user_id))
        if not result:
            raise ValueError("Import verification failed.")
        completed_units = total_units
        _report("complete", "Import complete", 100)
        return result
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
    """Owner OR shared (Viewer/Editor) access — the single choke point nearly
    every notebook-content function below funnels through. The returned
    notebook carries an `access` key ('owner'/'editor'/'viewer'); write
    operations further down additionally call assert_can_edit() on it."""
    return notes_db.get_accessible_notebook(validate_notebook_id(notebook_id), int(user_id))


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
    assert_can_edit(notebook)
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
    notebook = get_editor_notebook(user_id, notebook_id)
    if not notebook:
        return None
    assert_can_edit(notebook)
    title = str(title or "").strip()
    if not title or len(title) > 160:
        raise ValueError("Page titles must contain 1–160 characters.")
    if notes_db.page_name_exists(validate_notebook_id(notebook_id), title, page_id):
        raise ValueError("A page with this name already exists in this notebook.")
    return notes_db.update_page(page_id, validate_notebook_id(notebook_id), {"title": title})


def delete_page(user_id: int, notebook_id: str, page_id: str) -> bool:
    notebook = get_editor_notebook(user_id, notebook_id)
    if not notebook:
        return False
    assert_can_edit(notebook)
    return notes_db.delete_page(page_id, validate_notebook_id(notebook_id))


def get_page_objects(user_id: int, notebook_id: str, page_id: str) -> Optional[List[Dict[str, Any]]]:
    if not get_editor_notebook(user_id, notebook_id):
        return None
    if not any(page["id"] == page_id for page in notes_db.list_pages(validate_notebook_id(notebook_id))):
        return None
    return _refresh_image_urls(notes_db.list_page_objects(page_id))


def save_page_objects(user_id: int, notebook_id: str, page_id: str, objects: List[Dict[str, Any]], deleted: List[Dict[str, Any]], start_index: int = 0) -> Optional[Dict[str, Any]]:
    """A page has no cap on total objects — only a single save REQUEST does (see
    MAX_OBJECTS_PER_PAGE / notes_validation.py). A page bigger than that chunk size is saved
    as several sequential calls here (editor.js splits canvas.getObjects() client-side), each
    one this many objects and tagged with its own `start_index` so z_index — and therefore
    draw/stacking order on reload — stays correct and non-colliding across chunks, instead of
    every chunk restarting from 0.

    Optimistic concurrency: every object the client is UPDATING (has an
    `id`) must carry the `expected_version` it last read; a deletion is
    `{"id", "expected_version"}` instead of a bare id. Anything whose
    version no longer matches the server's is a conflict — reported back,
    never silently overwritten or resurrected — see
    notes_db.replace_page_objects_versioned for exactly how each case is
    decided. Returns {"saved": [...], "conflicts": [...]} rather than a
    flat list, so a save can partially succeed (everything not in conflict)
    while surfacing exactly what wasn't applied and why."""
    notebook = get_editor_notebook(user_id, notebook_id)
    if not notebook:
        return None
    assert_can_edit(notebook)
    current = get_page_objects(user_id, notebook_id, page_id)
    if current is None:
        return None
    if not isinstance(objects, list) or not isinstance(deleted, list):
        raise ValueError("Invalid canvas save data.")
    from app.utils.notes_validation import ALLOWED_OBJECT_TYPES, MAX_OBJECTS_PER_PAGE
    if len(objects) > MAX_OBJECTS_PER_PAGE:
        raise ValueError("Too many objects in a single save request.")

    # Bucketed by whether the client sent a real expected_version, NOT by whether it sent an
    # `id` — ids are always generated client-side (crypto.randomUUID(), see editor.js
    # objectRecord/addObject), so a brand-new never-saved object still carries one. A null/absent
    # expected_version is the actual "this is new to the server" signal.
    new_objects, updates = [], []
    for index, item in enumerate(objects):
        if not isinstance(item, dict) or item.get("object_type") not in ALLOWED_OBJECT_TYPES or not item.get("id"):
            raise ValueError("Invalid canvas object.")
        record = {"id": str(item["id"]), "object_type": item["object_type"], "z_index": start_index + index, "transform": item.get("transform") or {}, "payload": item.get("payload") or {}, "asset_id": item.get("asset_id")}
        raw_version = item.get("expected_version")
        if raw_version is None:
            new_objects.append(record)
        else:
            try:
                record["expected_version"] = int(raw_version)
            except (TypeError, ValueError):
                raise ValueError("Invalid version for an object being updated.")
            updates.append(record)

    deletions = []
    for item in deleted:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("Invalid deletion entry.")
        try:
            expected_version = int(item.get("expected_version"))
        except (TypeError, ValueError):
            raise ValueError("Missing version for a deleted object.")
        deletions.append({"id": str(item["id"]), "expected_version": expected_version})

    saved, conflicts = notes_db.replace_page_objects_versioned(page_id, new_objects, updates, deletions)

    # Deleting an image object leaves its asset (and file) orphaned unless cleaned up here —
    # works the same for local and cloud storage. Only clean up assets for deletions that
    # actually went through (not ones reported as conflicts, i.e. never applied).
    conflicted_ids = {c["id"] for c in conflicts}
    actually_deleted_ids = {d["id"] for d in deletions} - conflicted_ids
    removed_asset_ids = {str(o["asset_id"]) for o in current if str(o["id"]) in actually_deleted_ids and o.get("object_type") == "image" and o.get("asset_id")}
    orphaned = [aid for aid in removed_asset_ids if not notes_db.asset_still_referenced(aid)]
    if orphaned:
        notes_storage_service.delete_assets(notes_db.get_assets_by_ids(orphaned))
        notes_db.delete_assets_by_ids(orphaned)
    return {"saved": saved, "conflicts": conflicts}