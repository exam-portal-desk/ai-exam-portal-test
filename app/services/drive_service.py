"""
app/services/drive_service.py
Thin service layer over google_drive_service.py.
Owns: Drive init, image URL resolution, cache integration.
"""

import os
from typing import Tuple, Optional

import config
from app.utils import cache as _cache
from app.db.misc import get_subject_folder_id_by_name

# Global Drive service instance (reused across requests)
_drive = None


def init_drive_service() -> bool:
    """Initialize and store the global Drive service. Returns True on success."""
    global _drive
    try:
        from google_drive_service import create_drive_service
        svc = create_drive_service()
        if svc:
            _drive = svc
            print("✅ Drive service initialized")
            return True
        return False
    except Exception as e:
        print(f"[drive_service] init error: {e}")
        return False


def get_drive() :
    """Return the global Drive service, initializing lazily if needed."""
    global _drive
    if _drive is None:
        init_drive_service()
    return _drive


def get_image_url(image_path: str) -> Tuple[bool, Optional[str]]:
    """
    Resolve an image_path like "Electrostatics/elec-1.jpg" to a public Drive URL.
    Returns (has_image, url_or_None).
    Uses the unified cache layer.
    """
    if not image_path or str(image_path).strip().lower() in ("", "nan", "none"):
        return False, None

    image_path = str(image_path).strip()
    drive = get_drive()
    if not drive:
        return False, None

    try:
        # Parse subject + filename
        if "/" in image_path:
            parts = image_path.split("/")
            subject_raw = parts[0].strip()
            filename = parts[-1].strip()
        else:
            subject_raw = None
            filename = image_path

        # Resolve folder ID
        folder_id = None
        if subject_raw:
            folder_id = get_subject_folder_id_by_name(subject_raw)

        if not folder_id:
            folder_id = config.IMAGES_FOLDER_ID

        if not folder_id:
            return False, None

        # Check URL cache
        cache_key = f"img_url::{folder_id}::{filename}"
        cached_url = _cache.get(cache_key, ttl=30)
        if cached_url and not _cache.is_force_refresh():
            return True, cached_url

        # Find file in Drive
        from google_drive_service import find_file_by_name, get_public_url, clear_image_cache_immediate

        file_id = find_file_by_name(drive, filename, folder_id)
        if not file_id:
            return False, None

        force = _cache.is_force_refresh()
        if force:
            clear_image_cache_immediate(file_id)

        url = get_public_url(drive, file_id, force_refresh=force)
        if not url:
            return False, None

        _cache.set(cache_key, url)
        return True, url

    except Exception as e:
        print(f"[drive_service] get_image_url error: {e}")
        return False, None


def get_drive_service_for_upload():
    """Return a user-OAuth Drive service for uploads."""
    from google_drive_service import get_drive_service_for_upload as _get
    return _get()


def clear_all_caches() -> None:
    """Clear Drive + app image caches. Called from publish route."""
    from google_drive_service import clear_cache, clear_image_cache_immediate
    clear_cache()
    clear_image_cache_immediate()
    _cache.clear_all()
    print("✅ All Drive + app caches cleared")