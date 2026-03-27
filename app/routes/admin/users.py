"""
app/routes/admin/users.py — User role management
FIXED: Ghost user (id=-1, role='ghost', username='deleted_user') 
       is now blocked at backend level from any role updates.
"""
from datetime import datetime
from flask import render_template, request, jsonify
from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db import supabase

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


# ── Page ────────────────────────────────────────────────────────────────────
@admin_bp.route("/users/manage")
@require_admin_role
def users_manage():
    return render_template("admin/users_manage.html", users=[])


# ── AJAX: search + paginate users ───────────────────────────────────────────
@admin_bp.route("/api/users/search")
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
    end      = start + per_page - 1

    try:
        query = supabase.table("users").select(
            "id, username, email, full_name, role, created_at, updated_at",
            count="exact"
        )

        if role_f == "user":
            query = query.eq("role", "user")
        elif role_f == "admin":
            query = query.eq("role", "admin")
        elif role_f == "both":
            query = query.eq("role", "user,admin")

        if q:
            query = query.or_(
                f"username.ilike.%{q}%,"
                f"email.ilike.%{q}%,"
                f"full_name.ilike.%{q}%"
            )

        res   = query.order("created_at", desc=True).range(start, end).execute()
        total = res.count or 0
        users = res.data  or []

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


# ── AJAX: stats ─────────────────────────────────────────────────────────────
@admin_bp.route("/api/users/stats")
@require_admin_role
def api_users_stats():
    try:
        total  = (supabase.table("users").select("id", count="exact").execute().count or 0)
        users  = (supabase.table("users").select("id", count="exact").eq("role", "user").execute().count or 0)
        admins = (supabase.table("users").select("id", count="exact").eq("role", "admin").execute().count or 0)
        both   = (supabase.table("users").select("id", count="exact").eq("role", "user,admin").execute().count or 0)
        return jsonify({
            "total_users": total,
            "user_role":   users,
            "admin_role":  admins,
            "both_roles":  both,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"total_users":0,"user_role":0,"admin_role":0,"both_roles":0}), 500


# ── Update single user role ──────────────────────────────────────────────────
@admin_bp.route("/users/update-role", methods=["POST"])
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
        row = supabase.table("users").select("username, role").eq("id", user_id).execute()
        if row.data and _is_ghost(username=row.data[0].get("username"), role=row.data[0].get("role")):
            return jsonify({
                "success": False,
                "message": "System account cannot be modified."
            }), 403

        supabase.table("users").update({
            "role":       new_role,
            "updated_at": datetime.now().isoformat(),
        }).eq("id", user_id).execute()
        return jsonify({"success": True, "message": f"Role updated to {new_role}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ── Bulk update roles ────────────────────────────────────────────────────────
@admin_bp.route("/users/bulk-update-roles", methods=["POST"])
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
            row = supabase.table("users").select("username, role").eq("id", uid).execute()
            if row.data and _is_ghost(username=row.data[0].get("username"), role=row.data[0].get("role")):
                skipped += 1
                continue

            supabase.table("users").update({
                "role":       role,
                "updated_at": datetime.now().isoformat(),
            }).eq("id", uid).execute()
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