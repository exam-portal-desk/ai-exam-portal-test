"""Server-only storage operations for private Notes image assets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

import app.config as config
from app.db import notes as notes_db
from app.storage import get_storage


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def upload_image(owner_id: int, notebook_id: str, file_storage) -> Dict[str, Any]:
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose an image to upload.")
    content_type = (file_storage.mimetype or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Use a PNG, JPEG, GIF, or WebP image.")
    content = file_storage.read()
    if not content or len(content) > config.MAX_IMAGE_SIZE_BYTES:
        raise ValueError(f"Images must be smaller than {config.MAX_IMAGE_SIZE_KB} KB.")

    suffix = Path(file_storage.filename).suffix.lower() or ".img"
    asset_id = str(uuid4())
    path = f"{owner_id}/{notebook_id}/{asset_id}{suffix}"
    storage = get_storage()
    try:
        storage.upload(path, content, content_type)
        asset = notes_db.create_asset({"id": asset_id, "notebook_id": notebook_id, "owner_id": owner_id, "storage_path": path, "original_filename": file_storage.filename[:255], "content_type": content_type, "byte_size": len(content)})
        return {"asset": asset, "url": asset_proxy_url(asset["id"])}
    except Exception:
        try:
            storage.delete([path])
        except Exception:
            pass
        raise


def asset_proxy_url(asset_id: str) -> str:
    """Same-origin, ownership-checked URL for one asset — never a raw
    storage/bucket URL. Served by asset_file_api (app/routes/api/v01/
    notebooks.py), which re-checks uploader/owner/share/public access via
    notes_service.resolve_asset_for_serving_by_id() before streaming the
    bytes, regardless of which storage backend holds the file. Pure string
    formatting — no storage round-trip, and (unlike a presigned URL) never
    expires, so callers don't need to periodically re-resolve it."""
    return f"/api/v01/assets/{asset_id}/file"


def clone_asset(source_asset: Dict[str, Any], new_owner_id: int, new_notebook_id: str) -> Dict[str, Any]:
    """Copy a private image into the new owner's storage namespace."""
    storage = get_storage()
    suffix = Path(source_asset.get("original_filename") or source_asset["storage_path"]).suffix or ".img"
    asset_id = str(uuid4())
    storage_path = f"{new_owner_id}/{new_notebook_id}/{asset_id}{suffix}"
    storage.copy(source_asset["storage_path"], storage_path, source_asset.get("content_type"))
    try:
        return notes_db.create_asset({"id": asset_id, "notebook_id": new_notebook_id, "owner_id": new_owner_id, "storage_path": storage_path, "original_filename": source_asset.get("original_filename"), "content_type": source_asset.get("content_type"), "byte_size": source_asset.get("byte_size")})
    except Exception:
        storage.delete([storage_path])
        raise


def _copy_one_asset_for_import(storage, original_id: str, source_meta: Dict[str, Any],
                                new_owner_id: int, new_notebook_id: str) -> Tuple[str, Dict[str, Any]]:
    """Materialize one imported image under the importing user's storage namespace via the
    provider's native copy — image bytes never round-trip through this process. No separate
    exists() pre-check: storage.copy() already fails naturally when the source is missing (S3
    copy_object 404s, local shutil.copy2 raises FileNotFoundError) — catching that removes a
    whole extra round trip per asset instead of paying for exists()+copy() every time."""
    suffix = Path(source_meta.get("original_filename") or source_meta["storage_path"]).suffix or ".img"
    asset_id = str(uuid4())
    storage_path = f"{new_owner_id}/{new_notebook_id}/{asset_id}{suffix}"
    try:
        storage.copy(source_meta["storage_path"], storage_path, source_meta.get("content_type"))
    except Exception as exc:
        raise ValueError("This Notebook file references an image that is no longer available in storage.") from exc

    try:
        new_asset = notes_db.create_asset({
            "id": asset_id,
            "notebook_id": new_notebook_id,
            "owner_id": new_owner_id,
            "storage_path": storage_path,
            "original_filename": source_meta.get("original_filename"),
            "content_type": source_meta.get("content_type"),
            "byte_size": source_meta.get("byte_size"),
        })
    except Exception:
        try:
            storage.delete([storage_path])
        except Exception:
            pass
        raise
    return original_id, new_asset


def copy_assets_for_import_bulk(
    source_metas_by_original_id: Dict[str, Dict[str, Any]],
    new_owner_id: int,
    new_notebook_id: str,
    on_asset_done=None,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[Exception]]:
    """Copy every unique asset an import needs CONCURRENTLY instead of one at a time — used by
    notes_service.import_notebook(). Under this app's gevent monkey-patching, boto3's HTTP calls
    go through Python's socket/ssl modules (which ARE gevent-patched, unlike raw psycopg2), so
    these S3 head/copy round trips genuinely overlap instead of just interleaving.

    Returns (copied_by_original_id, first_error). Never raises itself: a failed copy is recorded
    as `first_error` but every OTHER asset is still allowed to finish, so the caller always gets
    back the complete set of what actually succeeded — required for the caller's rollback to
    clean up every real (billable/storage-occupying) copy it made, not just the ones before the
    first failure. `on_asset_done` fires once per asset attempt (success or failure), for progress
    reporting.
    """
    if not source_metas_by_original_id:
        return {}, None

    storage = get_storage()
    items = list(source_metas_by_original_id.items())
    results: Dict[str, Dict[str, Any]] = {}
    first_error: Optional[Exception] = None

    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        futures = {
            pool.submit(_copy_one_asset_for_import, storage, original_id, source_meta, new_owner_id, new_notebook_id): original_id
            for original_id, source_meta in items
        }
        for future in as_completed(futures):
            try:
                original_id, new_asset = future.result()
                results[original_id] = new_asset
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            finally:
                if on_asset_done:
                    on_asset_done()

    return results, first_error


def delete_assets(assets: list[Dict[str, Any]]) -> None:
    paths = [asset["storage_path"] for asset in assets if asset.get("storage_path")]
    if paths:
        get_storage().delete(paths)
