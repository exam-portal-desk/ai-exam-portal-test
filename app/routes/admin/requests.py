"""app/routes/admin/requests.py — Access request management"""
from datetime import datetime
from flask import render_template, request, jsonify, session
from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.misc import update_request
from app.db import supabase


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

    query = supabase.table("requests_raised").select("*", count="exact")

    if status == "pending":
        query = query.eq("request_status", "pending")
    else:
        query = query.in_("request_status", ["completed", "denied"])

    start = (page - 1) * per_page
    query = query.order("request_date", desc=True).range(start, start + per_page - 1)

    res   = query.execute()
    total = res.count or 0
    reqs  = res.data or []

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
        "request_date":     r.get("request_date", ""),
        "status":           r.get("request_status", ""),
        "reason":           r.get("reason", "") or "",
        "processed_by":     r.get("processed_by", "Admin"),
        "processed_date":   r.get("processed_date", ""),
    }


@admin_bp.route("/requests/approve/<int:request_id>", methods=["POST"])
@require_admin_role
def approve_request(request_id):
    data     = request.get_json() or {}
    approved = data.get("approved_access", "").strip()
    if not approved:
        return jsonify({"success": False, "message": "Please select an access level"}), 400

    req = supabase.table("requests_raised").select("*").eq("request_id", request_id).eq("request_status", "pending").execute().data
    if not req:
        return jsonify({"success": False, "message": "Request not found or already processed"}), 404
    req = req[0]

    user_r = supabase.table("users").select("id").eq("username", req["username"]).eq("email", req["email"]).execute().data
    if not user_r:
        return jsonify({"success": False, "message": "User not found"}), 404

    supabase.table("users").update({
        "role": approved, "updated_at": datetime.now().isoformat()
    }).eq("id", user_r[0]["id"]).execute()

    reason = (req.get("reason", "") or "") + f"\n[ADMIN APPROVAL] Approved: {approved}"
    update_request(request_id, {
        "request_status": "completed", "reason": reason,
        "processed_by": session.get("username", "Admin"),
        "processed_date": datetime.now().isoformat()
    })
    return jsonify({"success": True, "message": f"Approved. User now has {approved} access."})


@admin_bp.route("/requests/deny/<int:request_id>", methods=["POST"])
@require_admin_role
def deny_request(request_id):
    data   = request.get_json() or {}
    reason = data.get("reason", "").strip()
    if not reason:
        return jsonify({"success": False, "message": "Please provide a denial reason"}), 400

    req = supabase.table("requests_raised").select("*").eq("request_id", request_id).eq("request_status", "pending").execute().data
    if not req:
        return jsonify({"success": False, "message": "Not found or already processed"}), 404
    req = req[0]

    final_reason = (req.get("reason", "") or "") + f"\n[ADMIN DENIAL] {reason}"
    update_request(request_id, {
        "request_status": "denied", "reason": final_reason,
        "processed_by": session.get("username", "Admin"),
        "processed_date": datetime.now().isoformat()
    })
    return jsonify({"success": True, "message": "Request denied."})


@admin_bp.route("/api/requests/stats")
@require_admin_role
def api_requests_stats():
    pending   = supabase.table("requests_raised").select("request_id", count="exact").eq("request_status", "pending").execute().count or 0
    completed = supabase.table("requests_raised").select("request_id", count="exact").eq("request_status", "completed").execute().count or 0
    denied    = supabase.table("requests_raised").select("request_id", count="exact").eq("request_status", "denied").execute().count or 0
    return jsonify({"pending": pending, "completed": completed, "denied": denied, "total": pending + completed + denied})