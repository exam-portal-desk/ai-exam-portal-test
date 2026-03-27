"""app/routes/admin/attempts.py — Attempt management (AJAX, no cartesian product)"""
from datetime import datetime
from flask import render_template, request, jsonify
from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams
from app.db import supabase


# ── Page load — zero data, AJAX se fetch hoga ───────────────────────────
@admin_bp.route("/attempts")
@require_admin_role
def attempts():
    exams = get_all_exams()   # small list, always fine
    return render_template("admin/attempts.html", exams=exams, rows=[])


# ── AJAX: paginated attempt rows ─────────────────────────────────────────
@admin_bp.route("/api/attempts/search")
@require_admin_role
def api_attempts_search():
    """
    Query params:
      q        – username ilike search
      exam_id  – filter by exam ('' = all)
      status   – '' | 'unlimited' | 'available' | 'exhausted'
      page     – int (default 1)
    Strategy:
      1. Find matching user IDs via users table (ilike on username)
      2. Fetch exam_attempts counts per (student_id, exam_id)
      3. Join with exam max_attempts to compute remaining
      4. Return paginated rows
    """
    q        = request.args.get("q", "").strip()
    exam_id  = request.args.get("exam_id", "").strip()
    status_f = request.args.get("status", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    per_page = 50

    try:
        # ── Step 1: resolve matching user IDs ─────────────────────────
        if q:
            ur = (supabase.table("users")
                  .select("id, username")
                  .or_(f"username.ilike.%{q}%,full_name.ilike.%{q}%")
                  .limit(500)          # guard against huge result
                  .execute())
            users_data = ur.data or []
        else:
            # No text search — we'll just get users from attempt records
            users_data = None   # signal: don't pre-filter

        # ── Step 2: build attempt counts query ───────────────────────
        att_q = (supabase.table("exam_attempts")
                 .select("student_id, exam_id, status"))

        if exam_id:
            att_q = att_q.eq("exam_id", exam_id)
        if users_data is not None:
            uids = [u["id"] for u in users_data]
            if not uids:
                # No matching users → empty result
                return jsonify({
                    "rows": [], "total": 0,
                    "page": page, "per_page": per_page, "total_pages": 1
                })
            att_q = att_q.in_("student_id", uids)

        atts = att_q.execute().data or []

        # ── Step 3: aggregate attempt counts ─────────────────────────
        from collections import defaultdict
        count_map = defaultdict(int)       # (student_id, exam_id) → count
        sid_set   = set()
        eid_set   = set()
        for a in atts:
            sid = a["student_id"]
            eid = a["exam_id"]
            count_map[(sid, eid)] += 1
            sid_set.add(sid)
            eid_set.add(eid)

        if not count_map:
            return jsonify({
                "rows": [], "total": 0,
                "page": page, "per_page": per_page, "total_pages": 1
            })

        # ── Step 4: fetch user names (only for seen IDs) ──────────────
        if users_data is not None:
            user_map = {u["id"]: u["username"] for u in users_data}
        else:
            ur2 = (supabase.table("users")
                   .select("id, username")
                   .in_("id", list(sid_set))
                   .execute())
            user_map = {u["id"]: u["username"] for u in (ur2.data or [])}

        # ── Step 5: fetch exam details (only for seen IDs) ────────────
        if exam_id:
            exams_r = (supabase.table("exams")
                       .select("id, name, max_attempts")
                       .eq("id", exam_id)
                       .execute())
        else:
            exams_r = (supabase.table("exams")
                       .select("id, name, max_attempts")
                       .in_("id", list(eid_set))
                       .execute())
        exam_map = {e["id"]: e for e in (exams_r.data or [])}

        # ── Step 6: build rows ────────────────────────────────────────
        all_rows = []
        for (sid, eid), used in count_map.items():
            uname = user_map.get(sid, str(sid))
            exam  = exam_map.get(eid, {})
            ename = exam.get("name", f"Exam {eid}")
            max_r = exam.get("max_attempts")

            if not max_r:
                display_max = "∞"
                remaining   = "∞"
                status_val  = "unlimited"
            else:
                try:
                    m = int(float(max_r))
                    remaining  = max(m - used, 0)
                    display_max = str(m)
                    status_val = "exhausted" if remaining == 0 else "available"
                except (ValueError, TypeError):
                    display_max = str(max_r)
                    remaining   = "?"
                    status_val  = "available"

            if status_f and status_val != status_f:
                continue

            all_rows.append({
                "student_id":    sid,
                "username":      uname,
                "exam_id":       eid,
                "exam_name":     ename,
                "max_attempts":  display_max,
                "attempts_used": used,
                "remaining":     remaining,
                "status":        status_val,
            })

        # ── Step 7: sort + paginate ───────────────────────────────────
        all_rows.sort(key=lambda r: (r["username"].lower(), r["exam_name"].lower()))
        total     = len(all_rows)
        start     = (page - 1) * per_page
        page_rows = all_rows[start: start + per_page]

        return jsonify({
            "rows":        page_rows,
            "total":       total,
            "page":        page,
            "per_page":    per_page,
            "total_pages": max(1, -(-total // per_page)),
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


# ── Single modify ────────────────────────────────────────────────────────
@admin_bp.route("/attempts/modify", methods=["POST"])
@require_admin_role
def attempts_modify():
    p          = request.get_json(force=True) or {}
    student_id = str(p.get("student_id", ""))
    exam_id    = str(p.get("exam_id", ""))
    action     = p.get("action", "")
    amount     = int(p.get("amount") or 1)

    current = (supabase.table("exam_attempts")
               .select("id")
               .eq("student_id", student_id)
               .eq("exam_id", exam_id)
               .execute().data or [])
    used = len(current)

    if action == "reset":
        for a in current:
            supabase.table("exam_attempts").delete().eq("id", a["id"]).execute()

    elif action == "decrease":
        if used < amount:
            return jsonify({"success": False, "message": "Not enough attempts to remove"}), 400
        for a in sorted(current, key=lambda x: x["id"])[-amount:]:
            supabase.table("exam_attempts").delete().eq("id", a["id"]).execute()

    elif action == "increase":
        for i in range(amount):
            supabase.table("exam_attempts").insert({
                "student_id":    int(student_id),
                "exam_id":       int(exam_id),
                "attempt_number": used + i + 1,
                "status":        "manual_add",
                "start_time":    datetime.now().isoformat(),
                "end_time":      None,
            }).execute()
    else:
        return jsonify({"success": False, "message": "Invalid action"}), 400

    return jsonify({"success": True})


# ── Bulk modify ──────────────────────────────────────────────────────────
@admin_bp.route("/attempts/bulk-modify", methods=["POST"])
@require_admin_role
def attempts_bulk_modify():
    data   = request.get_json() or {}
    items  = data.get("items", [])
    action = data.get("action", "")
    amount = int(data.get("amount") or 1)
    ok = 0; errors = []

    for item in items:
        sid = str(item.get("student_id", ""))
        eid = str(item.get("exam_id", ""))
        cur = (supabase.table("exam_attempts")
               .select("id")
               .eq("student_id", sid)
               .eq("exam_id", eid)
               .execute().data or [])
        used = len(cur)
        try:
            if action == "reset":
                for a in cur:
                    supabase.table("exam_attempts").delete().eq("id", a["id"]).execute()
            elif action == "decrease":
                if used < amount:
                    errors.append(f"uid={sid}/eid={eid}: not enough attempts"); continue
                for a in sorted(cur, key=lambda x: x["id"])[-amount:]:
                    supabase.table("exam_attempts").delete().eq("id", a["id"]).execute()
            elif action == "increase":
                for i in range(amount):
                    supabase.table("exam_attempts").insert({
                        "student_id":    int(sid),
                        "exam_id":       int(eid),
                        "attempt_number": used + i + 1,
                        "status":        "manual_add",
                        "start_time":    datetime.now().isoformat(),
                    }).execute()
            ok += 1
        except Exception as e:
            errors.append(f"uid={sid}/eid={eid}: {e}")

    return jsonify({"success": ok > 0, "processed": ok, "errors": errors or None})