"""
app/services/image_storage_service.py
Provider-independent resolver/upload layer for category and question/subject
images, sitting in front of app/storage/ (Local | S3-compatible).

Existing DB references keep their current shape and meaning:
  - questions.image_path = "SubjectFolder/file.ext"  -> used AS the storage key.
  - categories.drive_file_id / categories.image_url   -> the storage key lives
    in drive_file_id (column name kept as-is — no schema change); image_url
    is only a legacy display fallback for rows never re-resolved through
    this service.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from app.storage import get_storage


def _url_for_key(key: str) -> str:
    """
    Resolve a storage key to a same-origin, auth-gated URL — never a raw
    storage/bucket URL (a leaked presigned S3 URL would bypass the app's
    own session checks entirely, since it requires no app involvement to
    fetch). Works identically for either storage backend: the proxy route
    (app/routes/api/v01/images.py) calls storage.download() under the hood
    regardless of which backend is active, same "any authenticated user"
    model as before (these images have no per-user ownership concept). Pure
    string formatting — no storage round-trip.
    """
    return f"/api/v01/images/asset/{quote(key)}"


def resolve_object_url(key: str) -> str:
    """Generic auth-gated URL for ANY key in the active storage backend
    (not just category/question images) — used by the admin Object Storage
    dashboard to preview arbitrary objects, including ones outside the
    Category/SubjectFolder convention (e.g. Notes assets sharing the same
    bucket)."""
    return _url_for_key(key)


def resolve_question_image_url(image_path: str) -> Tuple[bool, Optional[str]]:
    """Resolve a question image_path to a renderable URL."""
    if not image_path or str(image_path).strip().lower() in ("", "nan", "none"):
        return False, None

    key = str(image_path).strip()
    try:
        storage = get_storage()
        if storage.exists(key):
            return True, _url_for_key(key)
    except Exception as e:
        print(f"[image_storage_service] storage lookup error for {key}: {e}")

    return False, None


def resolve_question_image_urls_bulk(image_paths) -> Dict[str, Tuple[bool, Optional[str]]]:
    """
    Same result as calling resolve_question_image_url() once per path, but
    runs the storage existence checks concurrently instead of sequentially.

    A response/result page can reference dozens of unique question images —
    each resolution is one storage.exists() round trip (an S3 HEAD request
    on the S3-compatible backend), and doing those one at a time in a loop
    is what made large response pages block for a long time before any HTML
    was sent. This makes the exact same calls, just in parallel, so total
    wait time is roughly (N / worker count) round trips instead of N.
    """
    unique = [p for p in dict.fromkeys(image_paths) if p]
    if not unique:
        return {}
    if len(unique) == 1:
        return {unique[0]: resolve_question_image_url(unique[0])}

    with ThreadPoolExecutor(max_workers=min(8, len(unique))) as pool:
        resolved = list(pool.map(resolve_question_image_url, unique))
    return dict(zip(unique, resolved))


def resolve_category_image_url(category: dict) -> Optional[str]:
    """Resolve a category row's image URL. drive_file_id holds the storage
    key; image_url is a fallback for rows whose key can't be resolved
    (should not happen for current data, kept for safety)."""
    key = (category or {}).get("drive_file_id") or ""
    if key:
        try:
            storage = get_storage()
            if storage.exists(key):
                return _url_for_key(key)
        except Exception as e:
            print(f"[image_storage_service] category storage lookup error for {key}: {e}")
    return (category or {}).get("image_url")


def upload_category_image(content: bytes, filename: str, content_type: str) -> Tuple[str, str]:
    """Upload bytes to Category/<filename> (filename is expected to already
    be collision-safe, e.g. name_<uuid8>.ext). Returns (key, url) for the
    two DB columns."""
    storage = get_storage()
    key = f"Category/{filename}"
    storage.upload(key, content, content_type)
    return key, _url_for_key(key)


def upload_question_image(subject_folder: str, filename: str, content: bytes, content_type: str) -> str:
    """Upload bytes to <subject_folder>/<filename> (upsert by filename).
    Returns the storage key, which is exactly the value to store in
    questions.image_path."""
    storage = get_storage()
    key = f"{subject_folder}/{filename}"
    storage.upload(key, content, content_type)
    return key


def delete_image(key: str) -> None:
    if not key:
        return
    try:
        get_storage().delete([key])
    except Exception as e:
        print(f"[image_storage_service] delete error for {key}: {e}")


def delete_category_image(key: str) -> None:
    delete_image(key)


# ─────────────────────────────────────────────
# Profile photos (User + Admin portals) — same Category/-style convention,
# keyed per-account so re-uploads/removals never collide across users.
# ─────────────────────────────────────────────

def upload_profile_photo(user_id: int, content: bytes, filename: str, content_type: str) -> Tuple[str, str]:
    """Upload bytes to Profile/<user_id>_<filename>. Returns (key, url) —
    key is the value to persist in users.profile_photo_key."""
    storage = get_storage()
    key = f"Profile/{user_id}_{filename}"
    storage.upload(key, content, content_type)
    return key, _url_for_key(key)


def resolve_profile_photo_url(profile_photo_key: Optional[str]) -> Optional[str]:
    """Existence-checked resolution — used where accuracy matters more than
    per-request cost (the Profile page itself)."""
    if not profile_photo_key:
        return None
    try:
        storage = get_storage()
        if storage.exists(profile_photo_key):
            return _url_for_key(profile_photo_key)
    except Exception as e:
        print(f"[image_storage_service] profile photo lookup error for {profile_photo_key}: {e}")
    return None


def profile_photo_url_from_key(profile_photo_key: Optional[str]) -> Optional[str]:
    """Cheap, non-existence-checked URL construction — used for nav chrome
    rendered on every page, so no storage round-trip (S3: HEAD request;
    local: cheap but still avoided) happens on every request."""
    if not profile_photo_key:
        return None
    return _url_for_key(profile_photo_key)


def delete_profile_photo(key: str) -> None:
    delete_image(key)


# ─────────────────────────────────────────────
# Group profile photos — same Profile/-style convention, keyed per-group.
# ─────────────────────────────────────────────

def upload_group_photo(conv_id: int, content: bytes, filename: str, content_type: str) -> Tuple[str, str]:
    """Upload bytes to Group/<conv_id>_<filename>. Returns (key, url) — key
    is the value to persist in chat_conversations.group_photo_key."""
    storage = get_storage()
    key = f"Group/{conv_id}_{filename}"
    storage.upload(key, content, content_type)
    return key, _url_for_key(key)


def group_photo_url_from_key(group_photo_key: Optional[str]) -> Optional[str]:
    """Cheap, non-existence-checked URL construction — used for chat
    header/sidebar/notification rendering, no storage round-trip."""
    if not group_photo_key:
        return None
    return _url_for_key(group_photo_key)


def delete_group_photo(key: str) -> None:
    delete_image(key)


# ─────────────────────────────────────────────
# Chat backgrounds — same Profile/-style convention, keyed per-account.
# Only used when users.chat_background holds a real storage key (not a
# "preset:<id>" sentinel, which needs no storage at all).
# ─────────────────────────────────────────────

def upload_chat_background(user_id: int, content: bytes, filename: str, content_type: str) -> Tuple[str, str]:
    """Upload bytes to ChatBackground/<user_id>_<filename>. Returns (key, url)
    — key is the value to persist in users.chat_background."""
    storage = get_storage()
    key = f"ChatBackground/{user_id}_{filename}"
    storage.upload(key, content, content_type)
    return key, _url_for_key(key)


def chat_background_url_from_key(key: Optional[str]) -> Optional[str]:
    """Cheap, non-existence-checked URL construction — used to render the
    background on every chat page load, no storage round-trip."""
    if not key:
        return None
    return _url_for_key(key)


def delete_chat_background(key: str) -> None:
    delete_image(key)
