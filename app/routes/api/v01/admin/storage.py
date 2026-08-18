"""
app/routes/api/v01/admin/storage.py
Admin Object Storage JSON API (v01) — browse/search/preview/delete objects
in the active storage backend (config.STORAGE_BACKEND). Provider-agnostic:
every handler goes through app.storage.get_storage(), never a backend SDK
directly, so this page keeps working unchanged if the backend is switched
from S3-compatible to local or to a different S3-compatible provider.
"""

import os

from flask import jsonify, request

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.storage import get_storage
from app.services.image_storage_service import resolve_object_url
from app.utils.datetime_service import format_display
import app.config as config

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Safety caps for on-demand full-bucket scans (search / stats) so a very
# large bucket can't turn an admin page load into an unbounded operation.
_SCAN_PAGE_LIMIT = 1000
_SCAN_MAX_OBJECTS = 20000

# Tighter cap for the per-folder size/modified summary shown inline in the
# browse table — several folders' worth of these can run in a single page
# load, so each one gets a smaller budget than the explicit "Calculate
# usage" full scan above. A folder past the cap still shows a real (partial)
# total, flagged as truncated, rather than nothing.
_FOLDER_SCAN_MAX_OBJECTS = 2000


def _decorate(obj: dict) -> dict:
    ext = os.path.splitext(obj["key"])[1].lower()
    is_image = ext in _IMAGE_EXTS
    obj["is_image"] = is_image
    obj["preview_url"] = resolve_object_url(obj["key"]) if is_image else None
    # Pre-formatted with the project's shared display format (same one Jinja's
    # display_dt filter uses) so the frontend never does its own date parsing.
    obj["last_modified"] = format_display(obj["last_modified"]) if obj.get("last_modified") else None
    return obj


def _scan_prefix_stats(storage, prefix: str, max_objects: int = _SCAN_MAX_OBJECTS) -> dict:
    """Recursive scan under `prefix` -> real object_count/total_bytes/most-
    recent last_modified from the storage backend (never hardcoded/default
    values). Bounded by max_objects so a very large prefix can't turn a
    single request into an unbounded scan; `truncated` signals the returned
    totals are a partial (but still real) lower bound in that case."""
    count, total_bytes, last_modified, cursor, truncated = 0, 0, None, None, False
    while count < max_objects:
        page = storage.list_objects(prefix=prefix, cursor=cursor, limit=_SCAN_PAGE_LIMIT)
        count += len(page["objects"])
        for o in page["objects"]:
            total_bytes += o["size"]
            lm = o.get("last_modified")
            if lm and (last_modified is None or lm > last_modified):
                last_modified = lm
        cursor = page.get("next_cursor")
        if not cursor:
            break
    if cursor:
        truncated = True
    return {"object_count": count, "total_bytes": total_bytes, "last_modified": last_modified, "truncated": truncated}


@admin_api_bp.route("/storage/objects", methods=["GET"])
@require_admin_role
def list_storage_objects():
    storage = get_storage()
    prefix = request.args.get("prefix", "") or ""
    query = (request.args.get("q") or "").strip().lower()
    limit = min(max(int(request.args.get("limit", 50) or 50), 1), 200)

    if query:
        # No native substring search in the S3 API — scan flat listing pages
        # under `prefix` (bucket-wide if prefix is empty) until enough
        # matches are found or the safety cap is hit.
        matches, cursor, scanned, truncated = [], None, 0, False
        while len(matches) < limit and scanned < _SCAN_MAX_OBJECTS:
            page = storage.list_objects(prefix=prefix, cursor=cursor, limit=_SCAN_PAGE_LIMIT)
            scanned += len(page["objects"])
            matches.extend(o for o in page["objects"] if query in o["key"].lower())
            cursor = page.get("next_cursor")
            if not cursor:
                break
        if cursor and scanned >= _SCAN_MAX_OBJECTS:
            truncated = True
        return jsonify({
            "success": True,
            "objects": [_decorate(o) for o in matches[:limit]],
            "prefixes": [],
            "next_cursor": None,
            "truncated": truncated,
            "backend": config.STORAGE_BACKEND,
        })

    cursor = request.args.get("cursor") or None
    page = storage.list_objects(prefix=prefix, cursor=cursor, limit=limit, delimiter="/")

    folders = []
    for folder_prefix in page.get("prefixes", []):
        stats = _scan_prefix_stats(storage, folder_prefix, max_objects=_FOLDER_SCAN_MAX_OBJECTS)
        folders.append({
            "key": folder_prefix,
            "object_count": stats["object_count"],
            "size": stats["total_bytes"],
            "last_modified": format_display(stats["last_modified"]) if stats["last_modified"] else None,
            "truncated": stats["truncated"],
        })

    return jsonify({
        "success": True,
        "objects": [_decorate(o) for o in page["objects"]],
        "prefixes": folders,
        "next_cursor": page.get("next_cursor"),
        "truncated": False,
        "backend": config.STORAGE_BACKEND,
    })


@admin_api_bp.route("/storage/stats", methods=["GET"])
@require_admin_role
def storage_stats():
    """On-demand full scan for total object count + total bytes under
    `prefix` (bucket-wide if omitted). Not computed on every page load —
    the dashboard calls this only when the admin asks for it, since it's
    the one operation here that's proportional to bucket size."""
    storage = get_storage()
    prefix = request.args.get("prefix", "") or ""
    stats = _scan_prefix_stats(storage, prefix)
    return jsonify({"success": True, "backend": config.STORAGE_BACKEND, **stats})


@admin_api_bp.route("/storage/objects", methods=["DELETE"])
@require_admin_role
def delete_storage_objects():
    data = request.get_json(silent=True) or {}
    keys = [k for k in (data.get("keys") or []) if isinstance(k, str) and k.strip()]
    if not keys:
        return jsonify({"success": False, "message": "No object keys provided."}), 400
    if len(keys) > 500:
        return jsonify({"success": False, "message": "Delete at most 500 objects at a time."}), 400

    try:
        get_storage().delete(keys)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({"success": True, "deleted": len(keys)})
