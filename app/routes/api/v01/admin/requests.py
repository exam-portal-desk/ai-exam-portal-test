"""
app/routes/api/v01/admin/requests.py
Admin access-requests JSON API (v01). Relocated from
app/routes/admin/requests.py.

  GET    /api/v01/admin/access-requests                 -> paginated + filterable list
                                                             (status/q/current_role/requested_role/date_from/date_to)
  POST   /api/v01/admin/access-requests/<id>/approve
  POST   /api/v01/admin/access-requests/<id>/deny
  DELETE /api/v01/admin/access-requests/<id>             -> soft delete (hide, keep audit row)
  GET    /api/v01/admin/access-requests/stats
"""

from app.utils.datetime_service import now_utc_naive, format_display
from flask import request, jsonify, session

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import update_request, soft_delete_request
from app.db import fetch_one, fetch_all, execute
from app.utils.pagination import paginate_params, pagination_meta, attach_row_numbers

_VALID_ROLES  = ("user", "admin", "user,admin")
_VALID_STATUS = ("pending", "completed", "denied")


@admin_api_bp.route("/access-requests")
@require_admin_role
def api_requests_list():
    """Single paginated + filterable endpoint used by both the New Requests
    tab (status=pending, no other filters sent) and the History tab (any
    status, plus search/role/date filters). Soft-deleted rows are always
    excluded."""
    status         = request.args.get("status", "pending").strip().lower()
    q              = request.args.get("q", "").strip()
    current_role   = request.args.get("current_role", "").strip().lower()
    requested_role = request.args.get("requested_role", "").strip().lower()
    date_from      = request.args.get("date_from", "").strip()
    date_to        = request.args.get("date_to", "").strip()
    page, per_page, offset = paginate_params(request.args.get("page"), 25, max_per_page=100)

    where, params = ["is_deleted = FALSE"], []

    if status in _VALID_STATUS:
        where.append("request_status = %s")
        params.append(status)
    elif status != "all":
        # Any other/unknown value (including the old implicit "history"
        # caller) falls back to the original pending-vs-processed split so
        # existing behaviour for anyone still passing status=history is
        # unchanged.
        where.append("request_status = ANY(%s)")
        params.append(["completed", "denied"])

    if q:
        where.append("(username ILIKE %s OR email ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if current_role in _VALID_ROLES:
        where.append("current_access = %s")
        params.append(current_role)
    if requested_role in _VALID_ROLES:
        where.append("requested_access = %s")
        params.append(requested_role)
    if date_from:
        where.append("request_date >= %s::date")
        params.append(date_from)
    if date_to:
        where.append("request_date < (%s::date + INTERVAL '1 day')")
        params.append(date_to)

    where_sql = " AND ".join(where)

    total = fetch_one(f"SELECT COUNT(*) AS count FROM requests_raised WHERE {where_sql}", params)["count"]
    reqs = fetch_all(
        f"SELECT * FROM requests_raised WHERE {where_sql} ORDER BY request_date DESC LIMIT %s OFFSET %s",
        params + [per_page, offset],
    )
    attach_row_numbers(reqs, page, per_page)

    return jsonify({"requests": [_fmt(r) for r in reqs], **pagination_meta(total, page, per_page)})


def _raw_iso_utc(value):
    """request_date/processed_date come back as naive-UTC datetimes or
    already-ISO strings depending on the driver/cursor path — normalize
    either into a 'Z'-suffixed ISO string the frontend can hand straight
    to `new Date()` for the "pending for Xh" age indicator."""
    if not value:
        return None
    iso = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return iso if iso.endswith("Z") else iso + "Z"


def _fmt(r):
    return {
        "request_id":       int(r.get("request_id", 0)),
        "row_no":           r.get("row_no"),
        "username":         r.get("username", ""),
        "email":            r.get("email", ""),
        "current_access":   r.get("current_access", ""),
        "requested_access": r.get("requested_access", ""),
        "request_date":     format_display(r.get("request_date")),
        # Raw UTC ISO timestamp alongside the display string — the display
        # string is locale-formatted for reading, not for parsing, so the
        # "pending for Xh" age indicator needs this instead.
        "request_date_raw": _raw_iso_utc(r.get("request_date")),
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

    req = fetch_one(
        "SELECT * FROM requests_raised WHERE request_id=%s AND request_status=%s AND is_deleted=FALSE",
        (request_id, "pending"),
    )
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

    req = fetch_one(
        "SELECT * FROM requests_raised WHERE request_id=%s AND request_status=%s AND is_deleted=FALSE",
        (request_id, "pending"),
    )
    if not req:
        return jsonify({"success": False, "message": "Not found or already processed"}), 404

    final_reason = (req.get("reason", "") or "") + f"\n[ADMIN DENIAL] {reason}"
    update_request(request_id, {
        "request_status": "denied", "reason": final_reason,
        "processed_by": session.get("username", "Admin"),
        "processed_date": now_utc_naive().isoformat()
    })
    return jsonify({"success": True, "message": "Request denied."})


@admin_api_bp.route("/access-requests/<int:request_id>", methods=["DELETE"])
@require_admin_role
def delete_request(request_id):
    """Soft delete — hides the row from every list view but never touches
    users.role and never erases the row itself, so this can't be confused
    with (or accidentally cause) revoking an already-granted access change.
    Works on pending rows (clears it from the queue without approving or
    denying it) and on processed rows (clears it from History) alike."""
    req = fetch_one("SELECT request_id FROM requests_raised WHERE request_id=%s AND is_deleted=FALSE", (request_id,))
    if not req:
        return jsonify({"success": False, "message": "Request not found or already removed"}), 404

    ok = soft_delete_request(request_id, session.get("username", "Admin"))
    if not ok:
        return jsonify({"success": False, "message": "Failed to remove request"}), 500

    return jsonify({"success": True, "message": "Request removed. No user role or access was changed."})


@admin_api_bp.route("/access-requests/stats")
@require_admin_role
def api_requests_stats():
    # Single grouped query instead of 3 sequential COUNT round trips
    # (flagged in the architecture audit).
    rows = fetch_all(
        "SELECT request_status, COUNT(*) AS count FROM requests_raised WHERE is_deleted=FALSE GROUP BY request_status"
    )
    counts = {row["request_status"]: row["count"] for row in rows}
    pending   = counts.get("pending", 0)
    completed = counts.get("completed", 0)
    denied    = counts.get("denied", 0)
    return jsonify({"pending": pending, "completed": completed, "denied": denied, "total": pending + completed + denied})
