"""
app/routes/api/v01/admin/requests.py
Admin access-requests JSON API (v01). Relocated from
app/routes/admin/requests.py.

  GET  /admin/api/requests/list           -> GET  /api/v01/admin/access-requests
  POST /admin/requests/approve/<id>       -> POST /api/v01/admin/access-requests/<id>/approve
  POST /admin/requests/deny/<id>          -> POST /api/v01/admin/access-requests/<id>/deny
  GET  /admin/api/requests/stats          -> GET  /api/v01/admin/access-requests/stats
"""

from app.utils.datetime_service import now_utc_naive, format_display
from flask import request, jsonify, session

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import update_request
from app.db import fetch_one, fetch_all, execute


@admin_api_bp.route("/access-requests")
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


@admin_api_bp.route("/access-requests/<int:request_id>/approve", methods=["POST"])
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


@admin_api_bp.route("/access-requests/<int:request_id>/deny", methods=["POST"])
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


@admin_api_bp.route("/access-requests/stats")
@require_admin_role
def api_requests_stats():
    # Single grouped query instead of 3 sequential COUNT round trips
    # (flagged in the architecture audit).
    rows = fetch_all("SELECT request_status, COUNT(*) AS count FROM requests_raised GROUP BY request_status")
    counts = {row["request_status"]: row["count"] for row in rows}
    pending   = counts.get("pending", 0)
    completed = counts.get("completed", 0)
    denied    = counts.get("denied", 0)
    return jsonify({"pending": pending, "completed": completed, "denied": denied, "total": pending + completed + denied})
