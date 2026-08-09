"""app/routes/admin/requests.py — Access request management"""
from app.utils.datetime_service import now_utc_naive, format_display
from flask import render_template, request, jsonify, session
from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import update_request
from app.db import fetch_one, fetch_all, execute


@admin_bp.route("/requests")
@require_admin_role
def requests_dashboard():
    # Koi data nahi bhejte — AJAX se aayega
    return render_template("admin/requests.html",
        pending_requests=[],
        history_requests=[],
        users=[])


@admin_bp.route("/api/requests/list")
@require_admin_role
def api_requests_list():
    """AJAX endpoint for paginated requests"""
    status   = request.args.get("status", "pending")   # pending / completed / denied
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 25

    if status == "pending":
        where, where_params = "request_status=%s", ["pending"]
    else:
        where, where_params = "request_status = ANY(%s)", [["completed", "denied"]]

    total = fetch_one(f"SELECT COUNT(*) AS count FROM requests_raised WHERE {where}", where_params)["count"]

    start = (page - 1) * per_page
    reqs = fetch_all(
        f"SELECT * FROM requests_raised WHERE {where} ORDER BY request_date DESC LIMIT %s OFFSET %s",
        where_params + [per_page, start],
    )

    formatted = [_fmt(r) for r in reqs]

    return jsonify({
        "requests":    formatted,
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": max(1, -(-total // per_page)),
    })


def _fmt(r):
    return {
        "request_id":       int(r.get("request_id", 0)),
        "username":         r.get("username", ""),
        "email":            r.get("email", ""),
        "current_access":   r.get("current_access", ""),
        "requested_access": r.get("requested_access", ""),
        "request_date":     format_display(r.get("request_date")),
        "status":           r.get("request_status", ""),
        "reason":           r.get("reason", "") or "",
        "processed_by":     r.get("processed_by", "Admin"),
        "processed_date":   format_display(r.get("processed_date")),
    }


@admin_bp.route("/requests/approve/<int:request_id>", methods=["POST"])
@require_admin_role
def approve_request(request_id):
    data     = request.get_json() or {}
    approved = data.get("approved_access", "").strip()
    if not approved:
        return jsonify({"success": False, "message": "Please select an access level"}), 400

    req = fetch_one("SELECT * FROM requests_raised WHERE request_id=%s AND request_status=%s", (request_id, "pending"))
    if not req:
        return jsonify({"success": False, "message": "Request not found or already processed"}), 404

    user_r = fetch_one("SELECT id FROM users WHERE username=%s AND email=%s", (req["username"], req["email"]))
    if not user_r:
        return jsonify({"success": False, "message": "User not found"}), 404

    execute("UPDATE users SET role=%s, updated_at=%s WHERE id=%s", (approved, now_utc_naive().isoformat(), user_r["id"]))

    reason = (req.get("reason", "") or "") + f"\n[ADMIN APPROVAL] Approved: {approved}"
    update_request(request_id, {
        "request_status": "completed", "reason": reason,
        "processed_by": session.get("username", "Admin"),
        "processed_date": now_utc_naive().isoformat()
    })
    return jsonify({"success": True, "message": f"Approved. User now has {approved} access."})


@admin_bp.route("/requests/deny/<int:request_id>", methods=["POST"])
@require_admin_role
def deny_request(request_id):
    data   = request.get_json() or {}
    reason = data.get("reason", "").strip()
    if not reason:
        return jsonify({"success": False, "message": "Please provide a denial reason"}), 400

    req = fetch_one("SELECT * FROM requests_raised WHERE request_id=%s AND request_status=%s", (request_id, "pending"))
    if not req:
        return jsonify({"success": False, "message": "Not found or already processed"}), 404

    final_reason = (req.get("reason", "") or "") + f"\n[ADMIN DENIAL] {reason}"
    update_request(request_id, {
        "request_status": "denied", "reason": final_reason,
        "processed_by": session.get("username", "Admin"),
        "processed_date": now_utc_naive().isoformat()
    })
    return jsonify({"success": True, "message": "Request denied."})


@admin_bp.route("/api/requests/stats")
@require_admin_role
def api_requests_stats():
    pending   = fetch_one("SELECT COUNT(*) AS count FROM requests_raised WHERE request_status=%s", ("pending",))["count"]
    completed = fetch_one("SELECT COUNT(*) AS count FROM requests_raised WHERE request_status=%s", ("completed",))["count"]
    denied    = fetch_one("SELECT COUNT(*) AS count FROM requests_raised WHERE request_status=%s", ("denied",))["count"]
    return jsonify({"pending": pending, "completed": completed, "denied": denied, "total": pending + completed + denied})