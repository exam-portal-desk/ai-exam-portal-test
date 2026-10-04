"""
app/routes/api/v01/admin/users.py
Admin user-role-management JSON API (v01). Relocated from
app/routes/admin/users.py.

FIXED (kept from original): Ghost user (id=-1, role='ghost',
username='deleted_user') is blocked at backend level from any role
updates.

  GET  /admin/api/users/search        -> GET  /api/v01/admin/users/search
  GET  /admin/api/users/stats         -> GET  /api/v01/admin/users/stats
  POST /admin/users/update-role       -> POST /api/v01/admin/users/update-role
  POST /admin/users/bulk-update-roles -> POST /api/v01/admin/users/bulk-update-roles
  GET  /api/v01/admin/users/<id>/photo -> admin-only profile photo THUMBNAIL (see route below)
"""
import threading
import time

from app.utils.datetime_service import now_utc_naive, format_display
from flask import request, jsonify, session, Response
from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_permission
from app.db import fetch_one, fetch_all, execute
from app.db.sessions import invalidate_session
from app.storage import get_storage
from app.services import image_storage_service
from app import entitlements

# Ghost user identifiers — must match user_deletion_service.py
GHOST_USER_ID       = -1
GHOST_USERNAME      = "deleted_user"
GHOST_ROLE          = "ghost"

def _is_ghost(user_id=None, username=None, role=None):
    """Check if a user is the system ghost account."""
    if user_id is not None and str(user_id) == str(GHOST_USER_ID):
        return True
    if username and username == GHOST_USERNAME:
        return True
    if role and role == GHOST_ROLE:
        return True
    return False


@admin_api_bp.route("/users/search")
@require_admin_permission("user_management")
def api_users_search():
    q        = request.args.get("q", "").strip()
    role_f   = request.args.get("role", "").strip().lower()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    per_page = 50
    start    = (page - 1) * per_page

    try:
        where, params = [], []
        if role_f == "user":
            where.append("role=%s"); params.append("user")
        elif role_f == "admin":
            where.append("role=%s"); params.append("admin")
        elif role_f == "both":
            where.append("role=%s"); params.append("user,admin")

        if q:
            where.append("(username ILIKE %s OR email ILIKE %s OR full_name ILIKE %s)")
            params += [f"%{q}%", f"%{q}%", f"%{q}%"]

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        total = fetch_one(f"SELECT COUNT(*) AS count FROM users {where_sql}", params)["count"]
        users = fetch_all(
            f"SELECT id, username, email, full_name, role, created_at, updated_at, profile_photo_key FROM users "
            f"{where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [per_page, start],
        )
        try:
            access = entitlements.summaries_for([u["id"] for u in users])
        except Exception as e:
            print(f"[admin.users] plan summaries unavailable: {e}")
            access = {}
        for u in users:
            u["created_at"] = format_display(u.get("created_at"))
            u["updated_at"] = format_display(u.get("updated_at"))
            summary = access.get(u["id"])
            if summary and not _is_ghost(user_id=u["id"], username=u.get("username"), role=u.get("role")):
                u.update(plan=summary["plan"], plan_label=summary["plan_label"], plan_tone=summary["tone"], plan_icon=summary["icon"],
                         custom_access=summary["custom"], admin_access=summary["admin_access"])
            # avatar_url only — the raw storage key never leaves the server. Points at the
            # admin-only photo route below (require_admin_permission("user_management")), not the
            # app-wide /api/v01/images/asset/<key> route every other authenticated user can reach:
            # this list is only ever visible to an admin with this permission in the first place,
            # so its photos shouldn't be fetchable by anyone else even if they learned the URL.
            # The url itself carries a content hash (see admin_user_photo_url) so the browser can
            # cache it for a very long time without ever risking a stale photo after a re-upload.
            u["avatar_url"] = image_storage_service.admin_user_photo_url(u["id"], u.get("profile_photo_key"))
            del u["profile_photo_key"]

        return jsonify({
            "users":       users,
            "total":       total,
            "page":        page,
            "per_page":    per_page,
            "total_pages": max(1, -(-total // per_page)),
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@admin_api_bp.route("/users/stats")
@require_admin_permission("user_management")
def api_users_stats():
    try:
        # Single query with FILTER clauses instead of 4 sequential COUNT
        # round trips (flagged in the architecture audit).
        row = fetch_one(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE role=%s) AS user_role, "
            "COUNT(*) FILTER (WHERE role=%s) AS admin_role, "
            "COUNT(*) FILTER (WHERE role=%s) AS both_roles "
            "FROM users",
            ("user", "admin", "user,admin"),
        )
        return jsonify({
            "total_users": row["total"],
            "user_role":   row["user_role"],
            "admin_role":  row["admin_role"],
            "both_roles":  row["both_roles"],
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"total_users":0,"user_role":0,"admin_role":0,"both_roles":0}), 500


@admin_api_bp.route("/users/update-role", methods=["POST"])
@require_admin_permission("access_control")
def update_user_role():
    data     = request.get_json() or {}
    user_id  = data.get("user_id")
    new_role = (data.get("new_role") or "").strip()

    if not user_id or new_role not in ("user", "admin", "user,admin"):
        return jsonify({"success": False, "message": "Invalid data"}), 400
    try:
        entitlements.authorize_role_change(session.get("user_id"), int(user_id))
    except entitlements.AccessDenied as e:
        return jsonify({"success": False, "message": str(e)}), 403
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid data"}), 400

    # ── GHOST BLOCK ──────────────────────────────────────────────────────────
    if _is_ghost(user_id=user_id):
        return jsonify({
            "success": False,
            "message": "System account cannot be modified."
        }), 403

    try:
        # Double-check in DB that this isn't the ghost (in case id was spoofed)
        row = fetch_one("SELECT username, role FROM users WHERE id=%s", (user_id,))
        if row and _is_ghost(username=row.get("username"), role=row.get("role")):
            return jsonify({
                "success": False,
                "message": "System account cannot be modified."
            }), 403

        execute("UPDATE users SET role=%s, updated_at=%s WHERE id=%s", (new_role, now_utc_naive().isoformat(), user_id))
        # Forces a fresh login next time this person acts, same reason as approve_request in
        # api/v01/admin/requests.py — otherwise their already-active session keeps whatever
        # admin_session/role it cached at login, ignoring this change until they happen to log
        # out on their own.
        invalidate_session(int(user_id))
        return jsonify({"success": True, "message": f"Role updated to {new_role}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@admin_api_bp.route("/users/bulk-update-roles", methods=["POST"])
@require_admin_permission("access_control")
def bulk_update_user_roles():
    data    = request.get_json() or {}
    updates = data.get("updates", [])

    if not updates:
        return jsonify({"success": False, "message": "No updates provided"}), 400

    updated = 0
    skipped = 0
    denied  = 0
    errors  = []

    for upd in updates:
        uid  = upd.get("user_id")
        role = (upd.get("new_role") or "").strip()

        if not uid or role not in ("user", "admin", "user,admin"):
            errors.append(f"Skipped invalid entry: {upd}")
            continue

        # ── GHOST BLOCK ──────────────────────────────────────────────────────
        if _is_ghost(user_id=uid):
            skipped += 1
            continue

        try:
            entitlements.authorize_role_change(session.get("user_id"), int(uid))
        except entitlements.AccessDenied as e:
            denied += 1
            errors.append(f"uid={uid}: {e}")
            continue
        except (TypeError, ValueError):
            errors.append(f"Skipped invalid entry: {upd}")
            continue

        try:
            # DB-level ghost check
            row = fetch_one("SELECT username, role FROM users WHERE id=%s", (uid,))
            if row and _is_ghost(username=row.get("username"), role=row.get("role")):
                skipped += 1
                continue

            execute("UPDATE users SET role=%s, updated_at=%s WHERE id=%s", (role, now_utc_naive().isoformat(), uid))
            # Same reason as the single-user update-role route above: without this, each of
            # these people keeps whatever role/admin_session their already-active session cached
            # at login, ignoring this bulk change until they happen to log out on their own.
            invalidate_session(int(uid))
            updated += 1
        except Exception as e:
            errors.append(f"uid={uid}: {e}")

    if updated:
        msg = f"Successfully updated {updated} user(s)"
        if skipped:
            msg += f" ({skipped} system account skipped)"
        return jsonify({"success": True, "message": msg, "errors": errors or None})

    return jsonify({
        "success": False,
        "message": "No updates applied",
        "errors":  errors,
    }), (403 if denied else 400)


# Resized-thumbnail cache, keyed by storage KEY (not user_id) — a re-upload gets a brand-new key
# (see profile.py), so this needs no manual invalidation: an old entry simply stops being asked
# for once a user's photo changes, and just ages out on its own TTL. This is what actually makes
# these images fast: on STORAGE_BACKEND=s3, every cache miss is a real network round trip to the
# object store, and the stored originals are full-resolution uploads (one on this install is
# 960x1280, ~150KB) for what only ever renders as a ~30px circle — downloading and decoding that
# full image, on every one of a page's ~25 rows, on every tab switch, is the actual slowness this
# was asked to fix, not the permission check (see below). One resize pays that cost once; every
# other admin viewing the same user's row after that is served straight from this dict.
_THUMB_CACHE: dict[str, tuple[float, bytes]] = {}
_THUMB_LOCK = threading.Lock()
_THUMB_TTL = 3600
_THUMB_MAX_CACHED = 500
_THUMB_SIZE = (96, 96)  # ~3x a 30px CSS avatar-chip — crisp on a retina/high-DPI display, still tiny once JPEG-encoded


def _profile_thumbnail(key: str) -> bytes:
    now = time.monotonic()
    with _THUMB_LOCK:
        hit = _THUMB_CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]

    from io import BytesIO
    from PIL import Image
    data = get_storage().download(key)
    img = Image.open(BytesIO(data))
    img = img.convert("RGB")  # flattens any transparent PNG onto white — JPEG has no alpha channel, and at this size no one can tell
    img.thumbnail(_THUMB_SIZE, Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=82, optimize=True)
    thumb = buf.getvalue()

    with _THUMB_LOCK:
        if len(_THUMB_CACHE) >= _THUMB_MAX_CACHED:
            _THUMB_CACHE.clear()
        _THUMB_CACHE[key] = (now + _THUMB_TTL, thumb)
    return thumb


@admin_api_bp.route("/users/<int:user_id>/photo")
@require_admin_permission("user_management")
def admin_user_photo(user_id):
    """A small (~96x96) JPEG thumbnail of a user's profile photo — admin-only (same permission
    gating every other endpoint on this page), unlike the app-wide /api/v01/images/asset/<key>
    route every authenticated session (any role) can reach for a leaderboard/discussion/notes-
    share avatar. Requests & User Management shows photos for potentially any user in the
    system, not just people this admin would otherwise legitimately see an avatar for through
    one of those other features, so it gets its own dedicated, more tightly gated route instead
    of reusing that shared one. Takes a user id, not a raw storage key — the key itself is
    resolved here and never sent to or accepted from the browser, on either this route or
    /users/search's response above.

    The permission check itself (require_admin_permission) is already effectively free here: it
    reads through app.entitlements.store's own in-memory, 60s-TTL cache keyed by the CALLING
    admin's id (see load_subject there) — the very first request on this page (its own initial
    row-data fetch, before any image even starts loading) already warms that entry, so every one
    of a page's photo requests after that is an in-memory hit, not a database query. The real
    cost was always the image fetch/decode below, which _profile_thumbnail's cache above now
    also serves from memory after the first request for a given photo."""
    row = fetch_one("SELECT profile_photo_key FROM users WHERE id=%s", (user_id,))
    key = row and row.get("profile_photo_key")
    if not key:
        return jsonify({"success": False, "message": "No photo"}), 404

    import hashlib
    etag = hashlib.sha1(key.encode()).hexdigest()[:16]
    if request.headers.get("If-None-Match") == etag:
        return "", 304

    try:
        thumb = _profile_thumbnail(key)
    except Exception:
        return jsonify({"success": False, "message": "Image not found"}), 404

    resp = Response(thumb, mimetype="image/jpeg")
    resp.headers["ETag"] = etag
    # Safe to cache for a full year, immutably: the URL itself is content-addressed (a `v=` hash
    # of this same key — see admin_user_photo_url in image_storage_service.py), so a re-upload
    # produces a brand-new URL rather than invalidating this one. "private" — never a shared/CDN
    # cache, only this admin's own browser.
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return resp
