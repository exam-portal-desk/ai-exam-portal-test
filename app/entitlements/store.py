"""
app/entitlements/store.py
Everything that touches the database: a user's plan and overrides (read through a short per-user cache), the writes that
change them, and the audit trail. The catalog decides what a plan means; this only stores who has which plan and which
exceptions.
"""

import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from app.entitlements.resolver import DENY, GRANT, Override, Subject
from app.utils.datetime_service import now_utc_naive

_STATE_TTL = 60          # a change made by another process reaches this one within a minute; local writes apply at once
_DEGRADED_TTL = 20
_MAX_CACHED = 2000       # own small cache: the shared app cache is capped at 100 items, which per-user entries would flush

_lock = threading.Lock()
_cache: Dict[int, Tuple[float, Subject]] = {}
_last_warning = 0.0


def _parse_ts(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _override(row: dict) -> Override:
    return Override(row["feature_key"], row["effect"], dict(row.get("limits") or {}), _parse_ts(row.get("expires_at")),
                    row.get("reason") or "", row.get("created_by"), _parse_ts(row.get("created_at")))


def _warn(error: Exception) -> None:
    global _last_warning
    if time.monotonic() - _last_warning > 300:
        _last_warning = time.monotonic()
        print(f"[entitlements] user access data unavailable ({error}); using plan defaults. Has migrations/20260923_entitlements.sql been applied?")


def _as_admin(subject: Subject, is_admin: Optional[bool]) -> Subject:
    return subject if is_admin is None or is_admin == subject.is_admin else replace(subject, is_admin=is_admin)


def invalidate(user_id: Optional[int] = None) -> None:
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(int(user_id), None)


def load_subject(user_id: int, is_admin: Optional[bool] = None) -> Subject:
    """The user as the resolver needs them. `is_admin` is for callers that already know it (a request that passed the
    admin session guard); otherwise it comes from users.role."""
    uid = int(user_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(uid)
    if hit and hit[0] > now:
        return _as_admin(hit[1], is_admin)

    try:
        from app.db import fetch_all, fetch_one
        user = fetch_one("SELECT role, plan, plan_expires_at FROM users WHERE id=%s", (uid,)) or {}
        rows = fetch_all("SELECT feature_key, effect, limits, expires_at, reason, created_by, created_at "
                         "FROM user_feature_overrides WHERE user_id=%s", (uid,))
        subject = Subject(uid, "admin" in str(user.get("role") or "").lower(), user.get("plan") or None,
                          _parse_ts(user.get("plan_expires_at")), tuple(_override(r) for r in rows))
        ttl = _STATE_TTL
    except Exception as e:
        _warn(e)
        subject, ttl = Subject(uid, bool(is_admin)), _DEGRADED_TTL
    with _lock:
        if len(_cache) >= _MAX_CACHED:
            _cache.clear()
        _cache[uid] = (now + ttl, subject)
    return _as_admin(subject, is_admin)


def summaries(user_ids: Iterable[int]) -> Dict[int, dict]:
    """{user_id: {"plan", "plan_expires_at", "custom", "admin_access"}} for a page of users in two queries; custom = active
    special-access entries on user features, admin_access = active grants of admin features."""
    ids = [int(i) for i in user_ids]
    if not ids:
        return {}
    try:
        from app.db import fetch_all
        users = fetch_all("SELECT id, plan, plan_expires_at FROM users WHERE id = ANY(%s)", (ids,))
        counts = fetch_all("SELECT user_id, COUNT(*) FILTER (WHERE feature_key NOT LIKE 'admin.%%') AS n, "
                           "COUNT(*) FILTER (WHERE feature_key LIKE 'admin.%%' AND effect = 'grant') AS a "
                           "FROM user_feature_overrides WHERE user_id = ANY(%s) "
                           "AND (expires_at IS NULL OR expires_at > %s) GROUP BY user_id", (ids, now_utc_naive()))
    except Exception as e:
        _warn(e)
        return {}
    custom = {c["user_id"]: (int(c["n"]), int(c["a"])) for c in counts}
    return {u["id"]: {"plan": u.get("plan") or None, "plan_expires_at": _parse_ts(u.get("plan_expires_at")),
                      "custom": custom.get(u["id"], (0, 0))[0], "admin_access": custom.get(u["id"], (0, 0))[1]} for u in users}


def count_active_grants(feature_key: str) -> int:
    from app.db import fetch_one
    row = fetch_one("SELECT COUNT(*) AS n FROM user_feature_overrides WHERE feature_key=%s AND effect='grant' "
                    "AND (expires_at IS NULL OR expires_at > %s)", (feature_key, now_utc_naive()))
    return int(row["n"]) if row else 0


def list_overrides(user_id: int) -> List[Override]:
    from app.db import fetch_all
    rows = fetch_all("SELECT feature_key, effect, limits, expires_at, reason, created_by, created_at "
                     "FROM user_feature_overrides WHERE user_id=%s ORDER BY feature_key", (int(user_id),))
    return [_override(r) for r in rows]


def audit(actor_id: Optional[int], target_user_id: int, action: str, feature_key: Optional[str] = None, detail: Optional[dict] = None) -> None:
    from app.db import execute
    execute("INSERT INTO access_audit_log (actor_id, target_user_id, action, feature_key, detail) VALUES (%s, %s, %s, %s, %s)",
            (actor_id, int(target_user_id), action, feature_key, detail))


def set_plan(user_id: int, plan: Optional[str], expires_at: Optional[datetime], actor_id: Optional[int]) -> bool:
    from app.db import execute
    changed = execute("UPDATE users SET plan=%s, plan_expires_at=%s WHERE id=%s", (plan, expires_at, int(user_id))) > 0
    if changed:
        audit(actor_id, user_id, "plan_set", detail={"plan": plan, "expires_at": expires_at.isoformat() if expires_at else None})
        invalidate(user_id)
    return changed


def put_override(user_id: int, feature_key: str, effect: str, limits: Optional[dict], expires_at: Optional[datetime],
                 reason: str, actor_id: Optional[int]) -> None:
    if effect not in (GRANT, DENY):
        raise ValueError("effect must be 'grant' or 'deny'")
    from app.db import execute
    execute(
        "INSERT INTO user_feature_overrides (user_id, feature_key, effect, limits, expires_at, reason, created_by, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, feature_key) DO UPDATE SET effect=EXCLUDED.effect, limits=EXCLUDED.limits, "
        "expires_at=EXCLUDED.expires_at, reason=EXCLUDED.reason, created_by=EXCLUDED.created_by, created_at=EXCLUDED.created_at",
        (int(user_id), feature_key, effect, limits or None, expires_at, reason or None, actor_id, now_utc_naive()))
    audit(actor_id, user_id, f"override_{effect}", feature_key,
          {"limits": limits or None, "expires_at": expires_at.isoformat() if expires_at else None, "reason": reason or None})
    invalidate(user_id)


def remove_override(user_id: int, feature_key: str, actor_id: Optional[int]) -> bool:
    from app.db import execute
    removed = execute("DELETE FROM user_feature_overrides WHERE user_id=%s AND feature_key=%s", (int(user_id), feature_key)) > 0
    if removed:
        audit(actor_id, user_id, "override_removed", feature_key)
        invalidate(user_id)
    return removed
