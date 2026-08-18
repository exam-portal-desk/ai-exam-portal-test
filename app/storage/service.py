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
