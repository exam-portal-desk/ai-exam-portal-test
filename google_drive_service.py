import os
import json
import time
import random
import threading
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# In-memory caches with TTL
# ---------------------------------------------------------------------------
_file_cache = {}
_folder_cache = {}
_image_cache = {}
_cache_timestamps = {}


def _is_cache_valid(key: str, ttl_seconds: int) -> bool:
    ts = _cache_timestamps.get(key)
    return bool(ts and (time.time() - ts) < ttl_seconds)


def _set_cache(key: str, value, bucket: dict):
    bucket[key] = value
    _cache_timestamps[key] = time.time()


def clear_cache():
    _file_cache.clear()
    _folder_cache.clear()
    _image_cache.clear()
    _cache_timestamps.clear()


def clear_image_cache_immediate(file_id=None):
    if file_id:
        cache_key = f"url::{file_id}"
        _image_cache.pop(cache_key, None)
        _cache_timestamps.pop(cache_key, None)
    else:
        _image_cache.clear()
        for key in list(_cache_timestamps.keys()):
            if key.startswith("url::"):
                _cache_timestamps.pop(key, None)


# ---------------------------------------------------------------------------
# Service account credentials loader
# ---------------------------------------------------------------------------
def _load_service_account_info():
    """Load SA credentials from env var (accepts JSON string or file path)."""
    env_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        if env_value and env_value.strip().startswith("{"):
            return json.loads(env_value)
        if env_value and os.path.exists(env_value):
            with open(env_value, "r", encoding="utf-8") as f:
                return json.load(f)
        fallback = os.path.join(os.path.dirname(__file__), "credentials.json")
        if os.path.exists(fallback):
            with open(fallback, "r", encoding="utf-8") as f:
                return json.load(f)
        print("No service account JSON found. Set GOOGLE_SERVICE_ACCOUNT_JSON in .env")
        return None
    except Exception as e:
        print(f"Failed to load service account JSON: {e}")
        return None


def _scopes():
    return [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.readonly",
    ]


# ---------------------------------------------------------------------------
# SA Drive service — used for read operations (find, list, public URL)
# ---------------------------------------------------------------------------
_sa_service = None
_sa_initialized = False
_sa_lock = threading.RLock()
_sa_last_used = None
_SA_HEALTH_CHECK_INTERVAL = 300  # seconds


def create_drive_service():
    """Build and return the global SA Drive service, reusing if healthy."""
    global _sa_service, _sa_initialized, _sa_last_used

    with _sa_lock:
        now = datetime.now()

        if _sa_initialized and _sa_service is not None:
            try:
                # Skip health check if used within last 30s
                if _sa_last_used and (now - _sa_last_used).seconds < 30:
                    return _sa_service
                # Periodic health check every 5 min
                if _sa_last_used is None or (now - _sa_last_used).seconds > _SA_HEALTH_CHECK_INTERVAL:
                    _sa_service.about().get(fields="user(emailAddress)").execute()
                    _sa_last_used = now
                return _sa_service
            except Exception as e:
                print(f"SA health check failed, reinitializing: {e}")
                _sa_service, _sa_initialized, _sa_last_used = None, False, None

        try:
            info = _load_service_account_info()
            if not info:
                return None

            # Fix escaped newlines in private key (common when stored as env var string)
            if "private_key" in info and "\\n" in info["private_key"]:
                info["private_key"] = info["private_key"].replace("\\n", "\n")

            creds = Credentials.from_service_account_info(info, scopes=_scopes())
            service = build("drive", "v3", credentials=creds, cache_discovery=False)

            try:
                about = service.about().get(fields="user(emailAddress)").execute()
                print(f"SA Drive ready: {about.get('user', {}).get('emailAddress', 'unknown')}")
            except Exception as e:
                print(f"SA about() warn: {e}")

            _sa_service, _sa_initialized, _sa_last_used = service, True, now
            return service

        except Exception as e:
            print(f"create_drive_service error: {e}")
            _sa_service, _sa_initialized, _sa_last_used = None, False, None
            return None


def get_drive_service():
    return create_drive_service()


# ---------------------------------------------------------------------------
# User OAuth Drive service — used for uploads (SA has no My Drive quota)
# ---------------------------------------------------------------------------
def create_drive_service_user():
    """
    Build Drive service from token.json (user OAuth).
    Accepts an authorized token or a client_secret JSON (triggers one-time browser flow).
    """
    token_path = os.getenv("GOOGLE_SERVICE_TOKEN_JSON", "token.json")

    try:
        if not token_path or not os.path.exists(token_path):
            print(f"Token file not found: {token_path}")
            return None

        with open(token_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read().strip())

        # Already an authorized token
        if all(k in data for k in ("refresh_token", "token_uri", "client_id", "client_secret")):
            creds = UserCredentials.from_authorized_user_info(data, scopes=_scopes())
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            try:
                about = service.about().get(fields="user").execute()
                print(f"User OAuth ready: {about.get('user', {}).get('emailAddress', 'unknown')}")
            except Exception:
                print("User OAuth client ready.")
            return service

        # Client secret JSON — run one-time browser flow and persist token
        if "installed" in data or "web" in data:
            client_config = {"installed": data["installed"]} if "installed" in data else {"web": data["web"]}
            flow = InstalledAppFlow.from_client_config(client_config, scopes=_scopes())
            creds = flow.run_local_server(port=0, prompt="consent")
            service = build("drive", "v3", credentials=creds, cache_discovery=False)

            token_to_save = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or _scopes()),
                "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
            }
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(token_to_save, f)
            print(f"Token saved to {token_path}")
            return service

        print("Unrecognized token file format.")
        return None

    except Exception as e:
        print(f"create_drive_service_user error: {e}")
        return None


def get_drive_service_for_upload():
    """Return user OAuth service for uploads. Raises if token not available."""
    service = create_drive_service_user()
    if service:
        return service
    raise RuntimeError(
        "Upload requires token.json. Set GOOGLE_SERVICE_TOKEN_JSON in .env. "
        "If providing client_secret.json, a one-time browser window will open."
    )


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------
def find_file_by_name(service, filename: str, parent_folder_id: str | None = None, max_retries: int = 2):
    if not service:
        service = get_drive_service()
    if not service or not filename:
        return None

    cache_key = f"file::{parent_folder_id or 'root'}::{filename}"
    if _is_cache_valid(cache_key, 600):
        return _file_cache.get(cache_key)

    query = f"name = '{filename}' and trashed = false"
    if parent_folder_id:
        query += f" and '{parent_folder_id}' in parents"

    for attempt in range(1, max_retries + 1):
        try:
            res = service.files().list(
                q=query,
                spaces="drive",
                fields="files(id,name,modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=10
            ).execute()
            files = res.get("files", [])
            if files:
                fid = files[0]["id"]
                _set_cache(cache_key, fid, _file_cache)
                return fid
            return None
        except Exception as e:
            print(f"find_file_by_name (attempt {attempt}): {e}")
            time.sleep(1)
    return None


def find_folder_by_name(service, folder_name: str, parent_folder_id: str | None = None, max_retries: int = 2):
    if not service:
        service = get_drive_service()
    if not service or not folder_name:
        return None

    cache_key = f"folder::{parent_folder_id or 'root'}::{folder_name}"
    if _is_cache_valid(cache_key, 600):
        return _folder_cache.get(cache_key)

    query = (
        f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    if parent_folder_id:
        query += f" and '{parent_folder_id}' in parents"

    for attempt in range(1, max_retries + 1):
        try:
            res = service.files().list(
                q=query, spaces="drive", fields="files(id,name)", pageSize=5
            ).execute()
            folders = res.get("files", [])
            if folders:
                fid = folders[0]["id"]
                _set_cache(cache_key, fid, _folder_cache)
                return fid
            return None
        except Exception as e:
            print(f"find_folder_by_name (attempt {attempt}): {e}")
            time.sleep(1)
    return None


def list_drive_files(service, folder_id: str | None = None, max_retries: int = 2):
    if not service:
        service = get_drive_service()
    if not service:
        return []

    query = "trashed = false"
    if folder_id:
        query += f" and '{folder_id}' in parents"

    for attempt in range(1, max_retries + 1):
        try:
            res = service.files().list(
                q=query, spaces="drive",
                fields="files(id,name,mimeType)", pageSize=200
            ).execute()
            return res.get("files", [])
        except Exception as e:
            print(f"list_drive_files (attempt {attempt}): {e}")
            time.sleep(1)
    return []


def get_public_url(service, file_id: str, max_retries: int = 2, force_refresh: bool = False):
    """
    Returns a public thumbnail URL for the given file.
    Sets 'anyone with link' permission if not already set.
    Cache busting priority: md5 > version > modifiedTime > timestamp fallback.
    """
    if not service:
        service = get_drive_service()
    if not file_id:
        return None

    cache_key = f"url::{file_id}"

    if force_refresh:
        _image_cache.pop(cache_key, None)
        _cache_timestamps.pop(cache_key, None)
    elif _is_cache_valid(cache_key, 3600):
        cached = _image_cache.get(cache_key)
        if cached:
            return cached

    for attempt in range(1, max_retries + 1):
        try:
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"}
                ).execute()
            except Exception as e:
                print(f"Permission set warn: {e}")

            bust_token = None
            try:
                meta = service.files().get(
                    fileId=file_id, fields="modifiedTime,md5Checksum,version"
                ).execute()
                if meta.get("md5Checksum"):
                    bust_token = str(meta["md5Checksum"])[:16]
                elif meta.get("version"):
                    bust_token = f"v{meta['version']}"
                elif meta.get("modifiedTime"):
                    dt = datetime.fromisoformat(meta["modifiedTime"].replace("Z", "+00:00"))
                    bust_token = str(int(dt.timestamp()))
            except Exception as e:
                print(f"Metadata fetch warn: {e}")
                bust_token = str(int(time.time()))

            url = (
                f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000&v={bust_token}"
                if bust_token else
                f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000&t={int(time.time())}"
            )
            _set_cache(cache_key, url, _image_cache)
            return url

        except Exception as e:
            print(f"get_public_url error (attempt {attempt}): {e}")
            time.sleep(1)

    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000&t={int(time.time())}&r={random.randint(1000, 9999)}"


def create_subject_folder(service, subject_name: str):
    """
    Creates a folder inside IMAGES_FOLDER_ID (falls back to ROOT_FOLDER_ID).
    Returns (folder_id, created_at_str). Reuses folder if name already exists.
    """
    if not service:
        service = get_drive_service()
    if not service:
        raise RuntimeError("create_subject_folder: no Drive service available")

    parent = os.getenv("IMAGES_FOLDER_ID") or os.getenv("ROOT_FOLDER_ID")
    if not parent:
        raise RuntimeError("IMAGES_FOLDER_ID or ROOT_FOLDER_ID not set in .env")

    try:
        q = f"mimeType='application/vnd.google-apps.folder' and '{parent}' in parents and trashed=false"
        res = service.files().list(q=q, fields="files(id,name)").execute()
        for f in res.get("files", []):
            if f["name"].strip().lower() == subject_name.strip().lower():
                print(f"Reusing existing folder: {f['name']} ({f['id']})")
                return f["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"List subject folders warn: {e}")

    f = service.files().create(
        body={
            "name": subject_name.strip(),
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent]
        },
        fields="id"
    ).execute()
    print(f"Created subject folder '{subject_name}': {f.get('id')}")
    return f.get("id"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")