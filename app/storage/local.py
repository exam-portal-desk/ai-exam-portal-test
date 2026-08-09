"""
app/storage/local.py
Filesystem-backed storage provider. Files are never served through a public
static route — signed_url() points at an authorized application endpoint
that re-checks ownership/visibility before streaming the file.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote

import config
from app.storage.service import StorageProvider


class LocalStorageProvider(StorageProvider):
    def __init__(self):
        self.root = Path(config.STORAGE_LOCAL_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Invalid storage key.")
        return path

    def upload(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def download(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, keys: List[str]) -> None:
        for key in keys:
            try:
                self._path(key).unlink(missing_ok=True)
            except ValueError:
                pass

    def copy(self, src_key: str, dst_key: str, content_type: str = None) -> None:
        dst = self._path(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._path(src_key), dst)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{config.STORAGE_LOCAL_URL_PREFIX}/{quote(key)}"

    def signed_urls_bulk(self, keys: List[str], expires_in: int = 3600) -> Dict[str, str]:
        return {key: self.signed_url(key, expires_in) for key in keys}
