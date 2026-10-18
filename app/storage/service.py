"""
app/storage/service.py
Provider-independent object storage abstraction. Application code depends
only on this interface — never on a specific backend SDK.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, TypedDict

import app.config as config


class ObjectInfo(TypedDict):
    key: str
    size: int
    last_modified: Optional[str]  # ISO 8601, when the backend provides it


class StorageError(Exception):
    """A storage operation failed for a reason other than "the object does not exist". `kind` says why, so a caller can
    treat a genuinely missing object differently from a permission, network or throttling problem:
    permission | throttled | network | unavailable | invalid_key | unknown."""

    def __init__(self, kind: str, key: str = "", detail: str = ""):
        super().__init__(f"{kind}: {key}" + (f" ({detail})" if detail else ""))
        self.kind = kind
        self.key = key
        self.detail = detail


_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_PERMISSION_CODES = {"403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "AllAccessDisabled"}
_THROTTLE_CODES = {"429", "503", "SlowDown", "Throttling", "ThrottlingException", "RequestLimitExceeded", "ServiceUnavailable"}


def is_not_found_error(exc: BaseException) -> bool:
    """True only for "the object is not there" (a missing file, or an S3 404/NoSuchKey), never for any other failure."""
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        status = str(response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        return code in _NOT_FOUND_CODES or status == "404"
    return False


def classify_storage_error(exc: BaseException, key: str = "") -> StorageError:
    """Wrap any backend exception in a StorageError with a meaningful `kind`."""
    if isinstance(exc, StorageError):
        return exc
    if isinstance(exc, ValueError):
        return StorageError("invalid_key", key, str(exc))
    if isinstance(exc, PermissionError):
        return StorageError("permission", key, str(exc))
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        status = str(response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        if code in _PERMISSION_CODES or status == "403":
            return StorageError("permission", key, code or status)
        if code in _THROTTLE_CODES or status in ("429", "503"):
            return StorageError("throttled", key, code or status)
        if code == "NoSuchBucket" or status.startswith("5"):
            return StorageError("unavailable", key, code or status)
        return StorageError("unknown", key, code or status)
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return StorageError("network", key, f"{type(exc).__name__}: {exc}")
    if type(exc).__name__ in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError"):
        return StorageError("network", key, type(exc).__name__)
    return StorageError("unknown", key, f"{type(exc).__name__}: {exc}")


class StorageProvider(ABC):
    @abstractmethod
    def upload(self, key: str, content: bytes, content_type: str) -> None:
        ...

    @abstractmethod
    def download(self, key: str) -> bytes:
        ...

    @abstractmethod
    def delete(self, keys: List[str]) -> None:
        ...

    @abstractmethod
    def copy(self, src_key: str, dst_key: str, content_type: Optional[str] = None) -> None:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    def exists_strict(self, key: str) -> bool:
        """exists(), except that a failure to find out raises StorageError instead of answering False. Used where "missing"
        and "could not check" must not be confused (export / import). Backends override this; the default trusts exists()."""
        try:
            return self.exists(key)
        except Exception as e:
            raise classify_storage_error(e, key) from e

    @abstractmethod
    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        ...

    @abstractmethod
    def signed_urls_bulk(self, keys: List[str], expires_in: int = 3600) -> Dict[str, str]:
        ...

    @abstractmethod
    def list_objects(
        self, prefix: str = "", cursor: Optional[str] = None, limit: int = 100,
        delimiter: Optional[str] = None,
    ) -> "ListResult":
        """
        List objects under a key prefix, one page at a time (admin browse UI,
        migration tooling).

        delimiter=None (default): flat, recursive listing — every object key
        under `prefix`.

        delimiter="/": folder-style listing — only objects that are direct
        children of `prefix` are returned, and one level of sub-"folders" is
        returned in `prefixes` (e.g. prefix="" -> prefixes=["Category/",
        "Magnetism/", ...]), for a browsable folder UI over a flat key space.
        """
        ...

    def delete_prefix(self, prefix: str) -> int:
        """
        Delete every object under `prefix` (recursive) and, where the backend
        has real directories (local), prune the now-empty folder itself.
        Generic on top of list_objects()/delete() — no backend needs its own
        copy of this. Returns the number of objects deleted.
        """
        deleted = 0
        cursor = None
        while True:
            page = self.list_objects(prefix=prefix, cursor=cursor, limit=1000)
            keys = [o["key"] for o in page["objects"]]
            if keys:
                self.delete(keys)
                deleted += len(keys)
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return deleted


class ListResult(TypedDict):
    objects: List[ObjectInfo]
    prefixes: List[str]
    next_cursor: Optional[str]


_provider: Optional[StorageProvider] = None


def get_storage() -> StorageProvider:
    """Return the configured storage provider (singleton, backend selected by STORAGE_BACKEND)."""
    global _provider
    if _provider is not None:
        return _provider

    backend = config.STORAGE_BACKEND
    if backend == "local":
        from app.storage.local import LocalStorageProvider
        _provider = LocalStorageProvider()
    elif backend == "s3":
        from app.storage.s3 import S3StorageProvider
        _provider = S3StorageProvider()
    else:
        raise ValueError(f"Unsupported STORAGE_BACKEND: {backend}")
    return _provider
