"""
app/routes/api/v01/admin/attempts.py
Admin attempt-management JSON API (v01). Relocated from
app/routes/admin/attempts.py.

  GET  /admin/api/attempts/search  -> GET  /api/v01/admin/attempts/search
  POST /admin/attempts/modify      -> POST /api/v01/admin/attempts/modify
  POST /admin/attempts/bulk-modify -> POST /api/v01/admin/attempts/bulk-modify

NOTE on scope: api_attempts_search's pagination (fetch every matching
exam_attempts row, aggregate/sort/paginate in Python) is flagged in the
architecture audit as a High-severity issue — a true fix means pushing
the (student_id, exam_id) aggregation, the exam.max_attempts join, and
the derived status filter into SQL, which changes this endpoint's query
shape substantially. Given the risk of a live-data admin tool silently
returning wrong rows with no test suite to catch a mistake, that rewrite
is intentionally deferred rather than attempted here — relocated as-is.
The two write endpoints below DID get their flagged fix applied (batched
DELETE/INSERT instead of one statement per row), since that's a
mechanical, low-risk change that can't alter which rows are affected.
"""
from flask import request, jsonify
from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db import fetch_all, execute, insert_many
from app.utils.datetime_service import now_utc_naive


@admin_api_bp.route("/attempts/search")
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
            users_data = fetch_all(
                "SELECT id, username FROM users WHERE username ILIKE %s OR full_name ILIKE %s LIMIT 500",
                (f"%{q}%", f"%{q}%"),
            )
        else:
            # No text search — we'll just get users from attempt records
            users_data = None   # signal: don't pre-filter

        # ── Step 2: build attempt counts query ───────────────────────
        att_query = "SELECT student_id, exam_id, status FROM exam_attempts WHERE 1=1"
        att_params = []
        if exam_id:
            att_query += " AND exam_id=%s"
            att_params.append(exam_id)
        if users_data is not None:
            uids = [u["id"] for u in users_data]
            if not uids:
                # No matching users → empty result
                return jsonify({
                    "rows": [], "total": 0,
                    "page": page, "per_page": per_page, "total_pages": 1
                })
            att_query += " AND student_id = ANY(%s)"
            att_params.append(uids)

        atts = fetch_all(att_query, att_params)

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
            ur2 = fetch_all("SELECT id, username FROM users WHERE id = ANY(%s)", (list(sid_set),))
            user_map = {u["id"]: u["username"] for u in ur2}

        # ── Step 5: fetch exam details (only for seen IDs) ────────────
        if exam_id:
            exams_r = fetch_all("SELECT id, name, max_attempts FROM exams WHERE id=%s", (exam_id,))
        else:
            exams_r = fetch_all("SELECT id, name, max_attempts FROM exams WHERE id = ANY(%s)", (list(eid_set),))
        exam_map = {e["id"]: e for e in exams_r}

        # ── Step 6: build rows ────────────────────────────────────────
        all_rows = []
        for (sid, eid), used in count_map.items():
            uname = user_map.get(sid, "Deleted User")
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


def _apply_attempt_action(student_id: str, exam_id: str, action: str, amount: int):
    """Shared by attempts_modify and attempts_bulk_modify. Batched
    DELETE/INSERT instead of one statement per row (flagged in the
    architecture audit)."""
    current = fetch_all("SELECT id FROM exam_attempts WHERE student_id=%s AND exam_id=%s", (student_id, exam_id))
    used = len(current)

    if action == "reset":
        if current:
            execute("DELETE FROM exam_attempts WHERE id = ANY(%s)", ([a["id"] for a in current],))

    elif action == "decrease":
        if used < amount:
            return False, "Not enough attempts to remove"
        to_remove = sorted(current, key=lambda x: x["id"])[-amount:]
        execute("DELETE FROM exam_attempts WHERE id = ANY(%s)", ([a["id"] for a in to_remove],))

    elif action == "increase":
        insert_many("exam_attempts", [
            {
                "student_id":     int(student_id),
                "exam_id":        int(exam_id),
                "attempt_number": used + i + 1,
                "status":         "manual_add",
                "start_time":     now_utc_naive().isoformat(),
                "end_time":       None,
            }
            for i in range(amount)
        ])
    else:
        return False, "Invalid action"

    return True, None


# ── Single modify ────────────────────────────────────────────────────────
@admin_api_bp.route("/attempts/modify", methods=["POST"])
@require_admin_role
def attempts_modify():
    p          = request.get_json(force=True) or {}
    student_id = str(p.get("student_id", ""))
    exam_id    = str(p.get("exam_id", ""))
    action     = p.get("action", "")
    amount     = int(p.get("amount") or 1)

    ok, err = _apply_attempt_action(student_id, exam_id, action, amount)
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    return jsonify({"success": True})


# ── Bulk modify ──────────────────────────────────────────────────────────
@admin_api_bp.route("/attempts/bulk-modify", methods=["POST"])
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
        try:
            success, err = _apply_attempt_action(sid, eid, action, amount)
            if not success:
                errors.append(f"uid={sid}/eid={eid}: {err}")
                continue
            ok += 1
        except Exception as e:
            errors.append(f"uid={sid}/eid={eid}: {e}")

    return jsonify({"success": ok > 0, "processed": ok, "errors": errors or None})
