"""
app/utils/cache.py
Unified cache with Redis (primary) + in-memory fallback.
If REDIS_URL is set → uses Redis.
If not → falls back to in-memory (local dev).
"""

import time
import json
import threading
from typing import Any, Optional
import config

# ─────────────────────────────────────────────
# Redis client (lazy init)
# ─────────────────────────────────────────────
_redis = None
_redis_checked = False
_lock = threading.RLock()


def _get_redis():
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    if not config.REDIS_URL:
        print("ℹ️ [CACHE] No REDIS_URL — using in-memory cache")
        return None
    try:
        import redis
        client = redis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
        )
        client.ping()
        print("✅ [CACHE] Redis connected successfully")
        _redis = client
    except Exception as e:
        print(f"⚠️ [CACHE] Redis connection failed, using in-memory: {e}")
        _redis = None
    return _redis


# ─────────────────────────────────────────────
# In-memory fallback store
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
    r = _get_redis()
    if r:
        try:
            val = r.get(f"ep:{key}")
            return json.loads(val) if val else None
        except Exception as e:
            print(f"[CACHE] Redis get error: {e}")

    # In-memory fallback
    with _lock:
        ts = _store["timestamps"].get(key, 0)
        if time.time() - ts < ttl:
            return _store["data"].get(key)
        return None


def set(key: str, value: Any, ttl: int = config.CACHE_DEFAULT_TTL) -> None:
    r = _get_redis()
    if r:
        try:
            r.setex(f"ep:{key}", ttl, json.dumps(value, default=str))
            return
        except Exception as e:
            print(f"[CACHE] Redis set error: {e}")

    # In-memory fallback
    with _lock:
        _store["data"][key] = value
        _store["timestamps"][key] = time.time()
        _maybe_evict()


def delete(key: str) -> None:
    r = _get_redis()
    if r:
        try:
            r.delete(f"ep:{key}")
        except Exception as e:
            print(f"[CACHE] Redis delete error: {e}")

    with _lock:
        _store["data"].pop(key, None)
        _store["timestamps"].pop(key, None)


# ─────────────────────────────────────────────
# Image URL cache (short TTL = 30s)
# ─────────────────────────────────────────────

def get_image(file_id: str, ttl: int = 30) -> Optional[str]:
    r = _get_redis()
    if r:
        try:
            return r.get(f"ep:img:{file_id}")
        except Exception as e:
            print(f"[CACHE] Redis get_image error: {e}")

    with _lock:
        ts = _store["timestamps"].get(f"img::{file_id}", 0)
        if time.time() - ts < ttl:
            return _store["images"].get(file_id)
        return None


def set_image(file_id: str, url: str, ttl: int = 30) -> None:
    r = _get_redis()
    if r:
        try:
            r.setex(f"ep:img:{file_id}", ttl, url)
            return
        except Exception as e:
            print(f"[CACHE] Redis set_image error: {e}")

    with _lock:
        _store["images"][file_id] = url
        _store["timestamps"][f"img::{file_id}"] = time.time()


def clear_image(file_id: Optional[str] = None) -> None:
    r = _get_redis()
    if r:
        try:
            if file_id:
                r.delete(f"ep:img:{file_id}")
            else:
                # Delete all ep:img:* keys
                keys = r.keys("ep:img:*")
                if keys:
                    r.delete(*keys)
        except Exception as e:
            print(f"[CACHE] Redis clear_image error: {e}")

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
    r = _get_redis()
    if r:
        try:
            if value:
                r.setex("ep:force_refresh", 300, "1")
            else:
                r.delete("ep:force_refresh")
            return
        except Exception as e:
            print(f"[CACHE] Redis force_refresh set error: {e}")

    with _lock:
        _store["force_refresh"] = value


def is_force_refresh() -> bool:
    r = _get_redis()
    if r:
        try:
            return bool(r.get("ep:force_refresh"))
        except Exception as e:
            print(f"[CACHE] Redis force_refresh get error: {e}")

    with _lock:
        return bool(_store.get("force_refresh", False))


# ─────────────────────────────────────────────
# Clear all
# ─────────────────────────────────────────────

def clear_all() -> None:
    r = _get_redis()
    if r:
        try:
            keys = r.keys("ep:*")
            if keys:
                r.delete(*keys)
            print("✅ [CACHE] Redis cleared all ep:* keys")
        except Exception as e:
            print(f"[CACHE] Redis clear_all error: {e}")

    with _lock:
        _store["data"].clear()
        _store["timestamps"].clear()
        _store["images"].clear()
        _store["force_refresh"] = True


def cleanup_app_cache() -> None:
    """In-memory cleanup — called every 5 min. Redis handles TTL automatically."""
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