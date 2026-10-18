"""
app/services/storage_folder_service.py
Bulk folder deletion for the admin Object Storage page.

Two steps, both server-side and both re-validating every folder:
  1. preview_folders()  - normalises each requested prefix, rejects anything unsafe, measures it, applies the dependency
     rules and returns a signed, short-lived confirmation token bound to the admin and the exact folder set.
  2. delete_folders()   - requires that token AND the typed phrase, re-runs every check (nothing is trusted from the
     preview), then deletes page by page, tolerating per-folder failure and reporting each folder honestly.

Dependency rules (a folder is BLOCKED, never silently deleted, when any applies):
  * it is, or is inside, an application-owned prefix: Category/, Profile/, Group/, ChatBackground/ or a numeric
    "<owner id>/..." Notes attachment folder - other tables point at those keys;
  * questions.image_path still points at an object under it (those exams would lose their images).
A folder that is the registered Subject's folder (subjects.subject_name) is deletable when unreferenced, with a warning
that the Subject row itself remains.
"""

import logging
import re
import time
from typing import Dict, List, Optional

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.db import fetch_all, fetch_one
from app.storage import get_storage
from app.storage.service import StorageError, classify_storage_error

audit_log = logging.getLogger("smartaiexam.audit")

MAX_FOLDERS = 50
MAX_PREFIX_LEN = 512
MAX_OBJECTS_PER_FOLDER = 5000        # beyond this a folder must be emptied in smaller steps
MAX_OBJECTS_PER_REQUEST = 10000
LIST_PAGE = 1000                     # S3 DeleteObjects / ListObjectsV2 page size
DELETE_TIME_BUDGET_S = 45            # leaves headroom under typical 60 s gateway timeouts
TOKEN_MAX_AGE_S = 600
_TOKEN_SALT = "admin-storage-folder-delete"

RESERVED_ROOTS = ("category", "profile", "group", "chatbackground")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")


class FolderRequestError(ValueError):
    """The request itself is invalid (malformed list, bad token, wrong phrase)."""


def normalise_prefix(raw) -> str:
    """One requested folder -> a canonical "a/b/" prefix, or FolderRequestError. Rejects traversal, absolute paths,
    backslashes, control characters, empty segments and the bucket root, so no input can address anything but a real
    sub-folder of the storage root."""
    if not isinstance(raw, str):
        raise FolderRequestError("Folder must be text.")
    p = raw.strip()
    if not p or len(p) > MAX_PREFIX_LEN or _CONTROL.search(p):
        raise FolderRequestError("Invalid folder path.")
    if p.startswith("/") or "//" in p:
        raise FolderRequestError("Invalid folder path.")
    segs = p.rstrip("/").split("/")
    if any(s in ("", ".", "..") or s != s.strip() for s in segs):
        raise FolderRequestError("Invalid folder path.")
    return "/".join(segs) + "/"


def normalise_folders(raw) -> List[str]:
    if not isinstance(raw, list) or not raw:
        raise FolderRequestError("Select at least one folder.")
    if len(raw) > MAX_FOLDERS:
        raise FolderRequestError(f"Delete at most {MAX_FOLDERS} folders at a time.")
    out = []
    for r in raw:
        p = normalise_prefix(r)
        if p not in out:
            out.append(p)
    # a folder inside another selected folder is already covered by it
    return sorted(p for p in out if not any(o != p and p.startswith(o) for o in out))


def _is_reserved(prefix: str) -> Optional[str]:
    first = prefix.split("/", 1)[0]
    if first.lower() in RESERVED_ROOTS:
        return f"“{first}/” holds files other features depend on (category, profile or chat images) and cannot be deleted here."
    if first.isdigit():
        return "This is a Notes attachment folder owned by a user; its files are referenced by notebooks and cannot be deleted here."
    return None


def _scan(storage, prefix: str) -> Dict:
    count, total, cursor, truncated = 0, 0, None, False
    while True:
        page = storage.list_objects(prefix=prefix, cursor=cursor, limit=LIST_PAGE)
        for o in page["objects"]:
            count += 1
            total += int(o.get("size") or 0)
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if count >= MAX_OBJECTS_PER_FOLDER:
            truncated = True
            break
    return {"object_count": count, "total_bytes": total, "truncated": truncated}


def _question_references(prefix: str) -> Dict:
    row = fetch_one(
        "SELECT COUNT(*) AS questions, COUNT(DISTINCT exam_id) AS exams FROM questions "
        "WHERE image_path IS NOT NULL AND left(image_path, %s) = %s",
        (len(prefix), prefix),
    ) or {}
    return {"questions": int(row.get("questions") or 0), "exams": int(row.get("exams") or 0)}


def _is_registered_subject(prefix: str) -> bool:
    first = prefix.split("/", 1)[0]
    return bool(fetch_one("SELECT id FROM subjects WHERE subject_name = %s LIMIT 1", (first,)))


def inspect_folder(storage, prefix: str) -> Dict:
    """Everything the confirmation dialog and the delete step need to know about one (already normalised) folder."""
    info = {"key": prefix, "name": prefix.rstrip("/").split("/")[-1], "object_count": 0, "total_bytes": 0,
            "truncated": False, "status": "deletable", "reason": "", "warnings": []}
    reserved = _is_reserved(prefix)
    if reserved:
        info.update(status="blocked", reason=reserved)
        return info
    scan = _scan(storage, prefix)
    info.update(scan)
    if scan["object_count"] == 0:
        info.update(status="not_found", reason="This folder is empty or no longer exists.")
        return info
    if scan["truncated"]:
        info.update(status="blocked", reason=f"Contains more than {MAX_OBJECTS_PER_FOLDER} files. Open it and delete its sub-folders in smaller steps.")
        return info
    refs = _question_references(prefix)
    if refs["questions"]:
        info.update(status="blocked", reason=(
            f"{refs['questions']} question image(s) in {refs['exams']} exam(s) still use files in this folder. "
            "Remove or re-map those images first."))
        return info
    if _is_registered_subject(prefix):
        info["warnings"].append("This is a registered Subject's folder. The Subject itself is kept; only its stored images are removed.")
    return info


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=_TOKEN_SALT)


def expected_phrase(count: int) -> str:
    return f"DELETE {count} FOLDER" + ("" if count == 1 else "S")


def preview_folders(raw_folders, admin_id) -> Dict:
    folders = normalise_folders(raw_folders)
    storage = get_storage()
    items = [inspect_folder(storage, p) for p in folders]
    deletable = [i for i in items if i["status"] == "deletable"]
    total_objects = sum(i["object_count"] for i in deletable)
    if total_objects > MAX_OBJECTS_PER_REQUEST:
        raise FolderRequestError(f"That is more than {MAX_OBJECTS_PER_REQUEST} files in one go. Select fewer folders.")
    token = _serializer().dumps({"uid": admin_id, "folders": [i["key"] for i in deletable]}) if deletable else None
    return {
        "folders": items,
        "deletable_count": len(deletable),
        "total_objects": total_objects,
        "total_bytes": sum(i["total_bytes"] for i in deletable),
        "token": token,
        "phrase": expected_phrase(len(deletable)) if deletable else None,
    }


def _delete_one(storage, prefix: str, deadline: float) -> Dict:
    deleted, last_error = 0, None
    previous = None
    while True:
        if time.monotonic() > deadline:
            last_error = StorageError("timeout", prefix, "time budget reached")
            break
        try:
            page = storage.list_objects(prefix=prefix, cursor=None, limit=LIST_PAGE)
            keys = [o["key"] for o in page["objects"]]
            if not keys:
                break
            if keys == previous:
                deleted -= len(previous)          # the last batch was reported done but is still there: do not count it
                last_error = StorageError("unknown", prefix, "objects were not removed by the storage provider")
                break
            # defence in depth: never hand the provider a key that is not under the validated prefix
            keys = [k for k in keys if k.startswith(prefix)]
            storage.delete(keys)
            deleted += len(keys)
            previous = keys
        except Exception as e:
            last_error = classify_storage_error(e, prefix)
            break
    remaining = None
    if last_error is None:
        try:
            remaining = len(storage.list_objects(prefix=prefix, cursor=None, limit=1)["objects"])
        except Exception as e:
            last_error = classify_storage_error(e, prefix)
        else:
            if remaining:
                last_error = StorageError("unknown", prefix, "files are still present after deletion")
    return {"deleted": deleted, "error": last_error}


_ERROR_TEXT = {
    "permission": "The storage provider refused the request (permission).",
    "throttled": "The storage provider is rate-limiting requests. Wait a moment and try again.",
    "network": "Could not reach the storage provider.",
    "unavailable": "The storage provider is unavailable.",
    "timeout": "Stopped early to stay within the time limit. Run it again to finish.",
}


def delete_folders(raw_folders, admin_id, token, confirm, acknowledged) -> Dict:
    folders = normalise_folders(raw_folders)
    if acknowledged is not True:
        raise FolderRequestError("Please confirm that you understand this cannot be undone.")
    try:
        signed = _serializer().loads(token or "", max_age=TOKEN_MAX_AGE_S)
    except SignatureExpired:
        raise FolderRequestError("This confirmation expired. Review the folders again.")
    except BadSignature:
        raise FolderRequestError("Invalid confirmation. Review the folders again.")
    if signed.get("uid") != admin_id:
        raise FolderRequestError("Invalid confirmation. Review the folders again.")
    allowed = set(signed.get("folders") or [])
    if not allowed or not set(folders) <= allowed:
        raise FolderRequestError("The selection changed since it was reviewed. Review the folders again.")
    if confirm != expected_phrase(len(folders)):
        raise FolderRequestError("The confirmation text does not match.")

    storage = get_storage()
    deadline = time.monotonic() + DELETE_TIME_BUDGET_S
    results, stop = [], False
    for prefix in folders:
        item = inspect_folder(storage, prefix)        # nothing from the preview is trusted: re-check right now
        res = {"key": prefix, "name": item["name"], "deleted": 0, "status": item["status"], "message": item["reason"]}
        if stop:
            res.update(status="skipped", message="Not attempted because an earlier folder hit a provider limit.")
        elif item["status"] == "deletable":
            outcome = _delete_one(storage, prefix, deadline)
            res["deleted"] = outcome["deleted"]
            err = outcome["error"]
            if err is None:
                res.update(status="deleted", message=f"{outcome['deleted']} file(s) deleted.")
            else:
                res.update(status="partial" if outcome["deleted"] else "failed",
                           message=_ERROR_TEXT.get(err.kind, "The storage provider reported an error.")
                           + (f" {outcome['deleted']} file(s) were deleted before it stopped." if outcome["deleted"] else ""))
                if err.kind in ("throttled", "timeout", "unavailable", "network"):
                    stop = True
        results.append(res)
        audit_log.warning("storage_folder_delete admin=%s folder=%r status=%s deleted=%s", admin_id, prefix, res["status"], res["deleted"])

    all_ok = all(r["status"] == "deleted" for r in results)
    any_deleted = any(r["deleted"] for r in results)
    return {
        "results": results,
        "all_deleted": all_ok,
        "any_deleted": any_deleted,
        "deleted_files": sum(r["deleted"] for r in results),
        "deleted_folders": sum(1 for r in results if r["status"] == "deleted"),
        "failed_folders": sum(1 for r in results if r["status"] != "deleted"),
    }
