"""
app/utils/cache.py
In-process TTL cache (single-worker/single-process deployment; no external
cache service).
"""

import time
import threading
from typing import Any, Optional
import app.config as config

_lock = threading.RLock()

# ─────────────────────────────────────────────
# In-memory store
# ─────────────────────────────────────────────
_store: dict = {
    "data":         {},
    "timestamps":   {},
    "images":       {},
    "force_refresh": False,
}


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def get(key: str, ttl: int = config.CACHE_DEFAULT_TTL) -> Optional[Any]:
    with _lock:
        ts = _store["timestamps"].get(key, 0)
        if time.time() - ts < ttl:
            return _store["data"].get(key)
        return None


def set(key: str, value: Any, ttl: int = config.CACHE_DEFAULT_TTL) -> None:
    with _lock:
        _store["data"][key] = value
        _store["timestamps"][key] = time.time()
        _maybe_evict()


def delete(key: str) -> None:
    with _lock:
        _store["data"].pop(key, None)
        _store["timestamps"].pop(key, None)


# ─────────────────────────────────────────────
# Image URL cache (short TTL = 30s)
# ─────────────────────────────────────────────

def get_image(file_id: str, ttl: int = 30) -> Optional[str]:
    with _lock:
        ts = _store["timestamps"].get(f"img::{file_id}", 0)
        if time.time() - ts < ttl:
            return _store["images"].get(file_id)
        return None


def set_image(file_id: str, url: str, ttl: int = 30) -> None:
    with _lock:
        _store["images"][file_id] = url
        _store["timestamps"][f"img::{file_id}"] = time.time()


def clear_image(file_id: Optional[str] = None) -> None:
    with _lock:
        if file_id:
            _store["images"].pop(file_id, None)
            _store["timestamps"].pop(f"img::{file_id}", None)
        else:
            _store["images"].clear()
            for k in list(_store["timestamps"].keys()):
                if k.startswith("img::"):
                    _store["timestamps"].pop(k, None)


# ─────────────────────────────────────────────
# Force refresh flag
# ─────────────────────────────────────────────

def set_force_refresh(value: bool = True) -> None:
    with _lock:
        _store["force_refresh"] = value


def is_force_refresh() -> bool:
    with _lock:
        return bool(_store.get("force_refresh", False))


# ─────────────────────────────────────────────
# Clear all
# ─────────────────────────────────────────────

def clear_all() -> None:
    with _lock:
        _store["data"].clear()
        _store["timestamps"].clear()
        _store["images"].clear()
        _store["force_refresh"] = True


def cleanup_app_cache() -> None:
    """Evict stale entries — called every 5 min."""
    threshold = time.time() - 600
    with _lock:
        stale = [k for k, ts in _store["timestamps"].items() if ts < threshold]
        for k in stale:
            _store["data"].pop(k, None)
            _store["images"].pop(k.replace("img::", ""), None)
            _store["timestamps"].pop(k, None)


# ─────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────

def _maybe_evict() -> None:
    if len(_store["data"]) > config.CACHE_MAX_ITEMS:
        sorted_keys = sorted(_store["timestamps"].items(), key=lambda x: x[1])
        to_remove = sorted_keys[: len(sorted_keys) // 4]
        for k, _ in to_remove:
            _store["data"].pop(k, None)
            _store["timestamps"].pop(k, None)


# ─────────────────────────────────────────────
# Backward-compat shim
# ─────────────────────────────────────────────
app_cache = _store
