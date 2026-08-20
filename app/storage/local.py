"""
app/storage/local.py
Filesystem-backed storage provider. Files are never served through a public
static route — signed_url() points at an authorized application endpoint
that re-checks ownership/visibility before streaming the file.
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import app.config as config
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
        dirs_to_prune = set()
        for key in keys:
            try:
                path = self._path(key)
                path.unlink(missing_ok=True)
                dirs_to_prune.add(path.parent)
            except ValueError:
                pass
        for d in dirs_to_prune:
            self._prune_empty_dirs(d)

    def _prune_empty_dirs(self, directory: Path) -> None:
        """Remove `directory` and any now-empty ancestors, stopping at
        self.root. Object storage backends have no real directories to
        clean up — this only matters for local, so a deleted subject/
        notebook prefix stops showing up as a leftover empty folder."""
        try:
            while directory != self.root and directory.is_relative_to(self.root) \
                    and directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
                directory = directory.parent
        except OSError:
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

    def list_objects(
        self, prefix: str = "", cursor: Optional[str] = None, limit: int = 100,
        delimiter: Optional[str] = None,
    ) -> dict:
        if delimiter:
            # Folder-style: only direct children of prefix, one level of
            # sub-prefixes as "folders" (mirrors S3 Delimiter semantics).
            base = self._path(prefix) if prefix else self.root
            objects, prefixes = [], []
            if base.is_dir():
                for child in sorted(base.iterdir()):
                    key = child.relative_to(self.root).as_posix()
                    if child.is_dir():
                        prefixes.append(key + "/")
                    elif child.is_file():
                        stat = child.stat()
                        objects.append({
                            "key": key,
                            "size": stat.st_size,
                            "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        })
            start = int(cursor) if cursor else 0
            page = objects[start:start + limit]
            next_cursor = str(start + limit) if start + limit < len(objects) else None
            return {"objects": page, "prefixes": prefixes, "next_cursor": next_cursor}

        matches = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                stat = path.stat()
                matches.append({
                    "key": key,
                    "size": stat.st_size,
                    "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        matches.sort(key=lambda o: o["key"])

        start = int(cursor) if cursor else 0
        page = matches[start:start + limit]
        next_cursor = str(start + limit) if start + limit < len(matches) else None
        return {"objects": page, "prefixes": [], "next_cursor": next_cursor}
