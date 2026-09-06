"""
app/routes/api/v01/admin/storage.py
Admin Object Storage JSON API (v01) — browse/search/preview/delete objects
in the active storage backend (config.STORAGE_BACKEND). Provider-agnostic:
every handler goes through app.storage.get_storage(), never a backend SDK
directly, so this page keeps working unchanged if the backend is switched
from S3-compatible to local or to a different S3-compatible provider.
"""

import io
import mimetypes
import os
import zipfile
from datetime import date
from urllib.parse import quote

from flask import jsonify, request, Response

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.storage import get_storage
from app.services.image_storage_service import resolve_object_url
from app.utils.datetime_service import format_display
import app.config as config

# Same order-of-magnitude cap as delete_storage_objects()'s 500 — but zip
# building holds every downloaded object in memory at once (see
# download_storage_objects_zip() below), so this stays noticeably lower as
# a deliberate safety margin rather than matching that cap exactly.
_MAX_ZIP_OBJECTS = 300

_UNSAFE_ZIP_CHARS = frozenset('\\/:*?"<>|') | {chr(c) for c in range(32)}


def _content_disposition(filename: str) -> str:
    """A Content-Disposition value that works for both plain-ASCII and
    unicode filenames — an ASCII-only fallback for older clients plus the
    RFC 5987 filename* form every modern browser actually uses, so the
    saved file keeps its real (possibly non-ASCII) name instead of being
    silently transliterated or rejected."""
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def _sanitize_zip_segment(seg: str) -> str:
    """One path segment, made safe for a zip archive entry while staying
    readable — strips anything that isn't a letter/digit/space/dot/dash/
    underscore (unicode letters kept), and rejects '.'/'..'/empty segments
    outright so a crafted key can never traverse outside the archive."""
    seg = seg.strip()
    if seg in ("", ".", ".."):
        return "_"
    cleaned = ''.join('_' if ch in _UNSAFE_ZIP_CHARS else ch for ch in seg)
    return cleaned or "_"


def _safe_zip_arcname(key: str, used_names: set) -> str:
    """Turn a storage key into a zip entry path that preserves the
    original Subject/Category-style directory structure (readable, not a
    flat dump), can never contain '..' (path traversal), and can never
    collide with an entry already added to this same zip (a " (2)", " (3)"
    ... suffix is appended to the filename part only, on the rare exact
    duplicate)."""
    parts = [p for p in key.split("/") if p not in ("", ".", "..")]
    if not parts:
        parts = ["file"]
    safe_parts = [_sanitize_zip_segment(p) for p in parts]
    arcname = "/".join(safe_parts)
    base, ext = os.path.splitext(arcname)
    n = 1
    final = arcname
    while final in used_names:
        n += 1
        final = f"{base} ({n}){ext}"
    used_names.add(final)
    return final

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


@admin_api_bp.route("/storage/download", methods=["GET"])
@require_admin_role
def download_storage_object():
    """Single-object download — streams the real bytes through the same
    provider-agnostic get_storage().download() every other handler here
    uses (never a raw S3 URL/credential reaching the browser), with
    Content-Disposition:attachment so it saves under its own original
    filename instead of just opening inline like the preview link does."""
    key = (request.args.get("key") or "").strip()
    if not key:
        return jsonify({"success": False, "message": "No object key provided."}), 400

    try:
        content = get_storage().download(key)
    except Exception:
        return jsonify({"success": False, "message": "Object not found in storage."}), 404

    filename = os.path.basename(key.rstrip("/")) or "download"
    mime_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    resp = Response(content, mimetype=mime_type)
    resp.headers["Content-Disposition"] = _content_disposition(filename)
    return resp


@admin_api_bp.route("/storage/download-zip", methods=["POST"])
@require_admin_role
def download_storage_objects_zip():
    """Bulk download — bundles the selected objects into one ZIP, built
    entirely server-side (the browser makes one request and gets back one
    file; it never fetches each object itself). Preserves each object's
    own Subject/Category-style folder structure as the zip's internal
    paths (see _safe_zip_arcname), rather than flattening everything into
    the root or exposing the raw storage key layout unfiltered."""
    data = request.get_json(silent=True) or {}
    keys = [k for k in (data.get("keys") or []) if isinstance(k, str) and k.strip()]
    if not keys:
        return jsonify({"success": False, "message": "No object keys provided."}), 400
    if len(keys) > _MAX_ZIP_OBJECTS:
        return jsonify({"success": False, "message": f"Download at most {_MAX_ZIP_OBJECTS} objects at a time."}), 400

    storage = get_storage()
    used_names = set()
    missing = []
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for key in keys:
                try:
                    content = storage.download(key)
                except Exception:
                    missing.append(key)
                    continue
                zf.writestr(_safe_zip_arcname(key, used_names), content)
    except Exception as e:
        return jsonify({"success": False, "message": f"Unable to build the ZIP archive: {e}"}), 500

    if not used_names:
        return jsonify({"success": False, "message": "None of the selected objects could be found in storage."}), 404

    buf.seek(0)
    zip_filename = f"object-storage-download-{date.today().isoformat()}.zip"
    resp = Response(buf.getvalue(), mimetype="application/zip")
    resp.headers["Content-Disposition"] = _content_disposition(zip_filename)
    # Read client-side to warn about any selected object that vanished
    # between listing and download, without failing the whole archive.
    resp.headers["X-Missing-Count"] = str(len(missing))
    return resp
