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
"""
from app.utils.datetime_service import now_utc_naive, format_display
from flask import request, jsonify
from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db import fetch_one, fetch_all, execute

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
@require_admin_role
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
            f"SELECT id, username, email, full_name, role, created_at, updated_at FROM users "
            f"{where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [per_page, start],
        )
        for u in users:
            u["created_at"] = format_display(u.get("created_at"))
            u["updated_at"] = format_display(u.get("updated_at"))

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
@require_admin_role
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
@require_admin_role
def update_user_role():
    data     = request.get_json() or {}
    user_id  = data.get("user_id")
    new_role = (data.get("new_role") or "").strip()

    if not user_id or new_role not in ("user", "admin", "user,admin"):
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
        return jsonify({"success": True, "message": f"Role updated to {new_role}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@admin_api_bp.route("/users/bulk-update-roles", methods=["POST"])
@require_admin_role
def bulk_update_user_roles():
    data    = request.get_json() or {}
    updates = data.get("updates", [])

    if not updates:
        return jsonify({"success": False, "message": "No updates provided"}), 400

    updated = 0
    skipped = 0
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
            # DB-level ghost check
            row = fetch_one("SELECT username, role FROM users WHERE id=%s", (uid,))
            if row and _is_ghost(username=row.get("username"), role=row.get("role")):
                skipped += 1
                continue

            execute("UPDATE users SET role=%s, updated_at=%s WHERE id=%s", (role, now_utc_naive().isoformat(), uid))
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
    }), 400
